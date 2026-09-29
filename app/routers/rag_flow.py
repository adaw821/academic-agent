from pathlib import Path
from uuid import uuid4
import json
import logging
from time import perf_counter

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.services.chunk import chunking
from app.db.database import get_db, SessionLocal
from app.models.documents import Document
from app.models.projects import Project
from app.schemas.project_create import ProjectCreate
from app.models.users import User
from app.schemas.search import SearchRequest
from app.services.search_services import search_vector, search_bm25, rrf_fuse, rerank_results, history_messages_control, rewrite_query, search_document_first
from app.services.answer_services import generate_answer, get_history_messages
from app.services.user_services import get_currentUser

logger = logging.getLogger("uvicorn.error")
router = APIRouter()
FILE_TYPES = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation"
    ),
    ".docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
}

UPLOAD_ROOT = Path("data/uploads")


# 【前端配套新增：项目权限检查】供文档列表、文档状态、问答接口复用。
def require_project(db: Session, project_id: int, user: User) -> Project:
    # 登录校验解决“是谁”，这里进一步检查“能否访问这个项目”。
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return project
# 【前端配套新增结束：项目权限检查】


# 【前端配套新增：项目列表】前端侧栏展示当前用户的项目，刷新后也能重新读取。
@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    # where 限定项目归属；按 ID 倒序展示。scalars().all() 取出多个 Project 对象。
    projects = db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.id.desc())).all()
    # 将数据库对象整理成前端需要的 JSON 字段。
    return [{"id": p.id, "name": p.name} for p in projects]
# 【前端配套新增结束：项目列表】


# 【前端配套新增：文档列表】前端资料栏读取文件，并轮询排队、解析和完成状态。
@router.get("/projects/{project_id}/documents")
def list_documents(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    # 必须先确认项目属于当前用户，再读取该项目的文档。
    require_project(db, project_id, user)
    documents = db.scalars(select(Document).where(Document.project_id == project_id).order_by(Document.id.desc())).all()
    return [{"id": d.id, "filename": d.filename, "status": d.status} for d in documents]


# 普通 def 后台任务由框架放到线程池执行，使用独立数据库会话
def process_document(document_id: int):
    with SessionLocal() as db:
        try:
            document = db.get(Document, document_id)
            if document is None:
                raise ValueError(f"无效文档查询id: {document_id}")
            document.status = "processing"
            db.commit()

            content = Path(document.storage_path).read_bytes()
            chunking(
                content=content,
                db=db,
                document_id=document_id,
                file_name=document.filename,
            )

            # 分片和完成状态一起提交
            document.status = "completed"
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("event=document_processing_failed document_id=%s", document_id)
            # 保留原文件；回滚不完整的分片后，单独记录失败状态
            try:
                document = db.get(Document, document_id)
                if document is not None:
                    document.status = "failed"
                    db.commit()
            except Exception:
                db.rollback()
                logger.exception("event=document_status_update_failed document_id=%s", document_id)


@router.post("/projects/{project_id}/documents", status_code=202)
async def upload_files(
    project_id: int,
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_currentUser)
):
    """保存上传文件，返回后在后台切片入库。"""
    #计时起点
    request_started_at = perf_counter()

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    if project.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该项目")

    #给当前项目准备一个存放文件的文件夹
    project_directory = UPLOAD_ROOT / f"project_{project_id}"
    project_directory.mkdir(parents=True, exist_ok=True)

    saved_files = []
    failed_files = []

    #定义一个空列表，记录本次上传的文件路径，失败会删除
    created_paths: list[Path] = []

    file_save_ms = 0.0

    try:
        """处理一次性上传大量文件的情况，然后交给chunk分片"""
        for file in files:
            original_filename = Path(file.filename or "").name.strip()
            suffix = Path(original_filename).suffix.lower()

            #假如后缀不在需要类型就报错
            if not original_filename or suffix not in FILE_TYPES:
                failed_files.append(original_filename)
                continue

            content = await file.read()
            if not content:
                failed_files.append(original_filename)
                continue

            #生成一个随机文件名，并保留原来的扩展名
            stored_filename = f"{uuid4().hex}{suffix}"
            storage_path = project_directory / stored_filename

            #文件保存时间测速
            file_save_started_at = perf_counter()
            storage_path.write_bytes(content)
            file_save_ms += (perf_counter() - file_save_started_at) * 1000

            created_paths.append(storage_path)

            content_type = FILE_TYPES[suffix]

            document_record = Document(
                project_id=project_id,
                filename=original_filename,
                content_type=content_type,
                storage_path=storage_path.as_posix(),
                #状态默认为queued（文件进入上传队列)配合后台改动
                status="queued",
            )
            db.add(document_record)
            db.flush()
            saved_files.append(
                {
                    "id": document_record.id,
                    "filename": original_filename,
                    "status": document_record.status,
                }
            )

        commit_started_at = perf_counter()
        db.commit()
        commit_ms = (perf_counter() - commit_started_at) * 1000

    except Exception:
        db.rollback()
        for path in created_paths:
            path.unlink(missing_ok=True)
        logger.exception(
            "event=document_upload_failed project_id=%s total_ms=%.2f "
            "saved_count=%s failed_count=%s",
            project_id,
            (perf_counter() - request_started_at) * 1000,
            len(saved_files),
            len(failed_files),
        )
        raise HTTPException(status_code=500, detail="文件保存失败")
    finally:
        for file in files:
            await file.close()

    logger.info(
        "event=document_upload_completed project_id=%s total_ms=%.2f "
        "file_save_ms=%.2f commit_ms=%.2f saved_count=%s failed_count=%s",
        project_id,
        (perf_counter() - request_started_at) * 1000,
        file_save_ms,
        commit_ms,
        len(saved_files),
        len(failed_files),
    )

    # 文档记录提交成功后再登记任务，不传递本次请求的 db 或 UploadFile
    for item in saved_files:
        background_tasks.add_task(process_document, document_id=item["id"])

    return {
        "message": "上传检查完成，已保存文件等待后台处理",
        "saved_files": saved_files,
        "failed_files": failed_files,
    }


@router.get("/projects/{project_id}/documents/{document_id}")
def get_document_status(
    project_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    # 【前端配套修改】原接口补上登录检查，user 来自有效会话。
    user: User = Depends(get_currentUser),
):
    # 【前端配套修改】先检查项目归属，避免登录用户查询别人的文档状态。
    require_project(db, project_id, user)
    document = db.get(Document, document_id)
    if document is None or document.project_id != project_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {
        "id": document.id,
        "filename": document.filename,
        "status": document.status,
    }


@router.post("/projects", status_code=201)
async def project_creator(
    project: ProjectCreate,
    db: Session = Depends(get_db),
    # 【前端配套修改】根据 Cookie 得到当前用户，替代按请求中的 user_id 查用户。
    user: User = Depends(get_currentUser),
):
    new_project = Project(
        user_id=user.id,
        name=project.name,
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    return {
        "message": "项目创建成功",
        "project_id": new_project.id,
    }

@router.post("/projects/{project_id}/ask")
def user_asking(
    project_id: int,
    request: SearchRequest,
    db: Session = Depends(get_db),
    #提问前先验证登录状态。
    user: User = Depends(get_currentUser),
):
    """问答检索接口"""
    # 替代原来只检查项目存在的逻辑，同时检查项目归属。
    # 通过后继续调用你原有的检索与回答函数。
    require_project(db, project_id, user)

    selection = None
    if request.document_id is not None:
        document = db.get(Document, request.document_id)
        if document is None or document.project_id != project_id:
            raise HTTPException(status_code=404, detail="文档不存在")
        if document.status != "completed":
            raise HTTPException(status_code=409, detail="文档尚未处理完成，暂不能进行问答")
        selection = {
            "document_id": document.id,
            "filename": document.filename,
            "selected_text": request.selected_text,
        }

    #检索前校验会话并读取一次历史，供问题改写和最终回答共用。
    history, summary, summary_boundary = get_history_messages(request.conversation_id, db, project_id)
    retrieval_input = request.query
    if request.document_id is not None:
        retrieval_input = (
            f"用户问题：\n{request.query}\n\n"
            f"当前选中的原文（待分析资料）：\n{request.selected_text}"
        )
    search_query = rewrite_query(retrieval_input, history, summary=summary)
    if request.document_id is not None:
        final_chunks = search_document_first(
            search_query, project_id, request.document_id, db, request.candidate_k,
        )
    else:
        # 普通问答保持原来的全项目混合检索流程。
        results_vector = search_vector(search_query, project_id, db, request.candidate_k)
        results_BM25 = search_bm25(search_query, project_id, db, request.candidate_k)
        merged = rrf_fuse(results_vector, results_BM25)
        final_chunks = rerank_results(search_query, merged)

    #生成回答
    final_result = generate_answer(
        request.query, final_chunks, history, request.conversation_id, db, project_id,
        summary=summary,
        summary_boundary=summary_boundary,
        selection=selection,
    )
    # 保留原问题和选区，不用改写后的检索问题覆盖用户原意；复用现有消息表。
    history_query = request.query
    if selection is not None:
        history_query = json.dumps({"query": request.query, "selection": selection}, ensure_ascii=False)
    conversation_id = history_messages_control(final_result["answer"], history_query, request.conversation_id, db, project_id)
    final_result["conversation_id"] = conversation_id
    if selection is not None:
        final_result["selection"] = selection
    return final_result

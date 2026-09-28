from pathlib import Path
from uuid import uuid4
import logging
from time import perf_counter
import os
import json

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
from app.services.search_services import search_vector, search_bm25, rrf_fuse, rerank_results
from app.services.answer_services import generate_answer
from app.services.user_services import get_currentUser

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

translator = ChatOpenAI(
    model = "glm-4.5-airx",
    base_url="https://open.bigmodel.cn/api/paas/v4/",
    api_key=os.environ["ZHIPU_API_KEY"],
    timeout=15,
    max_retries=0,
)

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
    user: User = Depends(get_currentUser),
):
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

def require_document_scope(
    db: Session, project_id: int, doc_ids: list[int] | None
) -> list[int] | None:
    """项目鉴权后校验显式文档范围；任何无效 ID 都不能静默忽略。"""
    if doc_ids is None:
        return None
    if not doc_ids:
        raise HTTPException(status_code=422, detail="doc_ids 不能为空列表")

    unique_ids = list(dict.fromkeys(doc_ids))
    documents = db.execute(
        select(Document.id, Document.status).where(
            Document.project_id == project_id,
            Document.id.in_(unique_ids),
        )
    ).all()
    if len(documents) != len(unique_ids):
        raise HTTPException(status_code=404, detail="指定文档不存在或不属于当前项目")
    if any(document.status != "completed" for document in documents):
        raise HTTPException(status_code=409, detail="指定文档尚未完成处理，请检查文档状态")
    return unique_ids


@router.post("/projects/{project_id}/ask")
def user_asking(
    project_id: int,
    request: SearchRequest,
    db: Session = Depends(get_db),
    #提问前先验证登录状态。
    user: User = Depends(get_currentUser),
):
    """问答检索接口"""
    # 先确认检索范围有效，再调用可选的翻译服务。
    require_project(db, project_id, user)
    doc_ids = require_document_scope(db, project_id, request.doc_ids)
    original_query = request.query
    translated_query = None

    #对问题进行翻译 中英文一起检索
    translation_message = [
        SystemMessage(
            content=(
                "你是一个专业的问题翻译助手。\n\n"
                "任务：\n"
                "- 如果输入问题主要为中文，将其翻译成英文。\n"
                "- 如果输入问题主要为英文，将其翻译成中文。\n\n"
                "要求：\n"
                "1. 准确保留原问题的语义，不得增加、删除或猜测任何信息。\n"
                "2. 专业术语必须使用对应领域中标准、常用的专业译法。\n"
                "3. 产品名、型号、缩写、代码、专有名词等，如果没有必要，不要翻译或改写。\n"
                "4. 不要回答用户的问题，只进行翻译。\n"
                "5. 不要解释翻译过程，不要添加任何说明、前缀或后缀。\n"
                "6. 保持原问题的检索意图，避免为了语言自然度而改变关键词含义。\n"
                "7. 如果某个术语存在歧义且无法根据原文确定，不要自行猜测，尽可能保留原术语。\n\n"
                "必须严格返回合法 JSON，不要使用 Markdown 代码块，也不要输出其他内容。\n"
                '翻译失败时，将 translation 字段设为 JSON 的 null，不要返回字符串 "None"。\n'
                "返回格式如下：\n"
                "{\n"
                '  "original": "原始问题",\n'
                '  "translation": "翻译后的问题"\n'
                "}"
            )
        ),
        HumanMessage(
            content=f"query={request.query}"
        )
    ]

    # 翻译仅为辅助；调用、解析或字段校验失败都回退到原问题。
    try:
        response = translator.invoke(translation_message)
        translation_results = json.loads(response.content)
        if not isinstance(translation_results, dict):
            raise ValueError("翻译结果必须是 JSON 对象")
        translation = translation_results["translation"]
        if translation is not None:
            if not isinstance(translation, str):
                raise ValueError("translation 必须是字符串或 null")
            translation = translation.strip()
            if not translation or translation.casefold() in {"none", "null"}:
                raise ValueError("译文为空或无效")
            translated_query = translation
    except Exception as exc:
        # 不将用户问题、模型原始输出或异常详情写入日志。
        logger.warning("event=query_translation_failed error_type=%s", type(exc).__name__)
        translated_query = None

    #两次中英文问题得到的检索结果
    original_vector_results, original_BM25_results  = retrieve_documents(original_query, project_id, db, request.candidate_k_percentage, doc_ids)
    if translated_query is not None:
        translated_vector_results, translated_BM25_results = retrieve_documents(translated_query, project_id, db, request.candidate_k_percentage, doc_ids)
    else:
        translated_vector_results, translated_BM25_results = [], []

    results_vector = original_vector_results + translated_vector_results
    results_BM25 = original_BM25_results + translated_BM25_results
    #rrf排序
    merged=rrf_fuse(results_vector, results_BM25)
    final_chunks = rerank_results(request.query, translated_query, merged)
    return generate_answer(request.query, final_chunks)

def retrieve_documents(
        query: str,
        project_id: int,
        db: Session,
        candidate_k_percentage: float,
        doc_ids: list[int] | None
):
    #向量检索结果
    results_vector = search_vector(
        query, project_id, db, candidate_k_percentage, doc_ids=doc_ids
    )

    #存关键词检索结果
    results_BM25 = search_bm25(
        query, project_id, db, candidate_k_percentage, doc_ids=doc_ids
    )

    return results_vector,results_BM25

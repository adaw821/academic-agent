from app.services.performance import measure_stage, timed_stage, logger as performance_logger
from app.services.workflow_graphs import build_qa_graph
from pathlib import Path
from uuid import uuid4
import json
import os
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


def process_document(document_id: int):
    from app.models.workflow_run import WorkflowRun
    from app.services.durable_workflows import new_run, run_workflow
    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document is None:
            return
        run = db.scalar(select(WorkflowRun).where(WorkflowRun.document_id == document_id))
        if run is None:
            run = new_run(db, document.project_id, document_id=document_id)
            db.commit()
        run_id = run.id
    run_workflow(run_id)


@router.post("/projects/{project_id}/documents", status_code=202)
@timed_stage("upload.total")
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

            with measure_stage("upload.file_read"):
                content = await file.read()
            if not content:
                failed_files.append(original_filename)
                continue

            #生成一个随机文件名，并保留原来的扩展名
            stored_filename = f"{uuid4().hex}{suffix}"
            storage_path = project_directory / stored_filename

            #文件保存时间测速
            file_save_started_at = perf_counter()
            with measure_stage("upload.file_save", size_bytes=len(content)):
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
            with measure_stage("upload.db_flush"):
                db.flush()
            from app.services.durable_workflows import new_run
            run = new_run(db, project_id, document_id=document_record.id)
            saved_files.append(
                {
                    "id": document_record.id,
                    "filename": original_filename,
                    "status": document_record.status,
                    "job_id": run.id,
                }
            )

        commit_started_at = perf_counter()
        with measure_stage("upload.commit"):
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

    performance_logger.info(
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
        performance_logger.info(
            "event=document_queued project_id=%s document_id=%s",
            project_id, item["id"],
        )
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

@timed_stage("qa.translate")
def translate_query(query: str) -> str | None:
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
            content=f"query={query}"
        )
    ]

    # 翻译仅为辅助；调用、解析或字段校验失败都回退到原问题。
    try:
        with measure_stage("qa.translate_llm"):
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

    return translated_query


@timed_stage("qa.retrieve")
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

@router.post("/projects/{project_id}/ask")
@timed_stage("qa.total")
def user_asking(
    project_id: int,
    request: SearchRequest,
    db: Session = Depends(get_db),
    #提问前先验证登录状态。
    user: User = Depends(get_currentUser),
):
    return execute_qa(project_id, request, db, user)


def execute_qa(project_id, request, db, user, *, checkpoints=None):
    """Execute synchronously or under a durable run with the same graph."""
    if checkpoints is not None:
        db.info["durable_workflow"] = True
    def validate(state):
        require_project(db, project_id, user)
        doc_ids = require_document_scope(db, project_id, request.doc_ids)
        selection = None
        if request.document_id is not None:
            document = db.get(Document, request.document_id)
            if document is None or document.project_id != project_id:
                raise HTTPException(status_code=404, detail="文档不存在")
            if document.status != "completed":
                raise HTTPException(status_code=409, detail="文档尚未处理完成，暂不能进行问答")
            selection = {
                "document_id": document.id, "filename": document.filename,
                "selected_text": request.selected_text,
            }
        return {"doc_ids": doc_ids, "selection": selection}

    def history(state):
        messages, summary, boundary = get_history_messages(request.conversation_id, db, project_id)
        return {"history": messages, "summary": summary, "summary_boundary": boundary}

    def rewrite(state):
        retrieval_input = request.query
        if state["selection"] is not None:
            retrieval_input = f"用户问题：\n{request.query}\n\n当前选中的原文（待分析资料）：\n{request.selected_text}"
        return {"search_query": rewrite_query(retrieval_input, state["history"], summary=state["summary"])}

    def translate(state):
        return {"translated_query": translate_query(state["search_query"])}

    def retrieve(state, query_kind):
        query = state["search_query"] if query_kind == "original" else state["translated_query"]
        vector, bm25 = retrieve_documents(query, project_id, db, request.candidate_k_percentage, state["doc_ids"])
        with measure_stage("qa.rrf_per_query", query_kind=query_kind):
            candidates = rrf_fuse(vector, bm25)
        return {f"{query_kind}_candidates": candidates}

    def retrieve_original(state):
        return retrieve(state, "original")

    def retrieve_translated(state):
        return retrieve(state, "translated")

    def rerank(state):
        return {"final_chunks": rerank_results(
            state["search_query"], state.get("translated_query"),
            state["original_candidates"], state.get("translated_candidates", []),
        )}

    def selection_retrieve(state):
        return {"final_chunks": search_document_first(
            state["search_query"], project_id, request.document_id, db, request.candidate_k_percentage,
            doc_ids=state["doc_ids"], translated_query=state.get("translated_query"),
        )}

    def answer(state):
        return {"result": generate_answer(
            request.query, state["final_chunks"], state["history"], request.conversation_id, db, project_id,
            summary=state["summary"], summary_boundary=state["summary_boundary"], selection=state["selection"],
        )}

    def save(state):
        history_query = request.query
        if state["selection"] is not None:
            history_query = json.dumps({"query": request.query, "selection": state["selection"]}, ensure_ascii=False)
        result = dict(state["result"])
        result["conversation_id"] = history_messages_control(
            result["answer"], history_query, request.conversation_id, db, project_id,
            response_data=result, commit=checkpoints is None,
        )
        if state["selection"] is not None:
            result["selection"] = state["selection"]
        if checkpoints is not None:
            checkpoints.finish(db, result)
        return {"result": result}

    nodes = {
        "validate": validate, "history": history, "rewrite": rewrite, "translate": translate,
        "retrieve_original": retrieve_original, "retrieve_translated": retrieve_translated,
        "rerank": rerank, "selection_retrieve": selection_retrieve, "answer": answer, "save": save,
    }
    if request.use_school_tools and request.doc_ids is None and request.document_id is None:
        from app.services.school_qa import plan_assignment_query, query_assignments, answer_assignments

        def tool_route(state):
            return {"assignment_tool": plan_assignment_query(request.query, state["history"], state["summary"],
                                                            allow_school_sync=request.allow_school_sync, allow_school_actions=request.allow_school_actions)}

        def assignment_query(state):
            return {"assignment_data": query_assignments(db, user.id, state["assignment_tool"], project_id=project_id,
                                                        allow_school_sync=request.allow_school_sync, allow_school_actions=request.allow_school_actions)}

        def assignment_answer(state):
            return {"result": answer_assignments(request.query, state["assignment_tool"], state["assignment_data"])}

        nodes.update(tool_route=tool_route, assignment_query=assignment_query, assignment_answer=assignment_answer)
    if checkpoints is not None:
        nodes = checkpoints.wrap(nodes, uncached={"validate", "save"})
    graph = build_qa_graph(nodes)
    return graph.invoke({})["result"]

"""Authenticated history, document lifecycle and durable QA APIs."""
import json
import logging
from pathlib import Path
import shutil
import threading
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child
from app.models.projects import Project
from app.models.users import User
from app.models.workflow_run import WorkflowRun
from app.schemas.search import SearchRequest
from app.services.user_services import get_currentUser
from app.services.durable_workflows import new_run, run_workflow, recover_deleted_files

router = APIRouter(tags=["library"])
UPLOAD_ROOT = Path("data/uploads")
logger = logging.getLogger("uvicorn.error")

# 每个文档一把锁：同一份 PPT 被并发预览时只转换一次，避免写出半截 PDF。
_preview_locks: dict[int, threading.Lock] = {}
_preview_locks_guard = threading.Lock()


def _preview_lock(document_id: int) -> threading.Lock:
    with _preview_locks_guard:
        lock = _preview_locks.get(document_id)
        if lock is None:
            lock = threading.Lock()
            _preview_locks[document_id] = lock
        return lock


def _pdf_usable(path: Path) -> bool:
    """粗校验 PDF 是否完整：至少要有头部与一定体积，防止返回转换中断的残留文件。"""
    try:
        if path.stat().st_size < 1024:
            return False
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def ensure_preview_pdf(document, path: Path) -> Path | None:
    """按需把 PPT/PPTX 转成同目录 PDF，供浏览器预览。

    转换属于预览职责，不放在解析流程里（否则从不预览的课件也要白转一次）。
    已有可用 PDF 直接复用；没有才转换；转换失败返回 None，调用方回退原始文件，
    前端 pptx-preview 仍可渲染。
    """
    pdf_path = path.with_suffix(".pdf")
    if _pdf_usable(pdf_path):
        return pdf_path
    if not path.is_file():
        return None

    with _preview_lock(document.id):
        if _pdf_usable(pdf_path):  # 等锁期间别的请求已经转好
            return pdf_path
        try:
            from app.services.ppt_preview import convert_to_pdf

            converted = convert_to_pdf(path, timeout=90)
        except Exception as exc:  # noqa: BLE001 - 转换失败不能影响预览请求
            logger.warning("ppt_preview_error document=%s error=%s", document.id, exc)
            return None

    return converted if converted and _pdf_usable(converted) else None


def project_access(db, project_id, user, *, lock=False):
    query = select(Project).where(Project.id == project_id, Project.user_id == user.id)
    if lock:
        query = query.with_for_update()
    project = db.scalar(query)
    if project is None:
        raise HTTPException(404, "项目不存在")
    return project


def document_access(db, project_id, document_id, user, *, lock=False):
    project_access(db, project_id, user, lock=lock)
    query = select(Document).where(Document.id == document_id, Document.project_id == project_id)
    if lock:
        query = query.with_for_update()
    document = db.scalar(query)
    if document is None:
        raise HTTPException(404, "文档不存在")
    return document


def stored_path(document):
    expected_root = UPLOAD_ROOT.resolve() / f"project_{document.project_id}"
    root = expected_root.resolve()
    path = Path(document.storage_path).resolve()
    if root != expected_root or not path.is_relative_to(root) or path == root:
        raise HTTPException(409, "文档存储路径无效")
    return path


@router.get("/projects/{project_id}/conversations")
def conversations(project_id: int, limit: int = Query(20, ge=1, le=100),
                  offset: int = Query(0, ge=0), db: Session = Depends(get_db),
                  user: User = Depends(get_currentUser)):
    project_access(db, project_id, user)
    where = Conversation.project_id == project_id
    latest = select(Message.conversation_id, func.max(Message.id).label("last_id"),
                    func.max(Message.created_at).label("updated_at"),
                    func.count().label("message_count")).group_by(Message.conversation_id).subquery()
    rows = db.execute(select(Conversation, latest.c.updated_at, latest.c.message_count)
                      .outerjoin(latest, latest.c.conversation_id == Conversation.id).where(where)
                      .order_by(func.coalesce(latest.c.updated_at, Conversation.created_at).desc(),
                                Conversation.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [{"id": c.id, "title": c.title, "created_at": c.created_at,
                       "updated_at": updated or c.created_at, "message_count": count or 0}
                      for c, updated, count in rows],
            "total": db.scalar(select(func.count()).select_from(Conversation).where(where)),
            "limit": limit, "offset": offset}


@router.get("/projects/{project_id}/conversations/{conversation_id}/messages")
def messages(project_id: int, conversation_id: int, limit: int = Query(100, ge=1, le=500),
             offset: int = Query(0, ge=0), db: Session = Depends(get_db),
             user: User = Depends(get_currentUser)):
    project_access(db, project_id, user)
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project_id:
        raise HTTPException(404, "会话不存在")
    where = Message.conversation_id == conversation_id
    rows = db.scalars(select(Message).where(where).order_by(Message.id).offset(offset).limit(limit))
    items = []
    for message in rows:
        item = {"id": message.id, "role": message.role, "content": message.content,
                "turn_id": message.turn_id, "created_at": message.created_at,
                "response_data": message.response_data, "selection": None}
        if message.role == "user":
            try:
                parsed = json.loads(message.content)
                if isinstance(parsed, dict) and isinstance(parsed.get("query"), str) and "selection" in parsed:
                    item.update(content=parsed["query"], selection=parsed["selection"])
            except (ValueError, TypeError):
                pass
        items.append(item)
    return {"conversation_id": conversation.id, "title": conversation.title, "items": items,
            "total": db.scalar(select(func.count()).select_from(Message).where(where)),
            "limit": limit, "offset": offset}


@router.delete("/projects/{project_id}/conversations/{conversation_id}")
def delete_conversation(project_id: int, conversation_id: int, db: Session = Depends(get_db),
                        user: User = Depends(get_currentUser)):
    """删除整段会话：消息、该会话写进文件的批注、已结束的问答任务记录。

    批注是文件级的（同一文档可能被多个会话批注过），所以只删本会话产生的那几条，
    删完再按剩余记录重建副本；没有剩余记录时 rebuild_copy 会直接清掉副本。
    """
    project_access(db, project_id, user)
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id != project_id:
        raise HTTPException(404, "会话不存在")

    message_ids = list(db.scalars(select(Message.id).where(
        Message.conversation_id == conversation_id)))

    touched: set[int] = set()
    if message_ids:
        try:
            from app.models.document_annotation import DocumentAnnotation
            touched = {document_id for document_id in db.scalars(select(
                DocumentAnnotation.document_id).where(
                DocumentAnnotation.message_id.in_(message_ids)).distinct()) if document_id}
            with db.begin_nested():
                db.execute(delete(DocumentAnnotation).where(
                    DocumentAnnotation.message_id.in_(message_ids)))
        except Exception:  # 批注表尚未创建时忽略，不影响会话删除
            touched = set()

    db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    db.delete(conversation)

    # 只清理已结束的任务记录；排队/运行中的保留，避免打断后台执行
    try:
        with db.begin_nested():
            db.execute(delete(WorkflowRun).where(
                WorkflowRun.project_id == project_id, WorkflowRun.kind == "qa",
                WorkflowRun.request.op("->>")("conversation_id") == str(conversation_id),
                WorkflowRun.status.notin_(("queued", "running"))))
    except Exception:
        pass

    db.commit()

    for document_id in touched:
        try:
            from app.models.document_annotation import DocumentAnnotation
            from app.services import annotation_services as svc
            document = db.get(Document, document_id)
            if document is None:
                continue
            rows = list(db.scalars(select(DocumentAnnotation).where(
                DocumentAnnotation.document_id == document_id).order_by(DocumentAnnotation.id)))
            svc.rebuild_copy(document, rows)
        except Exception:
            continue

    return {"conversation_id": conversation_id, "deleted": True, "messages": len(message_ids)}


# —— 无项目的默认对话：归属看 user_id，project_id 为空；只有消息，无文档/批注 ——
def default_conversation_access(db, conversation_id, user):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.project_id is not None or conversation.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    return conversation


@router.get("/conversations")
def default_conversations(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                          db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    where = (Conversation.user_id == user.id) & (Conversation.project_id.is_(None))
    latest = select(Message.conversation_id, func.max(Message.id).label("last_id"),
                    func.max(Message.created_at).label("updated_at"),
                    func.count().label("message_count")).group_by(Message.conversation_id).subquery()
    rows = db.execute(select(Conversation, latest.c.updated_at, latest.c.message_count)
                      .outerjoin(latest, latest.c.conversation_id == Conversation.id).where(where)
                      .order_by(func.coalesce(latest.c.updated_at, Conversation.created_at).desc(),
                                Conversation.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [{"id": c.id, "title": c.title, "created_at": c.created_at,
                       "updated_at": updated or c.created_at, "message_count": count or 0}
                      for c, updated, count in rows],
            "total": db.scalar(select(func.count()).select_from(Conversation).where(where)),
            "limit": limit, "offset": offset}


@router.get("/conversations/{conversation_id}/messages")
def default_messages(conversation_id: int, limit: int = Query(100, ge=1, le=500),
                     offset: int = Query(0, ge=0), db: Session = Depends(get_db),
                     user: User = Depends(get_currentUser)):
    conversation = default_conversation_access(db, conversation_id, user)
    where = Message.conversation_id == conversation_id
    rows = db.scalars(select(Message).where(where).order_by(Message.id).offset(offset).limit(limit))
    items = []
    for message in rows:
        items.append({"id": message.id, "role": message.role, "content": message.content,
                      "turn_id": message.turn_id, "created_at": message.created_at,
                      "response_data": message.response_data, "selection": None})
    return {"conversation_id": conversation.id, "title": conversation.title, "items": items,
            "total": db.scalar(select(func.count()).select_from(Message).where(where)),
            "limit": limit, "offset": offset}


@router.delete("/conversations/{conversation_id}")
def default_delete_conversation(conversation_id: int, db: Session = Depends(get_db),
                                user: User = Depends(get_currentUser)):
    conversation = default_conversation_access(db, conversation_id, user)
    message_count = db.scalar(select(func.count()).select_from(Message).where(
        Message.conversation_id == conversation_id))
    db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    db.delete(conversation)
    db.commit()
    return {"conversation_id": conversation_id, "deleted": True, "messages": message_count or 0}


@router.get("/projects/{project_id}/documents/{document_id}/content")
def document_content(project_id: int, document_id: int, db: Session = Depends(get_db),
                     user: User = Depends(get_currentUser)):
    document = document_access(db, project_id, document_id, user)
    if document.status != "completed":
        raise HTTPException(409, "文档尚未处理完成")
    if document.parsed_text is None:
        raise HTTPException(409, detail={"code": "content_requires_reprocess",
                                        "message": "旧文档没有保存完整解析正文，请先重新处理或下载原文件"})
    return {"document_id": document.id, "filename": document.filename,
            "format": "markdown", "content": document.parsed_text}


@router.get("/projects/{project_id}/documents/{document_id}/file")
def document_file(project_id: int, document_id: int, preview: bool = Query(False),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_currentUser)):
    document = document_access(db, project_id, document_id, user)
    path = stored_path(document)
    if not path.is_file():
        raise HTTPException(404, "原文件不存在")

    # 预览模式：PPT/PPTX 在首次预览时按需转成 PDF，之后直接复用同目录副本；
    # 没有可用的渲染引擎或转换失败时回退原始文件。下载始终返回原始文件。
    if preview and path.suffix.lower() in {".ppt", ".pptx"}:
        pdf_path = ensure_preview_pdf(document, path)
        if pdf_path is not None and pdf_path.resolve().is_relative_to(path.parent.resolve()):
            return FileResponse(
                pdf_path,
                filename=Path(document.filename).with_suffix(".pdf").name,
                media_type="application/pdf",
                headers={"X-Content-Type-Options": "nosniff",
                         "Cache-Control": "private, no-store"},
            )

    return FileResponse(path, filename=document.filename, media_type=document.content_type,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


def ensure_idle(db, document):
    if document.status in {"queued", "processing"}:
        raise HTTPException(409, "文档正在处理，请等待任务结束")
    if db.scalar(select(WorkflowRun.id).where(
        WorkflowRun.project_id == document.project_id, WorkflowRun.kind == "qa",
        WorkflowRun.status.in_(["queued", "running"])).limit(1)):
        raise HTTPException(409, "项目中仍有问答任务，请等待任务结束")


def clear_chunks(db, document_id):
    parents = select(Parent.id).where(Parent.document_id == document_id)
    db.execute(delete(Child).where(Child.parent_id.in_(parents)))
    db.execute(delete(Parent).where(Parent.document_id == document_id))


def invalidate_failed_qa(db, project_id):
    # Deleted/reprocessed documents must never be reused from an old failed QA cache.
    db.execute(update(WorkflowRun).where(WorkflowRun.project_id == project_id,
               WorkflowRun.kind == "qa", WorkflowRun.status == "failed")
               .values(checkpoints={}, last_node=None))


@router.post("/projects/{project_id}/documents/{document_id}/retry", status_code=202)
def retry_document(project_id: int, document_id: int, background_tasks: BackgroundTasks,
                   restart: bool = False, db: Session = Depends(get_db),
                   user: User = Depends(get_currentUser)):
    document = document_access(db, project_id, document_id, user, lock=True)
    ensure_idle(db, document)
    if document.status == "completed" and not restart:
        raise HTTPException(409, "已完成文档重新处理需指定 restart=true")
    if not stored_path(document).is_file():
        raise HTTPException(409, "原文件不存在，无法重试")
    run = db.scalar(select(WorkflowRun).where(WorkflowRun.document_id == document_id).with_for_update())
    if run is None:
        run = new_run(db, project_id, document_id=document_id)
    elif run.status == "running":
        raise HTTPException(409, "任务仍在运行")
    run.status, run.result, run.error = "queued", None, None
    if restart:
        run.checkpoints, run.last_node = {}, None
    clear_chunks(db, document.id)
    invalidate_failed_qa(db, project_id)
    document.status, document.parsed_text = "queued", None
    db.commit()
    background_tasks.add_task(run_workflow, run.id)
    return {"document_id": document.id, "job_id": run.id, "status": "queued"}


@router.delete("/projects/{project_id}/documents/{document_id}")
def delete_document(project_id: int, document_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_currentUser)):
    document = document_access(db, project_id, document_id, user, lock=True)
    ensure_idle(db, document)
    path = stored_path(document)
    image_path = path.with_suffix(".images")
    # Check the resolved directory before any recursive removal/move.
    if not image_path.resolve().is_relative_to((UPLOAD_ROOT / f"project_{project_id}").resolve()):
        raise HTTPException(409, "图片存储路径无效")
    trash = UPLOAD_ROOT.resolve() / ".trash" / uuid4().hex
    trash.mkdir(parents=True)
    try:
        (trash / "manifest.json").write_text(json.dumps({"document_id": document_id,
            "project_id": project_id, "files": [source.name for source in (path, image_path) if source.exists()]}), encoding="utf-8")
        for source in (path, image_path):
            if source.exists():
                target = trash / source.name
                source.rename(target)
        # AI 批注副本与记录一并清理（表可能尚未创建，失败不影响删除）
        try:
            from app.services.annotation_services import annotated_path
            annotated = annotated_path(document)
            if annotated.is_file():
                annotated.unlink()
        except Exception:
            pass
        try:
            from app.models.document_annotation import DocumentAnnotation
            with db.begin_nested():
                db.execute(delete(DocumentAnnotation).where(
                    DocumentAnnotation.document_id == document_id))
        except Exception:
            pass
        clear_chunks(db, document_id)
        db.execute(delete(WorkflowRun).where(WorkflowRun.document_id == document_id))
        invalidate_failed_qa(db, project_id)
        db.execute(delete(Document).where(Document.id == document_id))
        db.commit()
    except Exception:
        db.rollback()
        # A connection error can leave COMMIT's outcome unknown. Re-read the DB
        # before restoring; if unavailable, retain the manifest for recovery.
        try:
            recover_deleted_files()
        except Exception:
            pass
        raise
    pending = False
    try:
        shutil.rmtree(trash)
    except FileNotFoundError:
        pass
    except OSError:
        pending = True
    return {"document_id": document_id, "deleted": True, "cleanup_pending": pending}


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_currentUser)):
    """删除整个项目：文档文件、检索块、批注、全部会话与任务记录。

    文件先挪进回收目录（与删除文档同款），事务提交成功后再真正清掉；
    中途失败走 recover_deleted_files 找回。
    """
    project_access(db, project_id, user, lock=True)
    busy_document = db.scalar(select(func.count()).select_from(Document).where(
        Document.project_id == project_id, Document.status.in_(("queued", "processing"))))
    busy_qa = db.scalar(select(WorkflowRun.id).where(
        WorkflowRun.project_id == project_id, WorkflowRun.kind == "qa",
        WorkflowRun.status.in_(("queued", "running"))).limit(1))
    if busy_document or busy_qa:
        raise HTTPException(409, "项目内有文档或问答任务正在处理，稍后再删")

    document_rows = list(db.scalars(select(Document).where(Document.project_id == project_id)))
    trash = UPLOAD_ROOT.resolve() / ".trash" / uuid4().hex
    trash.mkdir(parents=True)
    moved: list[str] = []
    for document in document_rows:
        try:
            sources = [stored_path(document), stored_path(document).with_suffix(".images")]
        except HTTPException:
            continue
        for source in sources:
            if source.exists():
                source.rename(trash / f"doc_{document.id}_{source.name}")
                moved.append(source.name)
        try:
            from app.services.annotation_services import annotated_path
            annotated = annotated_path(document)
            if annotated.is_file():
                annotated.rename(trash / f"annotated_{document.id}_{annotated.name}")
                moved.append(annotated.name)
        except Exception:
            pass
    (trash / "manifest.json").write_text(json.dumps(
        {"project_id": project_id, "files": moved}), encoding="utf-8")

    try:
        for document in document_rows:
            clear_chunks(db, document.id)
        try:
            from app.models.document_annotation import DocumentAnnotation
            with db.begin_nested():
                db.execute(delete(DocumentAnnotation).where(
                    DocumentAnnotation.project_id == project_id))
        except Exception:
            pass
        conversation_ids = list(db.scalars(select(Conversation.id).where(
            Conversation.project_id == project_id)))
        if conversation_ids:
            db.execute(delete(Message).where(Message.conversation_id.in_(conversation_ids)))
            db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
        db.execute(delete(WorkflowRun).where(WorkflowRun.project_id == project_id))
        db.execute(delete(Document).where(Document.project_id == project_id))
        db.execute(delete(Project).where(Project.id == project_id))
        db.commit()
    except Exception:
        db.rollback()
        try:
            recover_deleted_files()
        except Exception:
            pass
        raise
    pending = False
    try:
        shutil.rmtree(trash)
    except FileNotFoundError:
        pass
    except OSError:
        pending = True
    return {"project_id": project_id, "deleted": True, "cleanup_pending": pending}


@router.post("/projects/{project_id}/ask/jobs", status_code=202)
def submit_qa(project_id: int, request: SearchRequest, background_tasks: BackgroundTasks, http_request: Request,
              db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    project_access(db, project_id, user, lock=True)
    from app.routers.rag_flow import require_document_scope
    require_document_scope(db, project_id, request.doc_ids)
    if request.document_id is not None:
        document = document_access(db, project_id, request.document_id, user)
        if document.status != "completed":
            raise HTTPException(409, "文档尚未处理完成")
    if request.conversation_id:
        conversation = db.get(Conversation, request.conversation_id)
        if conversation is None or conversation.project_id != project_id:
            raise HTTPException(404, "会话不存在")
    if request.timetable_image_id:
        from app.services.timetable_attachments import read_image
        read_image(user.id, request.timetable_image_id)
    payload = request.model_dump(mode='json')
    payload['_local_browser_allowed'] = bool(http_request.client and http_request.client.host in {'127.0.0.1', '::1'})
    run = new_run(db, project_id, request=payload)
    db.commit()
    background_tasks.add_task(run_workflow, run.id)
    return {"job_id": run.id, "status": "queued"}


def run_access(db, project_id, run_id, user, *, lock=False):
    project_access(db, project_id, user, lock=lock)
    query = select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.project_id == project_id)
    if lock:
        query = query.with_for_update()
    run = db.scalar(query)
    if run is None:
        raise HTTPException(404, "任务不存在")
    return run


@router.get("/projects/{project_id}/jobs")
def jobs(project_id: int, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
         db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    project_access(db, project_id, user)
    where = WorkflowRun.project_id == project_id
    rows = db.scalars(select(WorkflowRun).where(where).order_by(WorkflowRun.created_at.desc(),
                      WorkflowRun.id.desc()).offset(offset).limit(limit))
    return {"items": [{"job_id": run.id, "kind": run.kind, "document_id": run.document_id,
                       "status": run.status, "last_completed_node": run.last_node,
                       "attempts": run.attempts, "error_type": run.error} for run in rows],
            "total": db.scalar(select(func.count()).select_from(WorkflowRun).where(where)),
            "limit": limit, "offset": offset}


@router.get("/projects/{project_id}/jobs/{run_id}")
def job_status(project_id: int, run_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_currentUser)):
    run = run_access(db, project_id, run_id, user)
    return {"job_id": run.id, "kind": run.kind, "document_id": run.document_id,
            "status": run.status, "last_completed_node": run.last_node,
            "attempts": run.attempts, "error_type": run.error, "result": run.result}


@router.post("/projects/{project_id}/jobs/{run_id}/retry", status_code=202)
def retry_qa(project_id: int, run_id: str, background_tasks: BackgroundTasks,
             restart: bool = False,
             db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    run = run_access(db, project_id, run_id, user, lock=True)
    if run.kind != "qa" or run.status != "failed":
        raise HTTPException(409, "仅可重试失败的问答任务；文档请使用文档重试接口")
    # A concurrent summary update can invalidate the captured history boundary.
    if restart or (run.error == "HTTPException" and "answer" not in run.checkpoints):
        run.checkpoints, run.last_node = {}, None
    run.status, run.error = "queued", None
    db.commit()
    background_tasks.add_task(run_workflow, run.id)
    return {"job_id": run.id, "status": "queued"}

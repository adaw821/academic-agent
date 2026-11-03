"""Database-backed node checkpoints, fenced leases and restart recovery.

Graph topology remains in workflow_graphs.py. Only successful, replay-safe node
outputs are cached. Final domain writes and run completion share a transaction.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import tempfile
import threading
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from app.db.database import SessionLocal
from app.models.workflow_run import WorkflowRun

logger = logging.getLogger(__name__)
LEASE_SECONDS = 120


class LeaseLost(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc)


def new_run(db, project_id, *, document_id=None, request=None):
    run = WorkflowRun(id=uuid4().hex, project_id=project_id,
                      document_id=document_id, kind="document" if document_id else "qa",
                      status="queued", request=request or {}, checkpoints={}, attempts=0)
    db.add(run)
    db.flush()
    return run


def encode(value):
    if hasattr(value, "save_as_json"):
        # Public Docling API embeds pixels, including images otherwise cached in PIL.
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "document.json"
            value.save_as_json(target)
            return {"__docling_document__": json.loads(target.read_text(encoding="utf-8"))}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"__docling_document__"}:
            from docling_core.types.doc import DoclingDocument
            return DoclingDocument.model_validate(value["__docling_document__"])
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def available():
    return or_(WorkflowRun.status == "queued", and_(
        WorkflowRun.status == "running", WorkflowRun.lease_until < now()))


class Checkpoints:
    def __init__(self, run_id, token, cached):
        self.run_id, self.token, self.cached = run_id, token, cached

    def wrap(self, nodes, *, uncached):
        def wrap_one(name, node):
            def invoke(state):
                if name in uncached:
                    return node(state)
                if name in self.cached:
                    return decode(self.cached[name])
                result = node(state)
                cached = dict(self.cached, **{name: encode(result)})
                with SessionLocal() as session:
                    count = session.execute(update(WorkflowRun).where(
                        WorkflowRun.id == self.run_id, WorkflowRun.lease_token == self.token,
                        WorkflowRun.status == "running",
                    ).values(checkpoints=cached, last_node=name)).rowcount
                    if count != 1:
                        raise LeaseLost("workflow lease lost")
                    session.commit()
                self.cached = cached
                return result
            return invoke
        return {name: wrap_one(name, node) for name, node in nodes.items()}

    def finish(self, db, result):
        count = db.execute(update(WorkflowRun).where(
            WorkflowRun.id == self.run_id, WorkflowRun.lease_token == self.token,
            WorkflowRun.status == "running", WorkflowRun.lease_until >= now(),
        ).values(status="completed", result=encode(result), checkpoints={},
                 lease_token=None, lease_until=None, error=None)).rowcount
        if count != 1:
            raise LeaseLost("workflow lease lost before commit")
        db.commit()


@contextmanager
def heartbeat(run_id, token):
    stop = threading.Event()
    def beat():
        while not stop.wait(20):
            try:
                with SessionLocal() as session:
                    count = session.execute(update(WorkflowRun).where(
                        WorkflowRun.id == run_id, WorkflowRun.lease_token == token,
                        WorkflowRun.status == "running",
                    ).values(lease_until=now() + timedelta(seconds=LEASE_SECONDS))).rowcount
                    session.commit()
                    if count != 1:
                        return
            except Exception:
                logger.warning("workflow_heartbeat_failed run_id=%s", run_id)
    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def run_workflow(run_id):
    token = uuid4().hex
    with SessionLocal() as db:
        claimed = db.execute(update(WorkflowRun).where(
            WorkflowRun.id == run_id, available(),
        ).values(status="running", lease_token=token,
                 lease_until=now() + timedelta(seconds=LEASE_SECONDS),
                 attempts=WorkflowRun.attempts + 1, error=None)).rowcount
        db.commit()
        if claimed != 1:
            return
        run = db.get(WorkflowRun, run_id)
        kind, project_id, document_id, request = run.kind, run.project_id, run.document_id, run.request
        checkpoints = Checkpoints(run_id, token, run.checkpoints)
    try:
        with heartbeat(run_id, token), SessionLocal() as db:
            if kind == "document":
                from app.models.documents import Document
                from app.services.chunk import chunking
                from app.routers.library import stored_path
                document = db.get(Document, document_id)
                if document is None:
                    raise ValueError("document missing")
                document.status = "processing"
                db.commit()
                path = stored_path(document)
                image_summary = chunking(path.read_bytes(), db, document.id,
                         document.filename,
                         image_output_dir=path.with_suffix(".images"),
                         checkpoints=checkpoints)
                document.status = "completed"
                checkpoints.finish(db, {"document_id": document.id, "image_summary": image_summary if isinstance(image_summary, dict) else None})
            else:
                from app.models.projects import Project
                from app.models.users import User
                from app.routers.rag_flow import execute_qa
                from app.schemas.search import SearchRequest
                project = db.get(Project, project_id)
                user = db.get(User, project.user_id)
                execute_qa(project_id, SearchRequest.model_validate(request), db, user,
                           checkpoints=checkpoints, local_browser_allowed=request.get('_local_browser_allowed') is True)
    except Exception as exc:
        from app.services.image_failures import TooManyImageFailures
        failure_result = {"image_summary": exc.summary} if isinstance(exc, TooManyImageFailures) else None
        # Do not expose upstream payloads, file paths or credentials via the API.
        logger.warning("workflow_failed run_id=%s error_type=%s", run_id, type(exc).__name__)
        with SessionLocal() as db:
            changed = db.execute(update(WorkflowRun).where(
                WorkflowRun.id == run_id, WorkflowRun.lease_token == token,
                WorkflowRun.status == "running",
            ).values(status="failed", error=type(exc).__name__, result=failure_result, lease_token=None, lease_until=None)).rowcount
            if changed and document_id:
                from app.models.documents import Document
                db.execute(update(Document).where(Document.id == document_id).values(status="failed"))
            db.commit()


def recover_pending(*, stop=None):
    """One bounded batch; CAS claims allow multiple server processes safely."""
    from app.models.documents import Document
    recover_deleted_files()
    # Adopt pre-upgrade queued/interrupted uploads. Completed/failed uploads stay put.
    with SessionLocal() as db:
        documents = db.scalars(select(Document).where(
            Document.status.in_(["queued", "processing"]),
            ~select(WorkflowRun.id).where(WorkflowRun.document_id == Document.id).exists(),
        ).with_for_update(skip_locked=True)).all()
        for document in documents:
            new_run(db, document.project_id, document_id=document.id)
        db.commit()
        ids = list(db.scalars(select(WorkflowRun.id).where(available())
                              .order_by(WorkflowRun.created_at).limit(10)))
    for run_id in ids:
        if stop is not None and stop.is_set():
            break
        run_workflow(run_id)


def recover_deleted_files():
    """Restore staged files after rollback, or remove them after committed deletion."""
    import shutil
    from app.models.documents import Document
    from app.routers.library import UPLOAD_ROOT
    root = UPLOAD_ROOT.resolve()
    trash_root = root / ".trash"
    if not trash_root.is_dir():
        return
    for folder in list(trash_root.iterdir())[:100]:
        if not folder.is_dir() or folder.is_symlink() or not folder.resolve().is_relative_to(trash_root):
            continue
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            document_id, project_id = int(data["document_id"]), int(data["project_id"])
            with SessionLocal() as db:
                # Wait for an in-flight deletion's transaction before deciding.
                document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
                destination = root / f"project_{project_id}"
                if document is not None:
                    if document.project_id != project_id or not destination.resolve().is_relative_to(root):
                        continue
                    for name in data["files"]:
                        if not isinstance(name, str) or Path(name).name != name:
                            raise ValueError("invalid staged file name")
                        source, target = folder / name, destination / name
                        if not source.resolve().is_relative_to(folder.resolve()) or not target.resolve().is_relative_to(destination.resolve()):
                            raise ValueError("invalid staged path")
                        if source.exists():
                            if target.exists():
                                raise ValueError("restore target already exists")
                            source.rename(target)
                shutil.rmtree(folder)
                db.commit()
        except (OSError, ValueError, KeyError):
            logger.warning("document_file_cleanup_deferred")

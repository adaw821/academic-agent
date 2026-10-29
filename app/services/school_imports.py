"""Import a downloaded resource through the existing durable document workflow."""
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.models.documents import Document
from app.models.projects import Project
from app.models.workflow_run import WorkflowRun
from app.services.durable_workflows import new_run
from app.services.school_downloads import owned_resource, resource_directory, saved_file

FILE_TYPES = {
    '.txt': 'text/plain', '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}


def import_resource(db, user_id, course_id, resource_id, project_id):
    # Use the same root as download/read/delete and the durable worker.
    from app.routers.library import UPLOAD_ROOT

    copied = None
    try:
        # Serialize imports in this project on PostgreSQL, including duplicate requests.
        project = db.scalar(select(Project.id).where(
            Project.id == project_id, Project.user_id == user_id).with_for_update())
        if project is None:
            raise HTTPException(404, '项目不存在')
        resource = owned_resource(db, user_id, course_id, resource_id)
        if resource['resource_type'] not in {'file', 'resource'} or resource['purpose'] == 'student_submission':
            raise HTTPException(422, '该资源不是可导入的教学附件')
        existing = db.execute(select(Document, WorkflowRun).join(
            WorkflowRun, WorkflowRun.document_id == Document.id).where(
            Document.project_id == project_id, WorkflowRun.kind == 'document',
            WorkflowRun.request['school_resource_id'].as_integer() == resource_id,
        )).first()
        if existing:
            document, run = existing
            result = dict(document_id=document.id, job_id=run.id, status=document.status, reused=True)
            db.rollback()
            return result
        source = saved_file(resource, resource_directory(user_id, course_id, resource_id))
        if source is None:
            raise HTTPException(409, '请先下载该资源附件')
        suffix = source.suffix.lower()
        if suffix not in FILE_TYPES or not 0 < source.stat().st_size <= 50 * 1024 * 1024:
            raise HTTPException(409, '本地附件格式或大小无效，请重新下载')
        root = UPLOAD_ROOT.resolve()
        directory = root / f'project_{project_id}'
        if directory.resolve() != directory:
            raise HTTPException(409, '项目存储目录无效')
        directory.mkdir(parents=True, exist_ok=True)
        copied = directory / (uuid4().hex + suffix)
        # Keep the school cache independent of project deletion and reprocessing.
        with source.open('rb') as incoming, copied.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        title = resource['title'].replace('\\', '/').rsplit('/', 1)[-1].strip()
        title = ''.join(char for char in title if char.isprintable()) or f'resource-{resource_id}'
        if title.lower().endswith(suffix):
            title = title[:-len(suffix)]
        filename = title[:255 - len(suffix)] + suffix
        document = Document(project_id=project_id, filename=filename,
                            content_type=FILE_TYPES[suffix], storage_path=copied.as_posix(), status='queued')
        db.add(document)
        db.flush()
        run = new_run(db, project_id, document_id=document.id, request={
            'school_resource_id': resource_id, 'course_id': course_id, 'source_url': resource['source_url'],
        })
        result = dict(document_id=document.id, job_id=run.id, status='queued', reused=False)
        db.commit()
        return result
    except Exception:
        db.rollback()
        if copied is not None:
            # A lost COMMIT response may still mean success. Keep the file if the
            # record exists, or if the database cannot confirm the outcome.
            try:
                saved = db.scalar(select(Document.id).where(Document.storage_path == copied.as_posix()))
            except Exception:
                saved = True
            if not saved:
                copied.unlink(missing_ok=True)
        raise

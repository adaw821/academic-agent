from pathlib import Path
from uuid import uuid4
import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.services.chunk import chunking
from app.db.database import get_db
from app.models.documents import Document
from app.models.projects import Project
from app.schemas.project_create import ProjectCreate
from app.models.users import User


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


@router.post("/projects/{project_id}/documents")
async def upload_files(
    project_id: int,
    files: list[UploadFile],
    db: Session = Depends(get_db),
):
    """文件上传切片接口"""
    request_started_at = perf_counter()
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    project_directory = UPLOAD_ROOT / f"project_{project_id}"
    project_directory.mkdir(parents=True, exist_ok=True)

    saved_files = []
    failed_files = []
    created_paths: list[Path] = []
    file_save_ms = 0.0

    try:
        for file in files:
            original_filename = Path(file.filename or "").name.strip()
            suffix = Path(original_filename).suffix.lower()

            if not original_filename or suffix not in FILE_TYPES:
                failed_files.append(original_filename or "未命名文件")
                continue

            content = await file.read()
            if not content:
                failed_files.append(original_filename)
                continue

            stored_filename = f"{uuid4().hex}{suffix}"
            storage_path = project_directory / stored_filename
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
                status="uploaded",
            )
            db.add(document_record)
            db.flush()
            document_id = document_record.id
            #进入service
            await chunking(
                content=content,
                db=db,
                document_id=document_id,
                file_name=original_filename,
            )


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

    return {
        "message": "文件上传完成",
        "saved_files": saved_files,
        "failed_files": failed_files,
    }


@router.post("/projects", status_code=201)
async def project_creator(
    project: ProjectCreate,
    db: Session = Depends(get_db),
):
    user = db.get(User, project.user_id)
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="用户不存在",
        )

    new_project = Project(
        user_id=project.user_id,
        name=project.name,
    )
    db.add(new_project)
    db.commit()
    db.refresh(new_project)

    return {
        "message": "项目创建成功",
        "project_id": new_project.id,
    }

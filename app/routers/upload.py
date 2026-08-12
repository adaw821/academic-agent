from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.services.chunk import chunking
from app.db.database import get_db
from app.models.documents import Document
from app.models.projects import Project


router = APIRouter()

ALLOWED_TYPES = {
    "text/plain",
    "image/png",
    "image/jpeg",
    "application/pdf",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

UPLOAD_ROOT = Path("data/uploads")


@router.post("/projects/{project_id}/documents")
async def upload_files(
    project_id: int,
    files: list[UploadFile],
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    project_directory = UPLOAD_ROOT / f"project_{project_id}"
    project_directory.mkdir(parents=True, exist_ok=True)

    saved_files = []
    failed_files = []
    created_paths: list[Path] = []

    try:
        for file in files:
            original_filename = Path(file.filename or "").name
            content_type = file.content_type or ""

            if not original_filename or content_type not in ALLOWED_TYPES:
                failed_files.append(original_filename or "未命名文件")
                continue

            content = await file.read()
            if not content:
                failed_files.append(original_filename)
                continue

            suffix = Path(original_filename).suffix.lower()
            stored_filename = f"{uuid4().hex}{suffix}"
            storage_path = project_directory / stored_filename
            storage_path.write_bytes(content)
            created_paths.append(storage_path)

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
            await chunking(content, content_type, db, document_id)


            saved_files.append(
                {
                    "id": document_record.id,
                    "filename": original_filename,
                    "status": document_record.status,
                }
            )

        db.commit()
    except Exception:
        db.rollback()
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="文件保存失败")
    finally:
        for file in files:
            await file.close()

    return {
        "message": "文件上传完成",
        "saved_files": saved_files,
        "failed_files": failed_files,
    }

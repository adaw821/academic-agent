"""AI 批注 API：把某条引用的一句话结论写进文件副本（Word 批注 / PDF 高亮 / PPT 备注）。"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.document_annotation import DocumentAnnotation
from app.models.documents import Document
from app.models.users import User
from app.routers.library import document_access, stored_path
from app.services import annotation_services as svc
from app.services.user_services import get_currentUser

logger = logging.getLogger(__name__)

router = APIRouter(tags=["annotations"])


class AnnotationCreate(BaseModel):
    cite_index: int = Field(default=0, ge=0, le=999)
    anchor_text: str = Field(min_length=1, max_length=svc.MAX_ANCHOR_CHARS)
    question: str = Field(default="", max_length=4000)
    answer: str = Field(default="", max_length=8000)
    pages: list[int] | None = None
    message_id: int | None = None
    # 单轮回答的指纹（前端用 问题+回答 算出）：不同轮次引用同一处时覆盖旧批注
    turn_key: str | None = Field(default=None, max_length=64)


def _serialize(row: DocumentAnnotation) -> dict:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "cite_index": row.cite_index,
        "summary": row.summary,
        "kind": row.kind,
        "pages": row.pages or [],
        "preview": (row.anchor_text or "").strip().replace("\n", " ")[:120],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _document(project_id: int, document_id: int, db: Session, user: User) -> Document:
    document = document_access(db, project_id, document_id, user)
    stored_path(document)  # 校验存储路径合法
    return document


def _download_url(project_id: int, document_id: int) -> str:
    return f"/api/projects/{project_id}/documents/{document_id}/annotated"


@router.post("/projects/{project_id}/documents/{document_id}/annotations", status_code=201)
def create_annotation(project_id: int, document_id: int, payload: AnnotationCreate,
                      db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    document = _document(project_id, document_id, db, user)
    suffix = Path(document.filename).suffix.lower()
    kind = svc.kind_for_suffix(suffix)
    if kind is None:
        raise HTTPException(409, "该文件格式暂不支持写入批注（支持 docx / pdf / pptx）")
    if suffix == ".ppt":
        raise HTTPException(409, "旧版 .ppt 不支持写入备注，请先另存为 .pptx")
    if not svc.clean_lines(payload.anchor_text):
        raise HTTPException(400, "引用原文为空，无法在文件中定位")

    svc.ensure_schema()
    fingerprint = svc.anchor_fingerprint(payload.anchor_text)

    existing = db.scalar(select(DocumentAnnotation).where(
        DocumentAnnotation.document_id == document_id,
        DocumentAnnotation.anchor_hash == fingerprint,
    ))
    created = False
    overwritten = False
    if existing is None:
        summary = svc.build_summary(payload.question, payload.anchor_text, payload.answer)
        lock = svc.document_lock(document_id)
        with lock:
            # 双检：并发点两次也只落一条
            existing = db.scalar(select(DocumentAnnotation).where(
                DocumentAnnotation.document_id == document_id,
                DocumentAnnotation.anchor_hash == fingerprint,
            ))
            if existing is None:
                existing = DocumentAnnotation(
                    document_id=document_id,
                    project_id=project_id,
                    message_id=payload.message_id,
                    cite_index=payload.cite_index,
                    anchor_text=payload.anchor_text[:svc.MAX_ANCHOR_CHARS],
                    anchor_hash=fingerprint,
                    summary=summary,
                    pages=payload.pages or [],
                    kind=kind,
                    turn_key=payload.turn_key,
                )
                db.add(existing)
                db.commit()
                db.refresh(existing)
                created = True
                try:
                    _rebuild(db, document)
                except Exception as exc:
                    logger.warning("annotation_rebuild_failed document=%s error_type=%s",
                                   document_id, type(exc).__name__)
                    db.delete(existing)
                    db.commit()
                    raise HTTPException(500, f"写入批注失败：{exc}") from exc

    elif (payload.turn_key and existing.turn_key != payload.turn_key) or (
        not payload.turn_key and payload.message_id and existing.message_id != payload.message_id
    ):
        # 高亮/批注随单轮回答走：新一轮引用了同一处时，用本轮的结论覆盖旧批注
        summary = svc.build_summary(payload.question, payload.anchor_text, payload.answer)
        with svc.document_lock(document_id):
            existing.message_id = payload.message_id
            existing.cite_index = payload.cite_index
            existing.summary = summary
            existing.pages = payload.pages or []
            if payload.turn_key:
                existing.turn_key = payload.turn_key
            db.commit()
            db.refresh(existing)
            overwritten = True
            try:
                _rebuild(db, document)
            except Exception as exc:
                logger.warning("annotation_rebuild_failed document=%s error_type=%s",
                               document_id, type(exc).__name__)
                raise HTTPException(500, f"更新批注失败：{exc}") from exc

    if not created and existing is not None:
        # 重复点击也要重建副本：磁盘上可能留着旧规则生成的副本（如旧版高亮），必须刷新
        try:
            with svc.document_lock(document_id):
                _rebuild(db, document)
        except Exception as exc:
            logger.warning("annotation_rebuild_skipped document=%s error_type=%s",
                           document_id, type(exc).__name__)

    total = db.scalar(select(func.count()).select_from(DocumentAnnotation).where(
        DocumentAnnotation.document_id == document_id)) or 0
    return {
        **(_serialize(existing) if existing else {}),
        "total": int(total),
        "created": created,
        "overwritten": overwritten,
        "filename": svc.annotated_filename(document),
        "download_url": _download_url(project_id, document_id),
    }


def _rebuild(db: Session, document: Document) -> None:
    rows = list(db.scalars(select(DocumentAnnotation).where(
        DocumentAnnotation.document_id == document.id).order_by(DocumentAnnotation.id)))
    svc.rebuild_copy(document, rows)


@router.get("/projects/{project_id}/documents/{document_id}/annotations")
def list_annotations(project_id: int, document_id: int, db: Session = Depends(get_db),
                     user: User = Depends(get_currentUser)):
    document = _document(project_id, document_id, db, user)
    svc.ensure_schema()
    rows = list(db.scalars(select(DocumentAnnotation).where(
        DocumentAnnotation.document_id == document_id).order_by(DocumentAnnotation.id)))
    return {
        "items": [_serialize(row) for row in rows],
        "total": len(rows),
        "filename": svc.annotated_filename(document),
        "download_url": _download_url(project_id, document_id),
        "has_copy": svc.annotated_path(document).is_file(),
    }


@router.delete("/projects/{project_id}/documents/{document_id}/annotations/{annotation_id}")
def delete_annotation(project_id: int, document_id: int, annotation_id: int,
                      db: Session = Depends(get_db), user: User = Depends(get_currentUser)):
    document = _document(project_id, document_id, db, user)
    svc.ensure_schema()
    row = db.scalar(select(DocumentAnnotation).where(
        DocumentAnnotation.id == annotation_id,
        DocumentAnnotation.document_id == document_id))
    if row is None:
        raise HTTPException(404, "批注不存在")
    with svc.document_lock(document_id):
        db.execute(delete(DocumentAnnotation).where(DocumentAnnotation.id == annotation_id))
        db.commit()
        _rebuild(db, document)
    return {"removed": annotation_id, "deleted": True}


@router.get("/projects/{project_id}/documents/{document_id}/annotated")
def annotated_file(project_id: int, document_id: int, db: Session = Depends(get_db),
                   user: User = Depends(get_currentUser)):
    document = _document(project_id, document_id, db, user)
    path = svc.annotated_path(document)
    if not path.is_file():
        raise HTTPException(404, "尚未生成带批注的版本")
    media_types = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    return FileResponse(
        path,
        filename=svc.annotated_filename(document),
        media_type=media_types.get(path.suffix.lower(), document.content_type),
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )

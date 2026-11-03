from fastapi import APIRouter, UploadFile, File, Depends
from fastapi import HTTPException
from app.services.timetable_service import (
    TimetableImageError,
    TimetableParseError,
    TimetableProviderError,
    timetable_service,
)

from sqlalchemy.orm import Session
from app.db.database import get_db
from app.models.users import User
from app.services.user_services import get_currentUser
from app.routers.timetable_entries import router as entries_router

    
router = APIRouter()
router.include_router(entries_router)
TYPE_ALLOWED = [
    "image/jpeg",
    "image/png",
]

@router.post('/timetable/attachments', status_code=201)
async def attach_timetable(file: UploadFile = File(...), user: User = Depends(get_currentUser)):
    from app.services.timetable_attachments import store_image, MAX_BYTES
    content = await file.read(MAX_BYTES + 1)
    return store_image(user.id, content, file.content_type)

@router.post("/timetable")
async def timetable_process(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_currentUser),
):
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail="图片不能为空"
        )
    elif not isinstance(content, bytes):
        raise TypeError("图片类型错误")

    content_type = file.content_type
    if content_type not in TYPE_ALLOWED:
            raise HTTPException(
                status_code=400,
                detail="图片类型错误"
            )
    try:
        result = await timetable_service(content, db, content_type, user.id)
    except TimetableImageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimetableParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TimetableProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result

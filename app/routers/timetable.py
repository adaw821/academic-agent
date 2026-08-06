from fastapi import APIRouter, UploadFile, File, Depends
from fastapi import HTTPException
from app.services.timetable_service import timetable_service

from sqlalchemy.orm import Session
from app.db.database import get_db


    
router = APIRouter()
TYPE_ALLOWED = [
    "image/jpeg",
    "image/png",
]
@router.post("/timetable")
async def timetable_process(file: UploadFile = File(...), db: Session = Depends(get_db)):
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
    result = await timetable_service(content, db, content_type)
    return result
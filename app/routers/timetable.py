from fastapi import APIRouter, UploadFile, File
from pathlib import Path
from fastapi import HTTPException
from app.services.timetable_service import timetable_service

router = APIRouter()
suffix_allowed = [".jpg", ".jpeg",".png"]
@router.post("/timetable")
async def timetable_process(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in suffix_allowed:
        raise HTTPException(
            status_code=400,
            detail="图片格式有问题"
        )
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=400,
            detail="图片不能为空"
        )
    elif not isinstance(content, bytes):
        raise TypeError("图片类型错误")

    await timetable_service(content)
    
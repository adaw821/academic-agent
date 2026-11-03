"""Immutable, user-scoped image attachments for synchronous and durable agent turns."""
from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4
from fastapi import HTTPException
from PIL import Image

ROOT = Path(__file__).resolve().parents[2] / '.local' / 'timetable-attachments'
MAX_BYTES = 10 * 1024 * 1024


def store_image(user_id, content, content_type):
    from app.services.timetable_service import validate_image, TimetableImageError
    if len(content) > MAX_BYTES:
        raise HTTPException(413, '课表图片不能超过 10 MB')
    try:
        validate_image(content, content_type)
    except TimetableImageError as exc:
        raise HTTPException(400, str(exc)) from None
    # Strip EXIF/metadata and normalize format before storing.
    with Image.open(BytesIO(content)) as img:
        output = BytesIO()
        img.convert('RGB').save(output, format='PNG')
    data = output.getvalue()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, '图片解码后过大，请缩小图片')
    folder = ROOT / str(int(user_id))
    folder.mkdir(parents=True, exist_ok=True)
    ident = uuid4().hex
    (folder / f'{ident}.png').write_bytes(data)
    return {'image_id': ident}


def read_image(user_id, image_id):
    if not image_id or not re.fullmatch(r'[0-9a-f]{32}', image_id):
        raise HTTPException(400, '请先在聊天中附加课表图片')
    path = ROOT / str(int(user_id)) / f'{image_id}.png'
    if not path.is_file():
        raise HTTPException(404, '课表图片不存在或不属于当前账号')
    return path.read_bytes()

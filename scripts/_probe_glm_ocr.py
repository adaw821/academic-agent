"""一次性诊断：复现 GLM-OCR(layout_parsing) 调用，打印真实状态码与响应体。
不打印 API key；仅用一张空白测试图，不含任何用户数据。用后可删。"""
import base64
import io
import os

from app.config import load_config

load_config()

import httpx
from PIL import Image

from app.services.timetable_service import LAYOUT_PARSING_URL

key = os.environ.get("ZHIPU_API_KEY", "")
print("ZHIPU_API_KEY set:", bool(key), "| len:", len(key))
print("URL:", LAYOUT_PARSING_URL)

buf = io.BytesIO()
Image.new("RGB", (64, 64), "white").save(buf, format="PNG")
b64 = base64.b64encode(buf.getvalue()).decode()

try:
    r = httpx.post(
        LAYOUT_PARSING_URL,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "glm-ocr", "file": f"data:image/png;base64,{b64}"},
        timeout=60,
    )
    print("HTTP status:", r.status_code)
    print("Response body:", r.text[:2000])
except Exception as exc:
    print("Request raised:", type(exc).__name__, str(exc)[:500])

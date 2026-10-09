"""VLM transport with bounded retries and errors that retain provider status."""
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
import re
from threading import Lock
from time import sleep

import httpx
from docling.datamodel.base_models import ApiImageRequestResult, VlmStopReason

from app.services.performance import logger, measure_stage


_request_lock = Lock()
MAX_ATTEMPTS = 4
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _retry_delay(response, attempt):
    delay = 5 * 2 ** (attempt - 1)
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            requested = float(value)
        except ValueError:
            try:
                requested = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                requested = 0
        delay = max(delay, requested)
    return delay


def _error_code(response):
    try:
        value = str(response.json().get("error", {}).get("code", "unknown"))
    except (ValueError, AttributeError, TypeError):
        return "unknown"
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) else "unknown"


def request_picture_description(image, *, options, picture_id):
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    payload = {
        **options.params,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            {"type": "text", "text": options.prompt},
        ]}],
    }
    # Serialize requests across document tasks in this process, including cooldown.
    with measure_stage("chunk.picture_vlm_queue_wait", picture_id=picture_id):
        _request_lock.acquire()
    try:
        with httpx.Client(timeout=options.timeout) as client:
            return _request_with_client(client, payload, options=options, picture_id=picture_id)
    finally:
        _request_lock.release()


def _request_with_client(client, payload, *, options, picture_id):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = None
        try:
            with measure_stage("chunk.picture_vlm_request", picture_id=picture_id, attempt=attempt):
                response = client.post(str(options.url), headers=options.headers, json=payload)
        except httpx.TransportError as exc:
            error = f"网络请求失败（{type(exc).__name__}）"
            retryable = True
        else:
            status = response.status_code
            if response.is_success:
                break
            code = _error_code(response)
            label = "服务端繁忙或限流" if status == 429 else "服务端请求失败"
            error = f"{label}（HTTP {status}, code={code}）"
            retryable = status in RETRY_STATUSES
            logger.warning("event=picture_vlm_http_error picture_id=%s attempt=%s http_status=%s api_code=%s",
                           picture_id, attempt, status, code)
        delay = _retry_delay(response, attempt)
        if not retryable or attempt == MAX_ATTEMPTS or delay > 60:
            raise ValueError(f"VLM {error}，已尝试 {attempt} 次；未保存不完整结果")
        if response is not None:
            response.close()
        logger.warning("event=picture_vlm_retry picture_id=%s attempt=%s delay_seconds=%s",
                       picture_id, attempt, delay)
        with measure_stage("chunk.picture_vlm_retry_wait", picture_id=picture_id, attempt=attempt):
            sleep(delay)

    try:
        data = response.json()
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        finish_reason = choice["finish_reason"]
        usage = data.get("usage") or {}
        if not isinstance(text, str) or not isinstance(usage, dict):
            raise TypeError("Invalid response fields")
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError("VLM 响应格式无效，缺少完整的 choices/message/content") from exc
    reason = {
        "stop": VlmStopReason.END_OF_SEQUENCE,
        "length": VlmStopReason.LENGTH,
        "content_filter": VlmStopReason.CONTENT_FILTERED,
    }.get(finish_reason, VlmStopReason.UNSPECIFIED)
    return ApiImageRequestResult(text, usage.get("total_tokens"), reason, usage=usage)

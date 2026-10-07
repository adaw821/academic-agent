"""Monotonic stage timings with request-local correlation; never log payloads."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import iscoroutinefunction, signature
import logging
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4


_context = ContextVar("performance_context", default=None)
logger = logging.getLogger("academic.performance")


class CorrelationFilter(logging.Filter):
    def filter(self, record):
        context = _context.get() or {}
        record.trace_id = context.get("trace_id", "none")
        record.span_id = context.get("span_id", "none")
        return True


def configure_performance_logging():
    """Append all timing events to one UTF-8 file, independently of Uvicorn."""
    if logger.handlers:
        return
    root = Path(__file__).resolve().parents[2]
    path = Path(os.environ.get("PERFORMANCE_LOG_FILE", "logs/performance.log"))
    if not path.is_absolute():
        path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(logging.Formatter(
        "%(asctime)s level=%(levelname)s pid=%(process)d "
        "trace_id=%(trace_id)s span_id=%(span_id)s %(message)s"
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


configure_performance_logging()


@contextmanager
def measure_stage(stage: str, **fields):
    """Log start/end even on failure; nested durations must not be summed.

    fields 仅传 ID、数量、阶段标签，不传问题、正文、模型响应或密钥。
    ContextVar 隔离并发请求，并将文档/项目 ID 传给内部阶段。
    """
    context = dict(_context.get() or {"trace_id": uuid4().hex})
    context.update(fields)
    context["parent_span_id"] = context.get("span_id", "none")
    context["span_id"] = uuid4().hex
    token = _context.set(context)
    labels = " ".join(f"{key}={value}" for key, value in context.items()
                      if key not in {"trace_id", "span_id"})
    started = perf_counter()
    status, error_type = "ok", "none"
    logger.info("event=pipeline_stage_started stage=%s %s", stage, labels)
    try:
        yield
    except BaseException as exc:
        status, error_type = "error", type(exc).__name__
        raise
    finally:
        elapsed_ms = (perf_counter() - started) * 1000
        try:
            logger.info(
                "event=pipeline_stage_finished stage=%s status=%s elapsed_ms=%.2f error_type=%s %s",
                stage, status, elapsed_ms, error_type, labels,
            )
        finally:
            _context.reset(token)


def timed_stage(stage: str):
    """Time sync/async functions while preserving signatures used by FastAPI."""
    def decorate(function):
        sig = signature(function)

        def identifiers(args, kwargs):
            bound = sig.bind_partial(*args, **kwargs).arguments
            return {key: bound[key] for key in (
                "project_id", "document_id", "conversation_id", "exclude_document_id",
            ) if key in bound and bound[key] is not None}

        if iscoroutinefunction(function):
            @wraps(function)
            async def async_wrapper(*args, **kwargs):
                with measure_stage(stage, **identifiers(args, kwargs)):
                    return await function(*args, **kwargs)
            return async_wrapper

        @wraps(function)
        def wrapper(*args, **kwargs):
            with measure_stage(stage, **identifiers(args, kwargs)):
                return function(*args, **kwargs)
        return wrapper
    return decorate

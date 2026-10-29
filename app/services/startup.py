"""Load local QA models before accepting requests."""
import asyncio
import threading
from contextlib import asynccontextmanager

from app.services.performance import measure_stage, logger


def preload_models():
    # Reuse the same cached getters as request handlers.
    from app.services.chunk import get_embedding_model
    from app.services.search_services import get_reranker
    from app.services.context_management import get_tokenizer

    with measure_stage("startup.models"):
        with measure_stage("startup.embedding_model"):
            get_embedding_model()
        with measure_stage("startup.reranker"):
            get_reranker()
        with measure_stage("startup.tokenizer"):
            get_tokenizer()
    logger.info("event=startup_models_ready")


@asynccontextmanager
async def lifespan(app):
    # Await completion: a failed load fails startup instead of a user's request.
    await asyncio.to_thread(preload_models)
    if app is None:  # Model-only callers/tests have no database lifecycle.
        yield
        return
    from app.services.durable_workflows import recover_pending
    stop = threading.Event()

    def recover():
        while not stop.is_set():
            try:
                recover_pending(stop=stop)
            except Exception as exc:
                logger.warning("workflow_recovery_failed error_type=%s", type(exc).__name__)
            stop.wait(15)

    # Dedicated daemon: asyncio's executor shutdown otherwise waits indefinitely
    # for model calls during reload. Process interruption is recovered by leases.
    worker = threading.Thread(target=recover, name="workflow-recovery", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()

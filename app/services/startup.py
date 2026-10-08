"""Load local QA models before accepting requests."""
import asyncio
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
    yield

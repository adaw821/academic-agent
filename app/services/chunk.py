from io import BytesIO
from functools import lru_cache
import logging
from time import perf_counter
from pathlib import Path

from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from app.models.document_child import Child
from app.models.document_parents import Parent


logger = logging.getLogger("uvicorn.error")


@lru_cache
def get_embedding_model():
    started_at = perf_counter()
    model = SentenceTransformer("BAAI/bge-m3")
    logger.info(
        "event=embedding_model_loaded model=BAAI/bge-m3 elapsed_ms=%.2f",
        (perf_counter() - started_at) * 1000,
    )
    return model


@lru_cache(maxsize=1)
def get_document_converter():
    started_at = perf_counter()
    converter = DocumentConverter()
    logger.info(
        "event=document_converter_initialized elapsed_ms=%.2f",
        (perf_counter() - started_at) * 1000,
    )
    return converter


def chunking(
    content: bytes,
    db: Session,
    document_id: int,
    file_name: str,
):
    """使用 Docling 解析文件，再创建父子分片。"""
    total_started_at = perf_counter()

    #把txt的分出来不用docling
    parse_started_at = perf_counter()
    is_txt = Path(file_name).suffix.lower() == ".txt"
    if is_txt:
        text = content.decode("utf-8-sig").strip()

    else:
        source = DocumentStream(
            name=file_name,
            stream=BytesIO(content),
        )
        converter = get_document_converter()

        #转成结构化内容
        result = converter.convert(source)
        #转成md
        text = result.document.export_to_markdown().strip()

    parse_ms = (perf_counter() - parse_started_at) * 1000

    if not text:
        raise ValueError("文件中没有解析到有效内容")

    #假如是txt就450 不是的话还是150
    stats = splitter_creator(
        text,
        db,
        document_id,
        child_chunk_size=450 if is_txt else 150,
        child_chunk_overlap=50 if is_txt else 30,
    )

    logger.info(
        "event=document_chunked document_id=%s file_size_bytes=%s "
        "parse_ms=%.2f parent_split_ms=%.2f child_split_ms=%.2f "
        "embedding_ms=%.2f parent_db_flush_ms=%.2f total_ms=%.2f "
        "parent_count=%s child_count=%s",
        document_id,
        len(content),
        parse_ms,
        stats["parent_split_ms"],
        stats["child_split_ms"],
        stats["embedding_ms"],
        stats["parent_db_flush_ms"],
        (perf_counter() - total_started_at) * 1000,
        stats["parent_count"],
        stats["child_count"],
    )


def splitter_creator(text, db, document_id, child_chunk_size, child_chunk_overlap):
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2000,
        chunk_overlap=80,
    )
    parent_split_started_at = perf_counter()
    parent_texts = parent_splitter.split_text(text)
    parent_split_ms = (perf_counter() - parent_split_started_at) * 1000

    child_splitter = RecursiveCharacterTextSplitter(
        chunk_size=child_chunk_size,
        chunk_overlap=child_chunk_overlap,
    )

    parent_index = 0
    child_count = 0
    child_split_ms = 0.0
    embedding_ms = 0.0
    parent_db_flush_ms = 0.0
    for parent_text in parent_texts:
        if not parent_text:
            continue

        parent = Parent(
            document_id=document_id,
            content=parent_text,
            chunk_index=parent_index,
        )
        parent_index += 1
        db.add(parent)
        flush_started_at = perf_counter()
        db.flush()
        
        parent_db_flush_ms += (perf_counter() - flush_started_at) * 1000

        child_split_started_at = perf_counter()
        #切子片
        child_texts = child_splitter.split_text(parent_text)
        #去掉空子片
        child_texts = [text for text in child_texts if text.strip()]
        child_split_ms += (perf_counter() - child_split_started_at) * 1000

        if not child_texts:
            continue
        embedding_model = get_embedding_model()

        embedding_started_at = perf_counter()
        vectors = embedding_model.encode(
            child_texts,
            batch_size=32,
            normalize_embeddings=True,
        )
        embedding_ms += (perf_counter() - embedding_started_at) * 1000

        child_index = 0
        for child_index, (child_text, vector) in enumerate(zip(child_texts, vectors)):
                child = Child(
                    parent_id=parent.id,
                    content=child_text,
                    chunk_index=child_index,
                    embedding=vector.tolist(),
                )
                child_count += 1
                db.add(child)

    return {
        "parent_count": parent_index,
        "child_count": child_count,
        "parent_split_ms": parent_split_ms,
        "child_split_ms": child_split_ms,
        "embedding_ms": embedding_ms,
        "parent_db_flush_ms": parent_db_flush_ms,
    }

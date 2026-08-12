from functools import lru_cache

from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from app.models.document_child import Child
from app.models.document_parents import Parent


@lru_cache
def get_embedding_model():
    return SentenceTransformer("BAAI/bge-m3")


def documents_router(
    content: bytes,
    content_type: str,
    db: Session,
    document_id: int,
):
    if content_type == "text/plain":
        return process_txt(content, db, document_id)

    if content_type == "application/pdf":
        return process_pdf(content, db, document_id)

    if content_type == (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation"
    ):
        return process_pptx(content, db, document_id)

    raise ValueError("不支持的文件类型")


def process_pdf(content: bytes, db: Session, document_id: int):
    raise NotImplementedError("PDF 解析暂未实现")


def process_pptx(content: bytes, db: Session, document_id: int):
    raise NotImplementedError("PPTX 解析暂未实现")


def process_txt(content: bytes, db: Session, document_id: int):
    try:
        text = content.decode("utf-8").strip()

        if not text:
            raise ValueError("TXT 文件没有有效文字")

        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=80,
        )
        parent_texts = parent_splitter.split_text(text)

        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=150,
            chunk_overlap=30,
        )

        parent_index = 0
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
            db.flush()

            child_texts = child_splitter.split_text(parent_text)
            embedding_model = get_embedding_model()

            child_index = 0
            for child_text in child_texts:
                if not child_text:
                    continue

                vector = embedding_model.encode(
                    child_text,
                    normalize_embeddings=True,
                )
                child = Child(
                    parent_id=parent.id,
                    content=child_text,
                    chunk_index=child_index,
                    embedding=vector.tolist(),
                )
                child_index += 1
                db.add(child)
    except Exception as exc:
        print(exc)
        raise


async def chunking(
    content: bytes,
    content_type: str,
    db: Session,
    document_id: int,
):
    documents_router(content, content_type, db, document_id)

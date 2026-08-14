from io import BytesIO
from functools import lru_cache

from docling.datamodel.base_models import DocumentStream
from docling.document_converter import DocumentConverter
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from app.models.document_child import Child
from app.models.document_parents import Parent


@lru_cache
def get_embedding_model():
    return SentenceTransformer("BAAI/bge-m3")


@lru_cache(maxsize=1)
def get_document_converter():
    return DocumentConverter()


async def chunking(
    content: bytes,
    db: Session,
    document_id: int,
    file_name: str,
):
    """使用 Docling 解析文件，再创建父子分片。"""
    source = DocumentStream(
        name=file_name,
        stream=BytesIO(content),
    )
    converter = get_document_converter()
    result = converter.convert(source)
    text = result.document.export_to_markdown().strip()

    if not text:
        raise ValueError("文件中没有解析到有效内容")

    splitter_creator(text, db, document_id)


def splitter_creator(text, db, document_id):
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

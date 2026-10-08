from io import BytesIO
from functools import lru_cache
import logging
from time import perf_counter
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import ConvertPipelineOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption, PowerpointFormatOption
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from app.models.document_child import Child
from app.models.document_parents import Parent
from app.services.image_extraction import extract_document_images, index_document_images
from app.services.docling_images import picture_description_options, validate_picture_descriptions
from app.services.pdf_chunking import split_pdf_document
from app.services.ppt_chunking import PPT_CHILD_SIZE, clean_slide_text, split_slide_text
from app.services.performance import measure_stage, timed_stage, logger as performance_logger
from app.services.workflow_graphs import build_document_graph


logger = logging.getLogger("uvicorn.error")


@lru_cache
@timed_stage("chunk.embedding_model_load")
#获取向量模型
def get_embedding_model():
    started_at = perf_counter()
    model = SentenceTransformer("BAAI/bge-m3")
    performance_logger.info(
        "event=embedding_model_loaded model=BAAI/bge-m3 elapsed_ms=%.2f",
        (perf_counter() - started_at) * 1000,
    )
    return model


@lru_cache(maxsize=1)
@timed_stage("chunk.converter_initialize")
def get_document_converter():
    started_at = perf_counter()
    # PDF 文字层照常提取；图片转录由 Docling 内置视觉描述完成，整页 OCR 暂不启用。
    pdf_options = PdfPipelineOptions(
        do_ocr=False, generate_picture_images=True, images_scale=2.0,
        enable_remote_services=True, do_picture_description=True,
        picture_description_options=picture_description_options(),
    )
    ppt_options = ConvertPipelineOptions(
        enable_remote_services=True, do_picture_description=True,
        picture_description_options=picture_description_options(),
    )
    converter = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options),
        InputFormat.PPTX: PowerpointFormatOption(pipeline_options=ppt_options),
        InputFormat.PPT: PowerpointFormatOption(pipeline_options=ppt_options),
    })
    performance_logger.info(
        "event=document_converter_initialized elapsed_ms=%.2f",
        (perf_counter() - started_at) * 1000,
    )
    return converter


@timed_stage("chunk.total")
def chunking(
    content: bytes,
    db: Session,
    document_id: int,
    file_name: str,
    *,
    image_output_dir: str | Path | None = None,
):
    """使用 Docling 解析文件，再创建父子分片。"""
    total_started_at = perf_counter()

    parse_started_at = perf_counter()
    suffix = Path(file_name).suffix.lower()
    is_txt = suffix == ".txt"
    is_paged = suffix in {".ppt", ".pptx", ".pdf"}

    #解码txt文件
    def decode(state):
        with measure_stage("chunk.txt_decode", size_bytes=len(content)):
            return {"text": content.decode("utf-8-sig").strip()}

    #将pdf和ppt用docling解析
    def convert(state):
        source = DocumentStream(name=file_name, stream=BytesIO(content))
        with measure_stage("chunk.converter_get"):
            converter = get_document_converter()
        with measure_stage("chunk.docling_convert", size_bytes=len(content)):
            result = converter.convert(source)
        if result.status != ConversionStatus.SUCCESS:
            raise ValueError(f"Docling 文档转换未完整成功：{result.status}")
        validate_picture_descriptions(result.document)
        return {"document": result.document}

    #处理图片
    def extract_images(state):
        # 描述已由 Docling 回填，此处只保存图片和位置以便检查。
        pictures = index_document_images(state["document"])

        records = extract_document_images(
            state["document"], document_id, image_output_dir, pictures=pictures,
        )
        return {"pictures": pictures, "image_records": records}

    #把 Docling document 变成 md文本
    def export(state):
        document = state["document"]
        if is_paged:
            if not document.pages:
                file_type = "PDF" if suffix == ".pdf" else "PPT"
                raise ValueError(f"{file_type} 解析结果缺少页码，无法按页分片")
            if suffix == ".pdf":
                with measure_stage("chunk.pdf_markdown_export"):
                    text = document.export_to_markdown(traverse_pictures=False).strip()
                return {"text": text, "pdf_document": document}
            page_texts = []
            for page_no in sorted(document.pages):
                with measure_stage("chunk.ppt_page_export", page_no=page_no):
                    markdown = document.export_to_markdown(page_no=page_no)
                with measure_stage("chunk.ppt_page_clean", page_no=page_no):
                    page_texts.append(clean_slide_text(markdown))
            return {"text": "\n\n".join(page_texts).strip(), "page_texts": page_texts}
        with measure_stage("chunk.markdown_export"):
            return {"text": document.export_to_markdown().strip()}

    #把处理好的text分片并入库
    def split_embed_store(state):
        parse_ms = (perf_counter() - parse_started_at) * 1000
        if not state["text"]:
            raise ValueError("文件中没有解析到有效内容")
        stats = splitter_creator(
            state["text"], db, document_id,
            child_chunk_size=PPT_CHILD_SIZE if is_paged else (450 if is_txt else 300),
            child_chunk_overlap=50 if is_txt else 30,
            page_texts=state.get("page_texts"), pdf_document=state.get("pdf_document"),
        )
        if stats["child_count"] == 0:
            raise ValueError("文件中没有可检索的正文；图片内容需要先进行识别")
        return {"stats": stats, "parse_ms": parse_ms}

    #把前边函数总体跑一遍
    nodes = {
        "decode": decode, "convert": convert, "extract_images": extract_images,
        "export": export,
        "split_embed_store": split_embed_store,
    }
    graph = build_document_graph(nodes, is_txt=is_txt,
                                 needs_images=is_paged and image_output_dir is not None)
    state = graph.invoke({})
    stats, parse_ms = state["stats"], state["parse_ms"]

    performance_logger.info(
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


@timed_stage("chunk.split_embed_store")
def splitter_creator(
    text, db, document_id, child_chunk_size, child_chunk_overlap,
    *, page_texts=None, pdf_document=None,
):
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=2000,
        chunk_overlap=80,
    )
    parent_split_started_at = perf_counter()
    # PDF 的 split_pdf_document 一次完成父子分片，计时名称明确包含两者。
    with measure_stage("chunk.pdf_parent_child_split" if pdf_document is not None else "chunk.parent_split"):
        pdf_parents = (
            split_pdf_document(pdf_document, child_size=child_chunk_size)
            if pdf_document is not None else None
        )
        parent_texts = (
            [parent.content for parent in pdf_parents] if pdf_parents is not None
            else page_texts if page_texts is not None else parent_splitter.split_text(text)
        )
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
    for source_index, parent_text in enumerate(parent_texts):
        if not parent_text:
            continue

        child_split_started_at = perf_counter()
        pdf_parent = pdf_parents[source_index] if pdf_parents is not None else None
        with measure_stage("chunk.child_prepare" if pdf_parent is not None else "chunk.child_split", source_index=source_index):
            if pdf_parent is not None:
                child_texts = [child.text for child in pdf_parent.children]
            else:
                child_texts = (
                    split_slide_text(parent_text, child_chunk_size)
                    if page_texts is not None else child_splitter.split_text(parent_text)
                )
            child_texts = [text for text in child_texts if text.strip()]
        child_split_ms += (perf_counter() - child_split_started_at) * 1000
        if not child_texts:
            continue

        parent = Parent(
            document_id=document_id,
            content=parent_text,
            chunk_index=parent_index,
            section_id=pdf_parent.section_id if pdf_parent is not None else None,
            heading_path=pdf_parent.heading_path if pdf_parent is not None else None,
            page_numbers=pdf_parent.page_numbers if pdf_parent is not None else None,
        )
        parent_index += 1
        db.add(parent)
        flush_started_at = perf_counter()
        # SQLAlchemy flush 也可能写入上轮已 add 的子片，故这里不称为仅父片写入。
        with measure_stage("chunk.db_flush", source_index=source_index):
            db.flush()
        
        parent_db_flush_ms += (perf_counter() - flush_started_at) * 1000

        with measure_stage("chunk.embedding_model_get", source_index=source_index):
            embedding_model = get_embedding_model()

        embedding_started_at = perf_counter()
        with measure_stage("chunk.embedding_encode", source_index=source_index, child_count=len(child_texts)):
            vectors = embedding_model.encode(
                child_texts,
                batch_size=32,
                normalize_embeddings=True,
            )
        embedding_ms += (perf_counter() - embedding_started_at) * 1000

        with measure_stage("chunk.child_db_stage", source_index=source_index, child_count=len(child_texts)):
            for child_index, (child_text, vector) in enumerate(zip(child_texts, vectors)):
                child = Child(
                    parent_id=parent.id,
                    content=child_text,
                    chunk_index=child_index,
                    embedding=vector.tolist(),
                    page_numbers=pdf_parent.children[child_index].page_numbers if pdf_parent is not None else None,
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

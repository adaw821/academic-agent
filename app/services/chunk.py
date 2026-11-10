from io import BytesIO
from functools import lru_cache
import logging
from time import perf_counter
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus, DocumentStream, InputFormat
from docling.datamodel.pipeline_options import ConvertPipelineOptions, PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption, PowerpointFormatOption
from docling.datamodel.settings import settings as docling_settings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from app.models.document_child import Child
from app.models.document_parents import Parent
from app.services.images_routers import vlm_router, ocr_process, is_enough_text, get_ocr_error_predictor, route_ocr_result
from app.services.docling_images import enrich_routed_images
from app.services.image_failures import summarize_images, check_image_failures
from app.services.image_extraction import extract_document_images
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


# docling 默认对版面/图片分类模型做 torch.compile；Windows 没有可用的 Triton，
# 首个批次就会抛 TritonMissing。必须在构造 pipeline options 之前关闭（默认值在构造时读取）。
docling_settings.inference.compile_torch_models = False


@lru_cache(maxsize=1)
@timed_stage("chunk.converter_initialize")
def get_document_converter():
    started_at = perf_counter()
    # PDF 保留文字层，并识别扫描页；图片分类与后续图表识别保持独立。
    pdf_options = PdfPipelineOptions(
        do_ocr=True, ocr_options=RapidOcrOptions(),
        generate_picture_images=True, images_scale=2.0,
        do_picture_description=False,
        do_picture_classification=True,
    )
    ppt_options = ConvertPipelineOptions(
        do_picture_description=False,
        do_picture_classification=True,
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
    checkpoints=None,
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

    #将pdf和ppt用docling解析,得到类型
    def convert(state):
        source = DocumentStream(name=file_name, stream=BytesIO(content))
        with measure_stage("chunk.converter_get"):
            converter = get_document_converter()
        with measure_stage("chunk.docling_convert", size_bytes=len(content)):
            result = converter.convert(source)
        if result.status != ConversionStatus.SUCCESS:
            raise ValueError(f"Docling 文档转换未完整成功：{result.status}")
        return {"document": result.document}


    @timed_stage("chunk.image_index")
    def extract_images(state):
        """提取所有pictureitem的类型 类型置信度 id作为唯一标识 图片地址"""
        document = state["document"]
        classifications = []
        for item in document.pictures:
            if item is None:
                continue
            classification = item.meta.classification if item.meta else None
            prediction = (
                classification.get_main_prediction()
                if classification and classification.predictions else None
            )
            classifications.append({
            "type": prediction.class_name if prediction else None,
            "id": item.self_ref,
            "confidence": prediction.confidence if prediction else None,
            "router": None,
            })
        update = {"classifications": classifications}
        if image_output_dir is not None:
            update["image_records"] = extract_document_images(document, document_id, image_output_dir)
        return update

    def images_type_router(state):
        """给images分流"""
        classifications = [dict(item) for item in (state["classifications"] or [])]
        document = state["document"]
        #先分一部分图给vlm
        _, others = vlm_router(classifications)
        #剩下的图做ocr 准备进一步分流
        ocr_results = ocr_process(others, document)
        #计算文字量
        ocr_units_results = is_enough_text(ocr_results)
        quality_candidates = []
        for record in ocr_units_results:
            route = route_ocr_result(record)
            if route == "quality":
                quality_candidates.append(record)
            else:
                record["router"] = route

        if quality_candidates:
            try:
                predictor = get_ocr_error_predictor()
                with measure_stage("chunk.ocr_quality_batch", image_count=len(quality_candidates)):
                    quality = predictor([item["text"] for item in quality_candidates])
                labels = quality.labels
            except Exception:
                # Unavailable OCR quality service: let VLM verify these images instead.
                labels = ["bad"] * len(quality_candidates)
            if len(labels) != len(quality_candidates):
                raise ValueError("OCR 质量检查返回数量与图片数量不一致")
            for item, label in zip(quality_candidates, labels):
                item["ocr_quality"] = label
                item["router"] = "ocr" if label == "good" else "vlm"

        performance_logger.info(
            "event=picture_routes total=%s ocr=%s vlm=%s skip=%s quality_checked=%s",
            len(classifications),
            sum(item["router"] == "ocr" for item in classifications),
            sum(item["router"] == "vlm" for item in classifications),
            sum(item["router"] == "skip" for item in classifications),
            len(quality_candidates),
        )

        return {"classifications": classifications}

    def enrich_images(state):
        document = state["document"]
        classifications = enrich_routed_images(document, state["classifications"])
        summary = summarize_images(classifications)
        # Every image has now been attempted, including those after an earlier failure.
        check_image_failures(summary)
        return {"document": document, "classifications": classifications, "image_summary": summary}

    def document_md(state):
        """把文件转成md"""
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
        "images_type_router": images_type_router,
        "enrich_images": enrich_images,
        "export": document_md,
        "split_embed_store": split_embed_store,
    }
    if checkpoints is not None:
        nodes = checkpoints.wrap(nodes, uncached={"split_embed_store"})
    graph = build_document_graph(nodes, is_txt=is_txt,
                                 needs_images=is_paged)
    state = graph.invoke({})
    from app.models.documents import Document
    document_record = db.get(Document, document_id)
    if document_record is not None:
        document_record.parsed_text = state["text"]
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

    return state.get("image_summary", {"total": 0, "failed": 0, "failures": []})


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

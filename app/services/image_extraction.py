"""Save Docling pictures and their source locations; no OCR or model calls."""

import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from PIL import Image
from docling_core.types.doc import DoclingDocument, PictureItem
from app.services.performance import measure_stage, timed_stage, logger as performance_logger


logger = logging.getLogger("uvicorn.error")


def image_worth_describing(
    item: PictureItem, document: DoclingDocument, pil_image: Image.Image,
) -> bool:
    """按提取后的像素尺寸、长宽比和灰度范围筛选；保留节点参数供后续扩展。"""
    if pil_image.width < 100 or pil_image.height < 100:
        return False

    ratio = pil_image.width / pil_image.height
    if ratio > 8 or ratio < 1 / 8:
        return False

    with pil_image.convert("L") as gray:
        lo, hi = gray.getextrema()
    if hi - lo < 15:
        return False

    return True


@timed_stage("chunk.image_index")
def index_document_images(document: DoclingDocument) -> dict[int, PictureItem]:
    """先扫描docling解析后md文档的图片位置：以全文 reading_order 为编号"""
    pictures = {}
    for reading_order, (item, _) in enumerate(document.iterate_items(traverse_pictures=True)):
        if isinstance(item, PictureItem):
            pictures[reading_order] = item
    return pictures


@timed_stage("chunk.image_extract_filter")
def extract_document_images(
    document: DoclingDocument,
    document_id: int,
    output_dir: str | Path,
    *,
    pictures: dict[int, PictureItem] | None = None,
) -> list[dict]:
    """按已有索引提取图片，保存为 {reading_order}.png。

    output_dir 应为当前文档独立的图片目录。未传索引时先扫描一次，
    传入空索引则不提取；图片提取失败也保留原编号，不给后续图片重新编号。
    低信息图片记录为 filtered，不写入 PNG，也不发送给视觉模型。
    """
    started_at = perf_counter()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if pictures is None:
        pictures = index_document_images(document)
    records = []
    for reading_order, item in pictures.items():
        #组合“文档 ID＋图片节点”生成 ID
        image_id = uuid5(NAMESPACE_URL, f"document:{document_id}:{item.self_ref}").hex
        provenance = []
        for source in item.prov:
            page = document.pages.get(source.page_no)
            provenance.append({
                "page_no": source.page_no,
                "bbox": source.bbox.model_dump(mode="json"),
                "page_size": page.size.model_dump(mode="json") if page else None,
            })
        first_source = provenance[0] if provenance else {}
        #reading_order 是阅读顺序编号
        record = {
            "image_id": image_id,
            "document_id": document_id,
            "source_ref": item.self_ref,
            "page_no": first_source.get("page_no"),
            "bbox": first_source.get("bbox"),
            "reading_order": reading_order,
            "provenance": provenance,
            "image_path": None,
            "status": "unavailable",
        }
        try:
            with measure_stage("chunk.image_get_pixels", reading_order=reading_order):
                image = item.get_image(document)
        except Exception as exc:
            image = None
            record["error_type"] = type(exc).__name__
        if image is not None:
            record.update(width=image.width, height=image.height)
            with measure_stage("chunk.image_filter", reading_order=reading_order):
                worth_describing = image_worth_describing(item, document, image)
            if not worth_describing:
                record["status"] = "filtered"
                records.append(record)
                continue
            #用reading order当文件名 用来找图片插入位置
            filename = f"{reading_order}.png"
            if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                image = image.convert("RGB")
            # Storage errors must propagate; otherwise the manifest could claim
            # a usable extraction when disk permissions/space prevented saving.
            with measure_stage("chunk.image_save", reading_order=reading_order):
                image.save(output_dir / filename, format="PNG")
            record.update(image_path=filename, status="saved")
        else:
            logger.warning("event=document_image_unavailable document_id=%s source_ref=%s error_type=%s",
                           document_id, item.self_ref, record.get("error_type", "MissingPixels"))
        records.append(record)

    manifest = {"schema_version": 1, "document_id": document_id, "images": records}
    temporary_manifest = output_dir / "manifest.json.tmp"
    with measure_stage("chunk.image_manifest_save", image_count=len(records)):
        temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_manifest.replace(output_dir / "manifest.json")
    saved_count = sum(record["status"] == "saved" for record in records)
    filtered_count = sum(record["status"] == "filtered" for record in records)
    unavailable_count = sum(record["status"] == "unavailable" for record in records)
    performance_logger.info(
        "event=document_images_extracted document_id=%s image_count=%s saved_count=%s "
        "filtered_count=%s unavailable_count=%s elapsed_ms=%.2f",
        document_id, len(records), saved_count, filtered_count, unavailable_count,
        (perf_counter() - started_at) * 1000,
    )
    return records

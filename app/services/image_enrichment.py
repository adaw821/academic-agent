"""Attach recognized Markdown to the original Docling picture nodes."""

from docling_core.types.doc import DescriptionMetaField, PictureItem, PictureMeta

from app.services.image_ocr import VISION_MODEL
from app.services.performance import timed_stage


@timed_stage("chunk.image_backfill")
def apply_image_ocr_results(
    pictures: dict[int, PictureItem],
    results: dict[int, str],
) -> None:
    """按扫描时保存的索引回填；不重新遍历文档，也不改变节点顺序。

    直接改docling返回结果的图片描述。
    """
    # 先校验全部结果，避免未知编号或错误类型导致只回填一部分。
    #results是位置：视觉模型返回
    for reading_order, text in results.items():
        if reading_order not in pictures:
            raise ValueError(f"OCR 结果找不到对应图片：reading_order={reading_order}")
        if not isinstance(text, str):
            raise ValueError(f"OCR 结果必须是字符串：reading_order={reading_order}")

    for reading_order, text in results.items():
        if not text.strip():
            continue
        picture = pictures[reading_order]
        if picture.meta is None:
            picture.meta = PictureMeta()
        picture.meta.description = DescriptionMetaField(
            text=text.strip(), created_by=VISION_MODEL,
        )

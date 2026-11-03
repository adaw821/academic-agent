import numpy as np
from rapidocr import RapidOCR
from docling_core.types.doc import DoclingDocument
import re
from functools import lru_cache
from app.services.performance import measure_stage, timed_stage

VLM_PICTURE_TYPES = [
"flow_chart",
"bar_chart",
"line_chart",
"pie_chart",
"scatter_plot",
"box_plot",
"other_chart",
"table",
"engineering_drawing",
"geographical_map",
"topographical_map",
]

VLM_TYPE_CONFIDENCE = 0.9
OCR_TRUST_CONFIDENCE = 0.9
OCR_ACCEPT_CONFIDENCE = 0.8
TEXT_IMAGE_TYPES = {"screenshot_from_computer", "screenshot_from_manual", "full_page_image"}


def route_ocr_result(record):
    """VLM 仅兜底有文字证据的识别失败，或高置信度的结构化内容。"""
    if record.get("ocr_failed"):
        return "vlm"
    scores = record.get("ocr_score")
    score = float(sum(scores) / len(scores)) if scores is not None and len(scores) else 0.0
    record["ocr_mean_score"] = score
    units = record["units"]
    if record["text"].strip() and score >= OCR_TRUST_CONFIDENCE:
        return "ocr"
    if units <= 5:
        if record["text"].strip() and score >= OCR_ACCEPT_CONFIDENCE:
            return "ocr"
        # 少量/空 OCR 不再把照片、装饰图、未知类型自动送入 VLM。
        if (record.get("type") in set(VLM_PICTURE_TYPES) | TEXT_IMAGE_TYPES
                and (record.get("confidence") or 0) >= VLM_TYPE_CONFIDENCE):
            return "vlm"
        return "skip"
    if score < OCR_ACCEPT_CONFIDENCE:
        return "vlm"
    return "ocr" if units <= 20 else "quality"

@lru_cache(maxsize=1)
@timed_stage("chunk.rapidocr_model_load")
def get_ocr_engine():
    return RapidOCR()

@lru_cache(maxsize=1)
@timed_stage("chunk.ocr_quality_model_load")
def get_ocr_error_predictor():
    from surya.ocr_error import OCRErrorPredictor

    # 当前安装版本默认连接共享服务；本地加载避免额外启动服务的要求。
    factory = getattr(OCRErrorPredictor, "local", OCRErrorPredictor)
    return factory()


def vlm_router(classifications: list[dict] | None):
    #初步判断要走vlm的
    vlms = []
    others = []
    if classifications is None:
        return [], []
    for classification in classifications:
        if classification is None:
            continue
        if (classification["type"] in VLM_PICTURE_TYPES
                and classification["confidence"] is not None
                and classification["confidence"] >= VLM_TYPE_CONFIDENCE):
            classification["router"] = "vlm"
            vlms.append(classification)
        else:
            others.append(classification)

    return vlms, others


@timed_stage("chunk.rapidocr_total")
def ocr_process(
    others: list[dict],
    document: DoclingDocument,
):
    """对剩下的图做ocr处理"""
    pictures_by_id = {
        item.self_ref: item
        for item in document.pictures
    }

    for other in others:
        picture_id = other["id"]
        item = pictures_by_id[picture_id]
        try:
            image = item.get_image(document)

            if image is None:
                other["text"] = ""
                other["ocr_score"] = []
                other["ocr_failed"] = True
                continue

            with measure_stage("chunk.rapidocr_image_prepare", picture_id=picture_id):
                pixels = np.array(image.convert("RGB"))[:, :, ::-1].copy()
            engine = get_ocr_engine()
            with measure_stage("chunk.rapidocr_image", picture_id=picture_id):
                result = engine(pixels)

            other["text"] = "\n".join(result.txts) if result.txts else ""
            other["ocr_score"] = list(result.scores) if result.scores is not None else []
        except Exception:
            # A failed OCR attempt gets one VLM fallback, counted only if that also fails.
            other.update(text="", ocr_score=[], ocr_failed=True)

    return others

def text_stats(text: str) -> int:
    """中英文分别计算"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))      # 汉字数
    words = len(re.findall(r"[A-Za-z0-9]+", text))        # 英文单词或数字串数
    return cjk + words

def is_enough_text(ocr_results: list[dict]):
    """判断文字量"""
    for ocr_result in ocr_results:
        text = ocr_result["text"]
        units = text_stats(text)
        if units <= 5:
            ocr_result["units"] = units
            ocr_result["is_enough"]=False
        else:
            ocr_result["units"] = units
            ocr_result["is_enough"] = True

    return ocr_results

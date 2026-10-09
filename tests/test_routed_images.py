"""Selective picture enrichment with real Docling metadata and serializers."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from docling.datamodel.base_models import ApiImageRequestResult, VlmStopReason
from docling_core.types.doc import (
    BoundingBox, DoclingDocument, DocItemLabel, ImageRef, ProvenanceItem, Size,
)

from app.services.docling_images import enrich_routed_images
from app.services.pdf_chunking import split_pdf_document


API_REQUEST = "app.services.docling_images.request_picture_description"


def sample():
    doc = DoclingDocument(name="routed images")
    doc.add_page(page_no=1, size=Size(width=600, height=800))
    prov = ProvenanceItem(page_no=1, bbox=BoundingBox(l=0, t=0, r=100, b=100), charspan=(0, 0))
    doc.add_text(label=DocItemLabel.TEXT, text="Before", prov=prov)
    image = ImageRef.from_pil(Image.new("RGB", (100, 100), "white"), dpi=72)
    for _ in range(3):
        doc.add_picture(image=image, prov=prov)
    doc.add_text(label=DocItemLabel.TEXT, text="After", prov=prov)
    rows = [
        {"id": doc.pictures[0].self_ref, "router": "ocr", "text": "OCR evidence"},
        {"id": doc.pictures[1].self_ref, "router": "vlm", "text": "old OCR"},
        {"id": doc.pictures[2].self_ref, "router": "skip", "text": "discarded"},
    ]
    return doc, rows


class RoutedImageTests(unittest.TestCase):
    def test_only_vlm_route_calls_api_and_all_text_reaches_chunks_in_order(self):
        doc, rows = sample()
        refs = [item.self_ref for item, _ in doc.iterate_items()]
        response = ApiImageRequestResult("VLM evidence", 5, VlmStopReason.END_OF_SEQUENCE)
        with patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}), patch(API_REQUEST, return_value=response) as api:
            result = enrich_routed_images(doc, rows)
        api.assert_called_once()
        self.assertEqual(result[1]["text"], "VLM evidence")
        self.assertEqual(rows[1]["text"], "old OCR")
        self.assertEqual(result[2]["text"], "")
        self.assertEqual(doc.pictures[0].meta.description.created_by, "rapidocr")
        self.assertEqual(doc.pictures[1].meta.description.created_by, "glm-4.6v-flash")
        self.assertEqual([item.self_ref for item, _ in doc.iterate_items()], refs)
        texts = [doc.export_to_markdown(page_no=1), "\n".join(c.text for p in split_pdf_document(doc) for c in p.children)]
        for text in texts:
            positions = [text.index(s) for s in ["Before", "OCR evidence", "VLM evidence", "After"]]
            self.assertEqual(positions, sorted(positions))
            self.assertNotIn("discarded", text)
            self.assertNotIn("old OCR", text)

    def test_ocr_and_skip_do_not_require_api_key(self):
        doc, rows = sample()
        with patch.dict(os.environ, {"ZHIPU_API_KEY": ""}), patch(API_REQUEST) as api:
            enrich_routed_images(doc, [rows[0], rows[2]])
            enrich_routed_images(doc, [])
        api.assert_not_called()

    def test_empty_vlm_output_fails_instead_of_accepting_old_ocr(self):
        doc, rows = sample()
        response = ApiImageRequestResult("", 0, VlmStopReason.UNSPECIFIED)
        with patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}), patch(API_REQUEST, return_value=response):
            with self.assertRaisesRegex(ValueError, "图片描述"):
                enrich_routed_images(doc, [rows[1]])

    def test_truncated_nonempty_response_is_rejected(self):
        doc, rows = sample()
        response = ApiImageRequestResult("Partial evidence", 8192, VlmStopReason.LENGTH)
        budgets = []
        def respond(*args, **kwargs):
            budgets.append(kwargs["options"].params["max_tokens"])
            return response
        with patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}), patch(API_REQUEST, side_effect=respond) as api:
            with self.assertRaisesRegex(ValueError, r"#/pictures/1.*stop_reason=length"):
                enrich_routed_images(doc, [rows[1]])
        self.assertEqual(api.call_count, 2)
        self.assertEqual(budgets, [8192, 32768])

    def test_length_retry_recovers_only_current_image_and_restores_budget(self):
        doc, rows = sample()
        rows[2]["router"] = "vlm"
        partial = ApiImageRequestResult("Partial", 8192, VlmStopReason.LENGTH)
        complete = ApiImageRequestResult("Complete", 100, VlmStopReason.END_OF_SEQUENCE)
        budgets = []
        responses = iter([partial, complete, complete])
        def respond(*args, **kwargs):
            budgets.append(kwargs["options"].params["max_tokens"])
            return next(responses)
        with patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}), patch(
            API_REQUEST, side_effect=respond,
        ) as api:
            output = enrich_routed_images(doc, rows)
        self.assertEqual(budgets, [8192, 32768, 8192])
        self.assertEqual(output[1]["text"], "Complete")
        self.assertEqual(doc.pictures[1].meta.description.text, "Complete")
        self.assertNotIn("Partial", doc.export_to_markdown())

    def test_filter_and_request_failure_have_distinct_reasons_and_picture_id(self):
        for reason in (VlmStopReason.CONTENT_FILTERED, VlmStopReason.UNSPECIFIED):
            doc, rows = sample()
            response = ApiImageRequestResult("", 0, reason)
            with patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}), patch(API_REQUEST, return_value=response) as api:
                with self.assertRaisesRegex(ValueError, rf"#/pictures/1.*stop_reason={reason.value}"):
                    enrich_routed_images(doc, [rows[1]])
            api.assert_called_once()

    def test_missing_pixels_and_invalid_route_fail(self):
        doc, rows = sample()
        doc.pictures[1].image = None
        with self.assertRaisesRegex(ValueError, "缺少像素"):
            enrich_routed_images(doc, [rows[1]])
        for invalid in ({"id": "missing", "router": "ocr", "text": "x"},
                        {"id": rows[0]["id"], "router": None},
                        {"id": rows[0]["id"], "router": "ocr", "text": ""}):
            with self.assertRaises(ValueError):
                enrich_routed_images(doc, [invalid])

    def test_long_text_quality_is_batched_and_labels_keep_picture_order(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            from app.services import chunk
        from app.services import images_routers
        for labels in (["good", "bad"], ["good"]):
            doc, _ = sample()
            texts = ["一二三四五六七八九十" * 3, "甲乙丙丁戊己庚辛壬癸" * 3, "短文本超过五字"]
            converter = Mock()
            converter.convert.return_value = SimpleNamespace(document=doc, status=chunk.ConversionStatus.SUCCESS)
            ocr = Mock(side_effect=[SimpleNamespace(txts=[text], scores=[.85]) for text in texts])
            predictor = Mock(return_value=SimpleNamespace(labels=labels))
            response = ApiImageRequestResult("VLM evidence", 5, VlmStopReason.END_OF_SEQUENCE)
            stats = dict(child_count=1, parent_count=1, parent_split_ms=0, child_split_ms=0,
                         embedding_ms=0, parent_db_flush_ms=0)
            with patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}), \
                 patch.object(chunk, "get_document_converter", return_value=converter), \
                 patch.object(images_routers, "get_ocr_engine", return_value=ocr), \
                 patch.object(chunk, "get_ocr_error_predictor", return_value=predictor), \
                 patch(API_REQUEST, return_value=response) as api, \
                 patch.object(chunk, "splitter_creator", return_value=stats) as splitter:
                if len(labels) == 1:
                    with self.assertRaisesRegex(ValueError, "数量"):
                        chunk.chunking(b"test", Mock(), 1, "paper.pdf")
                    api.assert_not_called()
                    splitter.assert_not_called()
                else:
                    chunk.chunking(b"test", Mock(), 1, "paper.pdf")
                    api.assert_called_once()
                    self.assertEqual(doc.pictures[0].meta.description.text, texts[0])
                    self.assertEqual(doc.pictures[1].meta.description.text, "VLM evidence")
                    self.assertEqual(doc.pictures[2].meta.description.text, texts[2])
            predictor.assert_called_once_with(texts[:2])

    def test_real_graph_routes_ocr_and_vlm_before_split(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            from app.services import chunk
        from app.services import images_routers
        stats = dict(child_count=1, parent_count=1, parent_split_ms=0, child_split_ms=0,
                     embedding_ms=0, parent_db_flush_ms=0)
        for filename in ("paper.pdf", "slides.pptx"):
            doc, _ = sample()
            converter = Mock()
            converter.convert.return_value = SimpleNamespace(document=doc, status=chunk.ConversionStatus.SUCCESS)
            ocr = Mock(side_effect=[
                SimpleNamespace(txts=["一二三四五六七八九十"], scores=[.95]),
                SimpleNamespace(txts=["需要视觉模型修复的文字"], scores=[.5]),
                SimpleNamespace(txts=["图"], scores=[.5]),
            ])
            response = ApiImageRequestResult("VLM evidence", 5, VlmStopReason.END_OF_SEQUENCE)
            with patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}), \
                 patch.object(chunk, "get_document_converter", return_value=converter), \
                 patch.object(images_routers, "get_ocr_engine", return_value=ocr), \
                 patch(API_REQUEST, return_value=response) as api, \
                 patch.object(chunk, "splitter_creator", return_value=stats) as splitter:
                chunk.chunking(b"test", Mock(), 1, filename)
            self.assertEqual(api.call_count, 1)
            self.assertIn("一二三四五六七八九十", splitter.call_args.args[0])
            self.assertIn("VLM evidence", splitter.call_args.args[0])

    def test_strict_direct_vlm_types_and_confidence_boundary(self):
        from app.services.images_routers import vlm_router
        for kind, confidence, expected in [
            ("flow_chart", .9, True), ("table", .95, True),
            ("flow_chart", .899, False), ("flow_chart", None, False),
            ("photograph", .99, False), ("other", .99, False),
            ("screenshot_from_computer", .99, False),
            ("full_page_image", .99, False), (None, None, False),
        ]:
            with self.subTest(kind=kind, confidence=confidence):
                row = {"type": kind, "confidence": confidence, "router": None}
                vlms, others = vlm_router([row])
                self.assertEqual(bool(vlms), expected)
                self.assertEqual(bool(others), not expected)

    def test_ocr_evidence_limits_fallback_without_discarding_reliable_short_text(self):
        from app.services.images_routers import route_ocr_result, is_enough_text
        long_text = "一二三四五六七八九十" * 3
        for text, score, kind, confidence, expected in [
            ("图", .5, None, None, "skip"),
            ("", 0, "photograph", .99, "skip"),
            ("", 0, "other", .99, "skip"),
            ("章", .8, "stamp", .99, "ocr"),
            ("", 0, "full_page_image", .9, "vlm"),
            ("", 0, "full_page_image", .899, "skip"),
            ("", 0, "flow_chart", .85, "skip"),
            ("这些文字需要修复", .79, None, None, "vlm"),
            ("这些文字识别可信", .8, None, None, "ocr"),
            (long_text, .9, None, None, "ocr"),
            (long_text, .899, None, None, "quality"),
            (long_text, .79, None, None, "vlm"),
        ]:
            with self.subTest(text=text, score=score, kind=kind):
                row = {"text": text, "ocr_score": [score], "type": kind, "confidence": confidence}
                is_enough_text([row])
                self.assertEqual(route_ocr_result(row), expected)


if __name__ == "__main__":
    unittest.main()

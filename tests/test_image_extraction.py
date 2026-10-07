"""Run: agent/Scripts/python.exe tests/test_image_extraction.py; no model downloads."""

import json
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from docling_core.types.doc import (
    BoundingBox, DocItemLabel, DoclingDocument, ImageRef, PictureItem, ProvenanceItem, Size,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.image_extraction import extract_document_images, image_worth_describing, index_document_images


def patterned_image(size=(200, 150), color="red"):
    image = Image.new("RGB", size, color)
    image.paste("white", (0, size[1] // 2, size[0], size[1]))
    return image


def sample_document():
    doc = DoclingDocument(name="pictures")
    doc.add_page(page_no=1, size=Size(width=600, height=800))
    doc.add_page(page_no=2, size=Size(width=600, height=800))
    doc.add_text(label=DocItemLabel.TEXT, text="Before picture", prov=ProvenanceItem(
        page_no=1, bbox=BoundingBox(l=0, t=0, r=100, b=20), charspan=(0, 14),
    ))
    picture = doc.add_picture(
        image=ImageRef.from_pil(patterned_image(), dpi=72),
        prov=ProvenanceItem(page_no=2, bbox=BoundingBox(l=10, t=20, r=50, b=50), charspan=(0, 0)),
    )
    return doc, picture


class ImageExtractionTests(unittest.TestCase):
    def test_filter_size_and_aspect_ratio_boundaries(self):
        doc, picture = sample_document()
        for size, expected in (
            ((99, 150), False), ((150, 99), False), ((100, 100), True),
            ((800, 100), True), ((801, 100), False),
            ((100, 800), True), ((100, 801), False),
        ):
            with self.subTest(size=size):
                self.assertEqual(image_worth_describing(picture, doc, patterned_image(size)), expected)

    def test_filter_grayscale_range_boundary(self):
        doc, picture = sample_document()
        for difference, expected in ((0, False), (14, False), (15, True)):
            with self.subTest(difference=difference):
                image = Image.new("L", (200, 150), 100)
                image.paste(100 + difference, (0, 75, 200, 150))
                self.assertEqual(image_worth_describing(picture, doc, image), expected)

    def test_filtered_pictures_keep_identifiers_and_only_useful_image_reaches_model(self):
        from app.services.image_ocr import recognize_document_images
        import httpx

        doc = DoclingDocument(name="filtered pictures")
        for image in (patterned_image((99, 150)), patterned_image((900, 100)),
                      Image.new("RGB", (200, 150), "white"), patterned_image()):
            doc.add_picture(image=ImageRef.from_pil(image, dpi=72))
        with tempfile.TemporaryDirectory() as directory:
            records = extract_document_images(doc, 42, directory)
            self.assertEqual([r["status"] for r in records], ["filtered"] * 3 + ["saved"])
            self.assertEqual([r["reading_order"] for r in records], [0, 1, 2, 3])
            self.assertEqual([r["image_path"] for r in records], [None, None, None, "3.png"])
            self.assertEqual(sorted(p.name for p in Path(directory).glob("*.png")), ["3.png"])
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["images"], records)
            with patch.dict(os.environ, {"ZHIPU_API_KEY": "unused-test-key"}), \
                 patch("app.services.image_ocr.httpx.Client") as client:
                post = client.return_value.__enter__.return_value.post
                post.return_value = httpx.Response(200, json={"choices": [{
                    "finish_reason": "stop", "message": {"content": "Useful image"},
                }]}, request=httpx.Request("POST", "https://example.com"))
                self.assertEqual(recognize_document_images(records, directory), {3: "Useful image"})
                post.assert_called_once()

    def test_index_preserves_full_reading_order_without_reading_pixels_or_changing_document(self):
        doc, first_picture = sample_document()
        doc.add_text(label=DocItemLabel.CAPTION, text="Picture caption", parent=first_picture)
        doc.add_text(label=DocItemLabel.TEXT, text="Between pictures")
        second_picture = doc.add_picture()
        before = doc.model_dump(mode="json")

        with patch.object(PictureItem, "get_image") as get_image:
            pictures = index_document_images(doc)

        self.assertEqual(list(pictures), [1, 4])
        self.assertIs(pictures[1], first_picture)
        self.assertIs(pictures[4], second_picture)
        get_image.assert_not_called()
        self.assertEqual(doc.model_dump(mode="json"), before)

    def test_saves_pixels_and_source_manifest_with_stable_occurrence_id(self):
        doc, picture = sample_document()
        with tempfile.TemporaryDirectory() as directory:
            records = extract_document_images(doc, 42, directory)
            record = records[0]
            self.assertEqual(record["page_no"], 2)
            self.assertEqual(record["reading_order"], 1)
            self.assertEqual(record["image_path"], "1.png")
            self.assertEqual(record["source_ref"], picture.self_ref)
            self.assertEqual(record["bbox"], picture.prov[0].bbox.model_dump(mode="json"))
            self.assertEqual(record["provenance"][0]["page_size"], {"width": 600.0, "height": 800.0})
            with Image.open(Path(directory) / record["image_path"]) as image:
                self.assertEqual(image.size, (200, 150))
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["images"], records)
            self.assertEqual(extract_document_images(doc, 42, directory)[0]["image_id"], record["image_id"])
            self.assertNotEqual(extract_document_images(doc, 43, directory)[0]["image_id"], record["image_id"])

    def test_identical_pictures_at_different_positions_keep_distinct_ids(self):
        doc, picture = sample_document()
        doc.add_picture(image=picture.image, prov=picture.prov[0])
        with tempfile.TemporaryDirectory() as directory:
            records = extract_document_images(doc, 42, directory)
            self.assertEqual(len({r["image_id"] for r in records}), 2)
            self.assertEqual([r["reading_order"] for r in records], [1, 2])

    def test_extraction_reuses_index_even_if_later_document_edits_shift_reading_order(self):
        doc, first_picture = sample_document()
        doc.add_picture(image=first_picture.image, prov=first_picture.prov[0])
        pictures = index_document_images(doc)
        doc.add_text(label=DocItemLabel.CAPTION, text="New caption", parent=first_picture)
        self.assertEqual(list(index_document_images(doc)), [1, 3])

        with tempfile.TemporaryDirectory() as directory:
            records = extract_document_images(doc, 42, directory, pictures=pictures)
            self.assertEqual([r["reading_order"] for r in records], [1, 2])
            self.assertEqual([r["image_path"] for r in records], ["1.png", "2.png"])
            self.assertTrue((Path(directory) / "2.png").is_file())
            self.assertFalse((Path(directory) / "3.png").exists())

    def test_explicit_empty_index_does_not_extract_other_pictures(self):
        doc, _ = sample_document()
        with tempfile.TemporaryDirectory() as directory:
            records = extract_document_images(doc, 42, directory, pictures={})
            self.assertEqual(records, [])
            self.assertEqual(list(Path(directory).glob("*.png")), [])
            manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["images"], [])

    def test_missing_pixels_or_location_are_reported_without_inventing_data(self):
        doc = DoclingDocument(name="no pixels")
        doc.add_picture()
        with tempfile.TemporaryDirectory() as directory:
            record = extract_document_images(doc, 42, directory)[0]
            self.assertEqual(record["status"], "unavailable")
            self.assertIsNone(record["image_path"])
            self.assertIsNone(record["page_no"])
            self.assertIsNone(record["bbox"])

    def test_one_failed_picture_does_not_hide_following_pictures(self):
        doc, picture = sample_document()
        doc.add_picture(image=picture.image, prov=picture.prov[0])
        with tempfile.TemporaryDirectory() as directory, patch.object(
            PictureItem, "get_image", side_effect=[ValueError("bad image"), patterned_image()],
        ):
            records = extract_document_images(doc, 42, directory)
            self.assertEqual([r["status"] for r in records], ["unavailable", "saved"])
            self.assertEqual([r["reading_order"] for r in records], [1, 2])
            self.assertEqual([r["image_path"] for r in records], [None, "2.png"])
            self.assertEqual(records[0]["error_type"], "ValueError")


class ImageIngestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            from app.services import chunk
        cls.chunk = chunk

    def test_pdf_converter_retains_images_with_ocr_disabled(self):
        with patch.object(self.chunk, "DocumentConverter") as converter:
            self.chunk.get_document_converter.__wrapped__()
        options = converter.call_args.kwargs["format_options"][self.chunk.InputFormat.PDF].pipeline_options
        self.assertTrue(options.generate_picture_images)
        self.assertEqual(options.images_scale, 2.0)
        self.assertFalse(options.do_ocr)
        self.assertFalse(options.do_picture_description)

    def test_real_pptx_conversion_extracts_embedded_picture(self):
        from pptx import Presentation
        from pptx.util import Inches

        slides = Presentation()
        slide = slides.slides.add_slide(slides.slide_layouts[6])
        png = BytesIO()
        patterned_image(color="blue").save(png, format="PNG")
        png.seek(0)
        slide.shapes.add_picture(png, Inches(1), Inches(1))
        content = BytesIO()
        slides.save(content)
        content.seek(0)
        converter = self.chunk.get_document_converter.__wrapped__()
        result = converter.convert(self.chunk.DocumentStream(name="test.pptx", stream=content))
        with tempfile.TemporaryDirectory() as directory:
            records = extract_document_images(result.document, 42, directory)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["page_no"], 1)
            self.assertEqual(records[0]["status"], "saved")
            self.assertIsNotNone(records[0]["bbox"])
            with Image.open(Path(directory) / records[0]["image_path"]) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))

    def test_pdf_and_ppt_uploads_extract_before_chunking(self):
        stats = dict(child_count=1, parent_count=1, parent_split_ms=0, child_split_ms=0,
                     embedding_ms=0, parent_db_flush_ms=0)
        for filename in ("paper.pdf", "slides.pptx"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                doc, _ = sample_document()
                converter = Mock()
                converter.convert.return_value = SimpleNamespace(document=doc)
                with patch.object(self.chunk, "get_document_converter", return_value=converter), \
                     patch.object(self.chunk, "recognize_document_images", return_value={1: "Image evidence"}) as ocr, \
                     patch.object(self.chunk, "splitter_creator", return_value=stats) as splitter:
                    self.chunk.chunking(b"fake", Mock(), 42, filename, image_output_dir=directory)
                manifest = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["images"][0]["status"], "saved")
                ocr.assert_called_once_with(manifest["images"], directory)
                self.assertIn("Image evidence", splitter.call_args.args[0])
                if filename.endswith(".pdf"):
                    parents = self.chunk.split_pdf_document(splitter.call_args.kwargs["pdf_document"])
                    self.assertEqual(parents[-1].children[0].text, "Image evidence")
                    self.assertEqual(parents[-1].children[0].page_numbers, [2])
                else:
                    pages = splitter.call_args.kwargs["page_texts"]
                    self.assertNotIn("Image evidence", pages[0])
                    self.assertIn("Image evidence", pages[1])

    def test_ocr_failure_stops_before_chunking(self):
        from app.services.image_ocr import ImageOCRError

        doc, picture = sample_document()
        converter = Mock()
        converter.convert.return_value = SimpleNamespace(document=doc)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.chunk, "get_document_converter", return_value=converter), \
             patch.object(self.chunk, "recognize_document_images", side_effect=ImageOCRError(1, "timeout")), \
             patch.object(self.chunk, "splitter_creator") as splitter:
            with self.assertRaises(ImageOCRError):
                self.chunk.chunking(b"fake", Mock(), 42, "paper.pdf", image_output_dir=directory)
        splitter.assert_not_called()
        self.assertIsNone(picture.meta)


if __name__ == "__main__":
    unittest.main()

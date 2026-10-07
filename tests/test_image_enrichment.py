"""Offline checks for OCR insertion and the existing PDF/PPT splitters."""

from pathlib import Path
import sys
import unittest

from docling_core.types.doc import BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.image_enrichment import apply_image_ocr_results
from app.services.image_extraction import index_document_images
from app.services.pdf_chunking import split_pdf_document
from app.services.ppt_chunking import clean_slide_text, split_slide_text


def provenance(page):
    return ProvenanceItem(page_no=page, bbox=BoundingBox(l=0, t=0, r=100, b=100), charspan=(0, 0))


def sample_document():
    doc = DoclingDocument(name="OCR insertion")
    for page in (1, 2):
        doc.add_page(page_no=page, size=Size(width=600, height=800))
    doc.add_heading(text="Section", prov=provenance(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Before picture", prov=provenance(1))
    doc.add_picture(prov=provenance(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Between pictures", prov=provenance(1))
    doc.add_picture(prov=provenance(2))
    doc.add_text(label=DocItemLabel.TEXT, text="After picture", prov=provenance(2))
    return doc, index_document_images(doc)


class ImageEnrichmentTests(unittest.TestCase):
    def test_out_of_order_results_return_to_original_positions_and_keep_markdown(self):
        doc, pictures = sample_document()
        original_refs = [item.self_ref for item, _ in doc.iterate_items(traverse_pictures=True)]
        table = "| key | value |\n| --- | --- |\n| x_1 | $x_1 < 2$ |"
        apply_image_ocr_results(pictures, {4: "Second image", 2: table})
        markdown = doc.export_to_markdown(traverse_pictures=False)
        fragments = ["Before picture", table, "Between pictures", "Second image", "After picture"]
        positions = [markdown.index(fragment) for fragment in fragments]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual([item.self_ref for item, _ in doc.iterate_items(traverse_pictures=True)], original_refs)
        self.assertEqual(pictures[4].prov[0].page_no, 2)

    def test_pdf_children_keep_ocr_text_heading_and_source_pages(self):
        doc, pictures = sample_document()
        apply_image_ocr_results(pictures, {2: "OCR on page one", 4: "OCR on page two"})
        parents = split_pdf_document(doc)
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0].heading_path, ["Section"])
        self.assertEqual(parents[0].page_numbers, [1, 2])
        self.assertIn("OCR on page one", parents[0].children[0].text)
        self.assertIn("OCR on page two", parents[0].children[0].text)
        self.assertEqual(parents[0].children[0].page_numbers, [1, 2])

    def test_ppt_default_export_keeps_ocr_on_its_own_slide(self):
        doc, pictures = sample_document()
        apply_image_ocr_results(pictures, {2: "OCR on page one", 4: "OCR on page two"})
        for page, expected, excluded in ((1, "OCR on page one", "OCR on page two"),
                                          (2, "OCR on page two", "OCR on page one")):
            text = clean_slide_text(doc.export_to_markdown(page_no=page))
            children = split_slide_text(text)
            self.assertIn(expected, "\n".join(children))
            self.assertNotIn(excluded, "\n".join(children))

    def test_image_only_document_becomes_searchable(self):
        doc = DoclingDocument(name="image only")
        doc.add_page(page_no=1, size=Size(width=600, height=800))
        doc.add_picture(prov=provenance(1))
        apply_image_ocr_results(index_document_images(doc), {0: "Visible evidence"})
        self.assertEqual(split_pdf_document(doc)[0].children[0].text, "Visible evidence")
        self.assertEqual(split_slide_text(clean_slide_text(doc.export_to_markdown(page_no=1))), ["Visible evidence"])

    def test_repeated_insertion_replaces_description_without_duplicate_text(self):
        doc, pictures = sample_document()
        apply_image_ocr_results(pictures, {2: "Old OCR"})
        apply_image_ocr_results(pictures, {2: "Updated OCR"})
        apply_image_ocr_results(pictures, {2: "Updated OCR"})
        markdown = doc.export_to_markdown()
        self.assertNotIn("Old OCR", markdown)
        self.assertEqual(markdown.count("Updated OCR"), 1)

    def test_empty_and_unavailable_results_leave_document_unchanged(self):
        doc, pictures = sample_document()
        before = doc.model_dump()
        apply_image_ocr_results(pictures, {2: " \n"})
        self.assertEqual(doc.model_dump(), before)

    def test_invalid_results_fail_before_any_insertion(self):
        for results in ({2: "valid", 99: "unknown image"}, {2: "valid", 4: None}):
            with self.subTest(results=results):
                doc, pictures = sample_document()
                before = doc.model_dump()
                with self.assertRaises(ValueError):
                    apply_image_ocr_results(pictures, results)
                self.assertEqual(doc.model_dump(), before)


if __name__ == "__main__":
    unittest.main()

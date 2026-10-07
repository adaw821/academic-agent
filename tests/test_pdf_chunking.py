"""Run: agent/Scripts/python.exe tests/test_pdf_chunking.py. No downloads or live DB."""

import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from docling_core.types.doc import (
    BoundingBox, DocItemLabel, DoclingDocument, ProvenanceItem, Size,
    TableCell, TableData,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.pdf_chunking import split_pdf_document
from app.services.image_enrichment import apply_image_ocr_results
from app.services.image_extraction import index_document_images


def document():
    doc = DoclingDocument(name="test")
    for page in range(1, 7):
        doc.add_page(page_no=page, size=Size(width=600, height=800))
    return doc


def provenance(page, text):
    return ProvenanceItem(page_no=page, bbox=BoundingBox(l=0, t=0, r=100, b=100),
                          charspan=(0, len(text)))


def heading(doc, text, level=1, page=1):
    return doc.add_heading(text=text, level=level, prov=provenance(page, text))


def paragraph(doc, text, page=1):
    return doc.add_text(label=DocItemLabel.TEXT, text=text, prov=provenance(page, text))


class PdfSplittingTests(unittest.TestCase):
    def test_section_and_child_can_cross_pages_with_full_heading_path(self):
        doc = document()
        doc.add_title(text="Databases", prov=provenance(1, "Databases"))
        heading(doc, "Indexes")
        heading(doc, "Composite indexes", level=2, page=2)
        paragraph(doc, "A composite index contains multiple columns.", page=2)
        paragraph(doc, "Queries follow the leftmost prefix rule.", page=3)
        parents = split_pdf_document(doc)
        self.assertEqual(len(parents), 1)
        parent = parents[0]
        self.assertEqual(parent.heading_path, ["Databases", "Indexes", "Composite indexes"])
        self.assertEqual(parent.page_numbers, [2, 3])
        self.assertEqual(len(parent.children), 1)
        self.assertEqual(parent.children[0].page_numbers, [2, 3])
        self.assertTrue(parent.children[0].text.startswith("# Databases > Indexes > Composite indexes\n\n"))
        self.assertIn("multiple columns", parent.children[0].text)
        self.assertIn("leftmost prefix", parent.children[0].text)

    def test_new_heading_on_same_page_ends_parent_and_clears_deeper_path(self):
        doc = document()
        heading(doc, "Database")
        heading(doc, "Indexes", level=2)
        heading(doc, "Composite", level=3)
        paragraph(doc, "Index evidence.")
        heading(doc, "Transactions", level=2)
        paragraph(doc, "Transaction evidence.")
        first, second = split_pdf_document(doc)
        self.assertEqual(first.heading_path, ["Database", "Indexes", "Composite"])
        self.assertEqual(second.heading_path, ["Database", "Transactions"])
        self.assertNotEqual(first.section_id, second.section_id)
        self.assertNotIn("Transaction evidence", first.content)
        self.assertNotIn("Index evidence", second.content)

    def test_large_section_keeps_identity_and_bounded_parents_and_children(self):
        doc = document()
        heading(doc, "Chapter")
        heading(doc, "Evidence", level=2)
        bodies = [f"Item {i}: " + "evidence " * 30 for i in range(35)]
        for i, body in enumerate(bodies):
            paragraph(doc, body, page=1 + i // 7)
        parents = split_pdf_document(doc)
        self.assertGreater(len(parents), 1)
        self.assertEqual(len({p.section_id for p in parents}), 1)
        children = [c for p in parents for c in p.children]
        self.assertTrue(all(len(p.content) <= 4000 for p in parents))
        self.assertTrue(all(len(c.text) <= 800 for c in children))
        self.assertTrue(all(c.text.startswith("# Chapter > Evidence\n\n") for c in children))
        for body in bodies:
            self.assertEqual(sum(body.strip() in p.content for p in parents), 1)
            self.assertEqual(sum(body.strip() in c.text for c in children), 1)
        for parent in parents:
            self.assertEqual(parent.page_numbers, sorted({page for c in parent.children for page in c.page_numbers}))

    def test_repeated_heading_text_is_not_a_shared_identity(self):
        doc = document()
        heading(doc, "Example")
        paragraph(doc, "First example.")
        heading(doc, "Example", page=2)
        paragraph(doc, "Second example.", page=2)
        first, second = split_pdf_document(doc)
        self.assertEqual(first.heading_path, second.heading_path)
        self.assertNotEqual(first.section_id, second.section_id)

    def test_unheaded_document_and_preamble_fall_back_to_pages(self):
        for with_heading in (False, True):
            with self.subTest(with_heading=with_heading):
                doc = document()
                paragraph(doc, "Page one preamble.")
                paragraph(doc, "Page two preamble.", page=2)
                if with_heading:
                    heading(doc, "Section", page=3)
                    paragraph(doc, "Section text.", page=3)
                parents = split_pdf_document(doc)
                self.assertEqual([p.page_numbers for p in parents[:2]], [[1], [2]])
                self.assertTrue(all(p.section_id is None and p.heading_path == [] for p in parents[:2]))
                self.assertEqual(len(parents), 3 if with_heading else 2)

    def test_blank_and_heading_only_sections_do_not_generate_children(self):
        doc = document()
        heading(doc, "Empty section")
        doc.add_picture(prov=provenance(2, ""))
        self.assertEqual(split_pdf_document(doc), [])

    def test_unheaded_list_spanning_pages_keeps_page_fallback(self):
        doc = document()
        group = doc.add_list_group()
        doc.add_list_item(text="First page item.", parent=group, prov=provenance(1, "First page item."))
        doc.add_list_item(text="Second page item.", parent=group, prov=provenance(2, "Second page item."))
        parents = split_pdf_document(doc)
        self.assertEqual([p.page_numbers for p in parents], [[1], [2]])
        self.assertIn("First page item", parents[0].content)
        self.assertNotIn("Second page item", parents[0].content)

    def test_picture_uses_vision_description_instead_of_docling_child_text(self):
        doc = document()
        heading(doc, "Outer section")
        picture = doc.add_picture(prov=provenance(1, ""))
        doc.add_heading(text="Scanned section", parent=picture, prov=provenance(1, "Scanned section"))
        doc.add_text(label=DocItemLabel.TEXT, text="OCR evidence.", parent=picture,
                     prov=provenance(1, "OCR evidence."))
        apply_image_ocr_results(index_document_images(doc), {1: "Vision evidence."})
        markdown = doc.export_to_markdown(traverse_pictures=False)
        self.assertIn("Vision evidence.", markdown)
        self.assertNotIn("Scanned section", markdown)
        self.assertNotIn("OCR evidence.", markdown)
        parents = split_pdf_document(doc)
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0].heading_path, ["Outer section"])
        self.assertIn("Vision evidence.", parents[0].content)
        self.assertNotIn("OCR evidence.", parents[0].content)
        self.assertIn("Vision evidence.", parents[0].children[0].text)
        self.assertEqual(parents[0].children[0].page_numbers, [1])

    def test_code_list_and_table_survive_serialization_and_packing(self):
        doc = document()
        heading(doc, "Example")
        doc.add_code(text="SELECT x\n\nFROM demo;", prov=provenance(1, "SELECT x\n\nFROM demo;"))
        doc.add_list_item(text="First list item.", parent=doc.add_list_group(),
                          prov=provenance(1, "First list item."))
        doc.add_table(data=TableData(num_rows=1, num_cols=1, table_cells=[TableCell(
            text="Table evidence", start_row_offset_idx=0, end_row_offset_idx=1,
            start_col_offset_idx=0, end_col_offset_idx=1,
        )]), prov=provenance(2, "Table evidence"))
        parent = split_pdf_document(doc)[0]
        text = "\n".join(child.text for child in parent.children)
        self.assertIn("SELECT x\n\nFROM demo;", text)
        self.assertIn("First list item.", text)
        self.assertEqual(text.count("Table evidence"), 1)
        self.assertEqual(parent.page_numbers, [1, 2])

    def test_oversized_block_has_no_text_loss_or_overlap(self):
        doc = document()
        heading(doc, "Large")
        paragraph(doc, "字" * 9000)
        parents = split_pdf_document(doc)
        self.assertEqual(sum(p.content.count("字") for p in parents), 9000)
        self.assertEqual(sum(c.text.count("字") for p in parents for c in p.children), 9000)
        self.assertTrue(all(len(p.content) <= 4000 for p in parents))
        self.assertTrue(all(len(c.text) <= 800 for p in parents for c in p.children))

    def test_long_title_is_preserved_or_fails_explicitly_if_it_cannot_fit(self):
        doc = document()
        heading(doc, "标" * 450)
        paragraph(doc, "文" * 900)
        children = split_pdf_document(doc)[0].children
        self.assertTrue(all(c.text.startswith("# " + "标" * 450 + "\n\n") and len(c.text) <= 800 for c in children))
        doc = document()
        heading(doc, "标" * 800)
        paragraph(doc, "正文")
        with self.assertRaisesRegex(ValueError, "标题路径过长"):
            split_pdf_document(doc)

    def test_missing_page_provenance_fails_explicitly(self):
        doc = document()
        doc.add_text(label=DocItemLabel.TEXT, text="Unknown page")
        with self.assertRaisesRegex(ValueError, "缺少来源页码"):
            split_pdf_document(doc)


class PdfIngestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            from app.models.users import User
            from app.models.projects import Project
            from app.models.documents import Document
            from app.models.courses import Course
            from app.models.course_timetable import CourseSchedule
            from app.services import chunk
        cls.chunk = chunk

    def test_metadata_and_heading_enriched_embeddings_are_saved(self):
        doc = document()
        heading(doc, "Chapter")
        heading(doc, "Section", level=2)
        paragraph(doc, "Page one evidence.")
        paragraph(doc, "Page two evidence.", page=2)
        records = []
        db = Mock()

        def add(record):
            if isinstance(record, self.chunk.Parent):
                record.id = len(records) + 1
            records.append(record)

        db.add.side_effect = add
        converter = Mock()
        converter.convert.return_value = SimpleNamespace(document=doc)
        embedding = Mock()
        embedding.encode.side_effect = lambda texts, **kwargs: np.zeros((len(texts), 1024))
        with patch.object(self.chunk, "get_document_converter", return_value=converter), \
             patch.object(self.chunk, "get_embedding_model", return_value=embedding):
            self.chunk.chunking(b"fake PDF", db, 99, "Paper.PDF")
        parents = [r for r in records if isinstance(r, self.chunk.Parent)]
        children = [r for r in records if isinstance(r, self.chunk.Child)]
        self.assertEqual(len(parents), 1)
        self.assertEqual(len(children), 1)
        self.assertEqual(parents[0].heading_path, ["Chapter", "Section"])
        self.assertIsNotNone(parents[0].section_id)
        self.assertEqual(parents[0].page_numbers, [1, 2])
        self.assertEqual(children[0].page_numbers, [1, 2])
        self.assertEqual(children[0].parent_id, parents[0].id)
        self.assertEqual(embedding.encode.call_args.args[0], [children[0].content])
        self.assertTrue(children[0].content.startswith("# Chapter > Section\n\n"))
        self.assertEqual(len(children[0].embedding), 1024)

    def test_empty_body_fails_before_database_or_model_calls(self):
        doc = document()
        heading(doc, "Title only")
        converter = Mock()
        converter.convert.return_value = SimpleNamespace(document=doc)
        db = Mock()
        with patch.object(self.chunk, "get_document_converter", return_value=converter), \
             patch.object(self.chunk, "get_embedding_model") as embedding:
            with self.assertRaisesRegex(ValueError, "没有可检索的正文"):
                self.chunk.chunking(b"fake", db, 99, "empty.pdf")
        embedding.assert_not_called()
        db.add.assert_not_called()


class SectionMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_keep_legacy_rows(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, inspect, text

        path = Path(__file__).resolve().parents[1] / "migration/versions/e63f2b908a14_add_pdf_section_metadata.py"
        spec = importlib.util.spec_from_file_location("section_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE document_parent (id INTEGER PRIMARY KEY, document_id INTEGER, content TEXT)"))
            connection.execute(text("CREATE TABLE document_child (id INTEGER PRIMARY KEY, content TEXT)"))
            connection.execute(text("INSERT INTO document_parent VALUES (1, 99, 'legacy')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                row = connection.execute(text("SELECT content, section_id, heading_path, page_numbers FROM document_parent")).one()
                self.assertEqual(tuple(row), ("legacy", None, None, None))
                self.assertIn("page_numbers", {c["name"] for c in inspect(connection).get_columns("document_child")})
                migration.downgrade()
                self.assertEqual(connection.scalar(text("SELECT content FROM document_parent")), "legacy")
                self.assertNotIn("section_id", {c["name"] for c in inspect(connection).get_columns("document_parent")})
        engine.dispose()


if __name__ == "__main__":
    unittest.main()

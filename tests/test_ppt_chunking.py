"""Run: agent/Scripts/python.exe tests/test_ppt_chunking.py. No live DB/model calls."""

import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.ppt_chunking import clean_slide_text, markdown_blocks, split_slide_text


INTRO = """# Introduction

- IBM Sequel Language was developed as a part of System R project to manage data in a relational database in the early 1970's.
- Later the language was renamed as Structured Query Language, aka SQL.
- SQL can define and manipulate data.
- Data definition is used when a user wants to create new databases or tables.
- Data manipulation or querying allows users search information from the database.
"""


class SlideSplittingTests(unittest.TestCase):
    def test_short_slide_keeps_origin_and_renaming_together(self):
        chunks = split_slide_text(INTRO)
        self.assertEqual(len(chunks), 1)
        self.assertIn("System R", chunks[0])
        self.assertIn("renamed as Structured Query Language", chunks[0])
        self.assertLessEqual(len(chunks[0]), 800)

    def test_long_slide_packs_whole_list_items_and_repeats_title(self):
        blocks = [f"- Item {i}: " + "evidence " * 12 for i in range(12)]
        chunks = split_slide_text("# Predicate\n\n" + "\n".join(blocks))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.startswith("# Predicate\n\n") and len(c) <= 800 for c in chunks))
        for block in blocks:
            self.assertEqual(sum(block.strip() in c for c in chunks), 1)

    def test_code_with_blank_lines_is_one_block(self):
        code = "```sql\nSELECT title\n\nFROM film WHERE rental_rate < 1;\n```"
        text = "# Example\n\n" + code + "\n\nFind inexpensive films."
        self.assertIn(code, markdown_blocks(text))
        chunks = split_slide_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertIn(code, chunks[0])
        self.assertIn("Find inexpensive films.", chunks[0])

    def test_empty_markers_and_title_only_slides_are_not_indexed(self):
        self.assertEqual(split_slide_text("# Basic Query\n\n<!-- image -->\n-\n1."), [])
        self.assertEqual(split_slide_text(""), [])
        self.assertEqual(clean_slide_text("# Query\n<!-- image -->\n- SQL evidence"),
                         "# Query\n\n- SQL evidence")

    def test_long_paragraph_splits_at_sentences_without_losing_text(self):
        sentences = [f"Sentence {i} contains a complete explanation." for i in range(30)]
        chunks = split_slide_text("# Query\n\n" + " ".join(sentences))
        self.assertTrue(all(len(c) <= 800 for c in chunks))
        for sentence in sentences:
            self.assertEqual(sum(sentence in c for c in chunks), 1)

    def test_unbroken_oversize_block_has_bounded_fallback(self):
        chunks = split_slide_text("# Long\n\n" + "字" * 2000)
        self.assertTrue(all(len(c) <= 800 for c in chunks))
        self.assertEqual(sum(c.count("字") for c in chunks), 2000)


class PptIngestionTests(unittest.TestCase):
    file_name = "Lab.PPTX"

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

    def setUp(self):
        self.db = Mock()
        self.records = []

        def add(record):
            if isinstance(record, self.chunk.Parent):
                record.id = len(self.records) + 1
            self.records.append(record)

        self.db.add.side_effect = add
        self.embedding = Mock()
        self.embedding.encode.side_effect = lambda texts, **kwargs: np.zeros((len(texts), 1024))

    def test_exports_sorted_pages_and_never_mixes_parents(self):
        pages = {3: INTRO, 4: "\n<!-- image -->\n", 5: "# Basic Query\n\n- Launch XAMPP and MySQL.", 6: "# End"}
        document = Mock()
        document.pages = {6: None, 5: None, 4: None, 3: None}
        document.export_to_markdown.side_effect = lambda *, page_no: pages[page_no]
        converter = Mock()
        converter.convert.return_value = SimpleNamespace(document=document)
        with patch.object(self.chunk, "get_document_converter", return_value=converter), \
             patch.object(self.chunk, "get_embedding_model", return_value=self.embedding):
            self.chunk.chunking(b"fake", self.db, 99, self.file_name)
        self.assertEqual([c.kwargs["page_no"] for c in document.export_to_markdown.call_args_list], [3, 4, 5, 6])
        parents = [r for r in self.records if isinstance(r, self.chunk.Parent)]
        children = [r for r in self.records if isinstance(r, self.chunk.Child)]
        self.assertEqual(len(parents), 2)
        self.assertEqual(len(children), 2)
        self.assertIn("System R", parents[0].content)
        self.assertNotIn("XAMPP", parents[0].content)
        self.assertEqual([r.parent_id for r in children], [p.id for p in parents])
        self.assertEqual([r.chunk_index for r in parents], [0, 1])
        self.assertEqual([r.chunk_index for r in children], [0, 0])
        self.assertTrue(all(len(r.embedding) == 1024 for r in children))

    def test_title_and_image_only_document_fails_without_loading_embedding(self):
        document = Mock()
        document.pages = {1: None}
        document.export_to_markdown.return_value = "# Query\n\n<!-- image -->"
        converter = Mock()
        converter.convert.return_value = SimpleNamespace(document=document)
        with patch.object(self.chunk, "get_document_converter", return_value=converter), \
             patch.object(self.chunk, "get_embedding_model") as get_embedding:
            with self.assertRaisesRegex(ValueError, "没有可检索的正文"):
                self.chunk.chunking(b"fake", self.db, 99, self.file_name)
        get_embedding.assert_not_called()
        self.assertEqual(self.records, [])

    def test_missing_pages_fails_without_writing_records(self):
        document = Mock()
        document.pages = {}
        converter = Mock()
        converter.convert.return_value = SimpleNamespace(document=document)
        with patch.object(self.chunk, "get_document_converter", return_value=converter), \
             patch.object(self.chunk, "get_embedding_model") as get_embedding:
            with self.assertRaisesRegex(ValueError, "解析结果缺少页码"):
                self.chunk.chunking(b"fake", self.db, 99, self.file_name)
        document.export_to_markdown.assert_not_called()
        get_embedding.assert_not_called()
        self.assertEqual(self.records, [])

    def test_txt_keeps_existing_splitter(self):
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        text = "Evidence about databases. " * 60
        expected = RecursiveCharacterTextSplitter(chunk_size=450, chunk_overlap=50).split_text(text)
        with patch.object(self.chunk, "get_document_converter") as converter, \
             patch.object(self.chunk, "get_embedding_model", return_value=self.embedding):
            self.chunk.chunking(text.encode(), self.db, 99, "notes.txt")
        converter.assert_not_called()
        children = [r.content for r in self.records if isinstance(r, self.chunk.Child)]
        self.assertEqual(children, expected)


if __name__ == "__main__":
    unittest.main()

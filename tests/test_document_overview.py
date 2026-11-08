"""Offline regression for whole-document coverage and bounded summary context."""
import types
import unittest
from unittest.mock import Mock, patch

import test_document_scope as fixtures
from test_document_scope import SessionLocal, Project, Document, Parent, rag
from app.services.document_overview import plan_document_overview, read_document_overview, _mentions
from app.services import overview_context as context


class DocumentOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.DocumentScopeTests.setUpClass()
        with SessionLocal() as db:
            db.add(Project(id=4, user_id=1, name="overview"))
            for id, name, status in [(40, "Lab02 Basic SQL.pptx", "completed"),
                                     (41, "Lab04 Advanced.pptx", "completed"),
                                     (42, "Lab05 Pending.pptx", "processing"),
                                     (43, "Lab06 Copy.pptx", "completed"),
                                     (44, "Lab06 Copy.pptx", "completed")]:
                db.add(Document(id=id, project_id=4, filename=name, status=status,
                                content_type="test", storage_path="unused"))
            for index in reversed(range(14)):
                db.add(Parent(id=400 + index, document_id=40, chunk_index=index,
                              content="# Lab 2 Basic SQL" if index == 0 else f"# Topic {index}\nSQL evidence {index}"))
            db.commit()

    @classmethod
    def tearDownClass(cls):
        fixtures.DocumentScopeTests.tearDownClass()

    def setUp(self):
        fixtures.DocumentScopeTests.setUp(self)

    def ask(self, query, **body):
        return self.client.post("/projects/4/ask", json={"query": query, **body})

    def test_overview_reads_all_pages_in_order_without_retrieval(self):
        with patch.object(rag, "search_vector") as vector, \
             patch.object(rag, "rewrite_query") as rewrite, \
             patch.object(rag, "get_history_messages", return_value=([{"role": "user", "content": "previous"}], "", None)), \
             patch.object(rag, "rerank_results") as rerank, \
             patch.object(rag, "translate_query") as translate, \
             patch.object(rag, "generate_answer", return_value={"answer": "ok"}) as answer:
            response = self.ask("lab02主要讲了什么")
            self.assertEqual(response.status_code, 200)
            chunks = answer.call_args.args[1]
            self.assertEqual([c["parent_id"] for c in chunks], list(range(400, 414)))
            self.assertIn("SQL evidence 13", chunks[-1]["parent_content"])
            self.assertTrue(answer.call_args.kwargs["overview"])
            vector.assert_not_called()
            rerank.assert_not_called()
            translate.assert_not_called()
            rewrite.assert_not_called()

    def test_history_dependent_overview_is_rewritten_before_resolving(self):
        with patch.object(rag, "get_history_messages", return_value=([{"role": "user", "content": "Lab02"}], "", None)), \
             patch.object(rag, "rewrite_query", return_value="Lab02主要讲什么") as rewrite, \
             patch.object(rag, "generate_answer", return_value={"answer": "ok"}) as answer:
            response = self.ask("总结这份 PPT")
            self.assertEqual(response.status_code, 200)
            rewrite.assert_called_once()
            self.assertEqual(len(answer.call_args.args[1]), 14)

    def test_selected_scope_and_generic_summary(self):
        with patch.object(rag, "generate_answer", return_value={"answer": "ok"}) as answer:
            response = self.ask("总结这份 PPT", doc_ids=[40])
            self.assertEqual(response.status_code, 200)
            self.assertEqual({c["document_id"] for c in answer.call_args.args[1]}, {40})

    def test_ambiguous_missing_and_unready_targets_request_clarification(self):
        for query, body in [("总结这份 PPT", {}), ("lab06主要讲什么", {}),
                            ("lab05主要讲什么", {}), ("lab020主要讲什么", {}),
                            ("lab02主要讲什么", {"doc_ids": [41]})]:
            with self.subTest(query=query, body=body), \
                 patch.object(rag, "generate_answer") as answer, \
                 patch.object(rag, "search_vector") as vector:
                response = self.ask(query, **body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["sources"], [])
                answer.assert_not_called()
                vector.assert_not_called()

    def test_cross_project_scope_rejected_before_read(self):
        with patch.object(rag, "read_document_overview") as read:
            self.assertEqual(self.ask("总结这份 PPT", doc_ids=[21]).status_code, 404)
            read.assert_not_called()
        with SessionLocal() as db:
            self.assertEqual(read_document_overview(4, [21], db), [])

    def test_specific_questions_still_use_retrieval(self):
        for query in ["SQL 的 WHERE 怎么用", "总结 SQL 用法", "Lab02 第3页讲什么"]:
            with self.subTest(query=query), \
                 patch.object(rag, "search_vector", return_value=[]) as vector, \
                 patch.object(rag, "search_bm25", return_value=[]), \
                 patch.object(rag, "rerank_results", return_value=[]), \
                 patch.object(rag, "translate_query", return_value=None):
                self.assertEqual(self.ask(query).status_code, 200)
                vector.assert_called_once()

    def test_identifier_boundary_and_topic_summary(self):
        self.assertFalse(_mentions("Lab020主要讲什么", "Lab02.pptx"))
        self.assertTrue(_mentions("Summarize Basic SQL.pptx", "Basic SQL.pptx"))
        with SessionLocal() as db:
            for query in ["lab02主要讲了什么", "Lab 2 主要讲什么", "Summarize Lab02"]:
                self.assertEqual(plan_document_overview(query, 4, db)["document_ids"], [40])
            self.assertIsNone(plan_document_overview("总结 SQL", 4, db, [40]))
            self.assertEqual(plan_document_overview("有哪些知识点", 4, db, [40])["document_ids"], [40])
            self.assertEqual(plan_document_overview("总结这些文档", 4, db, [40, 41])["document_ids"], [40, 41])
            self.assertEqual(plan_document_overview("lab06主要讲什么", 4, db, [43])["document_ids"], [43])


class OverviewContextTests(unittest.TestCase):
    def chunks(self, text):
        return [{"parent_id": 1, "document_id": 40, "filename": "Lab02.pptx", "parent_content": text}]

    def test_short_documents_are_passed_through_without_model_calls(self):
        model = Mock()
        chunks = self.chunks("short")
        with patch.object(context, "_tokens", side_effect=len):
            self.assertIs(context.prepare_overview_context(chunks, model), chunks)
        model.bind.assert_not_called()

    def test_long_parent_summarizes_every_segment_and_preserves_source(self):
        model = Mock()
        model.bind.return_value.invoke.return_value = types.SimpleNamespace(
            content="brief evidence", response_metadata={})
        original = "A" * 13000 + "END_OF_DOCUMENT"
        chunks = self.chunks(original)
        with patch.object(context, "_tokens", side_effect=len):
            prepared = context.prepare_overview_context(chunks, model)
        inputs = [call.args[0][1].content for call in model.bind.return_value.invoke.call_args_list]
        self.assertTrue(any("END_OF_DOCUMENT" in text for text in inputs))
        self.assertEqual("".join(text for text in inputs if text.startswith("A")), original)
        self.assertEqual(prepared[0]["parent_id"], 1)
        self.assertEqual(prepared[0]["context_kind"], "overview_summary")
        self.assertEqual(chunks[0]["parent_content"], original)

    def test_truncated_summary_fails_instead_of_saving_partial_evidence(self):
        from fastapi import HTTPException
        model = Mock()
        model.bind.return_value.invoke.return_value = types.SimpleNamespace(
            content="partial", response_metadata={"finish_reason": "length"})
        with patch.object(context, "_tokens", side_effect=len), self.assertRaises(HTTPException):
            context.prepare_overview_context(self.chunks("x" * 13000), model)


if __name__ == "__main__":
    unittest.main()

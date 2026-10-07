"""Run: agent/Scripts/python.exe -B tests/test_selection_qa.py.

Real HTTP routes/services and temporary SQLite; models and token counts are mocked.
"""
from contextlib import ExitStack, contextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_context_management as common
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from app.models.documents import Document
from app.models.document_parents import Parent
from app.models.document_child import Child

answers, search, rag = common.answers, common.search, common.rag_flow


class SelectionTests(unittest.TestCase):
    def setUp(self):
        common.ContextTests.setUp(self)
        common.Base.metadata.create_all(self.engine, tables=[
            Document.__table__, Parent.__table__, Child.__table__,
        ])
        self.db.add(common.Project(id=2, user_id=1, name="another project"))
        for doc_id, project_id, status in [
            (11, 1, "completed"), (12, 1, "completed"),
            (13, 1, "processing"), (14, 1, "failed"), (21, 2, "completed"),
        ]:
            self.db.add(Document(id=doc_id, project_id=project_id, filename=f"{doc_id}.txt",
                                 status=status, content_type="text/plain", storage_path="unused"))
            self.db.add(Parent(id=doc_id, document_id=doc_id, content="gene evidence", chunk_index=0))
            self.db.add(Child(id=doc_id, parent_id=doc_id, content="gene evidence",
                              chunk_index=0, embedding=[0.0] * 1024))
        self.db.commit()
        app = FastAPI()
        app.include_router(rag.router, prefix="/api")
        app.dependency_overrides[rag.get_db] = lambda: self.db
        app.dependency_overrides[rag.get_currentUser] = lambda: SimpleNamespace(id=1)
        self.client = TestClient(app)
        self.selection = dict(document_id=11, filename="11.txt", selected_text="gene passage")

    def tearDown(self):
        self.client.close()
        common.ContextTests.tearDown(self)

    @contextmanager
    def models(self, chunks=None, finish="stop"):
        model = common.model("answer", finish)
        with ExitStack() as stack:
            for target, name, value in [
                (answers, "llm", model), (search, "llm", common.model("gene query")),
            ]:
                stack.enter_context(patch.object(target, name, value))
            for target, name, value in [
                (answers, "count_message_tokens", 100), (search, "count_message_tokens", 100),
                (rag, "search_document_first", chunks or []), (rag, "search_vector", []),
                (rag, "search_bm25", []), (rag, "rerank_results", chunks or []),
            ]:
                stack.enter_context(patch.object(target, name, return_value=value))
            yield model

    def ask(self, **overrides):
        payload = dict(query="Explain this", document_id=11, selected_text="gene passage")
        payload.update(overrides)
        return self.client.post("/api/projects/1/ask", json=payload)

    def test_selection_only_answer_history_and_followup(self):
        with self.models() as model:
            response = self.ask()
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result["selection"], self.selection)
            self.assertEqual(result["sources"], [])
            self.assertEqual(result["retrieved_contexts"], [])
            self.assertIn("gene passage", model.invoke.call_args.args[0][1].content)
            self.assertIn("[选区]", model.invoke.call_args.args[0][0].content)
            messages = self.db.scalars(select(common.Message).order_by(common.Message.id)).all()
            self.assertEqual(json.loads(messages[0].content), dict(query="Explain this", selection=self.selection))
            self.assertEqual(messages[0].turn_id, messages[1].turn_id)
            # The client resends the active selection for a follow-up in the same document.
            followup = self.ask(query="Give an example", conversation_id=result["conversation_id"])
            self.assertEqual(followup.status_code, 200, followup.text)
            self.assertIn("Explain this", model.invoke.call_args.args[0][1].content)
            changed = self.ask(document_id=12, selected_text="new passage", conversation_id=result["conversation_id"])
            self.assertEqual(changed.json()["selection"]["document_id"], 12)
            prompt = model.invoke.call_args.args[0][1].content
            self.assertIn("new passage", prompt.split("历史对话:")[0])

    def test_sources_and_document_labels(self):
        chunks = [dict(document_id=i, parent_id=i, filename=f"{i}.txt", parent_content=f"evidence {i}")
                  for i in (11, 12)]
        chunks[0].update(section_id=3, heading_path=["Chapter", "Section"],
                         page_numbers=[2, 3], child_page_numbers=[3])
        with self.models(chunks) as model:
            result = self.ask().json()
            self.assertEqual([s["index"] for s in result["sources"]], [1, 2])
            self.assertEqual(result["retrieved_contexts"], ["evidence 11", "evidence 12"])
            self.assertEqual(result["sources"][0]["section_id"], 3)
            self.assertEqual(result["sources"][0]["heading_path"], ["Chapter", "Section"])
            self.assertEqual(result["sources"][0]["page_numbers"], [2, 3])
            self.assertEqual(result["sources"][0]["child_page_numbers"], [3])
            self.assertIsNone(result["sources"][1]["page_numbers"])
            prompt = model.invoke.call_args.args[0][1].content
            for text in ("gene passage", "资料范围：当前文档", "资料范围：项目其他文档"):
                self.assertIn(text, prompt)

    def test_ordinary_empty_retrieval_is_unchanged(self):
        with self.models() as model:
            response = self.client.post("/api/projects/1/ask", json={"query": "ordinary"})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("selection", response.json())
            self.assertEqual(response.json()["answer"], "没有检索到任何有用资料")
            model.invoke.assert_not_called()

    def test_irrelevant_parents_do_not_reach_answer_model(self):
        candidates = [dict(child_id=11, document_id=11, parent_id=11, filename="11.txt",
                           child_content="unrelated", parent_content="unrelated context")]
        reranker = Mock()
        reranker.predict.return_value = [0.1]
        with self.models() as model, \
             patch.object(rag, "search_vector", return_value=candidates), \
             patch.object(rag, "rerank_results", wraps=search.rerank_results), \
             patch.object(search, "get_reranker", return_value=reranker):
            response = self.client.post("/api/projects/1/ask", json={
                "query": "An unsupported question", "doc_ids": [11],
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["retrieved_contexts"], [])
            self.assertEqual(response.json()["sources"], [])
            self.assertEqual(response.json()["answer"], "没有检索到任何有用资料")
            model.invoke.assert_not_called()
            reranker.predict.assert_called_once()

    def test_ordinary_qa_reranks_children_and_returns_full_parent(self):
        parent_text = "IBM Sequel Language was renamed as Structured Query Language."
        candidates = [dict(child_id=i, document_id=11, parent_id=11, filename="11.txt",
                           child_content=f"fragment {i}", parent_content=parent_text)
                      for i in (1, 2)]
        reranker = Mock()
        reranker.predict.return_value = [0.2, 0.8]
        with self.models() as model, \
             patch.object(rag, "search_vector", return_value=candidates), \
             patch.object(rag, "rerank_results", wraps=search.rerank_results), \
             patch.object(search, "get_reranker", return_value=reranker):
            response = self.client.post("/api/projects/1/ask", json={
                "query": "How was SQL renamed?", "doc_ids": [11],
            })
            self.assertEqual(response.status_code, 200, response.text)
            reranker.predict.assert_called_once()
            self.assertEqual(reranker.predict.call_args.args[0],
                             [["How was SQL renamed?", f"fragment {i}"] for i in (1, 2)])
            self.assertEqual(response.json()["retrieved_contexts"], [parent_text])
            self.assertEqual([source["parent_id"] for source in response.json()["sources"]], [11])
            self.assertIn(parent_text, model.invoke.call_args.args[0][1].content)

    def test_max_score_fallback_keeps_scope_and_reaches_answer(self):
        candidates = [dict(child_id=11, document_id=11, parent_id=11, filename="11.txt",
                           child_content="fragment", parent_content="SQL history evidence")]
        reranker = Mock()
        reranker.predict.side_effect = [[0.1], [0.8]]
        with self.models() as model, \
             patch.object(rag, "translate_query", return_value="SQL history"), \
             patch.object(rag, "search_vector", return_value=candidates) as vector, \
             patch.object(rag, "rerank_results", wraps=search.rerank_results), \
             patch.object(search, "get_reranker", return_value=reranker):
            response = self.client.post("/api/projects/1/ask", json={
                "query": "SQL 的历史", "doc_ids": [11],
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["retrieved_contexts"], ["SQL history evidence"])
            self.assertEqual([source["document_id"] for source in response.json()["sources"]], [11])
            self.assertTrue(all(call.kwargs["doc_ids"] == [11] for call in vector.call_args_list))
            self.assertEqual(reranker.predict.call_count, 2)
            model.invoke.assert_called_once()

    def test_invalid_selection_and_permissions_stop_before_models(self):
        with self.models() as model:
            for fields, status in [
                ({"selected_text": None}, 422), ({"document_id": None}, 422),
                ({"selected_text": "   "}, 422), ({"selected_text": "x" * 10001}, 422),
                ({"document_id": 0}, 422), ({"document_id": 999}, 404),
                ({"document_id": 21}, 404), ({"document_id": 13}, 409),
                ({"document_id": 14}, 409),
            ]:
                with self.subTest(fields=str(fields)[:80]):
                    self.assertEqual(self.ask(**fields).status_code, status)
            owner = self.db.get(common.Project, 1)
            owner.user_id = 999
            self.db.commit()
            self.assertEqual(self.ask().status_code, 403)
            model.invoke.assert_not_called()

    def test_failed_generation_does_not_save_selection_turn(self):
        with self.models(finish="length"):
            self.assertEqual(self.ask().status_code, 502)
        self.assertEqual(list(self.db.scalars(select(common.Message))), [])

    def test_compression_preserves_current_selection_and_counts_it(self):
        common.ContextTests.add_turns(self, 1, 6)
        history, summary, boundary = answers.get_history_messages(1, self.db, 1)
        model = common.model()
        with patch.object(answers, "llm", model), \
             patch.object(answers, "count_message_tokens", side_effect=[24000, 100]) as counter, \
             patch.object(answers, "context_compress", return_value="older summary"):
            answers.generate_answer("Explain", [], history, 1, self.db, 1, selection=self.selection)
        for call in counter.call_args_list:
            self.assertIn("gene passage", call.args[0][1]["content"])
        prompt = model.invoke.call_args.args[0][1].content
        self.assertIn("gene passage", prompt)
        self.assertIn("older summary", prompt)
        with patch.object(answers, "llm", model), patch.object(answers, "count_message_tokens", return_value=24000):
            with self.assertRaises(common.HTTPException) as failure:
                answers.generate_answer("Explain", [], [], None, self.db, 1, selection=self.selection)
            self.assertEqual(failure.exception.status_code, 400)

    def test_bm25_document_scope_and_project_isolation(self):
        for filters, expected in [({}, {11, 12}), ({"document_id": 11}, {11}),
                                  ({"exclude_document_id": 11}, {12}),
                                  ({"document_id": 21}, set()), ({"document_id": 13}, set())]:
            with self.subTest(filters=filters):
                result = search.search_bm25("gene", 1, self.db, 1, **filters)
                self.assertEqual({r["document_id"] for r in result}, expected)

    def test_vector_scope_sql(self):
        for filters, clause in [({}, None), ({"document_id": 11}, "documents.id = 11"),
                                ({"exclude_document_id": 11}, "documents.id != 11")]:
            db = Mock()
            db.scalar.side_effect = self.db.scalar
            db.execute.return_value.all.return_value = []
            with patch.object(search, "embedding_query", return_value=[0.0] * 1024):
                search.search_vector("gene", 1, db, 0.4, **filters)
            sql = str(db.execute.call_args.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            self.assertIn("documents.project_id = 1", sql)
            self.assertIn("documents.status = 'completed'", sql)
            if clause:
                self.assertIn(clause, sql)

    def test_priority_and_fallback_all_counts(self):
        for local_count in range(6):
            for other_count in range(6):
                local = [dict(parent_id=i) for i in range(local_count)]
                others = [dict(parent_id=100+i) for i in range(other_count)]
                with patch.object(search, "search_vector", return_value=[]) as vector, \
                     patch.object(search, "search_bm25", return_value=[]) as bm25, \
                     patch.object(search, "rerank_results", side_effect=[local, others]):
                    result = search.search_document_first("gene", 1, 11, self.db, 0.4)
                supplement_count = min(other_count, 5-min(local_count, 3))
                self.assertEqual(result, local[:5-supplement_count] + others[:supplement_count])
                self.assertEqual(len(result), min(5, local_count+other_count))
                for mock in (vector, bm25):
                    self.assertEqual(mock.call_args_list[0].kwargs, {"doc_ids": None, "document_id": 11})
                    self.assertEqual(mock.call_args_list[1].kwargs, {"doc_ids": None, "exclude_document_id": 11})

    def test_explicit_scope_selection_and_translation_share_boundaries(self):
        with self.models(), patch.object(rag, "search_document_first", search.search_document_first), \
             patch.object(rag, "translate_query", return_value="translated selection"), \
             patch.object(search, "search_vector", return_value=[]) as vector, \
             patch.object(search, "search_bm25", return_value=[]) as bm25:
            response = self.ask(doc_ids=[11, 12, 11], candidate_k_percentage=0.25)
            self.assertEqual(response.status_code, 200, response.text)
            for retrieve in (vector, bm25):
                self.assertEqual(retrieve.call_count, 4)
                for index, call in enumerate(retrieve.call_args_list):
                    self.assertEqual(call.args[3], 0.25)
                    self.assertEqual(call.kwargs["doc_ids"], [11, 12])
                    scope = "document_id" if index < 2 else "exclude_document_id"
                    self.assertEqual(call.kwargs[scope], 11)
                    if index % 2:
                        self.assertEqual(call.args[0], "translated selection")
                    else:
                        self.assertIn("gene passage", call.args[0])

    def test_invalid_explicit_scope_stops_before_translation(self):
        for ids, status in [([], 422), ([12], 422), ([11, 21], 404), ([11, 13], 409)]:
            with self.subTest(ids=ids), patch.object(rag, "translate_query") as translate:
                self.assertEqual(self.ask(doc_ids=ids).status_code, status)
                translate.assert_not_called()

    def test_percentage_denominator_applies_all_scope_filters(self):
        for filters, expected in [
            ({"document_id": 11}, 1), ({"exclude_document_id": 11}, 1),
            ({"doc_ids": [11], "exclude_document_id": 11}, 0),
            ({"doc_ids": [12], "document_id": 11}, 0),
        ]:
            with self.subTest(filters=filters), patch.object(search, "embedding_query", return_value=[0.0] * 1024) as embed:
                db = Mock()
                db.scalar.side_effect = self.db.scalar
                db.execute.return_value.all.return_value = []
                search.search_vector("gene", 1, db, 0.4, **filters)
                self.assertEqual(len(search.search_bm25("gene", 1, self.db, 0.4, **filters)), expected)
                if expected:
                    compiled = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
                    self.assertEqual(compiled.params["param_1"], expected)
                else:
                    db.execute.assert_not_called()
                    embed.assert_not_called()

    def test_rewritten_query_is_translated_and_original_question_is_saved(self):
        cases = [("{\"translation\": \"translated standalone\"}", False, 2),
                 ("not JSON", False, 1), ("", True, 1)]
        for content, fails, calls in cases:
            with self.subTest(content=content), self.models(), \
                 patch.object(rag, "rewrite_query", return_value="standalone question"), \
                 patch.object(rag.translator, "invoke", return_value=SimpleNamespace(content=content),
                              side_effect=TimeoutError("test") if fails else None) as translate, \
                 patch.object(rag, "search_vector", return_value=[]) as vector, \
                 patch.object(rag, "rerank_results", return_value=[]) as rerank:
                response = self.client.post("/api/projects/1/ask", json={
                    "query": "what about it?", "conversation_id": 1, "doc_ids": [11],
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("standalone question", translate.call_args.args[0][1].content)
                self.assertEqual(vector.call_count, calls)
                self.assertEqual(vector.call_args_list[0].args[0], "standalone question")
                self.assertEqual(rerank.call_args.args[:2],
                                 ("standalone question", "translated standalone" if calls == 2 else None))
        messages = self.db.scalars(select(common.Message).where(common.Message.role == "user")).all()
        self.assertTrue(all(message.content == "what about it?" for message in messages))


if __name__ == "__main__":
    unittest.main(verbosity=2)

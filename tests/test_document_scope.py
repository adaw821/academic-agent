"""Run directly: agent/Scripts/python.exe tests/test_document_scope.py.

Uses a temporary SQLite database and fake model services; no API calls or downloads.
"""
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(TEMP.name) / "scope.db").as_posix()

chunk_stub = types.ModuleType("app.services.chunk")
chunk_stub.chunking = Mock()
chunk_stub.get_embedding_model = Mock(side_effect=AssertionError("No model calls"))
answer_stub = types.ModuleType("app.services.answer_services")
answer_stub.generate_answer = Mock(return_value={"answer": "test", "sources": []})
answer_stub.get_history_messages = Mock(return_value=([], "", None))
answer_stub._owns_conversation = lambda conversation, project_id, user_id: (
    conversation.project_id == project_id if project_id is not None
    else conversation.project_id is None and conversation.user_id == user_id
)
answer_stub.llm = Mock()
answer_stub.CONTEXT_LIMIT = 32_768
answer_stub.RESERVED_OUTPUT_TOKENS = 8_192
answer_stub.SAFETY_MARGIN = 1_024
translator_stub = types.ModuleType("langchain_openai")
translator_stub.ChatOpenAI = Mock()
translator_stub.ChatOpenAI.return_value.invoke.return_value = types.SimpleNamespace(
    content='{"translation": "gene"}'
)
reranker_stub = types.ModuleType("sentence_transformers")
reranker_stub.CrossEncoder = Mock(side_effect=AssertionError("No reranker calls"))
torch_nn_stub = types.ModuleType("torch.nn")
torch_nn_stub.Sigmoid = Mock()
transformers_stub = types.ModuleType("transformers")
transformers_stub.AutoTokenizer = Mock(side_effect=AssertionError("No tokenizer downloads"))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.db.database import Base, SessionLocal, engine
from app.models.users import User
from app.models.projects import Project
from app.models.documents import Document
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.document_parents import Parent
from app.models.document_child import Child

with patch.dict(sys.modules, {
    "app.services.chunk": chunk_stub,
    "app.services.answer_services": answer_stub,
    "langchain_openai": translator_stub,
    "sentence_transformers": reranker_stub,
    "torch.nn": torch_nn_stub,
    "transformers": transformers_stub,
}), patch.dict(os.environ, {"ZHIPU_API_KEY": "test-only"}):
    from app.routers import rag_flow as rag
    from app.services import search_services as search


class DocumentScopeTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        TEMP.cleanup()

    @classmethod
    def setUpClass(cls):
        tables = [User.__table__, Project.__table__, Document.__table__,
                  Parent.__table__, Child.__table__]
        Base.metadata.create_all(engine, tables=tables)
        with SessionLocal() as db:
            db.add_all([
                User(id=1, username="owner", email="owner@example.com", password_hash="unused"),
                User(id=2, username="other", email="other@example.com", password_hash="unused"),
                Project(id=1, user_id=1, name="current"),
                Project(id=2, user_id=2, name="private"),
                Project(id=3, user_id=1, name="another owned project"),
            ])
            for doc_id, project_id, status in [
                (11, 1, "completed"), (12, 1, "completed"),
                (13, 1, "processing"), (14, 1, "failed"),
                (21, 2, "completed"), (31, 3, "completed"),
            ]:
                db.add(Document(id=doc_id, project_id=project_id, filename=f"{doc_id}.txt",
                                status=status, content_type="text/plain", storage_path="unused"))
                db.add(Parent(id=doc_id, document_id=doc_id, content="gene evidence", chunk_index=0))
                db.add(Child(id=doc_id, parent_id=doc_id, content="gene evidence",
                             chunk_index=0, embedding=[0.0] * 1024))
            # doc 11 有三个子片，项目 1 内已完成文档共有四个子片。
            for child_id in [111, 112]:
                db.add(Child(id=child_id, parent_id=11, content="gene evidence",
                             chunk_index=child_id, embedding=[0.0] * 1024))
            db.commit()

    def setUp(self):
        self.enterContext(patch.object(rag, "history_messages_control", return_value=1))
        app = FastAPI()
        app.include_router(rag.router)
        app.dependency_overrides[rag.get_currentUser] = lambda: types.SimpleNamespace(id=1)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def ask(self, body, project_id=1):
        return self.client.post(f"/projects/{project_id}/ask", json={"query": "gene", **body})

    def test_scope_forwarded_to_both_retrievers_and_deduplicated(self):
        for body, expected in [({}, None), ({"doc_ids": None}, None),
                               ({"doc_ids": [12, 11, 12]}, [12, 11])]:
            with self.subTest(body=body), \
                 patch.object(rag, "search_vector", return_value=[]) as vector, \
                 patch.object(rag, "search_bm25", return_value=[]) as bm25:
                self.assertEqual(self.ask(body).status_code, 200)
                for retrieve in (vector, bm25):
                    self.assertEqual(retrieve.call_args.kwargs["doc_ids"], expected)
                    self.assertEqual(retrieve.call_args.args[1], 1)

    def test_invalid_or_unready_documents_fail_before_retrieval(self):
        for ids, status in [([999], 404), ([21], 404), ([31], 404),
                            ([11, 999], 404), ([11, 21], 404),
                            ([13], 409), ([11, 14], 409)]:
            with self.subTest(ids=ids), \
                 patch.object(rag, "search_vector") as vector, \
                 patch.object(rag, "search_bm25") as bm25:
                self.assertEqual(self.ask({"doc_ids": ids}).status_code, status)
                vector.assert_not_called()
                bm25.assert_not_called()

    def test_invalid_ids_rejected_by_request_validation(self):
        for ids in [[], [0], [-1], [True], ["11"], [1.5], "11"]:
            with self.subTest(ids=ids), patch.object(rag, "search_vector") as vector:
                self.assertEqual(self.ask({"doc_ids": ids}).status_code, 422)
                vector.assert_not_called()

    def test_project_permissions_checked_before_document_scope(self):
        for project_id, status in [(2, 403), (999, 404)]:
            with self.subTest(project_id=project_id), \
                 patch.object(rag, "require_document_scope") as scope:
                self.assertEqual(self.ask({"doc_ids": [21]}, project_id).status_code, status)
                scope.assert_not_called()

    def test_no_filename_inference(self):
        with patch.object(rag, "search_vector", return_value=[]) as vector, \
             patch.object(rag, "search_bm25", return_value=[]):
            response = self.ask({"query": "只根据《不存在的文件》回答"})
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(vector.call_args.kwargs["doc_ids"])

    def assert_translation_falls_back(self, content=None, error=None):
        candidate = {
            "child_id": 11, "parent_id": 11, "document_id": 11,
            "filename": "11.txt", "child_content": "gene evidence",
            "parent_content": "gene evidence",
        }
        model = Mock()
        model.predict.return_value = [0.8]
        with patch.object(rag.translator, "invoke", side_effect=error,
                          return_value=types.SimpleNamespace(content=content)), \
             patch.object(rag, "search_vector", return_value=[candidate]) as vector, \
             patch.object(rag, "search_bm25", return_value=[]) as bm25, \
             patch.object(search, "get_reranker", return_value=model), \
             patch.object(rag, "generate_answer", return_value={"answer": "ok"}) as answer:
            response = self.ask({"doc_ids": [11]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["answer"], "ok")
            for retrieve in (vector, bm25):
                retrieve.assert_called_once()
                self.assertEqual(retrieve.call_args.args[0], "gene")
                self.assertEqual(retrieve.call_args.kwargs["doc_ids"], [11])
            model.predict.assert_called_once()
            final_chunks = answer.call_args.args[1]
            self.assertEqual(len(final_chunks), 1)
            self.assertEqual(final_chunks[0]["total_score"], 0.8)
            self.assertEqual(final_chunks[0]["score_mode"], "original")

    def test_translation_call_failure_keeps_original_retrieval(self):
        for error in [TimeoutError("simulated timeout"), RuntimeError("simulated API failure")]:
            with self.subTest(error=type(error).__name__):
                self.assert_translation_falls_back(error=error)

    def test_invalid_translation_response_keeps_original_retrieval(self):
        for content in [
            "not JSON", "null", "[]", "{}", '{"translation": null}',
            '{"translation": ""}', '{"translation": "  "}',
            '{"translation": "None"}', '{"translation": "null"}',
            '{"translation": 42}', '{"translation": false}',
            '{"translation": []}', '{"translation": {}}',
            [{"type": "text", "text": "unexpected content blocks"}],
        ]:
            with self.subTest(content=content):
                self.assert_translation_falls_back(content=content)

    def test_valid_translation_retrieves_original_and_translated_queries(self):
        with patch.object(rag.translator, "invoke", return_value=types.SimpleNamespace(
                 content='{"translation": "  translated gene  "}')) as translate, \
             patch.object(rag, "search_vector", return_value=[]) as vector, \
             patch.object(rag, "search_bm25", return_value=[]) as bm25, \
             patch.object(rag, "rerank_results", return_value=[]) as rerank:
            self.assertEqual(self.ask({"doc_ids": [11]}).status_code, 200)
            for retrieve in (vector, bm25):
                self.assertEqual([call.args[0] for call in retrieve.call_args_list],
                                 ["gene", "translated gene"])
                self.assertTrue(all(call.kwargs["doc_ids"] == [11]
                                    for call in retrieve.call_args_list))
            rerank.assert_called_once_with("gene", "translated gene", [], [])
            self.assertIn("JSON 的 null", translate.call_args.args[0][0].content)

    def test_invalid_scope_skips_translation(self):
        for project_id, body, status in [(2, {}, 403), (1, {"doc_ids": [999]}, 404)]:
            with self.subTest(project_id=project_id), patch.object(rag.translator, "invoke") as translate:
                self.assertEqual(self.ask(body, project_id).status_code, status)
                translate.assert_not_called()

    def test_bm25_queries_only_selected_completed_documents(self):
        for ids, expected in [(None, {11, 12}), ([11], {11}),
                              ([11, 12], {11, 12}), ([21], set()),
                              ([13], set()), ([], set())]:
            with self.subTest(ids=ids), SessionLocal() as db:
                rows = search.search_bm25("gene", 1, db, 1, doc_ids=ids)
                self.assertEqual({row["document_id"] for row in rows}, expected)

    def test_bm25_percentage_and_rounding_use_scoped_total(self):
        for ids, percentage, expected in [
            (None, 0.25, 1), (None, 0.5, 2), (None, 1, 4),
            ([11], 0.5, 2), ([11], 0.01, 1), ([12], 0.01, 1),
        ]:
            with self.subTest(ids=ids, percentage=percentage), SessionLocal() as db:
                rows = search.search_bm25("gene", 1, db, percentage, doc_ids=ids)
                self.assertEqual(len(rows), expected)
        with SessionLocal() as db:
            self.assertEqual(search.search_bm25("unmatched", 1, db, 1), [])

    def test_percentage_contract_keeps_current_name_default_and_bounds(self):
        for body, expected in [({}, 0.4), ({"candidate_k_percentage": 0.01}, 0.01),
                               ({"candidate_k_percentage": 1}, 1)]:
            with self.subTest(body=body), \
                 patch.object(rag, "search_vector", return_value=[]) as vector, \
                 patch.object(rag, "search_bm25", return_value=[]) as bm25:
                self.assertEqual(self.ask(body).status_code, 200)
                for retrieve in (vector, bm25):
                    self.assertEqual(retrieve.call_args.args[3], expected)
        for value in [0, 1.01]:
            with self.subTest(value=value), patch.object(rag, "search_vector") as vector:
                self.assertEqual(self.ask({"candidate_k_percentage": value}).status_code, 422)
                vector.assert_not_called()

    def test_vector_query_limits_scope_in_sql_before_top_k(self):
        # SQLite cannot execute pgvector's cosine operator; inspect actual PostgreSQL SQL.
        for ids, percentage, expected in [
            (None, 0.25, 1), (None, 0.5, 2), (None, 1, 4),
            ([11], 0.5, 2), ([11], 0.01, 1), ([12], 0.01, 1),
        ]:
            with self.subTest(ids=ids, percentage=percentage), SessionLocal() as real_db, \
                 patch.object(search, "embedding_query", return_value=[0.0] * 1024):
                # Execute real scoped count on SQLite, only mock the vector SELECT.
                db = Mock()
                db.scalar.side_effect = real_db.scalar
                db.execute.return_value.all.return_value = []
                search.search_vector("gene", 1, db, percentage, doc_ids=ids)
                stmt = db.execute.call_args.args[0]
                compiled = stmt.compile(dialect=postgresql.dialect())
                sql = str(compiled)
                self.assertEqual(compiled.params["param_1"], expected)
                self.assertIn("documents.project_id =", sql)
                self.assertIn("documents.status =", sql)
                self.assertIn("completed", compiled.params.values())
                self.assertLess(sql.index("WHERE"), sql.index("LIMIT"))
                if ids is None:
                    self.assertNotIn("documents.id IN", sql)
                else:
                    self.assertIn("documents.id IN", sql)
                    self.assertIn(ids, compiled.params.values())
                    self.assertLess(sql.index("documents.id IN"), sql.index("LIMIT"))

    def test_empty_vector_scope_skips_embedding_and_retrieval(self):
        for ids in [[], [21], [13], [999]]:
            with self.subTest(ids=ids), SessionLocal() as real_db, \
                 patch.object(search, "embedding_query") as embedding:
                db = Mock()
                db.scalar.side_effect = real_db.scalar
                self.assertEqual(search.search_vector("gene", 1, db, 0.5, doc_ids=ids), [])
                embedding.assert_not_called()
                db.execute.assert_not_called()


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        engine.dispose()
        TEMP.cleanup()

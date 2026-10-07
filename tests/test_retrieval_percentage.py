"""Isolated service checks: agent/Scripts/python.exe tests/test_retrieval_percentage.py."""
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
import copy
import numpy as np
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(TEMP.name) / "retrieval.db").as_posix()

from app.db.database import Base, SessionLocal, engine
from app.models.users import User
from app.models.projects import Project
from app.models.documents import Document
from app.models.courses import Course
from app.models.course_timetable import CourseSchedule
from app.models.document_parents import Parent
from app.models.document_child import Child
from app.schemas.search import SearchRequest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

chunk_stub = types.ModuleType("app.services.chunk")
chunk_stub.get_embedding_model = Mock(side_effect=AssertionError("No model downloads"))
reranker_stub = types.ModuleType("sentence_transformers")
reranker_stub.CrossEncoder = Mock(side_effect=AssertionError("No reranker calls"))
torch_nn_stub = types.ModuleType("torch.nn")
torch_nn_stub.Sigmoid = Mock()
transformers_stub = types.ModuleType("transformers")
transformers_stub.AutoTokenizer = Mock(side_effect=AssertionError("No tokenizer downloads"))
with patch.dict(sys.modules, {
    "app.services.chunk": chunk_stub,
    "sentence_transformers": reranker_stub,
    "torch.nn": torch_nn_stub,
    "transformers": transformers_stub,
}), patch.dict(os.environ, {"ZHIPU_API_KEY": "unused-test-key"}):
    from app.services import search_services as search


class PercentageRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(engine, tables=[User.__table__, Project.__table__,
            Document.__table__, Parent.__table__, Child.__table__])
        with SessionLocal() as db:
            db.add(User(id=1, username="test", email="test@example.com", password_hash="unused"))
            db.add_all([Project(id=i, user_id=1, name=str(i)) for i in [1, 2]])
            for doc_id, project_id, status, texts in [
                (11, 1, "completed", ["gene evidence", "gene evidence", "other", ""]),
                (12, 1, "completed", ["gene evidence"]),
                (13, 1, "processing", ["gene evidence"] * 10),
                (21, 2, "completed", ["gene evidence"] * 10),
            ]:
                db.add(Document(id=doc_id, project_id=project_id, status=status,
                    filename=f"{doc_id}.txt", content_type="text/plain", storage_path="unused"))
                db.add(Parent(id=doc_id, document_id=doc_id, content="evidence", chunk_index=0))
                for index, content in enumerate(texts):
                    db.add(Child(parent_id=doc_id, content=content, chunk_index=index,
                                 embedding=[0.0] * 1024))
            db.commit()

    def test_vector_percentage_counts_only_current_scope(self):
        for ids, percentage, expected in [
            (None, 0.2, 1), (None, 0.5, 3), (None, 1, 5),
            ([11], 0.5, 2), ([11], 1, 4), ([12], 0.01, 1),
        ]:
            with self.subTest(ids=ids, percentage=percentage), SessionLocal() as session, \
                 patch.object(search, "embedding_query", return_value=[0.0] * 1024):
                db = Mock()
                db.scalar.side_effect = session.scalar
                db.execute.return_value.all.return_value = []
                search.search_vector("gene", 1, db, percentage, doc_ids=ids)
                compiled = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
                self.assertEqual(compiled.params["param_1"], expected)
                self.assertIn("documents.project_id =", str(compiled))
                self.assertIn("documents.status =", str(compiled))
                if ids is not None:
                    self.assertIn(ids, compiled.params.values())
                    self.assertLess(str(compiled).index("documents.id IN"), str(compiled).index("LIMIT"))

    def test_bm25_uses_total_before_token_or_match_filtering(self):
        # doc 11 has four children, but only two match. 50% must return both.
        for ids, percentage, expected in [
            (None, 0.2, 1), (None, 0.5, 3), (None, 1, 3),
            ([11], 0.5, 2), ([12], 0.01, 1),
        ]:
            with self.subTest(ids=ids, percentage=percentage), SessionLocal() as db:
                rows = search.search_bm25("gene", 1, db, percentage, doc_ids=ids)
                self.assertEqual(len(rows), expected)
                self.assertTrue(all(row["document_id"] in (ids or [11, 12]) for row in rows))
        with SessionLocal() as db:
            self.assertEqual(search.search_bm25("unmatched", 1, db, 1), [])

    def test_empty_scope_returns_empty_without_embedding(self):
        for ids in [[], [13], [21], [999]]:
            with self.subTest(ids=ids), SessionLocal() as db, \
                 patch.object(search, "embedding_query") as embedding:
                self.assertEqual(search.search_vector("gene", 1, db, 1, doc_ids=ids), [])
                embedding.assert_not_called()
                self.assertEqual(search.search_bm25("gene", 1, db, 1, doc_ids=ids), [])

    def test_section_and_page_metadata_survive_both_retrieval_paths(self):
        with SessionLocal() as session:
            parent = session.get(Parent, 11)
            parent.section_id = 3
            parent.heading_path = ["Chapter", "Section"]
            parent.page_numbers = [2, 3]
            child = parent.childs[0]
            child.page_numbers = [3]
            session.flush()
            keyword_rows = search.search_bm25("gene", 1, session, 1, doc_ids=[11])
            keyword_row = next(row for row in keyword_rows if row["child_id"] == child.id)
            db = Mock()
            db.scalar.return_value = 1
            db.execute.return_value.all.return_value = [(child, parent, session.get(Document, 11), 0.1)]
            with patch.object(search, "embedding_query", return_value=[0.0] * 1024):
                vector_row = search.search_vector("gene", 1, db, 1)[0]
            for row in (keyword_row, vector_row):
                self.assertEqual(row["section_id"], 3)
                self.assertEqual(row["heading_path"], ["Chapter", "Section"])
                self.assertEqual(row["page_numbers"], [2, 3])
                self.assertEqual(row["child_page_numbers"], [3])
                self.assertEqual(row["parent_content"], parent.content)

    def test_request_keeps_existing_name_default_and_bounds(self):
        self.assertEqual(SearchRequest(query="gene").candidate_k_percentage, 0.4)
        for value in [0.01, 0.2, 1]:
            self.assertEqual(SearchRequest(query="gene", candidate_k_percentage=value).candidate_k_percentage, value)
        for value in [0, -0.1, 1.01, float("nan"), float("inf")]:
            with self.assertRaises(ValidationError):
                SearchRequest(query="gene", candidate_k_percentage=value)

    def test_rounding_does_not_add_one_due_to_float_error(self):
        self.assertEqual(search.percentage_candidate_count(100, 0.07), 7)
        self.assertEqual(search.percentage_candidate_count(3, 0.4), 2)


class ChildRerankTests(unittest.TestCase):
    @staticmethod
    def child(child_id, parent_id):
        return {
            "child_id": child_id, "parent_id": parent_id,
            "child_content": f"short evidence {child_id}",
            "parent_content": f"complete context {parent_id}",
            "document_id": parent_id, "filename": f"{parent_id}.txt",
        }

    def test_scores_unique_children_and_returns_best_child_with_full_parent(self):
        candidates = [self.child(1, 10), self.child(2, 10), self.child(3, 20), self.child(2, 10)]
        before = copy.deepcopy(candidates)
        model = Mock()
        model.predict.side_effect = [np.array([0.2, 0.9, 0.8]), np.array([0.2, 0.8, 0.8])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated", candidates)
        self.assertEqual(model.predict.call_args_list[0].args[0],
                         [["original", f"short evidence {i}"] for i in [1, 2, 3]])
        self.assertEqual(model.predict.call_args_list[1].args[0],
                         [["translated", f"short evidence {i}"] for i in [1, 2, 3]])
        self.assertEqual([row["parent_id"] for row in rows], [10, 20])
        self.assertEqual(rows[0]["child_id"], 2)  # Later, stronger child represents its parent.
        self.assertAlmostEqual(rows[0]["total_score"], 0.87)
        self.assertEqual(rows[0]["original_rerank_score"], 0.9)
        self.assertEqual(rows[0]["translated_rerank_score"], 0.8)
        self.assertEqual(rows[0]["parent_content"], "complete context 10")
        self.assertEqual(candidates, before)

    def test_language_scores_are_weighted_per_child_before_parent_selection(self):
        model = Mock()
        model.predict.side_effect = [np.array([1.0, 0.2, 0.6]), np.array([0.2, 1.0, 0.9])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated",
                [self.child(1, 10), self.child(2, 10), self.child(3, 20)])
        self.assertEqual([row["parent_id"] for row in rows], [20, 10])
        self.assertAlmostEqual(rows[0]["total_score"], 0.81)
        self.assertAlmostEqual(rows[1]["total_score"], 0.76)

    def test_weight_follows_higher_score_regardless_of_language(self):
        model = Mock()
        model.predict.side_effect = [np.array([0.9, 0.2]), np.array([0.2, 0.9])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated",
                [self.child(1, 10), self.child(2, 20)])
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertAlmostEqual(row["total_score"], 0.69)
            self.assertEqual(row["score_mode"], "weighted")

    def test_weighted_filter_requires_strictly_more_than_point_six(self):
        model = Mock()
        model.predict.side_effect = [np.array([0.6, 0.600001]), np.array([0.6, 0.600001])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated",
                [self.child(1, 10), self.child(2, 20)])
        self.assertEqual([row["parent_id"] for row in rows], [20])
        self.assertEqual(rows[0]["score_mode"], "weighted")

    def test_missing_translation_preserves_original_score(self):
        for translated_query in [None, ""]:
            with self.subTest(translated_query=translated_query):
                model = Mock()
                model.predict.return_value = np.array([0.8, 0.7])
                with patch.object(search, "get_reranker", return_value=model):
                    rows = search.rerank_results("original", translated_query,
                                                  [self.child(1, 10), self.child(2, 10)])
                model.predict.assert_called_once()
                self.assertEqual(rows[0]["child_id"], 1)
                self.assertEqual(rows[0]["total_score"], 0.8)
                self.assertIsNone(rows[0]["translated_rerank_score"])

    def test_zero_translation_score_participates_in_weighting(self):
        model = Mock()
        model.predict.side_effect = [np.array([0.9]), np.array([0.0])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated", [self.child(1, 10)])
        # Zero is a valid score; weighted 0.63 passes without max fallback.
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mean_score"], 0.45)
        self.assertEqual(rows[0]["max_score"], 0.9)
        self.assertAlmostEqual(rows[0]["total_score"], 0.63)
        self.assertEqual(rows[0]["score_mode"], "weighted")
        self.assertEqual(model.predict.call_count, 2)

    def test_max_fallback_requires_strictly_more_than_point_six(self):
        model = Mock()
        model.predict.side_effect = [np.array([0.6, 0.0, 0.600001]),
                                     np.array([0.0, 0.6, 0.0])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated",
                [self.child(i, i) for i in range(1, 4)])
        self.assertEqual([row["parent_id"] for row in rows], [3])
        self.assertEqual(model.predict.call_count, 2)

    def test_any_weighted_pass_prevents_max_fallback(self):
        model = Mock()
        model.predict.side_effect = [np.array([0.7, 0.8]), np.array([0.7, 0.0])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated",
                [self.child(1, 10), self.child(2, 20)])
        self.assertEqual([row["parent_id"] for row in rows], [10])
        self.assertEqual(rows[0]["score_mode"], "weighted")
        self.assertAlmostEqual(rows[0]["total_score"], 0.7)

    def test_max_fallback_sorts_by_max_and_limits_to_five_parents(self):
        candidates = [self.child(i, i) for i in range(1, 8)]
        candidates.append(self.child(8, 1))
        before = copy.deepcopy(candidates)
        model = Mock()
        model.predict.side_effect = [np.array([0.75, 0.6, 0.61, 0.82, 0.7, 0.84, 0.65, 0.8]),
                                     np.array([0.2, 0.0, 0.0, 0.0, 0.3, 0.0, 0.0, 0.0])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated", candidates)
        self.assertEqual([row["parent_id"] for row in rows], [6, 4, 1, 5, 7])
        # Child 1 wins on weighted score; child 8 replaces it when fallback uses max.
        self.assertEqual(rows[2]["child_id"], 8)
        self.assertTrue(all(row["weighted_score"] <= 0.6 for row in rows))
        self.assertTrue(all(row["total_score"] > 0.6 for row in rows))
        self.assertTrue(all(row["score_mode"] == "max_fallback" for row in rows))
        self.assertEqual(model.predict.call_count, 2)
        self.assertEqual(candidates, before)

    def test_max_fallback_returns_empty_if_both_scores_are_low(self):
        model = Mock()
        model.predict.side_effect = [np.array([0.3, 0.6]), np.array([0.2, 0.0])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", "translated",
                [self.child(1, 10), self.child(2, 20)])
        self.assertEqual(rows, [])
        self.assertEqual(model.predict.call_count, 2)

    def test_all_low_scores_return_empty_without_rescoring(self):
        candidates = [self.child(1, 10), self.child(2, 10), self.child(3, 20),
                      self.child(4, 30), self.child(5, 40)]
        before = copy.deepcopy(candidates)
        model = Mock()
        model.predict.side_effect = [np.array([0.4, 0.3, 0.3, 0.5, 0.2])]
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", None, candidates)
        self.assertEqual(rows, [])
        model.predict.assert_called_once()
        self.assertEqual(candidates, before)

    def test_qualified_results_are_not_padded_with_low_scores(self):
        model = Mock()
        model.predict.return_value = np.array([0.9, 0.6, 0.3])
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", None,
                [self.child(1, 10), self.child(2, 20), self.child(3, 30)])
        self.assertEqual([row["parent_id"] for row in rows], [10])
        model.predict.assert_called_once()

    def test_filter_never_forces_low_scores(self):
        parents = [{**self.child(i, i), "total_score": i / 10} for i in range(1, 5)]
        self.assertEqual(search.chunk_filter(parents), [])
        self.assertEqual(search.chunk_filter([]), [])

    def test_final_limit_counts_parents_not_children(self):
        candidates = [self.child(i, 10) for i in range(6)]
        candidates += [self.child(10 + i, 20 + i) for i in range(6)]
        model = Mock()
        model.predict.return_value = np.array([0.99] * 6 + [0.95, 0.94, 0.93, 0.92, 0.91, 0.9])
        with patch.object(search, "get_reranker", return_value=model):
            rows = search.rerank_results("original", None, candidates)
        self.assertEqual([row["parent_id"] for row in rows], [10, 20, 21, 22, 23])

    def test_empty_candidates_do_not_load_model(self):
        with patch.object(search, "get_reranker") as get_model:
            self.assertEqual(search.rerank_results("original", None, []), [])
            get_model.assert_not_called()

    def test_score_lengths_checked_against_unique_children(self):
        candidates = [self.child(1, 10), self.child(2, 10), self.child(2, 10)]
        for scores, translated in [([np.array([0.9])], None),
                                   ([np.array([0.9, 0.8]), np.array([0.9])], "translated")]:
            with self.subTest(translated=translated):
                model = Mock()
                model.predict.side_effect = scores
                with patch.object(search, "get_reranker", return_value=model):
                    with self.assertRaisesRegex(RuntimeError, "子片数量不一致"):
                        search.rerank_results("original", translated, candidates)


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        engine.dispose()
        TEMP.cleanup()

"""Dependency-free checks of the actual ranking functions, with model calls mocked.

AST loading avoids importing database/model dependencies; function bodies are
compiled unchanged, with only timing decorators removed.
"""
import ast
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import Mock


class RerankRouteTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "app/services/search_services.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        self.model = Mock()
        self.ns = {
            "performance_logger": Mock(), "get_reranker": Mock(return_value=self.model),
            "Sigmoid": Mock(), "measure_stage": lambda *a, **kw: nullcontext(),
        }
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in {"rerank_results", "chunk_filter"}:
                node.decorator_list = []
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), self.ns)
        self.rerank = self.ns["rerank_results"]

    @staticmethod
    def child(identifier, parent=None):
        return {"child_id": identifier, "parent_id": parent or identifier,
                "child_content": f"text {identifier}", "parent_content": "parent text"}

    def test_each_query_only_scores_its_own_candidates(self):
        original = [self.child(1), self.child(2), self.child(1)]
        translated = [self.child(2), self.child(3)]
        self.model.predict.side_effect = [[0.4, 0.5], [0.9, 0.8]]
        rows = self.rerank("original", "translated", original, translated)
        self.assertEqual(self.model.predict.call_args_list[0].args[0],
                         [["original", "text 1"], ["original", "text 2"]])
        self.assertEqual(self.model.predict.call_args_list[1].args[0],
                         [["translated", "text 2"], ["translated", "text 3"]])
        self.assertEqual([r["child_id"] for r in rows], [2, 3, 1])
        self.assertEqual(rows[0]["total_score"], 0.9)
        self.assertNotIn("total_score", original[0])

    def test_missing_translation_empty_route_and_score_mismatch(self):
        self.model.predict.return_value = [0.8]
        self.rerank("original", None, [self.child(1)])
        self.model.predict.assert_called_once()
        self.model.reset_mock()
        self.rerank("original", "translated", [], [self.child(2)])
        self.assertEqual(self.model.predict.call_args.args[0], [["translated", "text 2"]])
        self.model.predict.return_value = []
        with self.assertRaises(RuntimeError):
            self.rerank("original", None, [self.child(1)])

    def test_final_threshold_parent_deduplication_and_top_five(self):
        candidates = [self.child(i, 1 if i < 3 else i) for i in range(1, 10)]
        self.model.predict.return_value = [0.3, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.2, 0.1]
        rows = self.rerank("original", None, candidates)
        self.assertEqual([r["child_id"] for r in rows], [2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()

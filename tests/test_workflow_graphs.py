"""Exercise real LangGraph routing without external models or databases."""
import unittest
from app.services.workflow_graphs import build_document_graph, build_qa_graph


class WorkflowGraphTests(unittest.TestCase):
    def qa(self, *, translated="en", selection=None, fail=None):
        calls = []
        updates = {
            "validate": {"selection": selection}, "translate": {"translated_query": translated},
            "answer": {"result": {"answer": "ok"}}, "save": {"result": {"answer": "ok", "conversation_id": 1}},
        }
        def node(name):
            def run(state):
                calls.append(name)
                if name == fail:
                    raise ValueError("failed")
                return updates.get(name, {})
            return run
        names = ["validate", "history", "rewrite", "translate", "retrieve_original",
                 "retrieve_translated", "rerank", "selection_retrieve", "answer", "save"]
        return build_qa_graph({name: node(name) for name in names}), calls

    def test_bilingual_and_translation_fallback(self):
        for translation in ("en", None):
            graph, calls = self.qa(translated=translation)
            result = graph.invoke({})
            self.assertEqual("retrieve_translated" in calls, bool(translation))
            self.assertNotIn("selection_retrieve", calls)
            self.assertEqual(calls[-3:], ["rerank", "answer", "save"])
            self.assertEqual(result["result"]["conversation_id"], 1)

    def test_selection_uses_document_scope(self):
        graph, calls = self.qa(selection={"document_id": 1})
        graph.invoke({})
        self.assertIn("selection_retrieve", calls)
        self.assertNotIn("retrieve_original", calls)

    def test_failure_never_saves_or_retries(self):
        for failure in ("validate", "rerank", "answer", "save"):
            graph, calls = self.qa(fail=failure)
            with self.assertRaises(ValueError):
                graph.invoke({})
            self.assertEqual(calls.count(failure), 1)
            if failure != "save":
                self.assertNotIn("save", calls)

    def test_document_routes_and_conversion_failure(self):
        for txt, images, saved, failure, expected in (
            (True, False, False, None, ["decode", "split_embed_store"]),
            (False, False, False, None, ["convert", "export", "split_embed_store"]),
            (False, True, False, None, ["convert", "extract_images", "export", "split_embed_store"]),
            (False, True, True, None, ["convert", "extract_images", "export", "split_embed_store"]),
            (False, True, True, "convert", ["convert"]),
        ):
            calls = []
            def node(name):
                def run(state):
                    calls.append(name)
                    if name == failure:
                        raise ValueError("OCR failed")
                    return {"image_records": [{"status": "saved" if saved else "filtered"}]} if name == "extract_images" else {}
                return run
            names = ["decode", "convert", "extract_images", "export", "split_embed_store"]
            graph = build_document_graph({name: node(name) for name in names}, is_txt=txt, needs_images=images)
            if failure:
                with self.assertRaises(ValueError):
                    graph.invoke({})
            else:
                graph.invoke({})
            self.assertEqual(calls, expected)


if __name__ == "__main__":
    unittest.main()

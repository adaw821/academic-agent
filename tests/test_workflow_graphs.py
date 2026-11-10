"""Exercise real LangGraph routing without external models or databases."""
import unittest
from app.services.workflow_graphs import build_document_graph, build_qa_graph


class WorkflowGraphTests(unittest.TestCase):
    def qa(self, *, translated="en", selection=None, fail=None, route="continue"):
        calls = []
        updates = {
            "validate": {"selection": selection}, "translate": {"translated_query": translated},
            "route": {"route": route}, "answer": {"result": {"answer": "ok"}},
            "save": {"result": {"answer": "ok", "conversation_id": 1}},
        }
        def node(name):
            def run(state):
                calls.append(name)
                if name == fail:
                    raise ValueError("failed")
                return updates.get(name, {})
            return run
        names = ["validate", "history", "route", "rewrite", "translate", "retrieve_original",
                 "retrieve_translated", "rerank", "selection_retrieve", "overview_retrieve",
                 "answer", "save"]
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

    def test_direct_route_skips_retrieval(self):
        graph, calls = self.qa(route="direct")
        graph.invoke({})
        # 直接答:route 后直奔 answer,不经 rewrite/translate/检索/rerank。
        self.assertEqual(calls, ["validate", "history", "route", "answer", "save"])

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
            (False, True, False, None, ["convert", "extract_images", "images_type_router", "enrich_images", "export", "split_embed_store"]),
            (False, True, True, None, ["convert", "extract_images", "images_type_router", "enrich_images", "export", "split_embed_store"]),
            (False, True, True, "convert", ["convert"]),
            (False, True, True, "images_type_router", ["convert", "extract_images", "images_type_router"]),
            (False, True, True, "enrich_images", ["convert", "extract_images", "images_type_router", "enrich_images"]),
        ):
            calls = []
            def node(name):
                def run(state):
                    calls.append(name)
                    if name == failure:
                        raise ValueError("OCR failed")
                    return {"image_records": [{"status": "saved" if saved else "filtered"}]} if name == "extract_images" else {}
                return run
            names = ["decode", "convert", "extract_images", "images_type_router", "enrich_images", "export", "split_embed_store"]
            graph = build_document_graph({name: node(name) for name in names}, is_txt=txt, needs_images=images)
            if failure:
                with self.assertRaises(ValueError):
                    graph.invoke({})
            else:
                graph.invoke({})
            self.assertEqual(calls, expected)

    def test_picture_routing_state_reaches_export(self):
        for classifications in ([], [{"id": "#/pictures/0", "router": None}]):
            def route(state):
                self.assertEqual(state["classifications"], classifications)
                return {"classifications": [dict(item, router="ocr") for item in state["classifications"]]}

            def export(state):
                self.assertEqual(state["classifications"], [dict(item, router="ocr") for item in classifications])
                return {"text": "exported"}

            nodes = {
                "decode": lambda state: {}, "convert": lambda state: {},
                "extract_images": lambda state: {"classifications": classifications},
                "images_type_router": route, "export": export,
                "enrich_images": lambda state: {},
                "split_embed_store": lambda state: {},
            }
            result = build_document_graph(nodes, is_txt=False, needs_images=True).invoke({})
            self.assertEqual(result["text"], "exported")


if __name__ == "__main__":
    unittest.main()

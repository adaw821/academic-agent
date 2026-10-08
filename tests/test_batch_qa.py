"""Offline tests: agent/Scripts/python.exe -B tests/test_batch_qa.py."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import httpx

spec = importlib.util.spec_from_file_location("batch_qa", Path(__file__).resolve().parents[1] / "scripts/batch_qa.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def api(self, handler):
        client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return batch.API(client, "test-account", "not-a-real-password")

    @staticmethod
    def login_response():
        return httpx.Response(200, headers={"set-cookie": "session_id=fake-session; Path=/"}, json={"message": "ok"})

    def test_parser_excludes_answers_and_old_results(self):
        source = self.root / "questions.md"
        source.write_text('''| Q01 | old result |
#### Q01 — fact ｜ doc 2 ｜ 问题：what is SQL?
- 参考答案：secret reference
> old model answer
#### Q02 — 问题：what is RNA?
''', encoding="utf-8")
        self.assertEqual(batch.read_questions(source), [
            {"id": "Q01", "query": "what is SQL?"}, {"id": "Q02", "query": "what is RNA?"},
        ])
        source.write_text(source.read_text(encoding="utf-8") + "\n#### Q01 — 问题：duplicate", encoding="utf-8")
        with self.assertRaises(ValueError):
            batch.read_questions(source)

    def test_upload_poll_and_ask_independent_questions(self):
        source = self.root / "lecture.txt"
        source.write_text("gene evidence", encoding="utf-8")
        output = self.root / "results"
        output.mkdir()
        uploaded, asked = [], []
        def handler(request):
            if request.url.path.endswith("/login"):
                return self.login_response()
            self.assertIn("session_id=fake-session", request.headers["cookie"])
            if request.method == "GET":
                return httpx.Response(200, json=uploaded)
            if request.url.path.endswith("/documents"):
                self.assertIn(b'name="files"', request.content)
                self.assertIn(b'filename="lecture.txt"', request.content)
                uploaded.append(dict(id=17, filename=source.name, status="completed"))
                return httpx.Response(202, json={"saved_files": uploaded, "failed_files": []})
            asked.append(json.loads(request.content))
            return httpx.Response(200, json=dict(answer="test answer", sources=[], retrieved_contexts=[], conversation_id=99))
        api = self.api(handler)
        mapping = batch.upload_corpus(api, 1, [source], output)
        self.assertEqual(mapping[0]["document_id"], 17)
        batch.wait_for_documents(api, 1, mapping, 1, 0.01)
        batch.ask_questions(api, 1, [dict(id="Q01", query="first"), dict(id="Q02", query="second")], 0.4, output)
        self.assertTrue(all(body["conversation_id"] is None and "doc_ids" not in body for body in asked))
        self.assertEqual([body["query"] for body in asked], ["first", "second"])
        self.assertEqual(len((output / "results.jsonl").read_text(encoding="utf-8").splitlines()), 2)
        self.assertNotIn("not-a-real-password", (output / "report.md").read_text(encoding="utf-8"))

    def test_frozen_questions_keep_references_out_of_requests(self):
        source = self.root / "frozen.md"
        source.write_text('''# Q round
## Q01 — fact ｜ doc: lecture
**问题**：what is SQL?
**参考答案**：local reference only

---
## Q02 — unsupported
**问题**：what is missing?
**参考答案**：insufficient evidence
''', encoding="utf-8")
        questions = batch.read_questions(source)
        self.assertEqual([q["id"] for q in questions], ["Q01", "Q02"])
        self.assertEqual(questions[0]["reference"], "local reference only")
        asked = []
        def handler(request):
            if request.url.path.endswith("/login"):
                return self.login_response()
            body = json.loads(request.content)
            self.assertEqual(set(body), {"query", "candidate_k_percentage", "conversation_id"})
            self.assertIsNone(body["conversation_id"])
            asked.append(body["query"])
            return httpx.Response(200, json={"answer": "answer", "sources": [], "retrieved_contexts": ["evidence"]})
        batch.ask_questions(self.api(handler), 14, questions, 0.4, self.root)
        self.assertEqual(asked, [q["query"] for q in questions])
        samples = [json.loads(line) for line in (self.root / "ragas.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(samples[0], dict(user_input="what is SQL?", response="answer",
                                        retrieved_contexts=["evidence"], reference="local reference only"))

    def test_frozen_question_without_body_is_not_silently_skipped(self):
        source = self.root / "invalid.md"
        source.write_text("## Q01 — fact\n**参考答案**：not a question\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "缺少问题正文"):
            batch.read_questions(source)

    def test_existing_corpus_requires_ready_documents_and_never_uploads(self):
        for docs in ([], [dict(id=1, filename="test.pdf", status="processing")],
                     [dict(id=1, filename="test.pdf", status="failed")],
                     [dict(id=1, filename="test.pdf", status="completed")]):
            def handler(request):
                if request.url.path.endswith("/login"):
                    return self.login_response()
                self.assertEqual(request.method, "GET")
                return httpx.Response(200, json=docs)
            api = self.api(handler)
            if docs and docs[0]["status"] == "completed":
                self.assertEqual(batch.existing_corpus(api, 14)[0]["document_id"], 1)
            else:
                with self.assertRaises(RuntimeError):
                    batch.existing_corpus(api, 14)

    def test_failed_parse_blocks_questions(self):
        api = self.api(lambda request: self.login_response() if request.url.path.endswith("/login") else
                       httpx.Response(200, json=[dict(id=1, filename="failed.pdf", status="failed")]))
        with self.assertRaisesRegex(RuntimeError, "解析失败"):
            batch.wait_for_documents(api, 1, [dict(document_id=1, filename="failed.pdf")], 1, 0.01)

    def test_existing_file_requires_explicit_reuse(self):
        source = self.root / "lecture.txt"
        source.write_text("test", encoding="utf-8")
        api = self.api(lambda request: self.login_response() if request.url.path.endswith("/login") else
                       httpx.Response(200, json=[dict(id=1, filename=source.name, status="completed")]))
        with self.assertRaisesRegex(RuntimeError, "reuse-existing"):
            batch.upload_corpus(api, 1, [source], self.root)
        result = batch.upload_corpus(api, 1, [source], self.root, reuse_existing=True)
        self.assertTrue(result[0]["reused"])

    def test_timeout_is_saved_and_never_retried(self):
        calls = []
        def handler(request):
            if request.url.path.endswith("/login"):
                return self.login_response()
            calls.append(request)
            raise httpx.ReadTimeout("test timeout", request=request)
        results = batch.ask_questions(self.api(handler), 1, [dict(id="Q01", query="test")], 0.4, self.root)
        self.assertEqual(len(calls), 1)
        self.assertFalse(results[0]["request_ok"])
        self.assertTrue((self.root / "Q01.json").exists())

    def test_session_expiry_rewinds_upload_after_relogin(self):
        uploads = []
        def handler(request):
            if request.url.path.endswith("/login"):
                return self.login_response()
            uploads.append(request.content)
            return httpx.Response(401 if len(uploads) == 1 else 202, json={})
        source = self.root / "lecture.txt"
        source.write_bytes(b"test upload body")
        with source.open("rb") as stream:
            response = self.api(handler).request("POST", "/api/projects/1/documents",
                                                files=[("files", (source.name, stream, "text/plain"))])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(uploads), 2)
        self.assertTrue(all(b"test upload body" in body for body in uploads))


if __name__ == "__main__":
    unittest.main(verbosity=2)

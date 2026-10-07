"""Offline OCR checks: agent/Scripts/python.exe tests/test_image_ocr.py."""

import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import image_ocr


def completion(content, finish_reason="stop", **message_fields):
    return {"choices": [{
        "finish_reason": finish_reason,
        "message": {"role": "assistant", "content": content, **message_fields},
    }]}


class ImageOCRTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        Image.new("RGB", (20, 20), "red").save(self.directory / "7.png")
        Image.new("RGB", (20, 20), "blue").save(self.directory / "2.png")
        self.records = [
            {"reading_order": 7, "image_path": "7.png", "status": "saved"},
            {"reading_order": 9, "image_path": None, "status": "unavailable"},
            {"reading_order": 2, "image_path": "2.png", "status": "saved"},
        ]
        self.enterContext(patch.dict(os.environ, {"ZHIPU_API_KEY": "unused-test-key"}))

    def mock_client(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return self.enterContext(patch.object(image_ocr.httpx, "Client", return_value=client))

    def test_requests_correct_pixels_and_preserves_nonconsecutive_identifiers(self):
        requests = []

        def handler(request):
            position = (7, 2)[len(requests)]
            requests.append(request)
            self.assertEqual(str(request.url), "https://open.bigmodel.cn/api/paas/v4/chat/completions")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["Authorization"], "Bearer unused-test-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "glm-4.1v-thinking-flash")
            self.assertFalse(payload["stream"])
            self.assertEqual(len(payload["messages"]), 1)
            message = payload["messages"][0]
            self.assertEqual(message["role"], "user")
            self.assertEqual(message["content"][0]["type"], "image_url")
            self.assertEqual(message["content"][1], {"type": "text", "text": image_ocr.OCR_PROMPT})
            prefix, encoded = message["content"][0]["image_url"]["url"].split(",", 1)
            self.assertEqual(prefix, "data:image/png;base64")
            self.assertEqual(base64.b64decode(encoded), (self.directory / f"{position}.png").read_bytes())
            return httpx.Response(200, json=completion(f"识别内容 {position}"))

        self.mock_client(handler)
        result = image_ocr.recognize_document_images(self.records, self.directory)
        self.assertEqual(result, {7: "识别内容 7", 2: "识别内容 2"})
        self.assertEqual(len(requests), 2)

    def test_no_saved_pictures_needs_no_credentials_or_requests(self):
        with patch.dict(os.environ, {"ZHIPU_API_KEY": ""}), patch.object(image_ocr.httpx, "Client") as client:
            for records in ([], [self.records[1]], [
                {"reading_order": 3, "image_path": None, "status": "filtered"},
            ]):
                self.assertEqual(image_ocr.recognize_document_images(records, self.directory), {})
            client.assert_not_called()

    def test_missing_credentials_fails_before_request(self):
        with patch.dict(os.environ, {"ZHIPU_API_KEY": ""}), patch.object(image_ocr.httpx, "Client") as client:
            with self.assertRaisesRegex(ValueError, "ZHIPU_API_KEY"):
                image_ocr.recognize_document_images(self.records, self.directory)
            client.assert_not_called()

    def test_duplicate_identifiers_fail_before_request(self):
        with patch.object(image_ocr.httpx, "Client") as client:
            with self.assertRaisesRegex(ValueError, "重复 reading_order"):
                image_ocr.recognize_document_images([self.records[0]] * 2, self.directory)
            client.assert_not_called()

    def test_provider_errors_keep_failed_identifier(self):
        cases = [
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(500, json={"error": "unavailable"}),
            httpx.Response(200, text="not JSON"),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, json={"choices": [None]}),
            httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {}}]}),
            httpx.Response(200, json=completion(None, reasoning_content="Only thinking")),
            httpx.Response(200, json=completion([])),
            httpx.Response(200, json=[]),
        ]
        for response in cases:
            with self.subTest(response=response):
                client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
                with patch.object(image_ocr.httpx, "Client", return_value=client):
                    with self.assertRaises(image_ocr.ImageOCRError) as caught:
                        image_ocr.recognize_document_images(self.records, self.directory)
                self.assertEqual(caught.exception.reading_order, 7)

    def test_timeout_on_later_picture_is_not_returned_as_partial_success(self):
        requests = []

        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, json=completion("first picture"))
            raise httpx.ReadTimeout("timeout", request=request)

        self.mock_client(handler)
        with self.assertRaises(image_ocr.ImageOCRError) as caught:
            image_ocr.recognize_document_images(self.records, self.directory)
        self.assertEqual(caught.exception.reading_order, 2)

    def test_empty_recognition_is_preserved(self):
        self.mock_client(lambda request: httpx.Response(200, json=completion("  \n")))
        self.assertEqual(image_ocr.recognize_document_images(self.records, self.directory), {7: "", 2: ""})

    def test_only_final_content_is_returned_without_reasoning(self):
        self.mock_client(lambda request: httpx.Response(200, json=completion(
            "  | 原文 | 数值 |\n| --- | --- |\n| A | 42 |  ",
            reasoning_content="这里是模型的思考过程，不应进入正文",
        )))
        result = image_ocr.recognize_document_images(self.records[:1], self.directory)
        self.assertEqual(result, {7: "| 原文 | 数值 |\n| --- | --- |\n| A | 42 |"})

    def test_unfinished_or_filtered_output_is_rejected(self):
        for reason in ("length", "sensitive", "content_filter", "tool_calls", None):
            with self.subTest(finish_reason=reason):
                client = httpx.Client(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=completion("部分内容", reason)),
                ))
                with patch.object(image_ocr.httpx, "Client", return_value=client):
                    with self.assertRaises(image_ocr.ImageOCRError) as caught:
                        image_ocr.recognize_document_images(self.records, self.directory)
                self.assertEqual(caught.exception.reading_order, 7)

    def test_missing_file_has_identifier_and_sends_no_request(self):
        self.mock_client(lambda request: self.fail("Missing file must not trigger OCR"))
        records = [{"reading_order": 12, "image_path": "12.png", "status": "saved"}]
        with self.assertRaises(image_ocr.ImageOCRError) as caught:
            image_ocr.recognize_document_images(records, self.directory)
        self.assertEqual(caught.exception.reading_order, 12)


if __name__ == "__main__":
    unittest.main()

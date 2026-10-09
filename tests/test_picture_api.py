"""Provider errors, cooldowns and responses without external requests."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from app.services import picture_api


class PictureApiTests(unittest.TestCase):
    def setUp(self):
        self.options = SimpleNamespace(
            url="https://example.test/chat/completions", headers={"Authorization": "Bearer test-only"},
            params={"model": "glm-4.6v-flash", "max_tokens": 8192}, prompt="Read visible text", timeout=120,
        )
        self.image = Image.new("RGB", (20, 20), "white")
        self.sleep = self.enterContext(patch.object(picture_api, "sleep"))

    def run_request(self, responses):
        self.calls = []
        def handle(request):
            self.calls.append(request)
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response
        client = httpx.Client(transport=httpx.MockTransport(handle))
        with patch.object(picture_api.httpx, "Client", return_value=client):
            return picture_api.request_picture_description(self.image, options=self.options, picture_id="#/pictures/0")

    @staticmethod
    def success(finish="stop"):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Visible evidence"}, "finish_reason": finish}],
            "usage": {"total_tokens": 30},
        })

    def test_1305_retries_with_cooldown_then_returns_text(self):
        busy = httpx.Response(429, json={"error": {"code": "1305"}})
        result = self.run_request(iter([busy, self.success()]))
        self.assertEqual(result.text, "Visible evidence")
        self.assertEqual(len(self.calls), 2)
        self.sleep.assert_called_once_with(5)

    def test_persistent_busy_is_bounded_and_preserves_http_and_api_code(self):
        responses = iter([httpx.Response(429, json={"error": {"code": "1305"}}) for _ in range(4)])
        with self.assertRaisesRegex(ValueError, r"HTTP 429, code=1305.*4"):
            self.run_request(responses)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [5, 10, 20])

    def test_retry_after_is_respected(self):
        response = httpx.Response(429, headers={"Retry-After": "25"}, json={"error": {"code": "1305"}})
        self.run_request(iter([response, self.success()]))
        self.sleep.assert_called_once_with(25)

    def test_long_retry_after_does_not_retry_early(self):
        response = httpx.Response(429, headers={"Retry-After": "120"}, json={"error": {"code": "1305"}})
        with self.assertRaisesRegex(ValueError, "HTTP 429"):
            self.run_request(iter([response]))
        self.sleep.assert_not_called()

    def test_auth_error_does_not_retry_or_expose_response_body(self):
        response = httpx.Response(401, json={"error": {"code": "1000", "message": "private response"}})
        with self.assertRaises(ValueError) as error:
            self.run_request(iter([response]))
        self.assertIn("HTTP 401", str(error.exception))
        self.assertNotIn("private response", str(error.exception))
        self.sleep.assert_not_called()

    def test_transport_error_can_recover(self):
        result = self.run_request(iter([httpx.ReadTimeout("timeout"), self.success()]))
        self.assertEqual(result.text, "Visible evidence")
        self.sleep.assert_called_once_with(5)

    def test_malformed_response_fails(self):
        with self.assertRaisesRegex(ValueError, "响应格式无效"):
            self.run_request(iter([httpx.Response(200, text="not JSON")]))

    def test_finish_reason_is_preserved_for_native_metadata_validation(self):
        result = self.run_request(iter([self.success("length")]))
        self.assertEqual(result.stop_reason.value, "length")
        self.assertEqual(result.num_tokens, 30)


if __name__ == "__main__":
    unittest.main()

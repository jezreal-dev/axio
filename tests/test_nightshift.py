import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app.config import Settings, normalize_runtime_url
from app.llm import ModelRouter


class NightshiftTests(unittest.TestCase):
    def router(self):
        return ModelRouter(Settings(model_provider="meterless", meterless_runtime_token="test-token", meterless_base_url="http://localhost:8088/v1", use_llm=True))

    def test_console_urls_and_secret_not_public(self):
        for suffix in ("", "/v1", "/v1/models", "/v1/chat/completions", "/healthz"):
            self.assertEqual(normalize_runtime_url("localhost:8088" + suffix)[0], "http://localhost:8088/v1")
        settings = Settings(meterless_base_url="http://localhost:8088/connect#k=secret-token")
        self.assertEqual(settings.provider_token, "secret-token")
        self.assertNotIn("secret-token", json.dumps(settings.public_view()))

    def test_wrong_token_is_unauthorized(self):
        with patch("urllib.request.urlopen", side_effect=HTTPError("", 401, "", {}, None)):
            self.assertEqual(self.router().connection_status()["status"], "unauthorized")

    def test_discovery_preserves_new_routes(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'{"data":[{"id":"meterless/new-route"}]}')):
            self.assertEqual(self.router().connection_status()["models"], ["meterless/new-route"])

    def test_text_request_auth_headers_and_actual_model(self):
        response = {"model": "nvidia/test-nemotron", "choices": [{"message": {"content": "connected"}}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as call:
            router = self.router()
            self.assertEqual(router.generate("fast", "system", "hello"), "connected")
            request = call.call_args.args[0]
            headers = {k.lower(): v for k, v in request.header_items()}
            self.assertEqual(headers["authorization"], "Bearer test-token")
            self.assertEqual(headers["meterless-app"], "axio")
            self.assertEqual(headers["meterless-scope"], "user")
            self.assertEqual(call.call_args.kwargs["timeout"], 180)
            self.assertEqual(router.calls[0]["model"], "nvidia/test-nemotron")

    def test_placeholder_disabled(self):
        self.assertFalse(Settings(meterless_runtime_token="rt-local", meterless_base_url="http://localhost:8088").llm_configured)

from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

from care.llm.client import LLMClient, MockLLMClient, OpenAICompatibleClient


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class LLMClientTests(unittest.TestCase):
    def test_mock_client_returns_deterministic_unified_diff(self) -> None:
        client = MockLLMClient()

        first = client.complete("prompt")
        second = client.complete("prompt", temperature=0.9)

        self.assertEqual(first, second)
        self.assertIn("--- a/CARE_MOCK_PATCH.c", first)
        self.assertIn("@@ -1 +1 @@", first)

    def test_from_env_defaults_to_mock(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            client = LLMClient.from_env()

        self.assertIsInstance(client, MockLLMClient)
        self.assertTrue(client.is_mock)

    def test_from_env_require_real_rejects_mock_provider(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                LLMClient.from_env(require_real=True)

    def test_from_env_builds_openai_compatible_client_without_hardcoded_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CARE_LLM_PROVIDER": "openai",
                "CARE_LLM_MODEL": "gpt-4.1",
                "CARE_LLM_API_KEY": "test-key",
                "CARE_LLM_BASE_URL": "https://example.test/v1/chat/completions",
            },
            clear=True,
        ):
            client = LLMClient.from_env()

        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual(client.model, "gpt-4.1")
        self.assertEqual(client.api_key, "test-key")
        self.assertEqual(client.base_url, "https://example.test/v1/chat/completions")

    def test_from_env_supports_standard_openai_api_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "standard-key",
                "CARE_LLM_MODEL": "gpt-4.1",
            },
            clear=True,
        ):
            client = LLMClient.from_env(require_real=True)

        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual(client.api_key, "standard-key")

    def test_openai_compatible_complete_parses_response(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse({"choices": [{"message": {"content": "diff"}}]})

        client = OpenAICompatibleClient(
            api_key="secret",
            model="gpt-test",
            base_url="https://example.test/chat",
            timeout_seconds=7,
        )
        with patch("urllib.request.urlopen", fake_urlopen):
            result = client.complete("hello", temperature=0.3)

        self.assertEqual(result, "diff")
        self.assertEqual(captured["url"], "https://example.test/chat")
        self.assertEqual(captured["body"]["model"], "gpt-test")
        self.assertEqual(captured["body"]["temperature"], 0.3)
        self.assertEqual(captured["body"]["messages"][0]["content"], "hello")
        self.assertEqual(captured["timeout"], 7)

"""LLM client abstraction with OpenAI-compatible and mock providers."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional


class LLMClient:
    """Generic completion interface used by CARE's planning and patching code."""

    is_mock: bool = False

    def complete(self, prompt: str, temperature: float = 0.2) -> str:
        raise NotImplementedError

    @classmethod
    def from_env(cls, require_real: bool = False) -> "LLMClient":
        provider = os.environ.get("CARE_LLM_PROVIDER", "").strip().lower()
        if not provider and os.environ.get("OPENAI_API_KEY"):
            provider = "openai"
        if not provider:
            provider = "mock"
        if provider in {"", "mock", "local_mock"}:
            if require_real:
                raise ValueError(
                    "CARE_LLM_PROVIDER must be a real provider when --require-real-llm is set"
                )
            return MockLLMClient()
        if provider == "openai":
            return OpenAICompatibleClient.from_env()
        raise ValueError(f"unsupported CARE_LLM_PROVIDER: {provider}")


@dataclass
class MockLLMClient(LLMClient):
    """Deterministic local mock for tests and offline prototype runs."""

    is_mock: bool = True
    diff: Optional[str] = None

    def complete(self, prompt: str, temperature: float = 0.2) -> str:
        if self.diff is not None:
            return self.diff
        return (
            "--- a/CARE_MOCK_PATCH.c\n"
            "+++ b/CARE_MOCK_PATCH.c\n"
            "@@ -1 +1 @@\n"
            "-/* CARE mock patch placeholder */\n"
            "+/* CARE mock patch placeholder */\n"
        )


@dataclass
class OpenAICompatibleClient(LLMClient):
    """Small OpenAI-compatible client for Chat Completions and Responses."""

    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1/chat/completions"
    timeout_seconds: int = 120
    max_retries: int = 8
    retry_base_seconds: float = 8.0
    api_style: str = "chat_completions"
    reasoning_effort: Optional[str] = None
    max_output_tokens: Optional[int] = None
    last_usage: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient":
        api_key = os.environ.get("CARE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "CARE_LLM_API_KEY or OPENAI_API_KEY is required when CARE_LLM_PROVIDER=openai"
            )
        api_style = os.environ.get("CARE_LLM_API_STYLE", "chat_completions").strip().lower()
        if api_style not in {"chat_completions", "responses"}:
            raise ValueError("CARE_LLM_API_STYLE must be chat_completions or responses")
        default_base_url = (
            "https://api.openai.com/v1/responses"
            if api_style == "responses"
            else "https://api.openai.com/v1/chat/completions"
        )
        raw_max_output = os.environ.get("CARE_LLM_MAX_OUTPUT_TOKENS", "").strip()
        return cls(
            api_key=api_key,
            model=os.environ.get("CARE_LLM_MODEL", "gpt-4.1"),
            base_url=os.environ.get(
                "CARE_LLM_BASE_URL",
                default_base_url,
            ),
            timeout_seconds=int(os.environ.get("CARE_LLM_TIMEOUT", "120")),
            max_retries=int(os.environ.get("CARE_LLM_MAX_RETRIES", "8")),
            retry_base_seconds=float(os.environ.get("CARE_LLM_RETRY_BASE_SECONDS", "8")),
            api_style=api_style,
            reasoning_effort=os.environ.get("CARE_LLM_REASONING_EFFORT") or None,
            max_output_tokens=int(raw_max_output) if raw_max_output else None,
        )

    def complete(self, prompt: str, temperature: float = 0.2) -> str:
        body = json.dumps(self._request_body(prompt, temperature)).encode("utf-8")
        request = urllib.request.Request(
            self.base_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        payload = self._open_with_retries(request)
        self.last_usage = dict(payload.get("usage") or {}) if isinstance(payload, dict) else {}

        if self.api_style == "responses":
            return self._response_output_text(payload)

        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected LLM response shape: {payload}") from exc

    def _request_body(self, prompt: str, temperature: float) -> dict[str, Any]:
        if self.api_style == "responses":
            body: dict[str, Any] = {"model": self.model, "input": prompt}
            if self.reasoning_effort:
                body["reasoning"] = {"effort": self.reasoning_effort}
            if self.max_output_tokens:
                body["max_output_tokens"] = self.max_output_tokens
            return body
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if self.max_output_tokens:
            body["max_output_tokens"] = self.max_output_tokens
        return body

    @staticmethod
    def _response_output_text(payload: dict[str, Any]) -> str:
        text = payload.get("output_text")
        if isinstance(text, str) and text:
            return text
        fragments: list[str] = []
        for item in payload.get("output") or []:
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    fragments.append(content["text"])
        if fragments:
            return "\n".join(fragments)
        raise RuntimeError(f"unexpected Responses API response shape: {payload}")

    def _open_with_retries(self, request: urllib.request.Request) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if not self._should_retry_http(exc, detail, attempt):
                    raise RuntimeError(f"LLM request failed with HTTP {exc.code}: {detail}") from exc
                last_error = RuntimeError(f"LLM request failed with HTTP {exc.code}: {detail}")
                time.sleep(self._retry_delay(exc, detail, attempt))
            except urllib.error.URLError as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"LLM request failed: {exc}") from exc
                last_error = exc
                time.sleep(self.retry_base_seconds * (attempt + 1))
        raise RuntimeError(f"LLM request failed after retries: {last_error}")

    def _should_retry_http(
        self,
        exc: urllib.error.HTTPError,
        detail: str,
        attempt: int,
    ) -> bool:
        if attempt >= self.max_retries:
            return False
        if exc.code in {500, 502, 503, 504}:
            return True
        if exc.code != 429:
            return False
        try:
            payload = json.loads(detail)
            error = payload.get("error") or {}
            if error.get("code") == "insufficient_quota":
                return False
        except json.JSONDecodeError:
            pass
        return True

    def _retry_delay(
        self,
        exc: urllib.error.HTTPError,
        detail: str,
        attempt: int,
    ) -> float:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass
        match = re.search(r"try again in ([0-9.]+)s", detail, flags=re.IGNORECASE)
        if match:
            return max(1.0, float(match.group(1)) + 1.0)
        return self.retry_base_seconds * (attempt + 1)


LlmClient = LLMClient

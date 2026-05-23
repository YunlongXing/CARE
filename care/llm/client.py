"""LLM client abstraction with OpenAI-compatible and mock providers."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


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
    """Minimal OpenAI-compatible chat completions client."""

    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1/chat/completions"
    timeout_seconds: int = 120
    max_retries: int = 8
    retry_base_seconds: float = 8.0

    @classmethod
    def from_env(cls) -> "OpenAICompatibleClient":
        api_key = os.environ.get("CARE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "CARE_LLM_API_KEY or OPENAI_API_KEY is required when CARE_LLM_PROVIDER=openai"
            )
        return cls(
            api_key=api_key,
            model=os.environ.get("CARE_LLM_MODEL", "gpt-4.1"),
            base_url=os.environ.get(
                "CARE_LLM_BASE_URL",
                "https://api.openai.com/v1/chat/completions",
            ),
            timeout_seconds=int(os.environ.get("CARE_LLM_TIMEOUT", "120")),
            max_retries=int(os.environ.get("CARE_LLM_MAX_RETRIES", "8")),
            retry_base_seconds=float(os.environ.get("CARE_LLM_RETRY_BASE_SECONDS", "8")),
        )

    def complete(self, prompt: str, temperature: float = 0.2) -> str:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
            }
        ).encode("utf-8")
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

        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected LLM response shape: {payload}") from exc

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

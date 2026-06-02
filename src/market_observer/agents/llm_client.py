"""LLM client abstraction + DeepSeek implementation.

The agent layer depends only on the ``LLMClient`` Protocol. ``DeepSeekClient``
talks to DeepSeek's OpenAI-compatible chat-completions endpoint over httpx,
with low temperature, a request timeout, and a bounded retry count. On
persistent failure it raises ``LLMError`` so the orchestrator can degrade
(mark that agent unavailable) rather than hang.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when an LLM call fails after exhausting retries."""


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str: ...


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        temperature: float = 0.2,
        timeout: float = 60.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeek api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries
        self._client = http_client or httpx.Client(timeout=timeout)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, object] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last_exc = exc
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
        raise LLMError(f"LLM call failed after {self.max_retries + 1} attempts") from last_exc

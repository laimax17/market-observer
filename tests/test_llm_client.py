"""T-07: DeepSeekClient over httpx.MockTransport (no network)."""

from __future__ import annotations

import httpx
import pytest

from market_observer.agents.llm_client import DeepSeekClient, LLMError


def _client(handler) -> DeepSeekClient:
    transport = httpx.MockTransport(handler)
    return DeepSeekClient(
        api_key="test-key",
        max_retries=2,
        http_client=httpx.Client(transport=transport),
    )


def test_requires_api_key() -> None:
    with pytest.raises(ValueError):
        DeepSeekClient(api_key="")


def test_complete_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi there"}}]})

    assert _client(handler).complete("sys", "user") == "hi there"


def test_json_mode_sets_response_format() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    _client(handler).complete("sys", "user", json_mode=True)
    assert seen["response_format"] == {"type": "json_object"}


def test_retries_then_succeeds() -> None:
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert _client(handler).complete("s", "u") == "ok"
    assert state["n"] == 2  # one failure, one success


def test_persistent_failure_raises_llmerror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    with pytest.raises(LLMError):
        _client(handler).complete("s", "u")


def test_timeout_raises_llmerror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(LLMError):
        _client(handler).complete("s", "u")

"""Unit tests for AsyncGroqClient.

The Groq client is structurally identical to AsyncGeminiClient
(generate, generate_many) but uses the chat completions API. These
tests stub the underlying groq.AsyncGroq client so we never make a
real network request.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from groq import APIError, RateLimitError

from daily_bot.config import Settings
from daily_bot.groq_client import AsyncGroqClient


class _NoEnvSettings(Settings):
    model_config = {"env_file": None, "extra": "ignore"}


def _fake_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _fake_response() -> httpx.Response:
    return httpx.Response(429, request=_fake_request())


def _settings(**overrides) -> Settings:
    """Build a Settings instance with a Groq key and minimal required fields."""
    base = dict(
        firebase_credentials="{}",
        sender_email="a@b.c",
        sender_password="password",
        groq_api_key="test-groq-key",
    )
    base.update(overrides)
    return _NoEnvSettings(**base)  # type: ignore[call-arg]


def _make_groq_response(text: str | None) -> SimpleNamespace:
    """Build a fake chat completion response object."""
    message = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


class _StubGroqClient:
    """Drop-in for groq.AsyncGroq with controllable chat responses/errors.

    responses: list of strings to return in order (None = empty completion)
    errors:    list of exceptions to raise in order before yielding responses
    """

    def __init__(
        self,
        responses: list[str | None] | None = None,
        errors: list[Exception] | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._errors = list(errors or [])
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._errors:
            raise self._errors.pop(0)
        if self._responses:
            return _make_groq_response(self._responses.pop(0))
        return _make_groq_response(None)


def test_construct_requires_groq_api_key():
    """AsyncGroqClient raises if no API key is configured."""
    settings = _NoEnvSettings(  # type: ignore[call-arg]
        firebase_credentials="{}",
        sender_email="a@b.c",
        sender_password="password",
    )
    with pytest.raises(ValueError, match="groq_api_key is required"):
        AsyncGroqClient(settings)


def test_construct_stores_settings():
    """The constructor stores the model, retries, and key."""
    settings = _settings(groq_model="llama-3.3-70b-versatile", groq_retries=5)
    client = AsyncGroqClient(settings)
    assert client._model == "llama-3.3-70b-versatile"
    assert client._retries == 5


async def test_generate_returns_text():
    """A successful call returns the stripped text."""
    client = AsyncGroqClient(_settings())
    client._client = _StubGroqClient(responses=["  Hello world  "])  # type: ignore[assignment]
    result = await client.generate("Summarize this article.")
    assert result == "Hello world"


async def test_generate_returns_none_on_empty_text():
    """An empty completion (choices[0].message.content = None) returns None."""
    client = AsyncGroqClient(_settings())
    client._client = _StubGroqClient(responses=[None])  # type: ignore[assignment]
    result = await client.generate("Summarize this article.")
    assert result is None


async def test_generate_returns_none_on_no_choices():
    """An empty choices list returns None without retrying."""
    client = AsyncGroqClient(_settings())
    stub = _StubGroqClient()
    stub.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(choices=[])
    )
    client._client = stub  # type: ignore[assignment]
    result = await client.generate("Summarize this article.")
    assert result is None


async def test_generate_retries_on_rate_limit():
    """A 429 is retried; subsequent success is returned."""
    client = AsyncGroqClient(_settings(groq_retries=3))
    stub = _StubGroqClient(
        errors=[RateLimitError("rate limited", response=_fake_response(), body=None)],
        responses=["Recovered"],
    )
    client._client = stub  # type: ignore[assignment]
    result = await client.generate("Summarize this article.")
    assert result == "Recovered"
    assert len(stub.calls) == 2  # one failure + one success


async def test_generate_retries_on_api_error():
    """A generic APIError is retried; subsequent success is returned."""
    client = AsyncGroqClient(_settings(groq_retries=3))
    err = APIError("server error", request=_fake_request(), body=None)
    stub = _StubGroqClient(errors=[err], responses=["OK."])
    client._client = stub  # type: ignore[assignment]
    result = await client.generate("Summarize this article.")
    assert result == "OK."
    assert len(stub.calls) == 2


async def test_generate_returns_none_after_all_retries_exhausted():
    """If every attempt fails, generate returns None."""
    client = AsyncGroqClient(_settings(groq_retries=2))
    err = RateLimitError("still rate limited", response=_fake_response(), body=None)
    stub = _StubGroqClient(errors=[err, err])
    client._client = stub  # type: ignore[assignment]
    result = await client.generate("Summarize this article.")
    assert result is None
    assert len(stub.calls) == 2  # both attempts raised


async def test_generate_passes_system_and_user_messages():
    """The chat completions API receives a system message and the user prompt."""
    client = AsyncGroqClient(_settings())
    stub = _StubGroqClient(responses=["output"])
    client._client = stub  # type: ignore[assignment]
    await client.generate("My prompt here.")
    call = stub.calls[0]
    assert call["model"] == "llama-3.3-70b-versatile"
    messages = call["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "news summarizer" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "My prompt here."}


async def test_generate_uses_configured_model():
    """A custom groq_model setting is passed to the API."""
    client = AsyncGroqClient(_settings(groq_model="llama-3.3-70b-versatile"))
    stub = _StubGroqClient(responses=["x"])
    client._client = stub  # type: ignore[assignment]
    await client.generate("prompt")
    assert stub.calls[0]["model"] == "llama-3.3-70b-versatile"


async def test_generate_many_runs_all_prompts():
    """generate_many returns one result per input, in order."""
    client = AsyncGroqClient(_settings())
    stub = _StubGroqClient(responses=["one", "two", "three"])
    client._client = stub  # type: ignore[assignment]
    results = await client.generate_many(
        ["p1", "p2", "p3"], concurrency=2
    )
    assert results == ["one", "two", "three"]


async def test_generate_many_preserves_order_with_failures():
    """A failed prompt returns None in the correct position."""
    client = AsyncGroqClient(_settings(groq_retries=1))
    err = APIError("boom", request=_fake_request(), body=None)
    stub = _StubGroqClient(
        errors=[err],  # first call fails
        responses=["only-response"],
    )
    client._client = stub  # type: ignore[assignment]
    results = await client.generate_many(["a", "b"], concurrency=1)
    # First prompt: all retries exhausted (groq_retries=1) -> None
    # Second prompt: returns "only-response"
    assert results[0] is None
    assert results[1] == "only-response"


def test_satisfies_llm_client_protocol():
    """AsyncGroqClient satisfies the LLMClient protocol (structural typing).

    The Protocol is structural (no @runtime_checkable), so we verify
    the contract by checking the method names exist and are async
    coroutine functions.
    """
    import inspect

    client = AsyncGroqClient(_settings())
    # Both protocol methods must exist on the client.
    assert hasattr(client, "generate")
    assert hasattr(client, "generate_many")
    # And they must be coroutine functions (callable with await).
    assert inspect.iscoroutinefunction(client.generate)
    assert inspect.iscoroutinefunction(client.generate_many)

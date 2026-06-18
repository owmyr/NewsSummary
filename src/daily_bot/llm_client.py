"""LLM client abstraction for the summarization layer.

The pipeline is provider-agnostic: any client that implements the
:class:`LLMClient` protocol can be passed to :func:`summarize_article`.
This lets the orchestrator mix providers (e.g. Groq for English, Gemini
for Portuguese) without leaking provider-specific code into the
summarizer.
"""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Minimal async interface for an LLM client used by the pipeline.

    Both :class:`daily_bot.summarizer.AsyncGeminiClient` and
    :class:`daily_bot.groq_client.AsyncGroqClient` satisfy this protocol.
    The summarizer depends on the interface, not on a concrete class.
    """

    async def generate(self, prompt: str) -> str | None:
        """Generate text for a single prompt.

        Returns the generated string, or ``None`` on failure (quota,
        network, validation, etc.). Implementations should retry on
        transient errors internally before returning ``None``.
        """
        ...

    async def generate_many(
        self, prompts: list[str], concurrency: int
    ) -> list[str | None]:
        """Generate text for many prompts, bounded by ``concurrency``.

        Returns one result per input prompt, in the same order. ``None``
        entries represent failed generations for that prompt.
        """
        ...

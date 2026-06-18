"""AI summarization via Groq (Llama 3.1 70B) — used for English sources.

This client is a drop-in alternative to :class:`AsyncGeminiClient`. It
satisfies the :class:`daily_bot.llm_client.LLMClient` protocol and can
be passed directly to :func:`summarize_article`.

Why Groq? The free tier offers 30 req/min and ~1440 req/day — far more
headroom than the Gemini free tier (5 req/min, 20 req/day). Groq
inference is also sub-second, so the pipeline finishes much faster.

Why not use Groq for everything? Llama 3.1 70B writes passable PT-BR
but tends to be slightly less natural than Gemini Flash. The orchestrator
routes English sources to Groq and Portuguese sources to Gemini.
"""

from __future__ import annotations

import asyncio
import logging

from groq import APIError, AsyncGroq, RateLimitError

from .config import Settings

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are a professional news summarizer. Write in a neutral, "
    "objective, newsroom tone. Focus on key facts, context, and major "
    "developments. Avoid commentary, opinion, or meta text."
)


class AsyncGroqClient:
    """Thin async wrapper around the Groq Python SDK with retries + backoff.

    Satisfies the :class:`daily_bot.llm_client.LLMClient` protocol.

    The Groq free tier (30 req/min, ~1440 req/day) is generous enough
    that we don't need a quota-exhaustion latch like the Gemini client.
    A simple retry-with-exponential-backoff is sufficient.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.groq_api_key:
            raise ValueError(
                "groq_api_key is required to construct AsyncGroqClient. "
                "Set GROQ_API_KEY in your environment or .env file."
            )
        self._client = AsyncGroq(api_key=settings.groq_api_key)
        self._model = settings.groq_model
        self._retries = settings.groq_retries

    async def generate(self, prompt: str) -> str | None:
        """Generate text from a prompt with retries. Returns None on failure.

        Transient errors (429 rate limit, 5xx server, network) are
        retried with exponential backoff. After all retries are
        exhausted, returns ``None`` so the caller can use a fallback.
        """
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                if not response.choices:
                    logger.warning(
                        "Groq returned no choices (attempt %d)", attempt
                    )
                    return None
                text = response.choices[0].message.content
                if text:
                    return text.strip()
                logger.warning("Groq returned empty text (attempt %d)", attempt)
                return None
            except RateLimitError as exc:
                wait = 2 * attempt
                logger.warning(
                    "Groq rate limit (attempt %d/%d): %s -- sleeping %.1fs",
                    attempt,
                    self._retries,
                    str(exc)[:160],
                    wait,
                )
                if attempt < self._retries:
                    await asyncio.sleep(wait)
            except APIError as exc:
                status = getattr(exc, "status_code", None)
                wait = 2 * attempt
                logger.warning(
                    "Groq API error (attempt %d/%d, status=%s): %s "
                    "-- sleeping %.1fs",
                    attempt,
                    self._retries,
                    status,
                    str(exc)[:160],
                    wait,
                )
                if attempt < self._retries:
                    await asyncio.sleep(wait)
            except Exception:
                logger.exception("Unexpected Groq error (attempt %d)", attempt)
                if attempt < self._retries:
                    await asyncio.sleep(2 * attempt)
        return None

    async def generate_many(
        self, prompts: list[str], concurrency: int
    ) -> list[str | None]:
        """Generate text for multiple prompts concurrently, bounded by `concurrency`."""
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(prompt: str) -> str | None:
            async with semaphore:
                return await self.generate(prompt)

        return await asyncio.gather(*(_one(p) for p in prompts))

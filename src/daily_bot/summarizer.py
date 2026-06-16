"""AI summarization via Google Gemini (new google-genai SDK).

Refactored from the previous google.generativeai implementation:
- Uses the new google-genai SDK (with native async support via client.aio)
- AsyncGeminiClient for concurrent calls
- Configurable retries, model, chunk size
- Category allowlist validation
- Honors the API's `retryDelay` hint on 429 quota errors so we don't
  burn retries by sleeping too little (or waste time sleeping too much)
"""

from __future__ import annotations

import asyncio
import logging
import re

from google import genai as _genai
from google.genai import errors as _genai_errors

from .config import Settings
from .models import VALID_CATEGORIES, Summary

logger = logging.getLogger(__name__)

# Cap the wait time on 429 retries so a single bad day doesn't block
# the pipeline forever. The API sometimes suggests 60s+ for daily-quota
# exhaustion, which isn't worth waiting through.
_RETRY_DELAY_CAP_SECONDS = 65.0


def _parse_retry_delay_seconds(raw: str | int | float | None) -> float | None:
    """Parse a retryDelay value from a Gemini API error.

    The SDK returns the delay as either a string ending in ``s`` (e.g. ``"13s"``)
    or, in some edge cases, a bare number. Returns seconds (float) or None
    if the value can't be parsed.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s.endswith("s"):
        s = s[:-1].strip()
    try:
        return float(s)
    except ValueError:
        return None


def _extract_retry_delay(exc: _genai_errors.APIError) -> float | None:
    """Best-effort extraction of the API's suggested retry delay from an error.

    Path: ``exc.details["error"]["details"]`` is a list; one entry has
    ``"@type" == "type.googleapis.com/google.rpc.RetryInfo"`` with
    ``"retryDelay"`` like ``"13s"``.
    """
    try:
        details = exc.details
        if not isinstance(details, dict):
            return None
        err = details.get("error", {})
        if not isinstance(err, dict):
            return None
        for entry in err.get("details", []) or []:
            if not isinstance(entry, dict):
                continue
            if "RetryInfo" in str(entry.get("@type", "")):
                return _parse_retry_delay_seconds(entry.get("retryDelay"))
    except (AttributeError, TypeError, KeyError):
        return None
    return None


class AsyncGeminiClient:
    """Thin async wrapper around the new google-genai SDK with retries + backoff.

    Retries honor the API's own ``retryDelay`` hint on 429 quota errors
    (clamped to ``_RETRY_DELAY_CAP_SECONDS``) so we don't waste retries
    by sleeping too short, or stall the pipeline by waiting 60+ seconds
    on a daily-quota exhaustion that won't recover in time.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = _genai.Client(api_key=settings.google_api_key)
        self._model = settings.gemini_model
        self._retries = settings.gemini_retries

    async def generate(self, prompt: str) -> str | None:
        """Generate text from a prompt with retries. Returns None on failure."""
        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                )
                text = getattr(response, "text", None)
                if text:
                    return text.strip()
                logger.warning("Gemini returned no text (attempt %d)", attempt)
                return None
            except _genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                status = getattr(exc, "status", "")
                is_quota = (
                    code == 429
                    or "RESOURCE_EXHAUSTED" in str(status)
                    or "QUOTA" in str(status).upper()
                )
                api_delay = _extract_retry_delay(exc) if is_quota else None
                fallback_delay = 2 * attempt
                wait = api_delay if api_delay is not None else fallback_delay
                wait = min(wait, _RETRY_DELAY_CAP_SECONDS)
                logger.warning(
                    "Gemini API error (attempt %d/%d, code=%s, status=%s): %s "
                    "-- sleeping %.1fs before retry",
                    attempt,
                    self._retries,
                    code,
                    status,
                    str(exc)[:160],
                    wait,
                )
                if attempt < self._retries:
                    await asyncio.sleep(wait)
            except Exception:
                logger.exception("Unexpected Gemini error (attempt %d)", attempt)
                if attempt < self._retries:
                    await asyncio.sleep(2 * attempt)
        return None

    async def generate_many(self, prompts: list[str], concurrency: int) -> list[str | None]:
        """Generate text for multiple prompts concurrently, bounded by `concurrency`."""
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(prompt: str) -> str | None:
            async with semaphore:
                return await self.generate(prompt)

        return await asyncio.gather(*(_one(p) for p in prompts))


def clean_article_text(text: str) -> str:
    """Remove junk, timestamps, boilerplate, duplicates."""
    lines = text.split("\n")
    cleaned: list[str] = []

    for line in lines:
        ln = line.strip()
        if not ln:
            continue
        if re.match(r"^\d{1,2}:\d{2}(\s*(GMT|BST))?$", ln):
            continue
        if "Follow BBC" in ln or "Related Topics" in ln:
            continue
        cleaned.append(ln)

    final = list(dict.fromkeys(cleaned))
    return "\n".join(final)


def chunk_text(text: str, max_words: int) -> list[str]:
    """Split text into safe word-bounded chunks for the LLM."""
    words = text.split()
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def _language_prefix(language: str) -> str:
    """Return an instruction prefix that asks Gemini to write in the given language.

    Returns an empty string for the default English case so existing behavior
    is preserved.
    """
    if language == "pt-BR":
        return "IMPORTANTE: Escreva o resumo inteiramente em português do Brasil.\n\n"
    return ""


def _build_chunk_prompt(idx: int, chunk: str, language: str = "en") -> str:
    prefix = _language_prefix(language)
    return f"""
{prefix}You are a professional BBC-style news summarizer.

Summarize the following portion of a BBC News article
in a neutral, objective newsroom tone in about 80-120 words.

PORTION {idx}:
{chunk}
"""


def _build_final_prompt(title: str, combined: str, language: str = "en") -> str:
    prefix = _language_prefix(language)
    return f"""
{prefix}You are a professional BBC-style news summarizer.

You are given several partial summaries of a BBC News article.
Using them, write a single coherent summary of the entire article
in about 120-180 words, in a neutral, factual, newsroom tone.

TITLE:
{title}

PARTIAL SUMMARIES:
{combined}

Return only the final summary text, with no headings or bullet points.
"""


def _build_fallback_prompt(title: str, cleaned: str, language: str = "en") -> str:
    prefix = _language_prefix(language)
    return f"""
{prefix}You are a professional BBC-style news summarizer.

Summarize the following BBC News article in a neutral, objective newsroom tone
in about 120-180 words. Focus on key facts, context, and major developments.
Avoid commentary, opinion, or meta text.

TITLE:
{title}

ARTICLE:
{cleaned}
"""


def _build_category_prompt(title: str, summary_text: str) -> str:
    return f"""
Classify this BBC News article into one of:
politics, world, business, tech, science, health, uk, europe, other.

Title: {title}
Summary: {summary_text}

Return ONLY the single category word.
"""


def _validate_category(raw: str | None) -> str:
    """Return the first valid category word, or 'other' if invalid/missing."""
    if not raw:
        return "other"
    first = raw.strip().split()[0].lower()
    return first if first in VALID_CATEGORIES else "other"


async def summarize_article(
    client: AsyncGeminiClient,
    article_text: str,
    title: str,
    settings: Settings,
    language: str = "en",
) -> Summary:
    """Summarize an article using chunk-merge strategy with a fallback path.

    All Gemini calls in the chunk phase are issued concurrently.
    """
    cleaned = clean_article_text(article_text or "")
    if len(cleaned.split()) < settings.summary_min_words:
        cleaned += "\n(Note: Article text is short; summary may be limited.)"

    chunks = chunk_text(cleaned, settings.chunk_max_words)

    if chunks:
        prompts = [_build_chunk_prompt(i, c, language) for i, c in enumerate(chunks, start=1)]
        partials = await client.generate_many(prompts, settings.summarize_concurrency)
        partial_summaries = [p for p in partials if p]
    else:
        partial_summaries = []

    if not partial_summaries:
        fallback = await client.generate(_build_fallback_prompt(title, cleaned, language))
        summary_text = fallback or "Summary generation failed."
    else:
        combined = "\n\n".join(partial_summaries)
        final = await client.generate(_build_final_prompt(title, combined, language))
        summary_text = final or "Summary generation failed."

    category_raw = await client.generate(_build_category_prompt(title, summary_text))
    category = _validate_category(category_raw)

    return Summary(title=title, summary=summary_text, category=category)

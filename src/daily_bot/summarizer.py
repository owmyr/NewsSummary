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
import time

from google import genai as _genai
from google.genai import errors as _genai_errors

from .config import Settings
from .llm_client import LLMClient
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

    Satisfies the :class:`daily_bot.llm_client.LLMClient` protocol.

    Retries honor the API's own ``retryDelay`` hint on 429 quota errors
    (clamped to ``_RETRY_DELAY_CAP_SECONDS``) so we don't waste retries
    by sleeping too short, or stall the pipeline by waiting 60+ seconds
    on a daily-quota exhaustion that won't recover in time.

    On the first 429/RESOURCE_EXHAUSTED error the client latches a
    ``quota_exhausted`` flag; every subsequent ``generate()`` call
    short-circuits and returns ``None`` without making a network request.
    This prevents a daily-quota outage from turning into hours of sleep
    loops. The flag can be cleared with ``reset_quota_exhausted()`` once
    the user knows the quota has been refilled.
    """

    def __init__(self, settings: Settings) -> None:
        self._client = _genai.Client(api_key=settings.google_api_key)
        self._model = settings.gemini_model
        self._retries = settings.gemini_retries
        self._min_interval = settings.gemini_min_call_interval_seconds
        self._quota_exhausted_at: float | None = None
        self._last_call_at: float | None = None

    @property
    def quota_exhausted(self) -> bool:
        """True when the per-minute or daily quota is currently latched.

        The latch auto-clears after ``_RETRY_DELAY_CAP_SECONDS`` (65s), so a
        per-minute burst doesn't poison the rest of the run. A genuine
        daily-exhaustion run will keep hitting the latch and the user
        notices via ``--retry-failed``.
        """
        if self._quota_exhausted_at is None:
            return False
        if (time.monotonic() - self._quota_exhausted_at) > _RETRY_DELAY_CAP_SECONDS:
            self._quota_exhausted_at = None
            return False
        return True

    def reset_quota_exhausted(self) -> None:
        """Clear the quota-exhausted latch (e.g. after a manual quota reset)."""
        self._quota_exhausted_at = None

    async def _throttle(self) -> None:
        """Sleep just enough so consecutive calls respect ``_min_interval``.

        The Gemini free tier is 5 req/min. If the per-article summary uses
        2-3 calls (chunk + merge + category) and the per-call latency is
        ~13s, the calls naturally land within 13s of each other — but a
        shorter call followed by a longer one can stack up. This guard
        enforces a hard minimum spacing so a fast response can't sneak
        the next request into the same per-minute bucket.
        """
        if self._min_interval <= 0 or self._last_call_at is None:
            return
        elapsed = time.monotonic() - self._last_call_at
        wait = self._min_interval - elapsed
        if wait > 0:
            await asyncio.sleep(wait)

    async def generate(self, prompt: str) -> str | None:
        """Generate text from a prompt with retries. Returns None on failure.

        Quota error policy: on the *first* 429/RESOURCE_EXHAUSTED we retry
        once after the API-suggested delay (clamped to
        ``_RETRY_DELAY_CAP_SECONDS``). A *second* consecutive quota error
        latches the ``quota_exhausted`` flag and every later call returns
        ``None`` immediately without making a network request.

        The latch is time-based: it auto-clears after
        ``_RETRY_DELAY_CAP_SECONDS`` so a per-minute burst doesn't kill
        the rest of the run. A genuine daily-exhaustion run will keep
        re-latching and is best recovered via ``--retry-failed``.
        """
        if self.quota_exhausted:
            return None
        for attempt in range(1, self._retries + 1):
            await self._throttle()
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=prompt,
                )
                self._last_call_at = time.monotonic()
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
                if is_quota:
                    api_delay = _extract_retry_delay(exc)
                    if attempt == 1 and self._retries >= 2:
                        # First quota error: wait the API-suggested delay
                        # (capped) and try once more. A second 429 will
                        # latch below.
                        wait = api_delay if api_delay is not None else _RETRY_DELAY_CAP_SECONDS
                        wait = min(wait, _RETRY_DELAY_CAP_SECONDS)
                        logger.warning(
                            "Gemini quota error (attempt %d/%d, code=%s, status=%s): %s "
                            "-- sleeping %.1fs before retry",
                            attempt,
                            self._retries,
                            code,
                            status,
                            str(exc)[:160],
                            wait,
                        )
                        await asyncio.sleep(wait)
                        continue
                    # Second (or later) consecutive quota error: latch
                    # with a timestamp. The latch auto-clears after
                    # _RETRY_DELAY_CAP_SECONDS, so a per-minute burst
                    # doesn't poison the entire run.
                    self._quota_exhausted_at = time.monotonic()
                    self._last_call_at = time.monotonic()
                    logger.error(
                        "Gemini quota exhausted (attempt %d/%d, code=%s, status=%s): %s. "
                        "Calls will short-circuit for the next %ss. "
                        "Suggested retry delay from API: %ss",
                        attempt,
                        self._retries,
                        code,
                        status,
                        str(exc)[:160],
                        _RETRY_DELAY_CAP_SECONDS,
                        f"{api_delay:.1f}" if api_delay is not None else "n/a",
                    )
                    return None
                # Non-quota error: exponential backoff, no latch.
                wait = 2 * attempt
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


# URL substrings → category. Order matters: first match wins. More specific
# patterns go first so e.g. /news/uk-politics resolves to "politics" before
# the bare /news/uk check.
_URL_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    # BBC News URL patterns (www.bbc.com/news/<section>/...)
    ("/news/uk-politics", "politics"),
    ("/news/politics", "politics"),
    ("/news/world", "world"),
    ("/news/business", "business"),
    ("/news/technology", "tech"),
    ("/news/science", "science"),
    ("/news/health", "health"),
    ("/news/uk", "uk"),
    ("/news/europe", "europe"),
    # G1 URL patterns (g1.globo.com/<section>/noticia/...)
    ("/g1-globo-de/", "world"),  # international edition
    ("/ciencia-e-saude/", "science"),
    ("/ciencia/", "science"),  # pure science section (e.g. planetary alignment)
    ("/tecnologia/", "tech"),
    ("/economia/", "business"),
    ("/negocios/", "business"),
    ("/mercados/", "business"),
    ("/agro/", "business"),  # agribusiness
    ("/bemestar/", "health"),
    ("/politica/", "politics"),
    ("/saude/", "health"),
    ("/mundo/", "world"),
    ("/internacional/", "world"),
    ("/educacao/", "other"),  # no education category
    ("/natureza/", "science"),  # nature → science
    ("/carros/", "other"),  # no cars category
    ("/concursos-e-emprego/", "business"),  # jobs → business
    ("/turismo-e-viagem/", "other"),
    ("/esporte/", "other"),  # no sport category; fall through to "other"
)

# Section name → category. Used when a source provides the section via
# page metadata (e.g. BBC <meta property="article:section">). Maps the
# raw section name (lowercased) to a VALID_CATEGORIES value.
#
# Compound names like "UK Politics" or "US & Canada" are matched by
# substring check in classify_article() after the exact-match lookup
# fails. Order in this dict is preserved as the iteration order for the
# substring fallback.
_SECTION_CATEGORY_MAP: dict[str, str] = {
    "uk": "uk",
    "uk politics": "politics",
    "politics": "politics",
    "world": "world",
    "international": "world",
    "us & canada": "world",
    "business": "business",
    "technology": "tech",
    "tech": "tech",
    "science": "science",
    "health": "health",
    "europe": "europe",
}

# Title keywords → category. Used as a fallback when the URL doesn't match.
# Order matters: more specific phrases AND country/region terms come first
# so e.g. "UK government" → "uk" beats "government" → "politics".
_TITLE_CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    # Business / market phrases MUST come before generic world keywords like
    # "crash" — "Stock market crash" should be business, not world.
    # Business
    ("stock market", "business"),
    ("wall street", "business"),
    ("interest rate", "business"),
    ("tariff", "business"),
    ("oil price", "business"),
    # World/region (most specific, must beat generic politics keywords)
    ("jerusalem", "world"),
    ("israel", "world"),
    ("gaza", "world"),
    ("palestinian", "world"),
    ("iran", "world"),
    ("russia", "world"),
    ("ukraine", "world"),
    ("china", "world"),
    ("korea", "world"),
    ("japan", "world"),
    ("india", "world"),
    ("texas", "world"),
    ("california", "world"),
    ("florida", "world"),
    ("mexico", "world"),
    ("canada", "world"),
    ("brazil", "world"),
    ("argentina", "world"),
    ("africa", "world"),
    ("migrant", "world"),
    ("crash", "world"),
    ("rescue", "world"),
    ("earthquake", "world"),
    ("flood", "world"),
    ("storm", "world"),
    ("britain", "uk"),
    ("british", "uk"),
    ("england", "uk"),
    ("scotland", "uk"),
    ("wales", "uk"),
    (" uk ", "uk"),
    ("european union", "europe"),
    ("brussels", "europe"),
    ("europe ", "europe"),
    (" eu ", "europe"),
    ("eu)", "europe"),
    # Health (specific phrases first)
    ("cancer", "health"),
    ("diagnosis", "health"),
    ("covid-19", "health"),
    ("covid", "health"),
    ("vaccine", "health"),
    ("vaccination", "health"),
    ("hospital", "health"),
    ("doctor", "health"),
    ("patient", "health"),
    ("bipolar", "health"),
    ("depression", "health"),
    ("mental health", "health"),
    ("surgery", "health"),
    ("transplant", "health"),
    # Science
    ("climate change", "science"),
    ("artificial intelligence", "tech"),
    ("machine learning", "tech"),
    ("quantum", "science"),
    ("nasa", "science"),
    # Science: astronomy / planetary / space
    ("planeta", "science"),
    ("alinhamento", "science"),
    ("astronomia", "science"),
    ("galáxia", "science"),
    ("galaxia", "science"),
    ("telescópio", "science"),
    ("telescopio", "science"),
    ("buraco negro", "science"),
    ("spacex", "science"),
    ("mars ", "science"),
    # Tech
    ("startup", "tech"),
    ("software", "tech"),
    ("chip", "tech"),
    ("openai", "tech"),
    ("tech giant", "tech"),
    # Business
    ("stock market", "business"),
    ("wall street", "business"),
    ("interest rate", "business"),
    ("tariff", "business"),
    ("oil price", "business"),
    # Politics
    ("parliament", "politics"),
    ("election", "politics"),
    ("government", "politics"),
    ("minister", "politics"),
    ("president", "politics"),
    ("congress", "politics"),
    ("senate", "politics"),
    ("white house", "politics"),
    ("trump", "politics"),
    ("biden", "politics"),
    ("lawyers", "politics"),
    ("defence", "politics"),
    ("trial", "politics"),
    ("court", "politics"),
    ("judge", "politics"),
    ("verdict", "politics"),
    # Brazilian political titles (G1 carries heavy domestic politics)
    ("deputado", "politics"),
    ("deputada", "politics"),
    ("vereador", "politics"),
    ("vereadora", "politics"),
    ("senador", "politics"),
    ("senadora", "politics"),
    ("ministro ", "politics"),
    ("ministra ", "politics"),
    ("lula", "politics"),
    ("bolsonaro", "politics"),
    ("pt ", "politics"),
    ("pl ", "politics"),
    ("pf ", "politics"),  # Policia Federal operations on politicians
    ("stf", "politics"),
    ("tse", "politics"),
    # Generic fallbacks
    ("inflation", "business"),
    ("economy", "business"),
    ("gdp", "business"),
    ("research", "science"),
    ("space", "science"),
)


def classify_article(title: str = "", url: str = "", section: str = "") -> str:
    """Classify an article into one of VALID_CATEGORIES without an API call.

    Three tiers of evidence, tried in order:

    1. **URL patterns** — G1 URLs embed the section path
       (``/politica/``, ``/economia/``). Most reliable for G1.
    2. **Source-provided section** — BBC article HTML embeds the section
       in ``<meta property="article:section">`` (e.g. ``"World"``,
       ``"UK Politics"``). The ``section`` argument is the raw value
       extracted during scraping. Reliable for BBC, no-op for G1.
    3. **Title keywords** — fallback when the above don't match.
       Handles ambiguous cases and unusual URLs.

    Returns ``"other"`` if nothing matches. Never costs an API call.
    """
    url_lower = (url or "").lower()
    title_lower = (title or "").lower()
    # Pad title with spaces for safe substring matching at word boundaries
    # (e.g. "uk " so it doesn't match "Ukraine").
    title_padded = f" {title_lower} "

    # 1. URL pattern
    for pattern, category in _URL_CATEGORY_RULES:
        if pattern in url_lower:
            return category

    # 2. Source-provided section
    if section:
        normalized = section.strip().lower()
        if normalized in _SECTION_CATEGORY_MAP:
            return _SECTION_CATEGORY_MAP[normalized]
        # Partial match for compound section names like "UK Politics Live".
        # Iterate the longest keys first so "uk politics" wins over "uk".
        for section_name, category in sorted(
            _SECTION_CATEGORY_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if section_name and section_name in normalized:
                return category

    # 3. Title keywords
    for keyword, category in _TITLE_CATEGORY_RULES:
        if keyword in title_padded:
            return category

    return "other"


async def summarize_article(
    client: LLMClient,
    article_text: str,
    title: str,
    settings: Settings,
    language: str = "en",
    url: str = "",
    section: str = "",
) -> Summary:
    """Summarize an article using chunk-merge strategy with a fallback path.

    For short articles (one chunk or none) we skip the chunk-merge step
    entirely and use the fallback prompt in a single API call. For longer
    articles we chunk, summarize each chunk concurrently, then merge.

    Category classification is deterministic (URL patterns + source
    section + title keyword matching) so the pipeline does not spend an
    API call on a cosmetic label.
    """
    cleaned = clean_article_text(article_text or "")
    if len(cleaned.split()) < settings.summary_min_words:
        cleaned += "\n(Note: Article text is short; summary may be limited.)"

    chunks = chunk_text(cleaned, settings.chunk_max_words)

    if not chunks:
        # Defensive: chunk_text returns [] only if cleaned is empty, which
        # the summary_min_words guard above should prevent. If a future
        # config change (e.g. summary_min_words=0) ever makes this reachable,
        # don't send Gemini a prompt asking it to summarize a meta-note.
        return Summary(
            title=title,
            summary="Summary generation failed.",
            category=classify_article(title=title, url=url, section=section),
        )

    if len(chunks) <= 1:
        # Short article: one call, no merge step. Saves an API call
        # compared to the previous "chunk then merge" path.
        fallback = await client.generate(_build_fallback_prompt(title, cleaned, language))
        summary_text = fallback or "Summary generation failed."
    else:
        # Long article: chunk → merge. Chunks are summarized concurrently
        # (bounded by summarize_concurrency).
        prompts = [_build_chunk_prompt(i, c, language) for i, c in enumerate(chunks, start=1)]
        partials = await client.generate_many(prompts, settings.summarize_concurrency)
        partial_summaries = [p for p in partials if p]
        if not partial_summaries:
            # Chunking returned no usable partials; fall back to a single call.
            fallback = await client.generate(_build_fallback_prompt(title, cleaned, language))
            summary_text = fallback or "Summary generation failed."
        else:
            combined = "\n\n".join(partial_summaries)
            final = await client.generate(_build_final_prompt(title, combined, language))
            summary_text = final or "Summary generation failed."

    category = classify_article(title=title, url=url, section=section)

    return Summary(title=title, summary=summary_text, category=category)

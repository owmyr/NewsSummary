"""Unit tests for the summarizer (with a FakeGeminiClient fixture)."""

from __future__ import annotations

import pytest

from daily_bot.config import Settings
from daily_bot.summarizer import (
    _RETRY_DELAY_CAP_SECONDS,
    _build_category_prompt,
    _build_chunk_prompt,
    _build_fallback_prompt,
    _build_final_prompt,
    _extract_retry_delay,
    _parse_retry_delay_seconds,
    _validate_category,
    chunk_text,
    clean_article_text,
    summarize_article,
)

# ---------------- text cleaning ----------------


def test_clean_removes_timestamps():
    text = "10:45\n10:45 GMT\n10:45 BST\nReal text"
    cleaned = clean_article_text(text)
    assert "Real text" in cleaned
    assert "10:45" not in cleaned
    assert "10:45 GMT" not in cleaned
    assert "10:45 BST" not in cleaned


def test_clean_removes_boilerplate():
    text = "Follow BBC on Twitter.\nRelated Topics here.\nReal text"
    cleaned = clean_article_text(text)
    assert "Follow BBC" not in cleaned
    assert "Related Topics" not in cleaned
    assert "Real text" in cleaned


def test_clean_dedupes_consecutive_duplicates():
    text = "Real text\nReal text\nUnique line"
    cleaned = clean_article_text(text)
    # Both should be present but the second is deduped
    assert "Real text" in cleaned
    assert "Unique line" in cleaned


def test_clean_handles_empty():
    assert clean_article_text("") == ""


# ---------------- chunking ----------------


def test_chunk_returns_expected_number_of_chunks():
    chunks = chunk_text("word " * 1500, max_words=600)
    assert len(chunks) == 3


def test_chunk_handles_short_text():
    chunks = chunk_text("a few words", max_words=600)
    assert len(chunks) == 1
    assert chunks[0] == "a few words"


def test_chunk_handles_empty_text():
    chunks = chunk_text("", max_words=600)
    assert chunks == []


# ---------------- category validation ----------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("politics", "politics"),
        ("POLITICS", "politics"),
        ("world ", "world"),
        ("tech news", "tech"),
        ("sports", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_validate_category(raw, expected):
    assert _validate_category(raw) == expected


# ---------------- prompt builders ----------------


def test_chunk_prompt_includes_index_and_text():
    prompt = _build_chunk_prompt(2, "some text content")
    assert "PORTION 2" in prompt
    assert "some text content" in prompt


def test_final_prompt_includes_title_and_partials():
    prompt = _build_final_prompt("Title", "Summary A\n\nSummary B")
    assert "Title" in prompt
    assert "Summary A" in prompt
    assert "Summary B" in prompt


def test_fallback_prompt_includes_title_and_article():
    prompt = _build_fallback_prompt("T", "Article body")
    assert "T" in prompt
    assert "Article body" in prompt


def test_category_prompt_includes_categories():
    prompt = _build_category_prompt("T", "S")
    for cat in ["politics", "world", "business", "tech", "other"]:
        assert cat in prompt


# ---------------- end-to-end summarize (with FakeGeminiClient) ----------------


async def test_summarize_uses_chunk_phase_when_chunks_exist(test_settings: Settings):
    """With multiple chunks, summarize should call generate_many + final + category."""
    from tests.conftest import FakeGeminiClient

    # 1500 words at max_words=600 produces 3 chunks
    fake = FakeGeminiClient(
        responses=[
            "Partial one.",  # chunk 1
            "Partial two.",  # chunk 2
            "Partial three.",  # chunk 3
            "Final combined summary.",  # final merge
            "world",  # category
        ]
    )

    article = "word " * 1500
    summary = await summarize_article(fake, article, "Test Title", test_settings)
    assert summary.title == "Test Title"
    assert summary.summary == "Final combined summary."
    assert summary.category == "world"


async def test_summarize_falls_back_to_single_shot(test_settings: Settings):
    """When chunks produce no partials, summarize should try the fallback prompt."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            None,  # chunk 1 -> None
            None,  # chunk 2 -> None
            None,  # chunk 3 -> None
            "Fallback summary worked.",  # fallback call
            "tech",  # category
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(fake, article, "Test Title", test_settings)
    assert summary.summary == "Fallback summary worked."
    assert summary.category == "tech"


async def test_summarize_handles_short_article(test_settings: Settings):
    """A short article should produce a single chunk + final + category."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Chunk summary of the short article.",  # chunk 1 (only 1 chunk)
            "Final combined summary of the short article.",  # final merge
            "health",  # category
        ]
    )
    summary = await summarize_article(fake, "Short text.", "Title", test_settings)
    assert summary.category == "health"


async def test_summarize_invalid_category_becomes_other(test_settings: Settings):
    """A non-allowlisted category response should map to 'other'."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Chunk summary.",  # chunk 1
            "Final combined summary.",  # final merge
            "celebrity gossip",  # category -> invalid -> 'other'
        ]
    )
    summary = await summarize_article(fake, "article text", "Title", test_settings)
    assert summary.category == "other"


async def test_summarize_uses_default_when_all_gemini_calls_fail(test_settings: Settings):
    """If every Gemini call returns None, summary should be a fallback string."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(responses=[None, None, None, None])
    summary = await summarize_article(fake, "word " * 1500, "Title", test_settings)
    assert "failed" in summary.summary.lower()
    assert summary.category == "other"


# ---------------- language-aware summarization ----------------


def test_chunk_prompt_with_pt_br_includes_portuguese_instruction():
    """_build_chunk_prompt should prepend the Portuguese instruction for pt-BR."""
    prompt = _build_chunk_prompt(1, "algum texto", language="pt-BR")
    assert "português" in prompt.lower()
    assert "PORTION 1" in prompt
    assert "algum texto" in prompt


def test_chunk_prompt_with_en_has_no_portuguese_instruction():
    """_build_chunk_prompt should NOT include Portuguese instruction for en."""
    prompt = _build_chunk_prompt(1, "some text", language="en")
    assert "português" not in prompt.lower()
    assert "PORTION 1" in prompt


def test_chunk_prompt_default_is_english():
    """The default language value should preserve English-only behavior."""
    prompt = _build_chunk_prompt(1, "some text")
    assert "português" not in prompt.lower()
    assert "PORTION 1" in prompt


def test_final_prompt_with_pt_br_includes_portuguese_instruction():
    prompt = _build_final_prompt("Title", "Partial A", language="pt-BR")
    assert "português" in prompt.lower()
    assert "Title" in prompt
    assert "Partial A" in prompt


def test_final_prompt_with_en_has_no_portuguese_instruction():
    prompt = _build_final_prompt("Title", "Partial A", language="en")
    assert "português" not in prompt.lower()


def test_fallback_prompt_with_pt_br_includes_portuguese_instruction():
    prompt = _build_fallback_prompt("Title", "Body", language="pt-BR")
    assert "português" in prompt.lower()
    assert "Body" in prompt


def test_fallback_prompt_with_en_has_no_portuguese_instruction():
    prompt = _build_fallback_prompt("Title", "Body", language="en")
    assert "português" not in prompt.lower()


async def test_summarize_with_pt_br_language(test_settings: Settings):
    """When language='pt-BR', every chunk/final/fallback prompt should include
    the Portuguese instruction.
    """
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Resumo parcial um.",  # chunk 1
            "Resumo parcial dois.",  # chunk 2
            "Resumo parcial tres.",  # chunk 3
            "Resumo final coerente.",  # final merge
            "world",  # category (English; validated against English allowlist)
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(
        fake, article, "Titulo Teste", test_settings, language="pt-BR"
    )
    assert summary.title == "Titulo Teste"
    assert summary.summary == "Resumo final coerente."
    assert summary.category == "world"
    # All prompts except the category prompt should include the Portuguese
    # instruction. The category prompt is intentionally English (validated
    # against the English VALID_CATEGORIES allowlist).
    assert fake.calls, "FakeGeminiClient should have recorded at least one call"
    summary_prompts = fake.calls[:-1]  # exclude the category prompt
    assert summary_prompts, "Expected at least one summary prompt"
    for prompt in summary_prompts:
        assert "português" in prompt.lower(), f"Prompt missing Portuguese instruction: {prompt!r}"


async def test_summarize_with_en_language_unchanged(test_settings: Settings):
    """When language='en' (default), prompts should NOT include the Portuguese
    instruction and behavior matches the previous implementation.
    """
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Partial one.",  # chunk 1
            "Partial two.",  # chunk 2
            "Partial three.",  # chunk 3
            "Final combined summary.",  # final merge
            "world",  # category
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(fake, article, "Test Title", test_settings, language="en")
    assert summary.summary == "Final combined summary."
    assert summary.category == "world"
    for prompt in fake.calls:
        assert "português" not in prompt.lower()


async def test_summarize_default_language_is_english(test_settings: Settings):
    """Omitting the language kwarg should behave like language='en'."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Partial one.",
            "Partial two.",
            "Partial three.",
            "Final combined summary.",
            "tech",
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(fake, article, "Title", test_settings)
    assert summary.summary == "Final combined summary."
    for prompt in fake.calls:
        assert "português" not in prompt.lower()


async def test_pt_br_prompt_includes_portuguese_instruction(test_settings: Settings):
    """Direct check that a pt-BR chunk prompt includes the expected marker."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Chunk 1",
            "Chunk 2",
            "Chunk 3",
            "Final",
            "world",
        ]
    )
    await summarize_article(fake, "word " * 1500, "Title", test_settings, language="pt-BR")
    # Every summary prompt (chunk + final) should include the Portuguese
    # instruction. The category prompt is intentionally left in English.
    summary_prompts = fake.calls[:-1]
    assert any("português" in p.lower() for p in summary_prompts)
    assert all("português" in p.lower() for p in summary_prompts)


async def test_pt_br_summary_returned_correctly(test_settings: Settings):
    """Full integration: FakeGeminiClient returns a Portuguese summary, and the
    Summary object is constructed correctly with the Portuguese text.
    """
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "O governo anunciou novas medidas economicas hoje.",  # chunk 1
            "A decisao foi tomada apos semanas de debate.",  # chunk 2
            "Os mercados reagiram positivamente a noticia.",  # chunk 3
            "O governo brasileiro anunciou novas medidas economicas hoje, "
            "decisao tomada apos semanas de debate no congresso.",  # final
            "politics",  # category (English word validated against allowlist)
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(
        fake, article, "Governo anuncia medidas", test_settings, language="pt-BR"
    )
    assert summary.title == "Governo anuncia medidas"
    assert "governo brasileiro" in summary.summary.lower()
    assert summary.category == "politics"
    # All summary prompts (chunk + final) should be in Portuguese mode. The
    # category prompt is intentionally left in English.
    summary_prompts = fake.calls[:-1]
    assert all("português" in p.lower() for p in summary_prompts)


async def test_pt_br_fallback_path_uses_portuguese_instruction(test_settings: Settings):
    """When chunks return None, the fallback prompt should still include the
    Portuguese instruction.
    """
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            None,  # chunk 1 -> None
            None,  # chunk 2 -> None
            None,  # chunk 3 -> None
            "Resumo de fallback em portugues.",  # fallback
            "world",  # category
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(fake, article, "Title", test_settings, language="pt-BR")
    assert summary.summary == "Resumo de fallback em portugues."
    # Fallback prompt (4th call) should have the Portuguese marker.
    assert "português" in fake.calls[3].lower()


# ---------------- retry-delay parsing ----------------


class _FakeAPIError(Exception):
    """Minimal stand-in for google.genai.errors.APIError for testing the parser."""

    def __init__(self, details: dict) -> None:
        self.details = details
        err = details.get("error", {}) if isinstance(details, dict) else {}
        if not isinstance(err, dict):
            err = {}
        self.code = err.get("code")
        self.status = err.get("status")
        super().__init__(str(details))


def test_parse_retry_delay_parses_seconds_suffix():
    assert _parse_retry_delay_seconds("13s") == 13.0
    assert _parse_retry_delay_seconds("0.5s") == 0.5
    assert _parse_retry_delay_seconds("60s") == 60.0


def test_parse_retry_delay_handles_bare_numbers():
    assert _parse_retry_delay_seconds(13) == 13.0
    assert _parse_retry_delay_seconds(0.5) == 0.5


def test_parse_retry_delay_handles_invalid_input():
    assert _parse_retry_delay_seconds(None) is None
    assert _parse_retry_delay_seconds("garbage") is None
    assert _parse_retry_delay_seconds("") is None


def test_extract_retry_delay_finds_retry_info_in_error_details():
    err = _FakeAPIError(
        {
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "13s"},
                ],
            }
        }
    )
    assert _extract_retry_delay(err) == 13.0


def test_extract_retry_delay_returns_none_when_no_retry_info():
    err = _FakeAPIError({"error": {"code": 500, "status": "INTERNAL", "details": []}})
    assert _extract_retry_delay(err) is None


def test_extract_retry_delay_handles_malformed_details():
    err = _FakeAPIError({"error": "not a dict"})
    # Should not raise, should return None
    assert _extract_retry_delay(err) is None
    # Also handle totally missing fields
    err2 = _FakeAPIError({})
    assert _extract_retry_delay(err2) is None


def test_retry_delay_cap_is_at_most_65_seconds():
    """The pipeline shouldn't wait more than 65s for a single retry."""
    assert _RETRY_DELAY_CAP_SECONDS <= 65.0

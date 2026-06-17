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
    classify_article,
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
    """With multiple chunks, summarize should call generate_many + final (no category call)."""
    from tests.conftest import FakeGeminiClient

    # 1500 words at max_words=600 produces 3 chunks
    fake = FakeGeminiClient(
        responses=[
            "Partial one.",  # chunk 1
            "Partial two.",  # chunk 2
            "Partial three.",  # chunk 3
            "Final combined summary.",  # final merge
        ]
    )

    article = "word " * 1500
    summary = await summarize_article(
        fake,
        article,
        "Test Title",
        test_settings,
        url="https://www.bbc.com/news/world/articles/abc",
    )
    assert summary.title == "Test Title"
    assert summary.summary == "Final combined summary."
    # Category now comes from the URL, not from Gemini.
    assert summary.category == "world"
    # 3 chunk calls + 1 final = 4 calls; no category call.
    assert len(fake.calls) == 4


async def test_summarize_falls_back_to_single_shot(test_settings: Settings):
    """When chunks produce no partials, summarize should try the fallback prompt."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            None,  # chunk 1 -> None
            None,  # chunk 2 -> None
            None,  # chunk 3 -> None
            "Fallback summary worked.",  # fallback call
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(
        fake,
        article,
        "Test Title",
        test_settings,
        url="https://www.bbc.com/news/technology/articles/abc",
    )
    assert summary.summary == "Fallback summary worked."
    # Category comes from URL (no Gemini call made for it).
    assert summary.category == "tech"


async def test_summarize_short_article_uses_single_call(test_settings: Settings):
    """A short article (<= 1 chunk) should use exactly one API call (the fallback)."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(responses=["Summary from fallback."])
    summary = await summarize_article(
        fake,
        "Short text.",
        "Title",
        test_settings,
        url="https://www.bbc.com/news/health/articles/abc",
    )
    assert summary.summary == "Summary from fallback."
    assert summary.category == "health"
    # Short article = exactly 1 API call (no chunk, no final, no category call).
    assert len(fake.calls) == 1


async def test_summarize_invalid_category_becomes_other(test_settings: Settings):
    """No URL + no keyword-matching title -> category defaults to 'other'."""
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(responses=["Summary."])
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
    the Portuguese instruction. Category is now deterministic (URL-based).
    """
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Resumo parcial um.",  # chunk 1
            "Resumo parcial dois.",  # chunk 2
            "Resumo parcial tres.",  # chunk 3
            "Resumo final coerente.",  # final merge
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(
        fake,
        article,
        "Titulo Teste",
        test_settings,
        language="pt-BR",
        url="https://g1.globo.com/politica/noticia/2026/06/16/artigo.ghtml",
    )
    assert summary.title == "Titulo Teste"
    assert summary.summary == "Resumo final coerente."
    assert summary.category == "politics"
    # All Gemini calls are summary prompts (chunks + final). No category call
    # is made anymore. Every summary prompt should include the Portuguese
    # instruction.
    assert fake.calls, "FakeGeminiClient should have recorded at least one call"
    for prompt in fake.calls:
        assert "português" in prompt.lower(), f"Prompt missing Portuguese instruction: {prompt!r}"


async def test_summarize_with_en_language_unchanged(test_settings: Settings):
    """When language='en' (default), prompts should NOT include the Portuguese
    instruction. Category is deterministic (URL-based), not from Gemini.
    """
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "Partial one.",  # chunk 1
            "Partial two.",  # chunk 2
            "Partial three.",  # chunk 3
            "Final combined summary.",  # final merge
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(
        fake,
        article,
        "Test Title",
        test_settings,
        language="en",
        url="https://www.bbc.com/news/world/articles/abc",
    )
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
        ]
    )
    await summarize_article(fake, "word " * 1500, "Title", test_settings, language="pt-BR")
    # Every prompt (chunk + final) is now a summary prompt; no category call.
    assert any("português" in p.lower() for p in fake.calls)
    assert all("português" in p.lower() for p in fake.calls)


async def test_pt_br_summary_returned_correctly(test_settings: Settings):
    """Full integration: FakeGeminiClient returns a Portuguese summary, and the
    Summary object is constructed correctly with the Portuguese text.
    Category comes from the URL (deterministic), not from Gemini.
    """
    from tests.conftest import FakeGeminiClient

    fake = FakeGeminiClient(
        responses=[
            "O governo anunciou novas medidas economicas hoje.",  # chunk 1
            "A decisao foi tomada apos semanas de debate.",  # chunk 2
            "Os mercados reagiram positivamente a noticia.",  # chunk 3
            "O governo brasileiro anunciou novas medidas economicas hoje, "
            "decisao tomada apos semanas de debate no congresso.",  # final
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(
        fake,
        article,
        "Governo anuncia medidas",
        test_settings,
        language="pt-BR",
        url="https://g1.globo.com/politica/noticia/2026/06/16/artigo.ghtml",
    )
    assert summary.title == "Governo anuncia medidas"
    assert "governo brasileiro" in summary.summary.lower()
    assert summary.category == "politics"
    # All Gemini prompts (chunks + final) are in Portuguese mode.
    assert all("português" in p.lower() for p in fake.calls)


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
        ]
    )
    article = "word " * 1500
    summary = await summarize_article(fake, article, "Title", test_settings, language="pt-BR")
    assert summary.summary == "Resumo de fallback em portugues."
    # Fallback prompt (4th call) should have the Portuguese marker.
    assert "português" in fake.calls[3].lower()


# ---------------- retry-delay parsing ----------------


class _FakeAPIError(Exception):
    """Minimal stand-in for google.genai.errors.APIError for testing the parser.

    Holds the same ``details``/``code``/``status`` attributes the production
    code reads from real APIError instances. Does NOT subclass the real
    class — use :func:`_make_api_error` when an isinstance check is required.
    """

    def __init__(self, details: dict) -> None:
        self.details = details
        err = details.get("error", {}) if isinstance(details, dict) else {}
        if not isinstance(err, dict):
            err = {}
        self.code = err.get("code")
        self.status = err.get("status")
        super().__init__(str(details))


def _make_api_error(code: int, status: str, retry_delay: str | None = None) -> "Exception":
    """Build a real google.genai.errors.APIError subclass for isinstance checks.

    ClientError covers 4xx (including 429 quota); ServerError covers 5xx.
    """
    from google.genai import errors as _genai_errors

    details_list: list[dict] = []
    if retry_delay is not None:
        details_list.append(
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": retry_delay,
            }
        )
    response_json = {
        "error": {
            "code": code,
            "status": status,
            "details": details_list,
        }
    }
    cls = _genai_errors.ClientError if 400 <= code < 500 else _genai_errors.ServerError
    return cls(code, response_json, response=None)


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


# ---------------- deterministic classifier ----------------


@pytest.mark.parametrize(
    "url,expected",
    [
        # BBC URL patterns
        ("https://www.bbc.com/news/politics/articles/abc", "politics"),
        ("https://www.bbc.com/news/uk-politics/articles/abc", "politics"),
        ("https://www.bbc.com/news/world/articles/abc", "world"),
        ("https://www.bbc.com/news/business/articles/abc", "business"),
        ("https://www.bbc.com/news/technology/articles/abc", "tech"),
        ("https://www.bbc.com/news/science/articles/abc", "science"),
        ("https://www.bbc.com/news/health/articles/abc", "health"),
        ("https://www.bbc.com/news/uk/articles/abc", "uk"),
        ("https://www.bbc.com/news/europe/articles/abc", "europe"),
        # G1 URL patterns
        ("https://g1.globo.com/politica/noticia/2026/06/16/x.ghtml", "politics"),
        ("https://g1.globo.com/economia/noticia/2026/06/16/x.ghtml", "business"),
        ("https://g1.globo.com/tecnologia/noticia/2026/06/16/x.ghtml", "tech"),
        ("https://g1.globo.com/ciencia-e-saude/noticia/2026/06/16/x.ghtml", "science"),
        ("https://g1.globo.com/saude/noticia/2026/06/16/x.ghtml", "health"),
        ("https://g1.globo.com/mundo/noticia/2026/06/16/x.ghtml", "world"),
        ("https://g1.globo.com/internacional/noticia/2026/06/16/x.ghtml", "world"),
        # G1 sections added in Phase 3
        ("https://g1.globo.com/educacao/noticia/2026/06/16/x.ghtml", "other"),
        ("https://g1.globo.com/natureza/noticia/2026/06/16/x.ghtml", "science"),
        ("https://g1.globo.com/carros/noticia/2026/06/16/x.ghtml", "other"),
        ("https://g1.globo.com/concursos-e-emprego/noticia/2026/06/16/x.ghtml", "business"),
        ("https://g1.globo.com/turismo-e-viagem/noticia/2026/06/16/x.ghtml", "other"),
    ],
)
def test_classify_article_url_patterns(url: str, expected: str):
    """URL pattern matching covers the major BBC and G1 sections."""
    assert classify_article(title="Some article", url=url) == expected


@pytest.mark.parametrize(
    "url,title,section,expected",
    [
        # Real BBC URL format (/news/articles/<hash>) -- no section path.
        # The section meta tag is what tells us the category.
        (
            "https://www.bbc.com/news/articles/c0wd9x1k20xo",
            "Some article",
            "World",
            "world",
        ),
        (
            "https://www.bbc.com/news/articles/abc123",
            "Some article",
            "UK Politics",
            "politics",
        ),
        (
            "https://www.bbc.com/news/articles/def456",
            "Some article",
            "Business",
            "business",
        ),
        # Real BBC URL, no section -- title keyword fallback
        (
            "https://www.bbc.com/news/articles/abc",
            "Government announces policy",
            "",
            "politics",
        ),
        # Real BBC URL, no section, no useful title -- falls to "other"
        (
            "https://www.bbc.com/news/articles/abc",
            "Lorem ipsum dolor sit amet",
            "",
            "other",
        ),
    ],
)
def test_classify_article_real_bbc_url_format(url: str, title: str, section: str, expected: str):
    """Real BBC URLs (no section in path) rely on the section parameter."""
    assert classify_article(title=title, url=url, section=section) == expected


def test_classify_article_url_takes_priority_over_title():
    """When both URL and title match, the URL wins."""
    # Title suggests "tech" but URL clearly says "world"
    result = classify_article(
        title="New AI startup launches product",
        url="https://www.bbc.com/news/world/articles/abc",
    )
    assert result == "world"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Government announces new policy", "politics"),
        ("Prime minister addresses parliament", "politics"),
        ("Stock market crashes amid inflation", "business"),
        ("New AI startup raises funding", "tech"),
        ("NASA launches new space telescope", "science"),
        ("Hospital reports new covid outbreak", "health"),
        ("UK government unveils plan", "uk"),
        ("European Union passes new law", "europe"),
    ],
)
def test_classify_article_title_keyword_fallback(title: str, expected: str):
    """When URL doesn't match, title keywords are tried in priority order."""
    assert classify_article(title=title, url="") == expected


def test_classify_article_uk_word_boundary_does_not_match_ukraine():
    """The 'uk' keyword should not match 'Ukraine' (word boundary)."""
    # 'ukraine' doesn't contain ' uk ' (with spaces)
    result = classify_article(title="Ukraine war update", url="")
    assert result != "uk"
    # A title that mentions 'UK' as a word should match
    result = classify_article(title="UK government acts on economy", url="")
    assert result == "uk"


def test_classify_article_returns_other_for_unknown():
    """Empty input or unrecognized patterns return 'other'."""
    assert classify_article() == "other"
    assert classify_article(title="", url="") == "other"
    assert classify_article(title="Lorem ipsum dolor", url="https://example.com/page") == "other"


def test_classify_article_case_insensitive():
    """URL and title matching are case-insensitive."""
    assert (
        classify_article(
            title="GOVERNMENT ANNOUNCES POLICY", url="HTTPS://WWW.BBC.COM/NEWS/POLITICS/X"
        )
        == "politics"
    )


# ---------------- section-based classification ----------------


@pytest.mark.parametrize(
    "section,expected",
    [
        ("World", "world"),
        ("UK", "uk"),
        ("UK Politics", "politics"),
        ("Politics", "politics"),
        ("Business", "business"),
        ("Technology", "tech"),
        ("Science", "science"),
        ("Health", "health"),
        ("Europe", "europe"),
        ("International", "world"),
        # Case-insensitive
        ("WORLD", "world"),
        ("tech", "tech"),
    ],
)
def test_classify_article_section_exact_match(section: str, expected: str):
    """BBC <meta article:section> maps directly to a VALID_CATEGORIES value."""
    assert (
        classify_article(
            title="Any title",
            url="https://www.bbc.com/news/articles/abc123",  # real-format URL
            section=section,
        )
        == expected
    )


def test_classify_article_section_substring_match():
    """Compound section names that contain a known key match via substring."""
    # "US & Canada" doesn't have an exact key but contains "canada" -> not a key.
    # The substring fallback only matches keys defined in the map, not arbitrary
    # substrings. Verify a section that DOES contain a known key as substring.
    # "UK Politics" exact-matches "uk politics". But "UK Politics Live" is
    # a real BBC section that should still resolve.
    assert (
        classify_article(
            url="https://www.bbc.com/news/articles/abc",
            section="UK Politics Live",
        )
        == "politics"
    )


def test_classify_article_section_takes_priority_over_title():
    """When both section and title match, section wins (more specific)."""
    # Title suggests "politics" via "government", but section says "World"
    assert (
        classify_article(
            title="Government announces new world order",
            url="https://www.bbc.com/news/articles/abc",
            section="World",
        )
        == "world"
    )


def test_classify_article_url_takes_priority_over_section():
    """URL patterns are still checked before section (covers G1 best)."""
    # G1 URL pattern /politica/ wins over a section hint
    assert (
        classify_article(
            title="Any title",
            url="https://g1.globo.com/politica/noticia/2026/06/16/x.ghtml",
            section="World",  # would otherwise map to "world"
        )
        == "politics"
    )


def test_classify_article_unknown_section_falls_to_title():
    """Sections not in the map fall through to title keyword matching."""
    # "Local" is not in the map; title keyword "council" should match politics
    # (or "other" if no keyword matches). The key behavior: no crash, no
    # misclassification to a default; falls through to the next tier.
    result = classify_article(
        title="Local council votes on housing plan",
        url="https://www.bbc.com/news/articles/abc",
        section="Local",
    )
    # "council" not in the keywords list; should fall to "other"
    assert result == "other"


def test_classify_article_empty_section_falls_through():
    """Empty section string is the same as no section at all."""
    assert (
        classify_article(
            title="Government policy update",
            url="https://www.bbc.com/news/articles/abc",
            section="",
        )
        == "politics"  # from title keyword
    )


# ---------------- single-pass optimization ----------------


async def test_summarize_one_chunk_uses_fallback_not_chunk_merge(test_settings: Settings):
    """When the article produces exactly 1 chunk, the pipeline must skip the
    chunk→merge step and go straight to the fallback prompt.
    """
    from tests.conftest import FakeGeminiClient

    # Article fits in one chunk (< 600 words)
    fake = FakeGeminiClient(responses=["Direct fallback summary."])
    summary = await summarize_article(
        fake,
        "A " * 500,  # 500 words = 1 chunk
        "One-chunk article",
        test_settings,
        url="https://www.bbc.com/news/business/articles/x",
    )
    assert summary.summary == "Direct fallback summary."
    assert summary.category == "business"
    # Exactly 1 call, not 3 (chunk + final + category)
    assert len(fake.calls) == 1


async def test_summarize_no_chunks_uses_fallback(test_settings: Settings):
    """When the article is empty/short, summarize still uses the fallback path."""
    from tests.conftest import FakeGeminiClient

    # With summary_min_words default (40), 0 words = short, gets the note appended
    fake = FakeGeminiClient(responses=["Fallback for tiny article."])
    summary = await summarize_article(
        fake, "tiny", "Tiny article", test_settings, url="https://example.com/x"
    )
    assert summary.summary == "Fallback for tiny article."
    assert len(fake.calls) == 1


async def test_summarize_long_article_uses_chunk_merge(test_settings: Settings):
    """When the article produces > 1 chunk, the pipeline uses chunk→merge."""
    from tests.conftest import FakeGeminiClient

    # 1500 words = 3 chunks (with max_words=600)
    fake = FakeGeminiClient(
        responses=[
            "P1",
            "P2",
            "P3",
            "Merged final.",
        ]
    )
    summary = await summarize_article(
        fake,
        "word " * 1500,
        "Long article",
        test_settings,
        url="https://www.bbc.com/news/science/articles/x",
    )
    assert summary.summary == "Merged final."
    assert summary.category == "science"
    # 3 chunk calls + 1 final = 4 calls; no category call
    assert len(fake.calls) == 4


async def test_summarize_short_article_no_api_call_for_category(test_settings: Settings):
    """For a short article, NO API call should be made to classify the article.
    The category is derived deterministically from the URL.
    """
    from tests.conftest import FakeGeminiClient

    # If summarize_article makes a category call, the second response would
    # be popped and we'd see "world" in the second prompt. With 1 call only,
    # the second response is never consumed.
    fake = FakeGeminiClient(responses=["Just the summary.", "world"])
    summary = await summarize_article(
        fake,
        "Short article text.",
        "Title",
        test_settings,
        url="https://www.bbc.com/news/world/articles/abc",
    )
    assert summary.summary == "Just the summary."
    # Category from URL ("world") matches what a Gemini call would have
    # returned, but no second call was actually made.
    assert summary.category == "world"
    assert len(fake.calls) == 1
    # The "world" scripted response was never consumed.
    assert fake.responses == ["world"]


async def test_summarize_empty_chunks_returns_failed_summary(test_settings: Settings, monkeypatch):
    """If chunk_text returns [] (e.g. summary_min_words=0 with empty input),
    summarize must return a failed summary WITHOUT calling Gemini at all.
    """
    from tests.conftest import FakeGeminiClient

    # Allow the test to bypass the summary_min_words guard so we can hit
    # the empty-chunks path.
    test_settings.summary_min_words = 0
    monkeypatch.setattr(
        "daily_bot.summarizer.chunk_text",
        lambda text, max_words: [],
    )
    fake = FakeGeminiClient(responses=["unused"])
    summary = await summarize_article(
        fake,
        "word " * 1500,
        "Test Title",
        test_settings,
        url="https://www.bbc.com/news/world/articles/abc",
    )
    assert summary.summary == "Summary generation failed."
    assert summary.category == "world"  # from URL
    # No Gemini calls should have been made.
    assert fake.calls == []


# ---------------- quota-exhausted latch ----------------


class _StubClient:
    """Minimal stand-in for google.genai.Client used by AsyncGeminiClient."""

    def __init__(self, errors: list[Exception], responses: list[str]) -> None:
        self._errors = list(errors)
        self._responses = list(responses)
        self.calls = 0
        # Build the nested aio.models namespace expected by the code.
        from types import SimpleNamespace

        async def _generate_content(*_a, **_kw):
            self.calls += 1
            if self._errors:
                raise self._errors.pop(0)
            return SimpleNamespace(text=self._responses.pop(0))

        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))


def _make_quota_error(retry_delay: str = "0.01s") -> "Exception":
    return _make_api_error(429, "RESOURCE_EXHAUSTED", retry_delay=retry_delay)


async def test_first_429_does_not_latch():
    """A single 429 retries once and recovers; no latch.

    This prevents a transient per-minute burst from killing the entire
    run while still capping burn when the daily cap is hit.
    """
    from daily_bot.config import Settings
    from daily_bot.summarizer import AsyncGeminiClient

    settings = Settings(
        google_api_key="test",
        firebase_credentials="{}",
        sender_email="a@b.com",
        sender_password="x" * 16,
        gemini_retries=6,
    )
    # First call raises 429; second call (the retry) returns "ok".
    stub = _StubClient(errors=[_make_quota_error()], responses=["ok"])
    client = AsyncGeminiClient(settings)
    client._client = stub  # type: ignore[assignment]

    assert not client.quota_exhausted
    result = await client.generate("hello")
    assert result == "ok"
    # 2 HTTP calls (1 initial + 1 retry after the 429), no latch.
    assert stub.calls == 2
    assert client.quota_exhausted is False


async def test_quota_exhausted_latches_on_second_consecutive_429():
    """Two consecutive 429s set quota_exhausted; subsequent calls short-circuit."""
    from daily_bot.config import Settings
    from daily_bot.summarizer import AsyncGeminiClient

    settings = Settings(
        google_api_key="test",
        firebase_credentials="{}",
        sender_email="a@b.com",
        sender_password="x" * 16,
        gemini_retries=6,
    )
    # First call: 429, retry. Second call: 429, latch.
    stub = _StubClient(
        errors=[_make_quota_error(), _make_quota_error()],
        responses=["unused"],
    )
    client = AsyncGeminiClient(settings)
    client._client = stub  # type: ignore[assignment]

    first = await client.generate("hello")
    assert first is None
    assert client.quota_exhausted is True
    assert stub.calls == 2  # 1 initial + 1 retry

    # Second call must NOT hit the network.
    pre = stub.calls
    second = await client.generate("hello again")
    post = stub.calls
    assert second is None
    assert pre == post, "generate() must short-circuit when quota is exhausted"


async def test_reset_quota_exhausted_clears_latch():
    """reset_quota_exhausted() must allow the client to make HTTP calls again."""
    from daily_bot.config import Settings
    from daily_bot.summarizer import AsyncGeminiClient

    settings = Settings(
        google_api_key="test",
        firebase_credentials="{}",
        sender_email="a@b.com",
        sender_password="x" * 16,
        gemini_retries=6,
    )
    stub = _StubClient(
        errors=[_make_quota_error(), _make_quota_error()],
        responses=["ok"],
    )
    client = AsyncGeminiClient(settings)
    client._client = stub  # type: ignore[assignment]

    assert (await client.generate("a")) is None
    assert client.quota_exhausted is True
    client.reset_quota_exhausted()
    assert client.quota_exhausted is False
    assert (await client.generate("b")) == "ok"


async def test_non_quota_error_does_not_latch():
    """A 500 INTERNAL error should retry but NOT set the quota flag."""
    from daily_bot.config import Settings
    from daily_bot.summarizer import AsyncGeminiClient

    settings = Settings(
        google_api_key="test",
        firebase_credentials="{}",
        sender_email="a@b.com",
        sender_password="x" * 16,
        gemini_retries=2,
    )
    internal_err = _make_api_error(500, "INTERNAL")
    stub = _StubClient(errors=[internal_err], responses=["ok"])
    client = AsyncGeminiClient(settings)
    client._client = stub  # type: ignore[assignment]

    result = await client.generate("hello")
    assert result == "ok"
    assert client.quota_exhausted is False


async def test_first_429_recovers_within_retry_budget():
    """Real exhaustion may eventually latch; verify the retry doesn't waste calls."""
    from daily_bot.config import Settings
    from daily_bot.summarizer import AsyncGeminiClient

    settings = Settings(
        google_api_key="test",
        firebase_credentials="{}",
        sender_email="a@b.com",
        sender_password="x" * 16,
        # Only 2 retries total -> attempt 1 = first call, attempt 2 = first
        # quota retry. With 2 retries the second 429 latches (attempt 2 is
        # not == 1 so the latch branch fires).
        gemini_retries=2,
    )
    stub = _StubClient(
        errors=[_make_quota_error(), _make_quota_error()],
        responses=["unused"],
    )
    client = AsyncGeminiClient(settings)
    client._client = stub  # type: ignore[assignment]

    result = await client.generate("hello")
    assert result is None
    assert client.quota_exhausted is True
    # Exactly 2 HTTP calls: 1 + 1 retry. The latch prevents any further calls.
    assert stub.calls == 2

"""Unit tests for the emailer (with mocked SMTP)."""

from __future__ import annotations

import re
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from daily_bot.emailer import (
    THEME,
    _branded_placeholder,
    _build_message,
    render_email_html,
    send_daily_digest_async,
    send_to_subscriber,
)
from daily_bot.models import Summary

# ---------------- helpers ----------------


def test_branded_placeholder_uses_theme():
    url = _branded_placeholder("Hello World")
    assert "placehold.co" in url
    assert "709775" in url
    assert "Hello%20World" in url


def test_build_message_has_subject_from_and_to(test_settings):
    msg = _build_message(test_settings, "user@example.com", "<html></html>")
    assert msg["Subject"].startswith("\u2728")
    assert "bot@example.com" in msg["From"]
    assert msg["To"] == "user@example.com"
    # HTML body should be attached
    assert any(part.get_content_type() == "text/html" for part in msg.walk())


# ---------------- HTML rendering ----------------


def test_render_includes_articles(sample_summaries: list[Summary]):
    html = render_email_html(sample_summaries)
    assert "Article One" in html
    assert "Summary one body text." in html
    # Article Two has quotes which are escaped during render
    assert "Two" in html
    assert "Edge case content." in html


def test_render_escapes_xss(sample_summaries: list[Summary]):
    html = render_email_html(sample_summaries)
    # script tag must be escaped
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    # ampersand and < > should be escaped in text content
    assert "x &amp; y" not in html  # we don't have "x & y" exactly
    assert "&amp;" in html or "&lt;" in html  # at least some escaping happened


def test_render_escapes_ampersand_and_angle_in_text(sample_summaries: list[Summary]):
    """'Summary two & <html> chars.' should be escaped."""
    html = render_email_html(sample_summaries)
    assert "Summary two &amp; &lt;html&gt; chars." in html


def test_render_escapes_double_quotes_in_text(sample_summaries: list[Summary]):
    """Double-quoted text content should be escaped to &#34; or &quot;."""
    html = render_email_html(sample_summaries)
    assert (
        "Article &#34;Two&#34; with quotes" in html or "Article &quot;Two&quot; with quotes" in html
    )


def test_render_includes_theme_colors():
    html = render_email_html([])
    assert THEME["primary"] in html
    assert THEME["text_dark"] in html


def test_render_empty_articles_shows_message():
    html = render_email_html([])
    assert "No news summaries were generated today" in html


def test_render_falls_back_image_when_missing(sample_summaries: list[Summary]):
    """Article 2 has empty image_url, should use the branded placeholder."""
    html = render_email_html(sample_summaries)
    assert "placehold.co" in html


def test_render_includes_category_badge(sample_summaries: list[Summary]):
    html = render_email_html(sample_summaries)
    assert "world" in html
    assert "tech" in html


# ---------------- Source badges & section headers ----------------


def test_render_email_includes_source_badge_for_bbc():
    """BBC articles should render with the 'source-badge bbc' class and 'BBC' text."""
    html = render_email_html(
        [
            Summary(
                title="BBC Story",
                summary="A BBC article.",
                category="world",
                url="https://www.bbc.com/news/articles/bbc1",
                image_url="https://example.com/bbc.jpg",
                source="bbc",
            )
        ]
    )
    assert "source-badge bbc" in html
    # Badge text appears inside the span (whitespace-tolerant via regex)
    assert re.search(r"source-badge bbc[^>]*>\s*BBC\s*<", html) is not None


def test_render_email_includes_source_badge_for_g1():
    """G1 articles should render with the 'source-badge g1' class and 'G1' text."""
    html = render_email_html(
        [
            Summary(
                title="G1 Story",
                summary="A G1 article.",
                category="world",
                url="https://g1.globo.com/noticia/g11",
                image_url="https://example.com/g1.jpg",
                source="g1",
            )
        ]
    )
    assert "source-badge g1" in html
    assert re.search(r"source-badge g1[^>]*>\s*G1\s*<", html) is not None


def test_render_email_shows_section_headers_when_multiple_sources():
    """When articles have different sources, section header divs are emitted for each."""
    html = render_email_html(
        [
            Summary(
                title="BBC Story",
                summary="A BBC article.",
                category="world",
                url="https://www.bbc.com/news/articles/bbc1",
                image_url="https://example.com/bbc.jpg",
                source="bbc",
            ),
            Summary(
                title="G1 Story",
                summary="A G1 article.",
                category="world",
                url="https://g1.globo.com/noticia/g11",
                image_url="https://example.com/g1.jpg",
                source="g1",
            ),
        ]
    )
    # Look for actual <div class="source-section-header"> instances (not just the CSS rule)
    assert 'class="source-section-header"' in html
    assert "BBC News" in html
    assert "G1" in html


def test_render_email_no_section_headers_when_single_source():
    """When all articles share the same source, no section header divs are rendered."""
    html = render_email_html(
        [
            Summary(
                title="BBC Story A",
                summary="First.",
                category="world",
                url="https://www.bbc.com/news/articles/a",
                image_url="https://example.com/a.jpg",
                source="bbc",
            ),
            Summary(
                title="BBC Story B",
                summary="Second.",
                category="tech",
                url="https://www.bbc.com/news/articles/b",
                image_url="https://example.com/b.jpg",
                source="bbc",
            ),
        ]
    )
    # The CSS rule for .source-section-header will always exist; check for actual div usage
    assert 'class="source-section-header"' not in html
    # Single-source path still includes the badge
    assert "source-badge bbc" in html


def test_g1_button_text_is_portuguese():
    """G1 articles should show 'Ler no G1' (PT) instead of English button text."""
    html = render_email_html(
        [
            Summary(
                title="G1 Story",
                summary="A G1 article.",
                category="world",
                url="https://g1.globo.com/noticia/g11",
                image_url="https://example.com/g1.jpg",
                source="g1",
            )
        ]
    )
    assert "Ler no G1" in html
    assert "Read on G1" not in html


def test_bbc_button_text_uses_english():
    """BBC articles should show 'Read on BBC' (EN) button text."""
    html = render_email_html(
        [
            Summary(
                title="BBC Story",
                summary="A BBC article.",
                category="world",
                url="https://www.bbc.com/news/articles/bbc1",
                image_url="https://example.com/bbc.jpg",
                source="bbc",
            )
        ]
    )
    assert "Read on BBC" in html
    assert "Ler no G1" not in html


def test_render_empty_source_defaults_to_bbc():
    """A Summary with source='' should be treated as 'bbc' (badge + button)."""
    html = render_email_html(
        [
            Summary(
                title="No Source",
                summary="Article with empty source.",
                category="other",
                url="https://example.com/x",
                image_url="https://example.com/x.jpg",
                source="",
            )
        ]
    )
    assert "source-badge bbc" in html
    assert "Read on BBC" in html


# ---------------- SMTP sending (sync) ----------------


@patch("daily_bot.emailer.smtplib.SMTP_SSL")
def test_send_to_subscriber_calls_login_and_sendmail(mock_smtp, test_settings):
    mock_server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = mock_server

    send_to_subscriber(test_settings, "user@example.com", "<html>body</html>")

    mock_server.login.assert_called_once_with("bot@example.com", "password1234")
    mock_server.sendmail.assert_called_once()
    args = mock_server.sendmail.call_args[0]
    assert args[0] == "bot@example.com"
    assert args[1] == "user@example.com"
    assert "Subject:" in args[2]


@patch("daily_bot.emailer.smtplib.SMTP_SSL")
def test_send_to_subscriber_reraises_auth_error(mock_smtp, test_settings):
    mock_smtp.return_value.__enter__.return_value.login.side_effect = (
        smtplib.SMTPAuthenticationError(535, b"auth failed")
    )
    with pytest.raises(smtplib.SMTPAuthenticationError):
        send_to_subscriber(test_settings, "user@example.com", "<html></html>")


# ---------------- Async dispatch ----------------


@patch("daily_bot.emailer.smtplib.SMTP_SSL")
async def test_send_daily_digest_async_sends_to_all_subscribers(
    mock_smtp, test_settings, sample_summaries: list[Summary]
):
    mock_server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = mock_server

    results: list[tuple[str, str, str | None]] = []
    subscribers = ["a@x.com", "b@x.com", "c@x.com"]
    sent, failed = await send_daily_digest_async(
        test_settings,
        sample_summaries,
        subscribers,
        on_result=lambda email, status, error: results.append((email, status, error)),
    )

    assert sent == 3
    assert failed == 0
    assert mock_server.sendmail.call_count == 3
    assert {r[0] for r in results} == set(subscribers)
    assert all(r[1] == "sent" for r in results)


@patch("daily_bot.emailer.smtplib.SMTP_SSL")
async def test_send_daily_digest_async_continues_on_failure(
    mock_smtp, test_settings, sample_summaries: list[Summary]
):
    """One subscriber's send should fail without blocking the others."""
    call_count = {"n": 0}

    def sendmail(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise smtplib.SMTPRecipientsRefused({"b@x.com": (550, b"blocked")})

    mock_server = MagicMock()
    mock_server.sendmail.side_effect = sendmail
    mock_smtp.return_value.__enter__.return_value = mock_server

    sent, failed = await send_daily_digest_async(
        test_settings, sample_summaries, ["a@x.com", "b@x.com", "c@x.com"]
    )
    assert sent == 2
    assert failed == 1


async def test_send_daily_digest_async_empty_subscribers(test_settings, sample_summaries):
    sent, failed = await send_daily_digest_async(test_settings, sample_summaries, [])
    assert sent == 0
    assert failed == 0


@patch("daily_bot.emailer.smtplib.SMTP_SSL")
async def test_send_daily_digest_async_batches_with_delay(
    mock_smtp, test_settings, sample_summaries: list[Summary]
):
    """With email_batch_size=2 and 5 subscribers, there should be 3 batches."""
    mock_server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = mock_server

    test_settings.email_batch_size = 2
    test_settings.email_batch_delay_seconds = 0  # no real sleep

    with patch("daily_bot.emailer.asyncio.sleep") as mock_sleep:
        sent, failed = await send_daily_digest_async(
            test_settings, sample_summaries, [f"u{i}@x.com" for i in range(5)]
        )
    assert sent == 5
    assert failed == 0
    # 3 batches (2+2+1) -> 2 inter-batch delays
    assert mock_sleep.call_count == 2

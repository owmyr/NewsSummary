"""Email rendering and dispatch.

Refactored from email_sender.py with:
- Jinja2 template (no inline HTML string)
- html.escape() on all dynamic content (XSS fix)
- Settings injection (no more positional arg sprawl)
- Per-subscriber sending (BCC-free, individual delivery)
- Batch + delay logic to respect SMTP rate limits
- Per-subscriber result callback for audit logging
- Async dispatch via asyncio.to_thread for non-blocking sends
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

import jinja2

from .config import Settings
from .models import Summary

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

THEME = {
    "bg_color": "#faf9f6",
    "card_bg": "#ffffff",
    "primary": "#709775",
    "primary_dark": "#4a6b4e",
    "text_dark": "#292524",
    "text_light": "#78716c",
    "accent": "#e5989b",
}

_jinja_env = jinja2.Environment(
    autoescape=True,
    loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
)


def _branded_placeholder(text: str) -> str:
    safe_text = quote(text)
    return f"https://placehold.co/600x300/F0F4F1/709775?text={safe_text}"


def _prepare_article(article: Summary) -> dict[str, str]:
    raw_img = article.image_url
    image_src = (
        raw_img if raw_img and raw_img.startswith("http") else _branded_placeholder("News Update")
    )
    return {
        "title": article.title,
        "summary": article.summary,
        "url": article.url,
        "image_src": image_src,
        "category": article.category,
        "source": article.source or "bbc",
    }


def render_email_html(summaries: list[Summary]) -> str:
    """Render the daily digest as branded, escaped HTML."""
    template = _jinja_env.get_template("email.html.j2")
    today_date = datetime.now(UTC).strftime("%A, %B %d")
    return template.render(
        theme=THEME,
        today_date=today_date,
        articles=[_prepare_article(a) for a in summaries],
        has_articles=bool(summaries),
    )


def _build_message(settings: Settings, recipient: str, html_body: str) -> MIMEMultipart:
    message = MIMEMultipart("alternative")
    today_str = datetime.now(UTC).strftime("%B %d")
    message["Subject"] = f"\u2728 Your Daily Briefing - {today_str}"
    message["From"] = f"The Daily Bot <{settings.sender_email}>"
    message["To"] = recipient
    message.attach(MIMEText(html_body, "html"))
    return message


def send_to_subscriber(settings: Settings, recipient: str, html_body: str) -> None:
    """Send the rendered HTML email to a single subscriber."""
    message = _build_message(settings, recipient, html_body)
    context = ssl.create_default_context()

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as server:
            server.login(settings.sender_email, settings.sender_password)
            server.sendmail(settings.sender_email, recipient, message.as_string())
        logger.info("Email sent to %s", recipient)
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP auth failed; check SENDER_PASSWORD")
        raise
    except Exception as exc:
        logger.error("Failed to send to %s: %s", recipient, exc)
        raise


def send_daily_digest(
    settings: Settings,
    summaries: list[Summary],
    subscribers: list[str],
    on_result: Callable[[str, str, str | None], None] | None = None,
) -> tuple[int, int]:
    """Render once, then send to all subscribers in batches with delay.

    `on_result(email, status, error)` is called after every individual send,
    so the caller can persist per-subscriber audit logs.

    Returns (sent_count, failed_count).
    """
    if not subscribers:
        logger.warning("No subscribers to send to; skipping dispatch")
        return 0, 0

    html_body = render_email_html(summaries)
    logger.info(
        "Dispatching digest to %d subscribers in batches of %d",
        len(subscribers),
        settings.email_batch_size,
    )

    sent = 0
    failed = 0
    batch_size = settings.email_batch_size
    delay = settings.email_batch_delay_seconds

    for start in range(0, len(subscribers), batch_size):
        batch = subscribers[start : start + batch_size]
        for email in batch:
            try:
                send_to_subscriber(settings, email, html_body)
            except Exception as exc:
                failed += 1
                if on_result:
                    on_result(email, "failed", str(exc))
                continue
            sent += 1
            if on_result:
                on_result(email, "sent", None)
        if start + batch_size < len(subscribers):
            time.sleep(delay)

    logger.info("Dispatch complete: %d sent, %d failed", sent, failed)
    return sent, failed


async def send_daily_digest_async(
    settings: Settings,
    summaries: list[Summary],
    subscribers: list[str],
    on_result: Callable[[str, str, str | None], None] | None = None,
) -> tuple[int, int]:
    """Async version of send_daily_digest.

    Uses asyncio.to_thread to run blocking SMTP calls without blocking the
    event loop, while still respecting batch boundaries and inter-batch
    delay for rate limiting.
    """
    if not subscribers:
        logger.warning("No subscribers to send to; skipping dispatch")
        return 0, 0

    html_body = render_email_html(summaries)
    logger.info(
        "Dispatching digest to %d subscribers in batches of %d (async)",
        len(subscribers),
        settings.email_batch_size,
    )

    sent = 0
    failed = 0
    batch_size = settings.email_batch_size
    delay = settings.email_batch_delay_seconds

    for start in range(0, len(subscribers), batch_size):
        batch = subscribers[start : start + batch_size]

        async def _send_one(email: str) -> tuple[str, bool, str | None]:
            try:
                await asyncio.to_thread(send_to_subscriber, settings, email, html_body)
            except Exception as exc:
                return email, False, str(exc)
            return email, True, None

        results = await asyncio.gather(*(_send_one(e) for e in batch))
        for email, ok, err in results:
            if ok:
                sent += 1
                if on_result:
                    on_result(email, "sent", None)
            else:
                failed += 1
                if on_result:
                    on_result(email, "failed", err)

        if start + batch_size < len(subscribers):
            await asyncio.sleep(delay)

    logger.info("Async dispatch complete: %d sent, %d failed", sent, failed)
    return sent, failed

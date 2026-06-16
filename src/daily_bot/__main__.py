"""Daily orchestration entry point.

Run with:  python -m daily_bot
           python -m daily_bot --retry-failed   # re-summarize only the failed articles from today

Async pipeline:
  1. Load settings, mark health-check start
  2. For each configured news source (default: BBC):
     a. Fetch top story URLs (async)
     b. Skip URLs already summarized today (dedup)
     c. Scrape + summarize remaining articles concurrently (async)
        - Save partial state to Firestore after each article (resilience)
        - Use a circuit breaker to short-circuit repeated failures
  3. Render and persist email template to Firestore
  4. Load subscriber list (sync Firestore read)
  5. Group subscribers by their source preferences and send each group
     a tailored digest containing only their requested sources
  6. Log per-subscriber results and record health-check completion
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime

import httpx

from . import db, health
from .circuit_breaker import CircuitBreaker
from .config import Settings, load_settings
from .emailer import render_email_html, send_daily_digest_async
from .models import Summary
from .scraper import _build_client
from .sources import NewsSource, default_registry
from .summarizer import AsyncGeminiClient, summarize_article

logger = logging.getLogger("daily_bot")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _safe_log(date_str: str, email: str, status: str, error: str | None) -> None:
    """Persist a per-subscriber send result; never raise into the dispatcher."""
    try:
        db.log_email_send(date_str, email, status, error)
    except Exception:
        logger.exception("Failed to write email_log entry for %s", email)


def _parse_sources(settings: Settings) -> list[str]:
    """Parse the comma-separated `sources` setting into a clean list."""
    return [s.strip() for s in settings.sources.split(",") if s.strip()]


def _resolve_source(
    name: str,
) -> NewsSource | None:
    """Look up a source in the default registry. Returns None on failure."""
    try:
        return default_registry.get(name)
    except KeyError:
        logger.error("Unknown source '%s'. Available: %s", name, default_registry.names())
        return None


async def _process_one(
    settings: Settings,
    http_client: httpx.AsyncClient,
    source: NewsSource,
    gemini: AsyncGeminiClient,
    date_str: str,
    url: str,
    breaker: CircuitBreaker,
) -> Summary | None:
    """Scrape + summarize one article, saving intermediate state on success."""
    if not breaker.allow():
        logger.warning("Circuit open; skipping: %s", url)
        return None

    article = await source.scrape_article(http_client, url)
    if not article:
        breaker.record_failure()
        return None
    breaker.record_success()

    try:
        summary = await summarize_article(
            gemini,
            article.content,
            article.title,
            settings,
            language=source.language,
            url=url,
        )
    except Exception:
        logger.exception("Summarization crashed for %s", url)
        return None

    summary.source = article.source or source.name
    summary.url = url
    summary.image_url = article.image_url or ""

    try:
        existing = db.get_existing_summaries(date_str)
        existing_objs: list[Summary] = [Summary(**a) for a in existing if a.get("title")]
        all_summaries = [*existing_objs, summary]
        db.save_summaries(date_str, [s.model_dump() for s in all_summaries])
        logger.info(
            "Summarized [%s]: %s [%s]",
            summary.source,
            summary.title,
            summary.category,
        )
    except Exception:
        logger.exception("Failed to persist summary for %s; continuing", url)

    return summary


async def _process_source(
    settings: Settings,
    http_client: httpx.AsyncClient,
    source: NewsSource,
    gemini: AsyncGeminiClient,
    date_str: str,
    existing_urls: set[str],
    breaker: CircuitBreaker,
) -> list[Summary]:
    """Fetch, dedup, and process all new articles for a single source."""
    logger.info(
        "[%s] Fetching top story URLs (limit=%d)",
        source.name,
        settings.article_limit,
    )
    try:
        urls = await source.fetch_urls(http_client, settings.article_limit)
    except Exception:
        logger.exception("[%s] fetch_urls crashed", source.name)
        return []

    if not urls:
        logger.warning("[%s] No URLs found; skipping", source.name)
        return []

    new_urls = [u for u in urls if u not in existing_urls]
    if not new_urls:
        logger.info("[%s] All URLs already processed today", source.name)
        return []

    scrape_semaphore = asyncio.Semaphore(settings.scrape_concurrency)
    process_semaphore = asyncio.Semaphore(settings.summarize_concurrency)

    async def _guarded(url: str) -> Summary | None:
        if not breaker.allow():
            logger.warning("Circuit open; skipping: %s", url)
            return None
        async with process_semaphore, scrape_semaphore:
            return await _process_one(
                settings,
                http_client,
                source,
                gemini,
                date_str,
                url,
                breaker,
            )

    results = await asyncio.gather(*(_guarded(u) for u in new_urls))
    return [r for r in results if r is not None]


FAILED_PLACEHOLDER = "Summary generation failed."


async def _retry_failed_articles(
    settings: Settings,
    gemini: AsyncGeminiClient,
    breaker: CircuitBreaker,
    today_str: str,
    existing_summaries: list[Summary],
) -> None:
    """Re-summarize every failed article in today's dailySummaries.

    Used by ``--retry-failed`` mode. Articles that successfully summarized
    before are left untouched. Updates Firestore with the new summaries
    and re-renders the email template at the end.
    """
    failed = [s for s in existing_summaries if (s.summary or "").strip() == FAILED_PLACEHOLDER]
    if not failed:
        logger.info("No failed summaries to retry. Exiting.")
        return
    logger.info("Found %d failed summaries to retry", len(failed))

    succeeded: list[Summary] = []
    still_failed: list[Summary] = []

    async with _build_client(settings) as http_client:
        for f in failed:
            if not breaker.allow():
                logger.warning(
                    "Circuit open; stopping retry pass at %d/%d", len(succeeded), len(failed)
                )
                break
            source = _resolve_source(f.source) if f.source else None
            if source is None:
                logger.error("Unknown source '%s' for failed article %s; skipping", f.source, f.url)
                still_failed.append(f)
                continue
            if not f.url:
                logger.error("Failed article %s has no URL; cannot re-scrape", f.title)
                still_failed.append(f)
                continue
            try:
                article = await source.scrape_article(http_client, f.url)
            except Exception:
                logger.exception("Re-scrape failed for %s", f.url)
                still_failed.append(f)
                continue
            if article is None:
                logger.error("Re-scrape returned None for %s", f.url)
                still_failed.append(f)
                continue
            try:
                new_summary = await summarize_article(
                    gemini,
                    article.content,
                    article.title,
                    settings,
                    language=source.language,
                    url=f.url,
                )
            except Exception:
                logger.exception("Re-summarize crashed for %s", f.url)
                still_failed.append(f)
                continue
            new_summary.source = f.source or source.name
            new_summary.url = f.url
            new_summary.image_url = f.image_url
            # Only replace if the new attempt actually produced something
            if (new_summary.summary or "").strip() and new_summary.summary != FAILED_PLACEHOLDER:
                succeeded.append(new_summary)
                logger.info(
                    "Retry succeeded [%s]: %s [%s]",
                    new_summary.source,
                    new_summary.title,
                    new_summary.category,
                )
            else:
                still_failed.append(f)
                logger.warning("Retry still failed for %s", f.url)

    if not succeeded:
        logger.warning("No retries succeeded. Exiting without touching Firestore.")
        return

    # Replace the old (failed) entries with the new (succeeded) entries
    by_url: dict[str, Summary] = {s.url: s for s in existing_summaries if s.url}
    for s in succeeded:
        if s.url:
            by_url[s.url] = s
    merged = [s for s in by_url.values() if s.title]
    try:
        db.save_summaries(today_str, [s.model_dump() for s in merged])
        logger.info(
            "Replaced %d failed summaries (still failing: %d). Total in Firestore: %d",
            len(succeeded),
            len(still_failed),
            len(merged),
        )
    except Exception:
        logger.exception("Failed to persist retried summaries; exiting")
        return

    # Re-render the email template so latestNews preview reflects new content
    try:
        template_html = render_email_html(merged)
        db.save_latest_template(template_html)
        logger.info("Re-rendered email template saved to Firestore")
    except Exception:
        logger.exception("Failed to re-render email template")

    health.record_run_complete(
        today_str,
        scraped=0,
        summarized=len(merged),
        sent=0,
        failed=len(still_failed),
    )
    logger.info(
        "=== Retry pass complete: %d recovered, %d still failing ===",
        len(succeeded),
        len(still_failed),
    )


async def run_async(settings: Settings, retry_failed_only: bool = False) -> None:
    """Execute the full daily pipeline asynchronously.

    Args:
        retry_failed_only: When True, skip the scrape step entirely. Re-summarize
            every article already stored in today's dailySummaries whose
            ``summary`` matches ``FAILED_PLACEHOLDER``. Articles that
            successfully summarized previously are left alone.
    """
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")
    logger.info("=== The Daily Bot starting (date=%s) ===", today_str)

    health.record_run_start(today_str)
    gemini = AsyncGeminiClient(settings)
    breaker = CircuitBreaker(
        threshold=settings.circuit_breaker_threshold,
        cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
    )

    try:
        source_names = _parse_sources(settings)
        if not source_names:
            logger.error("No sources configured; aborting")
            return
        logger.info(
            "Configured sources: %s (available: %s)",
            source_names,
            default_registry.names(),
        )

        existing = db.get_existing_summaries(today_str)
        existing_urls: set[str] = set()
        for a in existing:
            if a.get("url"):
                existing_urls.add(str(a["url"]))
        logger.info("Already summarized today: %d articles", len(existing_urls))

        existing_summaries: list[Summary] = [Summary(**a) for a in existing if a.get("title")]
        new_summaries: list[Summary] = []

        if retry_failed_only:
            await _retry_failed_articles(settings, gemini, breaker, today_str, existing_summaries)
            return

        async with _build_client(settings) as http_client:
            for name in source_names:
                source = _resolve_source(name)
                if source is None:
                    continue
                try:
                    produced = await _process_source(
                        settings,
                        http_client,
                        source,
                        gemini,
                        today_str,
                        existing_urls,
                        breaker,
                    )
                except Exception:
                    logger.exception(
                        "[%s] source processing crashed; continuing with others",
                        name,
                    )
                    continue
                # Mark these URLs as seen so a later source doesn't re-summarize them
                for s in produced:
                    if s.url:
                        existing_urls.add(s.url)
                new_summaries.extend(produced)

        summaries: list[Summary] = [*existing_summaries, *new_summaries]

        if not summaries:
            logger.error("No summaries produced; aborting dispatch")
            health.record_run_failure(today_str, "no_summaries")
            return

        try:
            template_html = render_email_html(summaries)
            db.save_latest_template(template_html)
            logger.info("Rendered template saved to Firestore")
        except Exception:
            logger.exception("Failed to render/save template; continuing")

        subscribers = db.get_all_subscribers()
        if not subscribers:
            logger.warning("No subscribers in Firestore; nothing to send")
            health.record_run_complete(
                today_str,
                len(new_summaries),
                len(summaries),
                0,
                0,
            )
            return

        summaries_by_source: dict[str, list[Summary]] = defaultdict(list)
        for s in summaries:
            summaries_by_source[s.source].append(s)

        preference_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for sub in subscribers:
            key = tuple(sorted(sub.sources))
            preference_groups[key].append(sub.email)

        sent, failed = 0, 0
        for sources_key, emails in preference_groups.items():
            relevant_summaries: list[Summary] = []
            for src in sources_key:
                relevant_summaries.extend(summaries_by_source.get(src, []))

            if not relevant_summaries:
                logger.warning(
                    "No summaries for preference group %s; skipping %d subscribers",
                    sources_key,
                    len(emails),
                )
                continue

            logger.info(
                "Sending digest to %d subscribers (sources=%s, %d articles)",
                len(emails),
                sources_key,
                len(relevant_summaries),
            )

            group_sent, group_failed = await send_daily_digest_async(
                settings,
                relevant_summaries,
                emails,
                on_result=lambda email, status, error: _safe_log(today_str, email, status, error),
            )
            sent += group_sent
            failed += group_failed

        health.record_run_complete(
            today_str,
            scraped=len(new_summaries),
            summarized=len(summaries),
            sent=sent,
            failed=failed,
        )
        logger.info("=== Run complete: %d sent, %d failed ===", sent, failed)
    except Exception as exc:
        logger.exception("Unhandled error in pipeline")
        try:
            health.record_run_failure(today_str, str(exc))
        except Exception:
            logger.exception("Failed to record health failure")
        raise


def run(settings: Settings, retry_failed_only: bool = False) -> None:
    """Synchronous entry point: configure logging, then run the async pipeline.

    Args:
        retry_failed_only: When True, skip scraping and only re-summarize any
            articles stored in today's dailySummaries that have a
            "Summary generation failed." placeholder. Useful after a quota
            exhaustion run.
    """
    _configure_logging(settings.log_level)
    asyncio.run(run_async(settings, retry_failed_only=retry_failed_only))


def _parse_args(argv: list[str]) -> dict[str, bool]:
    """Parse minimal CLI flags without pulling in argparse."""
    return {"retry_failed_only": "--retry-failed" in argv}


def main() -> None:
    args = _parse_args(sys.argv[1:])
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)
    if args["retry_failed_only"]:
        logger_start = logging.getLogger("daily_bot")
        logger_start.info("=== --retry-failed mode: re-summarizing failed articles only ===")
    run(settings, **args)


if __name__ == "__main__":
    main()

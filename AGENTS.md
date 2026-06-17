# AGENTS.md — Daily Bot Project Guide

## Project Overview

**Daily Bot** is an async Python pipeline that scrapes news from configurable sources (BBC News in English, G1 in Portuguese), summarizes articles with Google Gemini, and emails tailored digests to Firestore-managed subscribers based on their per-subscriber source preferences. Runs daily via GitHub Actions cron at 09:00 UTC.

- **Package**: `daily-bot` v2.0.0
- **Python**: 3.12 (3.11+ required)
- **Build system**: hatchling
- **Entry point**: `python -m daily_bot` -> `src/daily_bot/__main__.py:main()`
- **Firebase project**: `news-summary-3baaa` (GCP project; Firestore + Cloud Functions + Hosting)
- **Public site (canonical)**: <https://thedailybot.web.app> — Firebase Hosting site ID `thedailybot`. The legacy default URL `https://news-summary-3baaa.web.app` mirrors the same content and remains accessible.

## Commands

```bash
pip install -e ".[dev]"          # Install package with dev deps
python -m daily_bot              # Run the pipeline
python -m daily_bot --retry-failed   # Re-summarize only failed articles from today
pytest                           # Run all 236 tests
pytest --cov=daily_bot           # Tests with coverage
pytest tests/unit/               # Unit tests only
pytest tests/integration/        # Integration tests only
ruff check src/daily_bot/ tests/ # Lint
ruff format src/daily_bot/ tests/ # Format
mypy src/daily_bot/              # Type check
```

## Architecture

```
src/daily_bot/
├── __init__.py              # Version (2.0.0)
├── __main__.py              # Orchestrator: load sources -> scrape -> summarize -> route -> email
├── main.py                  # Thin shim re-exporting __main__.main
├── config.py                # Pydantic BaseSettings (reads .env automatically)
├── models.py                # ScrapedArticle, Summary, Subscriber, EmailSendResult, Category
├── scraper.py               # Async httpx scraper (BBC-specific helpers, _extract_section)
├── summarizer.py            # AsyncGeminiClient (google-genai SDK), language-aware prompts
├── emailer.py               # Jinja2 template -> HTML, SMTP batch dispatch
├── db.py                    # Firestore lazy-init data layer (get_all_subscribers, etc.)
├── circuit_breaker.py       # CircuitBreaker (CLOSED->OPEN->HALF_OPEN)
├── health.py                # Dead-man's-switch writes to Firestore health/last_run
├── sources/
│   ├── __init__.py          # Registers built-in sources, exports default_registry
│   ├── base.py              # NewsSource ABC (with `language` property), NewsSourceProtocol, SourceRegistry
│   ├── bbc.py               # BBCSource wrapping scraper functions (language="en")
│   └── g1.py                # G1Source for g1.globo.com (language="pt-BR")
└── templates/
    └── email.html.j2        # Jinja2 email template (autoescape=True, source badges, section headers)

tests/
├── conftest.py              # Shared fixtures (test_settings, sample_summaries, FakeGeminiClient, HTML stubs)
├── unit/
│   ├── test_circuit_breaker.py
│   ├── test_config_and_models.py
│   ├── test_emailer.py
│   ├── test_g1_scraper.py   # G1 source URL extraction, article scraping, section mapping
│   ├── test_scraper.py
│   ├── test_sources.py
│   └── test_summarizer.py
└── integration/
    ├── test_multi_source.py
    ├── test_pipeline.py
    └── test_subscriber_routing.py  # Per-subscriber source-preference dispatch

public/                      # Firebase Hosting - subscription website
├── index.html               # Subscribe form (with BBC/G1 checkboxes), news preview with source badges
└── unsubscribe.html         # Unsubscribe page

functions/                   # Cloud Functions (Node.js)
└── index.js                 # addSubscriber (with sources validation), unsubscribeUser, latestNews (returns source)
```

## Key Patterns

### Pipeline Flow (`__main__.py`)

1. Load settings -> record health start
2. For each configured source (comma-separated `SOURCES` env var, e.g. `"bbc,g1"`):
   a. `fetch_urls()` -> get article URLs
   b. Dedup against Firestore `dailySummaries/{date}`
   c. Scrape + summarize concurrently (`asyncio.Semaphore` bounded). The scraper extracts `article.section` from page metadata (OG/legacy `<meta>` tags, JSON-LD) for sources that expose it (BBC).
   d. Save after each article (partial persistence / resilience)
   e. Circuit breaker short-circuits on repeated failures
3. Render Jinja2 email template -> save to Firestore `emailTemplates/latest`
4. Load subscribers from Firestore `subscribers` collection via `get_all_subscribers()`
5. **Group subscribers by their `sources` preference** (sorted tuple, e.g. `("bbc",)`, `("g1",)`, `("bbc", "g1")`)
6. For each preference group, build a tailored digest containing only summaries from preferred sources, then send (batch+delay, `asyncio.to_thread` for SMTP)
7. Audit log per subscriber to `email_log`
8. Record health completion with grouped send/fail counts

### Adding a New Source

1. Create `src/daily_bot/sources/<name>.py` with a `NewsSource` subclass
2. Implement `name` (property), `fetch_urls(client, limit)`, `scrape_article(client, url)`
3. **Override `language` property** if the source is non-English (defaults to `"en"`)
4. Register in `sources/__init__.py`: `default_registry.register("name", MySource)`
5. Add `SOURCES="bbc,<name>"` to `.env`

### Language-Aware Summarization

- `summarize_article(client, article_text, title, settings, language="en")` accepts a `language` param.
- When `language == "pt-BR"`, a Portuguese instruction is prepended to chunk/final/fallback prompts. The category-classification prompt stays English (it validates against `VALID_CATEGORIES`).
- The orchestrator passes `source.language` automatically — sources don't need to know about the summarizer.

### Category Classification (deterministic, no API call)

- `classify_article(title="", url="", section="")` returns a `VALID_CATEGORIES` value without spending a Gemini call.
- **Priority order**: URL pattern → source-provided section → title keyword → `"other"`.
- URL patterns (`_URL_CATEGORY_RULES`) cover BBC section paths (`/news/politics/`, etc.) and G1 section paths (`/politica/`, `/economia/`, `/tecnologia/`, `/educacao/`, `/natureza/`, `/carros/`, `/concursos-e-emprego/`, `/turismo-e-viagem/`, etc.).
- Section mapping (`_SECTION_CATEGORY_MAP`) normalizes raw BBC section names from `<meta property="article:section">` (e.g. `"World"` → `"world"`, `"UK Politics"` → `"politics"`, `"Technology"` → `"tech"`). Substring matching is checked longest-key-first so compound names resolve correctly.
- Title keyword fallback (`_TITLE_CATEGORY_RULES`) handles the rare case where neither URL nor section give a hint.
- BBC article URLs are `https://www.bbc.com/news/articles/<hash>` — they contain no section path, so the URL-pattern check fails and classification falls through to the section parameter (populated by `_extract_section()` during scrape) or title keywords.

### Subscriber Source Preferences

- `Subscriber.sources: list[str]` field. Defaults to `["bbc"]` (backward-compatible for existing Firestore docs).
- `db.get_all_subscribers()` returns full `Subscriber` objects (with sources), replacing the old `get_all_subscriber_emails()` for routing. The email-only function is kept for tests/backward-compat.
- Routing in `__main__.py` uses `defaultdict` to group subscribers by `tuple(sorted(sub.sources))`. Same preference set → same email.

### Async Patterns

- All I/O uses `httpx.AsyncClient` + `asyncio.gather`
- SMTP is blocking -> wrapped in `asyncio.to_thread()`
- Gemini SDK: `client.aio.models.generate_content()` for async
- Tests use `asyncio_mode = "auto"` in pytest — no `@pytest.mark.asyncio` needed

### Firestore Collections

| Collection | Purpose |
|---|---|
| `dailySummaries/{date}` | Articles array for each day (each with `source` field) |
| `subscribers` | Subscriber docs with `email`, `sources`, `subscribedAt` |
| `email_log` | Per-send audit trail |
| `emailTemplates/latest` | Rendered HTML for Cloud Function |
| `health/last_run` | Dead-man's-switch status |

### Config (Pydantic BaseSettings)

All settings in `config.py`, loaded from `.env` or env vars. Required: `GOOGLE_API_KEY`, `FIREBASE_CREDENTIALS`, `SENDER_EMAIL`, `SENDER_PASSWORD`. Everything else has defaults. Notable: `SOURCES="bbc,g1"`, `G1_HOMEPAGE_URL="https://g1.globo.com"`, `BBC_NEWS_URL="https://www.bbc.com/news"`.

### Important Import Bindings

- `__main__.py` imports `_build_client` and `default_registry` at module level. Tests must patch `daily_bot.__main__._build_client` AND `daily_bot.__main__.default_registry`, not the source modules.
- Monkey-patched methods in tests MUST be restored in `finally` blocks to avoid polluting subsequent tests.

## Cloud Functions (Node.js)

`functions/index.js` exports:

- `addSubscriber` (POST): Validates email, validates `sources` array against `["bbc", "g1"]` allowlist (defaults to `["bbc"]`), dedup check, saves to Firestore, sends welcome email
- `latestNews` (GET): Returns most recent `dailySummaries` doc with XSS-escaped articles, each including `source` field
- `unsubscribeUser` (POST): Deletes matching subscriber docs

## Testing Notes

- `FakeGeminiClient` in conftest returns scripted responses in order. PT-BR tests need additional scripted responses (chunk + final + category = 3 calls minimum).
- `httpx.MockTransport` for HTTP mocking (not `respx` or `aioresponses`)
- `NoEnvSettings(Settings)` subclass with `env_file=None` isolates tests from `.env`
- `MockFirestoreClient` in integration tests patches `daily_bot.db.get_db`. Subclass per test with `subscriber_docs=` for different preference scenarios.
- `FakeSMTP` / `_RecordingSMTP` patches `smtplib.SMTP_SSL`. For tests that inspect message bodies, use `_extract_html_body()` helper to decode base64/quoted-printable MIME parts.
- `FakeSource` in multi-source tests **must subclass `NewsSource`** (the registry's `issubclass` check rejects duck-typed classes).

## Common Pitfalls

- **Firebase credentials**: `FIREBASE_CREDENTIALS` must be the full JSON string, not a file path
- **google-genai SDK**: Use `google.genai` (new), not `google.generativeai` (deprecated). Async via `client.aio.models.generate_content()`
- **XSS**: Email template uses Jinja2 `autoescape=True`. Never bypass with `|safe` for user content. Cloud Function `latestNews` escapes via `escapeHtml`.
- **Circuit breaker**: After 3 consecutive failures, opens for 30s. Tests must `reset()` or use fresh instances
- **SMTP**: Gmail requires app passwords, not account passwords. Port 465 with SSL
- **Source registration**: `SourceRegistry.register()` requires subclassing `NewsSource` ABC; duck-typed classes are rejected
- **Language defaults**: `NewsSource.language` defaults to `"en"`. If you forget to override, G1 articles will get English summaries. Always set `language` explicitly for non-English sources.
- **Subscriber migration**: Existing Firestore subscribers without `sources` field default to `["bbc"]` on read. No manual migration needed.
- **MIME body in tests**: `SMTP.sendmail()` captures base64-encoded HTML. To assert on rendered content, decode it first using the `_extract_html_body()` helper from `tests/integration/test_subscriber_routing.py`.
- **BBC article URLs**: Real BBC URLs are `https://www.bbc.com/news/articles/<hash>` — they carry no section in the path. The section must come from page metadata (extracted by `_extract_section()` in `scraper.py`). If you add a new BBC-like source whose URLs also lack a section path, populate `ScrapedArticle.section` in the scrape step.
- **Gemini free-tier quota**: The free tier is **5 req/min, 20 req/day**. With `SOURCES=bbc,g1` and `article_limit=4` (default), the pipeline uses **8-16 API calls/day** (1-2 per short article, 2-3 per long article, **0 for category classification**). BBC is processed first and always fits; G1 fits on most days. Two optimizations keep the daily budget in check: (1) **short articles** (≤ 1 chunk) use a single fallback prompt instead of the chunk-merge step, and (2) **category classification is deterministic** (URL pattern + source-provided section + title keyword matching) so no API call is spent on a cosmetic email-card label. If the quota is exhausted mid-run, some articles will have `summary: "Summary generation failed."`. The `AsyncGeminiClient` latches a `quota_exhausted` flag on the **second consecutive** 429 (first 429 retries once with the API's suggested `retryDelay`, capped at 65s) so subsequent calls short-circuit instantly. To recover: wait for the quota to reset, then re-run `python -m daily_bot --retry-failed`. To stay under the per-minute cap, keep `summarize_concurrency=1`.

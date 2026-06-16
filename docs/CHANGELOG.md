# Changelog

All notable changes to **Daily Bot** are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — Stay within the Gemini free tier

The pipeline now consistently fits within the free-tier daily quota (20 calls/day) for both BBC and G1 combined.

- **`article_limit` default lowered from 5 to 4.** With 4 articles per source, the pipeline uses 8-16 calls/day, leaving 4-12 calls of margin.
- **Single-pass summarization for short articles.** Articles that fit in a single chunk (≤ 600 words, the vast majority of news articles) now use the fallback prompt directly in one API call instead of the previous chunk-then-merge pipeline (two calls). This saves ~1 call per short article.
- **Deterministic category classification.** `summarize_article()` no longer spends an API call on a cosmetic category label. `classify_article(title, url)` matches BBC and G1 URL patterns first (e.g. `/news/politics/` → "politics", `/economia/` → "business"), then falls back to title keywords. Returns `"other"` if nothing matches. Saves 1 call per article (8 calls/day with `article_limit=4`).
- **BBC prioritized over G1.** The orchestrator already processes sources in the order they appear in `SOURCES`. With `SOURCES=bbc,g1` (the default), BBC always runs first and gets the full quota headroom. If `quota_exhausted` fires, only G1 articles are affected.

### Added — Quota-exhaustion latch on the Gemini client

When a 429/RESOURCE_EXHAUSTED error is observed, `AsyncGeminiClient` latches a `quota_exhausted` flag. Every subsequent `generate()` call returns `None` immediately without making a network request. This prevents a daily-quota outage from turning into hours of sleep loops. The flag can be cleared with `reset_quota_exhausted()` once the quota has been refilled. Two new properties: `client.quota_exhausted` (read-only) and `client.reset_quota_exhausted()` (clear the latch).

### Fixed — Resilient retry on Gemini 429 quota errors

When the free-tier Gemini quota is exhausted mid-pipeline, some articles end up with `summary: "Summary generation failed."` This release makes the pipeline more resilient and adds a recovery path.

- **`AsyncGeminiClient.generate()` now reads the API's own `retryDelay` hint** on 429 quota errors (e.g. "Please retry in 13s") instead of a hardcoded `2 * attempt` backoff. The delay is clamped to 65s so a single daily-quota exhaustion doesn't block the pipeline forever.
- **`gemini_retries` default raised from 4 to 6** to give the client a better chance of surviving short backoff windows.
- **`summarize_concurrency` default lowered from 3 to 1** so the pipeline stays comfortably under the free-tier 5 req/min limit.
- **New `--retry-failed` CLI flag** for the orchestrator:
  ```bash
  python -m daily_bot --retry-failed
  ```
  This re-summarizes only the articles already stored in today's `dailySummaries` whose `summary` matches the failure placeholder. It re-scrapes (so the article body is fresh), calls Gemini, and replaces the failed entries in Firestore. The email template is re-rendered at the end. Use this after a quota-exhaustion run, or whenever you see "Summary generation failed." in `dailySummaries`.

- **New `_parse_retry_delay_seconds` / `_extract_retry_delay` helpers** in `summarizer.py`, exported and unit-tested.
- **New `pipeline.FAILED_PLACEHOLDER` constant** = `"Summary generation failed."` so tests and downstream consumers can detect failed summaries reliably.

### Tests
- 7 new unit tests in `test_summarizer.py` for the retry-delay parser (handles `"13s"`, bare numbers, malformed payloads, etc.).
- 2 new integration tests in `test_pipeline.py` for the `--retry-failed` path: one verifies a failed article gets re-summarized while a successful one is untouched, the other verifies a no-op when nothing failed.
- `MockFirestoreClient` and `MockCollection` now support a `pre_existing_articles` parameter to seed the `dailySummaries` collection before a run.

### Changed — Canonical public site is now `thedailybot.web.app`

The Firebase Hosting site ID `thedailybot` is now the canonical public URL. The legacy site `news-summary-3baaa` mirrors the same content and remains accessible.

- **Canonical URL**: <https://thedailybot.web.app>
- **Legacy URL** (still works): <https://news-summary-3baaa.web.app>
- `firebase.json` targets the `thedailybot` site.
- `public/index.html` `og:url` meta tag updated to `https://thedailybot.web.app`.
- README.md and AGENTS.md updated to point at the canonical URL first.
- Cloud Function URLs (`addsubscriber`, `latestNews`, `unsubscribeUser`) are project-scoped and work regardless of which hosting URL the visitor came from.

### Operator note
To deploy to BOTH hosting sites (so the legacy URL doesn't get stale), temporarily change `firebase.json` to `"site": "news-summary-3baaa"`, run `firebase deploy --only hosting`, then revert. Or, simpler: keep the `thedailybot` deployment as the source of truth and re-run the same deploy with the `news-summary-3baaa` site override.

### Added — G1 News Source + Subscriber Source Preferences

The headline feature of this release is the addition of **G1 (g1.globo.com)** as a second news source, with **per-subscriber source preferences** and **language-aware summarization** in Portuguese (PT-BR). Subscribers can now choose which sources they want in their daily digest.

#### New: `G1Source` (Phase 1)

- **`src/daily_bot/sources/g1.py`** — new `G1Source(NewsSource)` class:
  - `name = "g1"`
  - `language = "pt-BR"` (drives the summarizer's output language)
  - `DEFAULT_HOMEPAGE = "https://g1.globo.com"`, overridable via `G1_HOMEPAGE_URL`
  - `fetch_urls()`: parses `a.feed-post-link[href]` elements, filters for `.ghtml` URLs, deduplicates
  - `scrape_article()`: extracts title (`<h1>`), body (article paragraphs with multi-strategy fallback), image (`og:image` meta), sets `article.source = "g1"`
  - G1-specific helpers inlined: `_normalize_url`, `_extract_article_text`, `_extract_article_image`
  - 89% line coverage

- **`src/daily_bot/sources/base.py`** — added a concrete `language` property (default `"en"`) to the `NewsSource` ABC. The property is not abstract; English sources inherit the default. Non-English sources override.

- **`src/daily_bot/sources/__init__.py`** — registered `G1Source` as `"g1"`. `default_registry.names()` now returns `["bbc", "g1"]`.

- **`src/daily_bot/config.py`** — added `g1_homepage_url: str` setting (default `"https://g1.globo.com"`).

- **`.env.example`** — added `G1_HOMEPAGE_URL` line and updated `SOURCES` example to `"bbc,g1"`.

- **`tests/unit/test_g1_scraper.py`** — new test file with 18 tests covering:
  - `test_g1_source_name` — `G1Source().name == "g1"`
  - `test_g1_source_language` — `G1Source().language == "pt-BR"`
  - `test_g1_fetch_urls_extracts_ghtml_links` — homepage feed links extracted
  - `test_g1_fetch_urls_deduplicates` — duplicate URLs handled
  - `test_g1_fetch_urls_respects_limit` — `limit` parameter enforced
  - `test_g1_fetch_urls_handles_network_error` — graceful failure on HTTP error
  - `test_g1_scrape_article_extracts_title_and_content` — article body parsed
  - `test_g1_scrape_article_extracts_og_image` — `og:image` meta used for image
  - `test_g1_scrape_article_handles_network_error` — `None` returned on failure
  - `test_g1_registered_in_default_registry` — `"g1"` in `default_registry.names()`
  - 8 more tests covering edge cases, custom homepage URL, and other selectors

#### New: Language-Aware Summarization (Phase 2)

- **`src/daily_bot/summarizer.py`** — added `language: str = "en"` parameter to:
  - `summarize_article()` (public API)
  - `_build_chunk_prompt()` (chunk phase)
  - `_build_final_prompt()` (merge phase)
  - `_build_fallback_prompt()` (single-shot fallback)
  - New helper `_language_prefix(language: str) -> str` returns a Portuguese instruction string when `language == "pt-BR"`, or `""` otherwise. The category-classification prompt remains English so it can still validate against `VALID_CATEGORIES`.

- **`src/daily_bot/__main__.py`** — `_process_one()` now passes `language=source.language` to `summarize_article()`. This means BBC articles (default `language="en"`) get English summaries, G1 articles (`language="pt-BR"`) get Portuguese summaries — automatically.

- **`tests/unit/test_summarizer.py`** — added 14 new tests:
  - `test_chunk_prompt_includes_pt_br_instruction` (and en/empty variants)
  - `test_final_prompt_includes_pt_br_instruction` (and en/empty variants)
  - `test_fallback_prompt_includes_pt_br_instruction` (and en/empty variants)
  - `test_summarize_with_pt_br_language` — full e2e with FakeGeminiClient
  - `test_summarize_with_en_language_unchanged` — default behavior preserved
  - `test_summarize_default_language_is_english` — backward compatibility
  - `test_pt_br_prompt_includes_portuguese_instruction` — prompt inspection
  - `test_pt_br_summary_returned_correctly` — e2e Portuguese summary
  - `test_pt_br_fallback_path_uses_portuguese_instruction` — fallback path coverage
  - 5 more for chunk/final/fallback en and edge cases

#### New: Subscriber Source Preferences + Routing (Phase 3)

- **`src/daily_bot/models.py`** — added `sources: list[str]` field to `Subscriber` (default `["bbc"]`). Existing subscribers without this field are auto-defaulted to BBC-only.

- **`src/daily_bot/db.py`** — added `get_all_subscribers() -> list[Subscriber]` that returns full subscriber objects (not just emails). The old `get_all_subscriber_emails()` is kept for backward compatibility. Defensive coercion: missing field → `["bbc"]`, non-list → `["bbc"]`, each item is cast to `str`.

- **`src/daily_bot/__main__.py`** — replaced the broadcast-to-all logic with **preference-based routing**:
  1. Group summaries by `summary.source` into `summaries_by_source: dict[str, list[Summary]]`
  2. Group subscribers by their sorted source tuple into `preference_groups: dict[tuple[str, ...], list[str]]`
  3. For each unique preference group, collect summaries from all preferred sources, render and send one tailored email
  4. Subscribers in the same preference group share the same email
  5. Subscribers with no matching summaries (e.g., BBC-only subscriber on a day with no BBC articles) are logged and skipped
  6. Audit logging (`email_log`) continues per-subscriber via `_safe_log`

- **`tests/unit/test_config_and_models.py`** — 4 new tests for `Subscriber` with `sources`:
  - `test_subscriber_default_sources_is_bbc`
  - `test_subscriber_custom_sources`
  - `test_subscriber_empty_sources_defaults_to_bbc`
  - `test_subscriber_independent_default_factory` (no shared mutable state)

- **`tests/integration/test_subscriber_routing.py`** — new integration test file with 4 tests:
  - `test_subscribers_with_only_bbc_get_only_bbc_articles`
  - `test_subscribers_with_only_g1_get_only_g1_articles`
  - `test_subscribers_with_both_sources_get_both`
  - `test_mixed_preferences_create_separate_emails` (verifies same preferences → same article set, different preferences → different article sets, order-insensitive grouping)
  - Includes a `_RecordingSMTP` helper and `_extract_html_body()` utility that decodes base64/quoted-printable MIME bodies for assertion

- **`tests/integration/test_pipeline.py`** and **`tests/integration/test_multi_source.py`** — updated `MockFirestoreClient` / `MockCollection` to return subscribers with `sources` field (defaulting to `["bbc"]`).

#### New: Email Template — Source Badges & Section Headers (Phase 4)

- **`src/daily_bot/emailer.py`** — `_prepare_article()` now includes `"source": article.source or "bbc"` in the dict passed to Jinja2.

- **`src/daily_bot/templates/email.html.j2`** — two structural changes:
  1. **Single source**: each article card shows a source badge (e.g. `BBC`, `G1`) inline above the category. No section headers.
  2. **Multiple sources**: a section header is rendered per source (e.g. `BBC News`, `G1 — O portal de notícias da Globo`) followed by that source's article cards.
  3. The CTA button text is source-aware: `"Read on BBC →"` for English sources, `"Ler no G1 →"` for G1.
  4. Added CSS for `.source-badge`, `.source-badge.bbc` (BBC red), `.source-badge.g1` (G1 red), and `.source-section-header`.

- **`tests/unit/test_emailer.py`** — 7 new tests:
  - `test_render_email_includes_source_badge_for_bbc`
  - `test_render_email_includes_source_badge_for_g1`
  - `test_render_email_shows_section_headers_when_multiple_sources`
  - `test_render_email_no_section_headers_when_single_source`
  - `test_g1_button_text_is_portuguese`
  - `test_bbc_button_text_is_english`
  - `test_empty_source_defaults_to_bbc_badge`

#### New: Frontend Source Selection UI (Phase 5)

- **`public/index.html`** — added source checkboxes to the subscribe form:
  - `BBC News (English)` — default checked
  - `G1 (Português)` — default unchecked
  - Styled with `accent-brand-sage`, hover transitions, `text-sm text-stone-500`
  - Form submit handler collects checked values into a `sources` array (default `["bbc"]` if none checked)
  - News preview cards now show a source badge (`BBC` or `G1`) above the title

- **`functions/index.js`** — three changes:
  1. **`addSubscriber`**: accepts and validates an optional `sources` array. Valid sources are `["bbc", "g1"]`. Defensive coercion:
     - Missing field → `["bbc"]`
     - Non-array → `["bbc"]`
     - Empty array → `["bbc"]`
     - Invalid values filtered out; if all invalid → `["bbc"]`
     - Valid values stored in Firestore subscriber doc
  2. **`latestNews`**: API response now includes `source` field per article (XSS-escaped). Frontend uses this to render source badges.
  3. Welcome email flow unchanged (still uses the latest template from Firestore).

- JS syntax verified with `node --check functions/index.js` and visual inspection of inline HTML/JS.

#### New: Documentation

- **`docs/UPGRADE_PLAN_G1.md`** — 7-phase execution plan for the G1 upgrade. Covers scrapability verification, source implementation, language-aware summarization, subscriber routing, template updates, frontend UI, testing, and deployment.

- **`docs/CHANGELOG.md`** — this file.

- **`AGENTS.md`** — updated to reflect:
  - `G1Source` in the source list
  - `language` property on `NewsSource` ABC
  - `Subscriber.sources` field
  - Preference-group routing in pipeline flow
  - Test count: 150 (was 104)

- **`README.md`** — updated to reflect:
  - Multi-source support (BBC + G1)
  - Language-aware summaries
  - Per-subscriber source preferences
  - Updated architecture diagram (8 steps including grouping)
  - "Supported Sources" table

### Changed

- **`src/daily_bot/sources/base.py`** — `NewsSource` ABC gained a `language` property (default `"en"`). This is backward-compatible: BBCSource inherits the default.

- **`src/daily_bot/__main__.py`** — dispatch logic refactored from "send one digest to all subscribers" to "group by preference, send tailored digest per group". The single-source (BBC-only) case behaves identically to before for existing subscribers.

- **`src/daily_bot/emailer.py`** — `_prepare_article()` dict now includes `source` key. Any external callers of this function (none in the repo) would need to accept this new key — it's added, not renamed.

- **`src/daily_bot/templates/email.html.j2`** — the article rendering loop now branches on whether there's one source or many. Single-source emails look identical to before, except for the new source badge above the category.

- **`public/index.html`** — subscribe form now has a checkbox row below the email input. Existing markup is preserved; the change is additive (no fields removed, no IDs changed).

- **`functions/index.js`** — `addSubscriber` accepts an optional `sources` field. The Cloud Function remains backward-compatible: clients that don't send `sources` get `["bbc"]` by default.

- **Test count**: 104 → 150 (46 new tests, 0 removed).

- **Coverage**: 81% → 82%. New code paths (G1 scraper, language prompts, routing logic, template branches) are exercised. The `g1.py` module is at 89% coverage.

### Fixed

- **`tests/integration/test_subscriber_routing.py`** — initial implementation captured raw base64-encoded MIME bodies. The test helper `_extract_html_body()` now properly decodes base64 and quoted-printable MIME parts, allowing the tests to assert on the decoded HTML.

### Security

- No new XSS surface area introduced. The Jinja2 email template continues to use `autoescape=True`. Cloud Function `latestNews` continues to XSS-escape article fields with the same `escapeHtml` helper. The new `source` field is also `escapeHtml`-d.

- Source preferences are validated against a `VALID_SOURCES = ["bbc", "g1"]` allowlist in the Cloud Function. Invalid values are silently filtered out, never stored.

### Performance

- No regression. The preference-group dispatch sends one email per unique source tuple. For a deployment with only BBC subscribers (the common case), this is **one email** — the same as before. The grouping overhead is O(n_subscribers) and the rendered email is built only once per group.

- The G1 homepage fetch is a single `httpx.AsyncClient.get()` with the same connection-pool and timeout settings as BBC. The article scraper is also a single GET per URL with the same semaphore-bounded concurrency.

### Notes for Operators

- **To enable G1 in production**: set `SOURCES=bbc,g1` in the GitHub Actions secret (or `.env`). The default remains `bbc` for backward compat.
- **Existing subscribers** without a `sources` field in Firestore will default to `["bbc"]` on the next read — no migration needed.
- **New subscribers** through the updated `public/index.html` will have their checkbox selections saved.
- **First G1 run**: the Firestore `subscribers` docs that pre-date this change will all be treated as BBC-only until they re-subscribe (or you backfill the `sources` field manually).
- **CI secrets** unchanged — no new secrets required for G1.

---

## [2.0.0] — 2026-06-10

### Added — Multi-Source Architecture (Phase 6 of earlier refactor)

- **`src/daily_bot/sources/`** — new package with `NewsSource` ABC, `SourceRegistry`, and `BBCSource` reference implementation. The orchestrator iterates over multiple sources.
- **`ScrapedArticle.source` and `Summary.source`** fields track which source produced each article.
- **22 unit tests** in `tests/unit/test_sources.py` for registry, protocol, BBCSource, default_registry.
- **3 integration tests** in `tests/integration/test_multi_source.py` for multi-source pipeline (tagging, unknown source, failure isolation).

### Added — Async, Concurrency & Reliability (Phase 4 of earlier refactor)

- Replaced `requests` with `httpx.AsyncClient`.
- Migrated from `google.generativeai` (deprecated) to `google-genai` SDK with async via `client.aio.models.generate_content()`.
- `asyncio.gather` for concurrent scraping/summarizing.
- `CircuitBreaker` class (CLOSED → OPEN → HALF_OPEN) for resilience.
- `health.py` dead-man's-switch writing to `health/last_run` Firestore doc.
- Intermediate Firestore writes after each article.
- `send_daily_digest_async()` with `asyncio.to_thread()` for SMTP.

### Added — Testing Infrastructure (Phase 5 of earlier refactor)

- 79 tests across 8 files with 79% coverage.
- `pytest-asyncio` with `asyncio_mode = "auto"`.
- `httpx.MockTransport` for HTTP mocking.
- `FakeGeminiClient` for Gemini mocking.
- `MockFirestoreClient` for DB mocking.
- `FakeSMTP` for email mocking.

### Added — Subscriber Integration (Phase 3 of earlier refactor)

- `main.py` queries Firestore `subscribers` collection and sends to each individually.
- Per-subscriber audit logging to `email_log`.
- Batch+delay SMTP dispatch.
- Email template stored in Firestore `emailTemplates/latest` for Cloud Function to use.
- Updated `functions/index.js` to fetch template from Firestore.

### Added — Core Refactoring (Phase 2 of earlier refactor)

- Jinja2 template (autoescape=True) — XSS fix.
- Article deduplication against `dailySummaries/{date}`.
- `logging` module throughout (no more `print`).
- `GeminiClient` class.
- Category allowlist validation (`VALID_CATEGORIES`).
- Removed inline HTML.

### Added — Project Structure (Phase 1 of earlier refactor)

- `src/daily_bot/` package with `pyproject.toml` (hatchling).
- Pydantic `BaseSettings` config.
- Data models in `models.py`.
- Lazy Firestore init in `db.py`.
- Removed module-level side effects.
- Updated `.env.example`.

---

## Pre-history

Before changelog tracking began, the project lived as a collection of standalone scripts (`main.py`, `MyNews.py`, `email_sender.py`, `daily_news_summary.json`) with hardcoded values and no test coverage.

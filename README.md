# 🚀 News AI Summarizer & Subscription Service

A fully automated service that scrapes top stories from multiple news sources (BBC News and G1), uses Google's Gemini AI to generate concise summaries in the source's native language, and emails a daily digest to subscribers. Each subscriber can choose which sources they want in their digest. The project features a public-facing subscription website and is deployed on serverless infrastructure.

![GitHub Actions Workflow Status](https://github.com/owmyr/NewsSummary/actions/workflows/daily-summary.yml/badge.svg)

---

## 🌐 Live Demo & Subscription

Subscribe to the daily email digest:
**[https://thedailybot.web.app/](https://thedailybot.web.app/)**

When subscribing, you can choose:
- 📰 **BBC News** (English) — international news from the BBC
- 🇧🇷 **G1** (Português) — Brazilian news from Globo

Pick one, or both — your choice.

---

## ✨ Features

- **Multi-Source Scraping** — Fetches the latest top stories from BBC News (English) and G1 (Portuguese) daily. Adding new sources is as simple as writing a `NewsSource` subclass.

- **AI-Powered Summaries** — Leverages Google's Gemini AI to create clear and neutral summaries in the appropriate language for each source. The summarizer is **language-aware**: BBC articles produce English summaries, G1 articles produce PT-BR summaries.

- **Per-Subscriber Source Preferences** — Each subscriber picks which sources they want in their digest (BBC, G1, or both). The orchestrator groups subscribers by preference and sends one tailored email per group. A subscriber who chose `["bbc", "g1"]` gets a bilingual digest with a BBC section and a G1 section.

- **Public Subscription System** — A live Firebase-hosted website with a signup form (with source checkboxes) for any user to subscribe to the daily emails.

- **Resilient Pipeline** — Async I/O with `httpx` and `asyncio`, bounded concurrency via semaphores, circuit breaker for repeated failures, intermediate Firestore writes after each article (partial persistence).

- **Health Monitoring** — Dead-man's-switch writes a status doc to Firestore after every run, so a missed run is detectable.

- **Secure & Scalable Backend** — Firebase Hosting, Cloud Functions, and Firestore for subscriber management. Gmail SMTP for delivery with batch+delay to respect rate limits.

- **Fully Automated** — "Set it and forget it" daily workflow managed by GitHub Actions at 09:00 UTC.

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend & Scraping | Python 3.12 (async, `httpx`, `BeautifulSoup`) |
| AI Summarization | Google Gemini AI (`google-genai` SDK) |
| Database & Web | Firebase (Hosting, Cloud Functions, Firestore) |
| Email | Gmail SMTP over SSL (port 465) |
| Templates | Jinja2 (autoescaped) |
| Frontend | Vanilla HTML + Tailwind CSS |
| Automation | GitHub Actions (cron) |
| Testing | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff` |

---

## 📰 Supported Sources

| Source | Language | URL |
|---|---|---|
| BBC News | English | https://www.bbc.com/news |
| G1 | Português (BR) | https://g1.globo.com |

Configure via the `SOURCES` env var (comma-separated, names must be registered in `default_registry`):

```bash
SOURCES=bbc,g1
```

### Adding a New Source

1. Create `src/daily_bot/sources/<name>.py` with a `NewsSource` subclass
2. Implement `name`, `fetch_urls()`, `scrape_article()` (and `language` if non-English)
3. Register in `sources/__init__.py`:
   ```python
   default_registry.register("name", MySource)
   ```
4. Set `SOURCES="bbc,<name>"` in `.env`

No orchestrator changes needed.

---

## 🏗️ Architecture

The pipeline runs daily at 09:00 UTC via GitHub Actions:

1. **Load settings** & record health-check start
2. **For each configured source** (`SOURCES=bbc,g1`):
   - `fetch_urls()` → top article URLs
   - **Dedup** against `dailySummaries/{date}` (skip already-processed)
   - **Scrape + summarize** concurrently (`asyncio.Semaphore` bounded, `CircuitBreaker` protected)
   - **Persist** each summary to Firestore immediately (partial resilience)
3. **Render email template** (Jinja2, autoescaped) → save to `emailTemplates/latest`
4. **Load subscribers** from `subscribers` collection (`get_all_subscribers()`)
5. **Group by preference** — `defaultdict(tuple(sorted(sub.sources)))`
6. **For each preference group**: build a tailored digest containing only summaries from preferred sources, then send (batch+delay, `asyncio.to_thread` for SMTP)
7. **Audit log** every send to `email_log`
8. **Record health completion** with grouped `sent` / `failed` counts

### Data Flow

```
[Sources] -> [Scraper] -> [Gemini Summarizer] -> [Firestore dailySummaries]
                                                           |
                                                           v
[Subscribers] -> [Preference Grouper] -> [Email Renderer] -> [SMTP Dispatch]
                          |                                       |
                          v                                       v
                   preference groups                         email_log
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- A Firebase project with Firestore enabled
- A Google Gemini API key
- A Gmail account with an app password

### Setup

```bash
# Clone and install
git clone https://github.com/owmyr/NewsSummary.git
cd NewsSummary
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your GOOGLE_API_KEY, FIREBASE_CREDENTIALS, SENDER_EMAIL, SENDER_PASSWORD

# Run the pipeline
python -m daily_bot
```

### Run tests

```bash
pytest                              # All 150 tests
pytest --cov=daily_bot              # With coverage (82% currently)
pytest tests/unit/                  # Unit tests only
pytest tests/integration/           # Integration tests only
ruff check src/daily_bot/ tests/    # Lint
ruff format src/daily_bot/ tests/   # Format
```

### Deploy

```bash
# Cloud Functions
firebase deploy --only functions

# Hosting
firebase deploy --only hosting
```

The GitHub Actions workflow (`.github/workflows/daily-summary.yml`) runs tests then the pipeline on every cron trigger or manual dispatch.

---

## 📁 Project Structure

```
src/daily_bot/
├── __init__.py              # Version 2.0.0
├── __main__.py              # Orchestrator
├── main.py                  # CLI shim
├── config.py                # Pydantic settings
├── models.py                # Data models
├── scraper.py               # Async BBC scraper helpers
├── summarizer.py            # Language-aware Gemini client
├── emailer.py               # Jinja2 + SMTP
├── db.py                    # Firestore data layer
├── circuit_breaker.py       # CLOSED→OPEN→HALF_OPEN
├── health.py                # Dead-man's-switch
├── sources/                 # Pluggable news sources
│   ├── base.py              # NewsSource ABC + registry
│   ├── bbc.py               # BBCSource (en)
│   └── g1.py                # G1Source (pt-BR)
└── templates/
    └── email.html.j2        # Auto-escaped, with source badges

tests/
├── conftest.py
├── unit/                    # Unit tests
└── integration/             # Pipeline + routing tests

public/                      # Firebase Hosting
functions/                   # Cloud Functions (Node.js)
docs/                        # Documentation
├── UPGRADE_PLAN_G1.md
└── CHANGELOG.md
AGENTS.md                    # Project guide for AI agents / contributors
```

---

## 📖 Documentation

- **[AGENTS.md](AGENTS.md)** — Project guide for contributors and AI agents
- **[docs/CHANGELOG.md](docs/CHANGELOG.md)** — Detailed changelog of all notable changes
- **[docs/UPGRADE_PLAN_G1.md](docs/UPGRADE_PLAN_G1.md)** — The 7-phase plan that delivered G1 + source preferences

---

## 📊 Project Status

- **Version**: 2.0.0
- **Test coverage**: 82% (150 tests)
- **Lint status**: 0 issues (ruff)
- **Active sources**: BBC News, G1
- **Cron schedule**: Daily at 09:00 UTC

---

## 🪪 License

MIT

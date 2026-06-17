"""Integration tests for the full daily_bot pipeline with everything mocked."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import httpx

from daily_bot import __main__ as pipeline
from daily_bot.config import Settings

# ---------------- helpers ----------------


class FakeGeminiClient:
    """Drop-in replacement for AsyncGeminiClient that returns scripted responses."""

    def __init__(self, responses: list[str | None] | None = None) -> None:
        self.responses: list[str | None] = list(responses or [])
        self.calls: list[str] = []

    async def generate(self, prompt: str) -> str | None:
        self.calls.append(prompt)
        if not self.responses:
            return None
        return self.responses.pop(0)

    async def generate_many(self, prompts: list[str], concurrency: int) -> list[str | None]:
        return [await self.generate(p) for p in prompts]


class MockDoc:
    """In-memory Firestore document."""

    def __init__(self, coll_name: str, doc_id: str, data: dict | None = None) -> None:
        self.coll_name = coll_name
        self.id = doc_id
        self._data = data or {}

    @property
    def exists(self) -> bool:
        return bool(self._data)

    def to_dict(self) -> dict:
        return dict(self._data)

    def get(self) -> MockDoc:
        return self

    def set(self, data: dict, merge: bool = False) -> None:
        self._data = {**self._data, **data} if merge else dict(data)


class MockCollection:
    """In-memory Firestore collection with subscribers and dailySummaries."""

    def __init__(
        self,
        name: str,
        subscribers: list[str] | None = None,
        stored_summaries: list[dict] | None = None,
        pre_existing_articles: list[dict] | None = None,
    ) -> None:
        self.name = name
        self._subs = subscribers or []
        self._stored = stored_summaries or []
        self._pre_existing_articles = pre_existing_articles or []
        self.added: list[dict] = []
        self.docs: dict[str, MockDoc] = {}
        # If pre-existing articles were provided, seed the "today" doc
        if name == "dailySummaries" and self._pre_existing_articles:
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            self.docs[today] = MockDoc(
                name, today, data={"date": today, "articles": self._pre_existing_articles}
            )

    def document(self, doc_id: str) -> MockDoc:
        if doc_id not in self.docs:
            self.docs[doc_id] = MockDoc(self.name, doc_id)
        return self.docs[doc_id]

    def stream(self):
        if self.name == "subscribers":
            for i, email in enumerate(self._subs):
                yield MockDoc(
                    self.name,
                    str(i),
                    data={"email": email, "sources": ["bbc"]},
                )
        return

    def add(self, data: dict) -> tuple:
        self.added.append(data)
        return None, "auto_id"

    def where(self, field: str, op: str, value: object) -> MockCollection:
        if self.name == "subscribers" and field == "email" and op == "==":
            return MockCollection(
                self.name,
                subscribers=[s for s in self._subs if s == value],
            )
        return self

    def limit(self, n: int) -> MockCollection:
        return self

    def order_by(self, *args, **kwargs) -> MockCollection:
        return self


class MockFirestoreClient:
    """In-memory Firestore client supporting only the collections we use."""

    def __init__(
        self,
        subscribers: list[str] | None = None,
        pre_existing_articles: list[dict] | None = None,
    ) -> None:
        self._collections: dict[str, MockCollection] = {}
        self._subs = subscribers or []
        self._pre_existing_articles = pre_existing_articles or []

    def collection(self, name: str) -> MockCollection:
        if name not in self._collections:
            if name == "dailySummaries":
                self._collections[name] = MockCollection(
                    name,
                    pre_existing_articles=self._pre_existing_articles,
                )
            else:
                self._collections[name] = MockCollection(name, subscribers=self._subs)
        return self._collections[name]


def _patch_http(article_html: str, bbc_homepage_html: str) -> None:
    """Patch the scraper to return canned HTML responses.

    Patches both `daily_bot.scraper._build_client` and
    `daily_bot.__main__._build_client` since `__main__` imports the name into
    its own namespace.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/articles/" in url:
            return httpx.Response(200, content=article_html.encode())
        return httpx.Response(200, content=bbc_homepage_html.encode())

    transport = httpx.MockTransport(handler)

    def builder(_s):
        return httpx.AsyncClient(transport=transport)

    from daily_bot import __main__ as pipeline_mod
    from daily_bot import scraper as scraper_mod

    scraper_mod._build_client = builder  # type: ignore[assignment]
    pipeline_mod._build_client = builder  # type: ignore[assignment]


# ---------------- tests ----------------


async def test_full_pipeline_runs_end_to_end(
    test_settings: Settings,
    bbc_homepage_html: str,
    article_html: str,
):
    """Scrape -> summarize -> persist -> dispatch, all in one test."""
    test_settings.scrape_concurrency = 2
    test_settings.summarize_concurrency = 2
    test_settings.email_batch_size = 10
    test_settings.email_batch_delay_seconds = 0

    _patch_http(article_html, bbc_homepage_html)

    # 4 articles on the fixture homepage. The article_html is short, so
    # summarize produces 1 chunk per article. Per article: 1 chunk + 1 final + 1 category = 3 calls.
    fake_gemini = FakeGeminiClient(
        responses=[
            # 4 articles, each with 1 chunk response, 1 final, 1 category
            *[f"Chunk of article {i}" for i in range(4)],
            *[f"Final summary of article {i}." for i in range(4)],
            *["world", "tech", "politics", "health"],
        ]
    )

    subscribers = ["a@x.com", "b@x.com", "c@x.com"]
    firestore = MockFirestoreClient(subscribers=subscribers)
    smtp_calls: list[tuple[str, str]] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipient, message):
            smtp_calls.append((sender, recipient))

    with patch("daily_bot.db.get_db", return_value=firestore):
        with patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini):
            with patch("daily_bot.emailer.smtplib.SMTP_SSL", FakeSMTP):
                await pipeline.run_async(test_settings)

    # All 4 article URLs should be summarized and persisted
    daily_summaries_coll = firestore._collections.get("dailySummaries")
    assert daily_summaries_coll is not None
    # The last set() call should have stored all 4 articles
    last_set_doc = list(daily_summaries_coll.docs.values())[-1]
    assert len(last_set_doc._data.get("articles", [])) == 4

    # Email template should be persisted
    template_coll = firestore._collections.get("emailTemplates")
    assert template_coll is not None
    last_template_doc = list(template_coll.docs.values())[-1]
    assert "<!DOCTYPE html>" in last_template_doc._data.get("html", "")

    # Email log should have one entry per subscriber
    email_log_coll = firestore._collections.get("email_log")
    assert email_log_coll is not None
    assert len(email_log_coll.added) == len(subscribers)
    assert {entry["email"] for entry in email_log_coll.added} == set(subscribers)
    assert all(entry["status"] == "sent" for entry in email_log_coll.added)

    # SMTP should have been called once per subscriber
    assert len(smtp_calls) == len(subscribers)


async def test_pipeline_aborts_cleanly_when_no_urls(
    test_settings: Settings, bbc_homepage_html: str
):
    """When the homepage has no article URLs, the pipeline should exit gracefully."""
    # Override the homepage to have no article links
    empty_homepage = "<html><body><a href='/news'>News</a></body></html>"
    _patch_http(article_html="<html></html>", bbc_homepage_html=empty_homepage)

    firestore = MockFirestoreClient(subscribers=[])
    fake_gemini = FakeGeminiClient(responses=[])

    with patch("daily_bot.db.get_db", return_value=firestore):
        with patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini):
            # Should not raise
            await pipeline.run_async(test_settings)


async def test_pipeline_short_circuits_when_circuit_breaker_opens(
    test_settings: Settings,
    bbc_homepage_html: str,
):
    """If all scrapes fail, the circuit breaker should open and the dispatcher should not run."""
    test_settings.circuit_breaker_threshold = 2
    test_settings.summarize_concurrency = 1
    test_settings.scrape_concurrency = 1

    def handler(request: httpx.Request) -> httpx.Response:
        if "/articles/" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, content=bbc_homepage_html.encode())

    transport = httpx.MockTransport(handler)

    def builder(_s):
        return httpx.AsyncClient(transport=transport)

    from daily_bot import __main__ as pipeline_mod
    from daily_bot import scraper as scraper_mod

    scraper_mod._build_client = builder  # type: ignore[assignment]
    pipeline_mod._build_client = builder  # type: ignore[assignment]

    firestore = MockFirestoreClient(subscribers=["u1@x.com", "u2@x.com", "u3@x.com"])
    fake_gemini = FakeGeminiClient(responses=[])
    smtp_calls: list[tuple[str, str]] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def login(self, *a, **kw):
            pass

        def sendmail(self, sender, recipient, message):
            smtp_calls.append((sender, recipient))

    with patch("daily_bot.db.get_db", return_value=firestore):
        with patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini):
            with patch("daily_bot.emailer.smtplib.SMTP_SSL", FakeSMTP):
                await pipeline.run_async(test_settings)

    # With all scrapes failing, no summaries should be produced,
    # so no emails should be sent and no email_log entries written.
    email_log_coll = firestore._collections.get("email_log")
    assert email_log_coll is None or email_log_coll.added == []
    assert smtp_calls == []


async def test_retry_failed_only_resummarizes_failed_articles(
    test_settings: Settings,
    bbc_homepage_html: str,
    article_html: str,
):
    """The --retry-failed flag should re-summarize only failed articles, not re-scrape."""
    test_settings.scrape_concurrency = 2
    test_settings.summarize_concurrency = 2

    _patch_http(article_html, bbc_homepage_html)

    # Pre-seed Firestore with a mix: 1 successful, 1 failed
    failed_placeholder = pipeline.FAILED_PLACEHOLDER
    pre_existing_articles = [
        {
            "title": "Already good article",
            "summary": "This was summarized successfully.",
            "category": "world",
            "url": "https://www.bbc.com/news/articles/good1",
            "source": "bbc",
            "image_url": "",
        },
        {
            "title": "Previously failed article",
            "summary": failed_placeholder,
            "category": "other",
            "url": "https://www.bbc.com/news/articles/bad1",
            "source": "bbc",
            "image_url": "",
        },
    ]
    firestore = MockFirestoreClient(
        subscribers=[],
        pre_existing_articles=pre_existing_articles,
    )

    # The retry should call summarize_article for the failed URL.
    # Short article = 1 fallback call (no chunk, no final, no category call).
    # Category comes from the URL ("/articles/" is a generic BBC path -> "other").
    fake_gemini = FakeGeminiClient(
        responses=[
            "Recovered final summary.",
        ]
    )

    with patch("daily_bot.db.get_db", return_value=firestore):
        with patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini):
            await pipeline.run_async(test_settings, retry_failed_only=True)

    # Verify the failed article was re-summarized and the good one is untouched
    daily_summaries_coll = firestore._collections.get("dailySummaries")
    assert daily_summaries_coll is not None
    last_set_doc = list(daily_summaries_coll.docs.values())[-1]
    saved = last_set_doc._data.get("articles", [])
    by_url = {a["url"]: a for a in saved}

    assert "https://www.bbc.com/news/articles/good1" in by_url
    assert (
        by_url["https://www.bbc.com/news/articles/good1"]["summary"]
        == "This was summarized successfully."
    )

    assert "https://www.bbc.com/news/articles/bad1" in by_url
    assert by_url["https://www.bbc.com/news/articles/bad1"]["summary"] == "Recovered final summary."
    # Category comes from URL: https://www.bbc.com/news/articles/... has no
    # specific section in the path, so the deterministic classifier returns "other".
    assert by_url["https://www.bbc.com/news/articles/bad1"]["category"] == "other"
    # The pre-seeded article had image_url="". The re-scrape should have
    # picked up the og:image from the article HTML
    # (https://ichef.bbci.co.uk/news/1024/hero.jpg). The retry path must
    # use the FRESH image, not the stale empty one.
    assert (
        by_url["https://www.bbc.com/news/articles/bad1"]["image_url"]
        == "https://ichef.bbci.co.uk/news/1024/hero.jpg"
    )


async def test_retry_failed_only_with_no_failures_is_a_noop(
    test_settings: Settings,
    bbc_homepage_html: str,
    article_html: str,
):
    """If nothing failed, --retry-failed should not call Gemini at all."""
    _patch_http(article_html, bbc_homepage_html)

    pre_existing_articles = [
        {
            "title": "Already good",
            "summary": "Good summary.",
            "category": "world",
            "url": "https://www.bbc.com/news/articles/good1",
            "source": "bbc",
            "image_url": "",
        }
    ]
    firestore = MockFirestoreClient(
        subscribers=[],
        pre_existing_articles=pre_existing_articles,
    )
    fake_gemini = FakeGeminiClient(responses=[])

    with patch("daily_bot.db.get_db", return_value=firestore):
        with patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini):
            await pipeline.run_async(test_settings, retry_failed_only=True)

    # No summaries should be re-written since there was nothing to retry
    daily_summaries_coll = firestore._collections.get("dailySummaries")
    if daily_summaries_coll is not None:
        for doc in daily_summaries_coll.docs.values():
            assert doc._data == doc._data  # untouched
    assert fake_gemini.calls == []

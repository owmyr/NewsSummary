"""Integration tests for multi-source pipeline behavior.

Verifies that the orchestrator can run with multiple sources and that
each source's articles are tagged with the correct source name.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from daily_bot import __main__ as pipeline
from daily_bot.config import Settings
from daily_bot.sources import BBCSource
from daily_bot.sources.base import NewsSource, SourceRegistry

# ---------------- helpers ----------------


class FakeGeminiClient:
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
    def __init__(self, coll_name: str, doc_id: str, data: dict | None = None) -> None:
        self.coll_name = coll_name
        self.id = doc_id
        self._data = data or {}

    @property
    def exists(self) -> bool:
        return bool(self._data)

    def to_dict(self) -> dict:
        return dict(self._data)

    def get(self) -> "MockDoc":
        return self

    def set(self, data: dict, merge: bool = False) -> None:
        self._data = {**self._data, **data} if merge else dict(data)


class MockCollection:
    def __init__(self, name: str, subscribers: list[str] | None = None) -> None:
        self.name = name
        self._subs = subscribers or []
        self.added: list[dict] = []
        self.docs: dict[str, MockDoc] = {}

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
                    data={"email": email, "sources": ["bbc", "guardian"]},
                )
        return

    def add(self, data: dict) -> tuple:
        self.added.append(data)
        return None, "auto_id"

    def where(self, *args, **kwargs) -> "MockCollection":
        return self

    def limit(self, n: int) -> "MockCollection":
        return self

    def order_by(self, *args, **kwargs) -> "MockCollection":
        return self


class MockFirestoreClient:
    def __init__(self, subscribers: list[str] | None = None) -> None:
        self._collections: dict[str, MockCollection] = {}
        self._subs = subscribers or []

    def collection(self, name: str) -> MockCollection:
        if name not in self._collections:
            self._collections[name] = MockCollection(name, subscribers=self._subs)
        return self._collections[name]


class FakeSMTP:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, *a, **kw):
        pass

    def sendmail(self, *a, **kw):
        pass


class FakeSource(NewsSource):
    """A test source that returns canned URLs and articles.

    Data is provided via class-level attributes so the registry can
    instantiate it with no arguments.
    """

    fake_urls: list[str] = []
    fake_articles: dict[str, dict] = {}

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return type(self).__name__.lower().replace("source", "")

    async def fetch_urls(self, client, limit):
        return self.fake_urls[:limit]

    async def scrape_article(self, client, url):
        from daily_bot.models import ScrapedArticle

        data = self.fake_articles.get(url)
        if data is None:
            return None
        return ScrapedArticle(source=self.name, **data)


@pytest.fixture
def bbc_stub():
    """Replace BBCSource's network methods for the duration of one test.

    Restores the originals even if the test raises.
    """
    from daily_bot.sources import bbc as bbc_mod

    original_fetch = bbc_mod.BBCSource.fetch_urls
    original_scrape = bbc_mod.BBCSource.scrape_article

    yield

    bbc_mod.BBCSource.fetch_urls = original_fetch  # type: ignore[method-assign]
    bbc_mod.BBCSource.scrape_article = original_scrape  # type: ignore[method-assign]


# ---------------- tests ----------------


async def test_multi_source_pipeline_tags_each_article(test_settings: Settings, bbc_stub: dict):
    """Each source's summaries should be tagged with the source name."""
    from daily_bot.models import ScrapedArticle

    bbc_urls = ["https://bbc.com/articles/1", "https://bbc.com/articles/2"]
    guardian_urls = ["https://guardian.com/articles/1"]

    bbc_articles = {
        "https://bbc.com/articles/1": {
            "url": "https://bbc.com/articles/1",
            "title": "BBC Article 1",
            "content": "BBC content 1",
            "image_url": "https://x.com/b1.jpg",
        },
        "https://bbc.com/articles/2": {
            "url": "https://bbc.com/articles/2",
            "title": "BBC Article 2",
            "content": "BBC content 2",
            "image_url": "",
        },
    }
    guardian_articles = {
        "https://guardian.com/articles/1": {
            "url": "https://guardian.com/articles/1",
            "title": "Guardian Article 1",
            "content": "Guardian content 1",
            "image_url": "https://x.com/g1.jpg",
        },
    }

    test_settings.sources = "bbc,guardian"
    test_settings.scrape_concurrency = 2
    test_settings.summarize_concurrency = 2
    test_settings.email_batch_size = 10
    test_settings.email_batch_delay_seconds = 0

    class GuardianSource(FakeSource):
        fake_urls = guardian_urls
        fake_articles = guardian_articles

    fake_registry = SourceRegistry()
    fake_registry.register("bbc", BBCSource)
    fake_registry.register("guardian", GuardianSource)

    async def fake_fetch_urls(self, client, limit):
        return bbc_urls[:limit]

    async def fake_scrape_article(self, client, url):
        data = bbc_articles.get(url)
        if data is None:
            return None
        return ScrapedArticle(source="bbc", **data)

    from daily_bot.sources import bbc as bbc_mod

    bbc_mod.BBCSource.fetch_urls = fake_fetch_urls  # type: ignore[method-assign]
    bbc_mod.BBCSource.scrape_article = fake_scrape_article  # type: ignore[method-assign]

    # 2 BBC + 1 Guardian, 1 chunk each (short content) + 1 final + 1 category = 9 calls
    fake_gemini = FakeGeminiClient(
        responses=[
            "BBC chunk 1",
            "BBC chunk 2",
            "Guardian chunk 1",
            "BBC final 1",
            "BBC final 2",
            "Guardian final 1",
            "world",
            "tech",
            "politics",
        ]
    )
    firestore = MockFirestoreClient(subscribers=["a@x.com"])

    with (
        patch("daily_bot.db.get_db", return_value=firestore),
        patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini),
        patch("daily_bot.__main__.default_registry", fake_registry),
        patch("daily_bot.emailer.smtplib.SMTP_SSL", FakeSMTP),
    ):
        await pipeline.run_async(test_settings)

    daily_summaries_coll = firestore._collections.get("dailySummaries")
    assert daily_summaries_coll is not None
    last_set_doc = list(daily_summaries_coll.docs.values())[-1]
    articles = last_set_doc._data.get("articles", [])
    assert len(articles) == 3

    sources_seen = {a["source"] for a in articles}
    assert sources_seen == {"bbc", "guardian"}

    bbc_titles = {a["title"] for a in articles if a["source"] == "bbc"}
    guardian_titles = {a["title"] for a in articles if a["source"] == "guardian"}
    assert bbc_titles == {"BBC Article 1", "BBC Article 2"}
    assert guardian_titles == {"Guardian Article 1"}


async def test_unknown_source_is_logged_and_skipped(test_settings: Settings, bbc_stub: dict):
    """An unknown source name should not crash the pipeline."""
    from daily_bot.models import ScrapedArticle
    from daily_bot.sources import bbc as bbc_mod

    test_settings.sources = "bbc,nonexistent"

    async def fake_fetch_urls(self, client, limit):
        return ["https://bbc.com/articles/1"]

    async def fake_scrape_article(self, client, url):
        return ScrapedArticle(
            source="bbc",
            url=url,
            title="Test",
            content="Content",
            image_url=None,
        )

    bbc_mod.BBCSource.fetch_urls = fake_fetch_urls  # type: ignore[method-assign]
    bbc_mod.BBCSource.scrape_article = fake_scrape_article  # type: ignore[method-assign]

    fake_gemini = FakeGeminiClient(responses=["chunk", "final", "world"])
    firestore = MockFirestoreClient(subscribers=["a@x.com"])

    with (
        patch("daily_bot.db.get_db", return_value=firestore),
        patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini),
        patch("daily_bot.emailer.smtplib.SMTP_SSL", FakeSMTP),
    ):
        await pipeline.run_async(test_settings)

    daily_summaries_coll = firestore._collections.get("dailySummaries")
    assert daily_summaries_coll is not None
    last_set_doc = list(daily_summaries_coll.docs.values())[-1]
    articles = last_set_doc._data.get("articles", [])
    assert len(articles) == 1
    assert articles[0]["source"] == "bbc"


async def test_continues_to_next_source_when_one_fails(test_settings: Settings, bbc_stub: dict):
    """If one source throws, the orchestrator should still try the others."""
    from daily_bot.sources import bbc as bbc_mod

    test_settings.sources = "bbc,guardian"

    async def bbc_fetch(self, client, limit):
        raise RuntimeError("simulated network failure")

    bbc_mod.BBCSource.fetch_urls = bbc_fetch  # type: ignore[method-assign]

    class GuardianSource(FakeSource):
        fake_urls = ["https://guardian.com/1"]
        fake_articles = {
            "https://guardian.com/1": {
                "url": "https://guardian.com/1",
                "title": "Guardian 1",
                "content": "Content",
                "image_url": "",
            },
        }

    fake_registry = SourceRegistry()
    fake_registry.register("bbc", BBCSource)
    fake_registry.register("guardian", GuardianSource)

    fake_gemini = FakeGeminiClient(responses=["chunk", "final", "world"])
    firestore = MockFirestoreClient(subscribers=["a@x.com"])

    with (
        patch("daily_bot.db.get_db", return_value=firestore),
        patch("daily_bot.__main__.AsyncGeminiClient", return_value=fake_gemini),
        patch("daily_bot.__main__.default_registry", fake_registry),
        patch("daily_bot.emailer.smtplib.SMTP_SSL", FakeSMTP),
    ):
        await pipeline.run_async(test_settings)

    daily_summaries_coll = firestore._collections.get("dailySummaries")
    assert daily_summaries_coll is not None
    last_set_doc = list(daily_summaries_coll.docs.values())[-1]
    articles = last_set_doc._data.get("articles", [])
    assert len(articles) == 1
    assert articles[0]["source"] == "guardian"

"""Integration tests for per-subscriber source-preference routing.

Verifies that the orchestrator dispatches each subscriber a digest that
only contains the news sources they subscribed to, and that subscribers
sharing the same preference set are sent the same email.
"""

from __future__ import annotations

import base64
import quopri
import re
from unittest.mock import patch

import pytest

from daily_bot import __main__ as pipeline
from daily_bot.config import Settings
from daily_bot.models import ScrapedArticle
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

    def get(self) -> MockDoc:
        return self

    def set(self, data: dict, merge: bool = False) -> None:
        self._data = {**self._data, **data} if merge else dict(data)


class MockCollection:
    """Collection that can yield subscribers with arbitrary source preferences."""

    def __init__(
        self,
        name: str,
        subscriber_docs: list[dict] | None = None,
    ) -> None:
        self.name = name
        self._subscriber_docs = subscriber_docs or []
        self.added: list[dict] = []
        self.docs: dict[str, MockDoc] = {}

    def document(self, doc_id: str) -> MockDoc:
        if doc_id not in self.docs:
            self.docs[doc_id] = MockDoc(self.name, doc_id)
        return self.docs[doc_id]

    def stream(self):
        if self.name == "subscribers":
            for i, data in enumerate(self._subscriber_docs):
                yield MockDoc(self.name, str(i), data=dict(data))
        return

    def add(self, data: dict) -> tuple:
        self.added.append(data)
        return None, "auto_id"

    def where(self, *args, **kwargs) -> MockCollection:
        return self

    def limit(self, n: int) -> MockCollection:
        return self

    def order_by(self, *args, **kwargs) -> MockCollection:
        return self


class MockFirestoreClient:
    def __init__(self, subscriber_docs: list[dict] | None = None) -> None:
        self._collections: dict[str, MockCollection] = {}
        self._subscriber_docs = subscriber_docs or []

    def collection(self, name: str) -> MockCollection:
        if name not in self._collections:
            if name == "subscribers":
                self._collections[name] = MockCollection(
                    name, subscriber_docs=self._subscriber_docs
                )
            else:
                self._collections[name] = MockCollection(name)
        return self._collections[name]


class _RecordingSMTP:
    """Captures every (recipient, html_body) pair on the class."""

    sends: list[tuple[str, str]] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, *args, **kwargs):
        pass

    def sendmail(self, sender: str, recipient: str, message: str) -> None:
        type(self).sends.append((recipient, message))


def _extract_html_body(message: str) -> str:
    """Extract and decode the HTML body from a raw MIME message.

    Handles both base64 and quoted-printable encodings.
    """
    boundary_match = re.search(r'boundary="([^"]+)"', message)
    if not boundary_match:
        return message
    boundary = boundary_match.group(1)

    parts = message.split(f"--{boundary}")
    for part in parts:
        if "Content-Type: text/html" in part:
            transfer_match = re.search(r"Content-Transfer-Encoding:\s*(\S+)", part, re.IGNORECASE)
            _, _, payload = part.partition("\n\n")
            if transfer_match and transfer_match.group(1).lower() == "base64":
                return base64.b64decode(payload).decode("utf-8", errors="replace")
            if transfer_match and transfer_match.group(1).lower() == "quoted-printable":
                return quopri.decodestring(payload).decode("utf-8", errors="replace")
            return payload
    return message


class FakeBBCSource(NewsSource):
    """Fake BBC source returning two canned articles."""

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "bbc"

    async def fetch_urls(self, client, limit):
        return ["https://bbc.com/a/1", "https://bbc.com/a/2"][:limit]

    async def scrape_article(self, client, url):
        data = {
            "https://bbc.com/a/1": ("BBC One", "BBC content one"),
            "https://bbc.com/a/2": ("BBC Two", "BBC content two"),
        }
        if url not in data:
            return None
        title, content = data[url]
        return ScrapedArticle(source="bbc", url=url, title=title, content=content, image_url="")


class FakeG1Source(NewsSource):
    """Fake G1 source returning two canned articles."""

    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "g1"

    @property
    def language(self) -> str:
        return "pt-BR"

    async def fetch_urls(self, client, limit):
        return ["https://g1.globo.com/a/1", "https://g1.globo.com/a/2"][:limit]

    async def scrape_article(self, client, url):
        data = {
            "https://g1.globo.com/a/1": ("G1 Um", "G1 conteudo um"),
            "https://g1.globo.com/a/2": ("G1 Dois", "G1 conteudo dois"),
        }
        if url not in data:
            return None
        title, content = data[url]
        return ScrapedArticle(source="g1", url=url, title=title, content=content, image_url="")


def _fake_registry() -> SourceRegistry:
    reg = SourceRegistry()
    reg.register("bbc", FakeBBCSource)
    reg.register("g1", FakeG1Source)
    return reg


def _fake_gemini() -> FakeGeminiClient:
    """Enough scripted responses for 4 articles: 4 chunks, 4 finals, 4 categories."""
    return FakeGeminiClient(
        responses=[
            "BBC chunk 1",
            "BBC chunk 2",
            "G1 chunk 1",
            "G1 chunk 2",
            "BBC final 1",
            "BBC final 2",
            "G1 final 1",
            "G1 final 2",
            "world",
            "tech",
            "politics",
            "business",
        ]
    )


def _configure_test_settings(settings: Settings) -> None:
    settings.sources = "bbc,g1"
    settings.scrape_concurrency = 2
    settings.summarize_concurrency = 2
    settings.email_batch_size = 10
    settings.email_batch_delay_seconds = 0


@pytest.fixture(autouse=True)
def _reset_smtp_sends():
    _RecordingSMTP.sends = []
    yield
    _RecordingSMTP.sends = []


# ---------------- tests ----------------


async def test_subscribers_with_only_bbc_get_only_bbc_articles(test_settings: Settings):
    """Subscribers with sources=['bbc'] should only receive BBC summaries."""
    _configure_test_settings(test_settings)

    firestore = MockFirestoreClient(
        subscriber_docs=[
            {"email": "alice@example.com", "sources": ["bbc"]},
            {"email": "bob@example.com", "sources": ["bbc"]},
        ]
    )

    with (
        patch("daily_bot.db.get_db", return_value=firestore),
        patch("daily_bot.__main__.AsyncGeminiClient", return_value=_fake_gemini()),
        patch("daily_bot.__main__.default_registry", _fake_registry()),
        patch("daily_bot.emailer.smtplib.SMTP_SSL", _RecordingSMTP),
    ):
        await pipeline.run_async(test_settings)

    recipients = {r for r, _ in _RecordingSMTP.sends}
    assert recipients == {"alice@example.com", "bob@example.com"}

    for _, message in _RecordingSMTP.sends:
        assert "BBC One" in message
        assert "BBC Two" in message
        assert "G1 Um" not in message
        assert "G1 Dois" not in message


async def test_subscribers_with_only_g1_get_only_g1_articles(test_settings: Settings):
    """Subscribers with sources=['g1'] should only receive G1 summaries."""
    _configure_test_settings(test_settings)

    firestore = MockFirestoreClient(
        subscriber_docs=[
            {"email": "carlos@example.com", "sources": ["g1"]},
        ]
    )

    with (
        patch("daily_bot.db.get_db", return_value=firestore),
        patch("daily_bot.__main__.AsyncGeminiClient", return_value=_fake_gemini()),
        patch("daily_bot.__main__.default_registry", _fake_registry()),
        patch("daily_bot.emailer.smtplib.SMTP_SSL", _RecordingSMTP),
    ):
        await pipeline.run_async(test_settings)

    assert len(_RecordingSMTP.sends) == 1
    recipient, message = _RecordingSMTP.sends[0]
    assert recipient == "carlos@example.com"
    assert "G1 Um" in message
    assert "G1 Dois" in message
    assert "BBC One" not in message
    assert "BBC Two" not in message


async def test_subscribers_with_both_sources_get_both(test_settings: Settings):
    """Subscribers with sources=['bbc', 'g1'] should receive every summary."""
    _configure_test_settings(test_settings)

    firestore = MockFirestoreClient(
        subscriber_docs=[
            {"email": "dana@example.com", "sources": ["bbc", "g1"]},
        ]
    )

    with (
        patch("daily_bot.db.get_db", return_value=firestore),
        patch("daily_bot.__main__.AsyncGeminiClient", return_value=_fake_gemini()),
        patch("daily_bot.__main__.default_registry", _fake_registry()),
        patch("daily_bot.emailer.smtplib.SMTP_SSL", _RecordingSMTP),
    ):
        await pipeline.run_async(test_settings)

    assert len(_RecordingSMTP.sends) == 1
    recipient, message = _RecordingSMTP.sends[0]
    assert recipient == "dana@example.com"
    body = _extract_html_body(message)
    assert "BBC One" in body
    assert "BBC Two" in body
    assert "G1 Um" in body
    assert "G1 Dois" in body


async def test_mixed_preferences_create_separate_emails(test_settings: Settings):
    """Different preference groups should each receive their own tailored digest."""
    _configure_test_settings(test_settings)

    firestore = MockFirestoreClient(
        subscriber_docs=[
            {"email": "bbc_only@example.com", "sources": ["bbc"]},
            {"email": "bbc_only_two@example.com", "sources": ["bbc"]},
            {"email": "g1_only@example.com", "sources": ["g1"]},
            {"email": "both@example.com", "sources": ["bbc", "g1"]},
            # Same preference written in a different order; should group with `both`.
            {"email": "both_reordered@example.com", "sources": ["g1", "bbc"]},
        ]
    )

    with (
        patch("daily_bot.db.get_db", return_value=firestore),
        patch("daily_bot.__main__.AsyncGeminiClient", return_value=_fake_gemini()),
        patch("daily_bot.__main__.default_registry", _fake_registry()),
        patch("daily_bot.emailer.smtplib.SMTP_SSL", _RecordingSMTP),
    ):
        await pipeline.run_async(test_settings)

    all_titles = {"BBC One", "BBC Two", "G1 Um", "G1 Dois"}

    def titles_in(message: str) -> frozenset[str]:
        body = _extract_html_body(message)
        return frozenset(t for t in all_titles if t in body)

    by_recipient: dict[str, frozenset[str]] = {
        recipient: titles_in(message) for recipient, message in _RecordingSMTP.sends
    }
    assert set(by_recipient) == {
        "bbc_only@example.com",
        "bbc_only_two@example.com",
        "g1_only@example.com",
        "both@example.com",
        "both_reordered@example.com",
    }

    bbc_only_titles = frozenset({"BBC One", "BBC Two"})
    g1_only_titles = frozenset({"G1 Um", "G1 Dois"})
    all_titles_frozen = frozenset(all_titles)

    assert by_recipient["bbc_only@example.com"] == bbc_only_titles
    assert by_recipient["bbc_only_two@example.com"] == bbc_only_titles
    assert by_recipient["g1_only@example.com"] == g1_only_titles
    assert by_recipient["both@example.com"] == all_titles_frozen
    assert by_recipient["both_reordered@example.com"] == all_titles_frozen

    # Subscribers with the same preference set should share the same article set.
    assert by_recipient["bbc_only@example.com"] == by_recipient["bbc_only_two@example.com"]
    assert by_recipient["both@example.com"] == by_recipient["both_reordered@example.com"]
    # Different preferences must produce different article sets.
    assert by_recipient["bbc_only@example.com"] != by_recipient["g1_only@example.com"]
    assert by_recipient["bbc_only@example.com"] != by_recipient["both@example.com"]

"""Shared pytest fixtures and test helpers."""

from __future__ import annotations

import pytest

from daily_bot.config import Settings
from daily_bot.models import Summary


@pytest.fixture
def test_settings() -> Settings:
    """A Settings instance with dummy values, suitable for tests."""
    return Settings(
        google_api_key="test-key",
        firebase_credentials='{"type":"service_account"}',
        sender_email="bot@example.com",
        sender_password="password1234",
    )


@pytest.fixture
def sample_summary() -> Summary:
    """A single Summary with realistic content."""
    return Summary(
        title="Hamas mobilises fighters in Gaza",
        summary="Hamas has recalled 7,000 security forces to reassert control.",
        category="world",
        url="https://www.bbc.com/news/articles/abc123",
        image_url="https://ichef.bbci.co.uk/news/1024/test.jpg",
    )


@pytest.fixture
def sample_summaries() -> list[Summary]:
    """Multiple Summary objects for rendering and dispatch tests."""
    return [
        Summary(
            title="Article One",
            summary="Summary one body text.",
            category="world",
            url="https://www.bbc.com/news/articles/one",
            image_url="https://example.com/one.jpg",
        ),
        Summary(
            title='Article "Two" with quotes',
            summary="Summary two & <html> chars.",
            category="tech",
            url="https://www.bbc.com/news/articles/two",
            image_url="",
        ),
        Summary(
            title="<script>alert(1)</script>",
            summary="Edge case content.",
            category="other",
            url="https://www.bbc.com/news/articles/three",
            image_url="https://x.k/y.jpg",
        ),
    ]


@pytest.fixture
def bbc_homepage_html() -> str:
    """A small BBC News homepage with several article links."""
    return """<!DOCTYPE html>
<html>
<body>
  <nav><a href="/news">News</a></nav>
  <main>
    <h1>Top Stories</h1>
    <a href="/news/articles/abc111">First headline</a>
    <a href="/news/articles/abc222">Second headline</a>
    <a href="/news/articles/abc333">Third headline</a>
    <a href="/news/articles/abc444">Fourth headline</a>
    <a href="/news/world">World section (not article)</a>
    <a href="/sport">Sport (not news)</a>
  </main>
</body>
</html>"""


@pytest.fixture
def article_html() -> str:
    """A small BBC article page with title, text blocks, and an image."""
    return """<!DOCTYPE html>
<html>
<head>
  <meta property="og:image" content="https://ichef.bbci.co.uk/news/1024/hero.jpg">
  <meta name="twitter:image" content="https://ichef.bbci.co.uk/news/1024/twitter.jpg">
</head>
<body>
  <main>
    <h1>Test Article Title</h1>
    <div data-component="text-block">
      <p>First paragraph of the article body.</p>
      <p>10:45 GMT</p>
      <p>Second paragraph of the article body.</p>
    </div>
    <div data-component="text-block">
      <p>Follow BBC on Twitter.</p>
      <p>Third paragraph with more content.</p>
    </div>
  </main>
</body>
</html>"""


class FakeGeminiClient:
    """A drop-in replacement for AsyncGeminiClient that returns scripted responses.

    Attributes
    ----------
    responses : list[str | None]
        Pre-canned responses, returned in order.
    """

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


@pytest.fixture
def fake_gemini() -> FakeGeminiClient:
    """Default FakeGeminiClient returning a realistic summary + category."""
    return FakeGeminiClient(
        responses=[
            "Partial summary text.",  # chunk 1
            "Another partial summary.",  # chunk 2
            "Final coherent summary of the article in about 150 words.",  # final
            "world",  # category
        ]
    )

"""Unit tests for the sources/ package and SourceRegistry."""

from __future__ import annotations

import pytest

from daily_bot.models import ScrapedArticle
from daily_bot.sources import BBCSource, NewsSource, SourceRegistry, default_registry
from daily_bot.sources.base import NewsSourceProtocol

# ---------------- SourceRegistry tests ----------------


def test_registry_starts_empty():
    reg = SourceRegistry()
    assert len(reg) == 0
    assert reg.names() == []


def test_registry_register_and_get():
    reg = SourceRegistry()
    reg.register("bbc", BBCSource)
    assert len(reg) == 1
    assert "bbc" in reg
    instance = reg.get("bbc")
    assert isinstance(instance, NewsSource)
    assert instance.name == "bbc"


def test_registry_get_unknown_raises():
    reg = SourceRegistry()
    with pytest.raises(KeyError, match="Unknown news source"):
        reg.get("nytimes")


def test_registry_get_error_message_lists_available():
    reg = SourceRegistry()
    reg.register("bbc", BBCSource)
    with pytest.raises(KeyError, match="bbc"):
        reg.get("guardian")


def test_registry_unregister():
    reg = SourceRegistry()
    reg.register("bbc", BBCSource)
    reg.unregister("bbc")
    assert "bbc" not in reg
    assert len(reg) == 0


def test_registry_unregister_unknown_does_not_raise():
    reg = SourceRegistry()
    reg.unregister("nope")  # should not raise


def test_registry_rejects_invalid_name():
    reg = SourceRegistry()
    with pytest.raises(ValueError, match="non-empty string"):
        reg.register("", BBCSource)


def test_registry_rejects_non_source_class():
    reg = SourceRegistry()
    with pytest.raises(ValueError, match="must subclass NewsSource"):
        reg.register("bad", dict)  # type: ignore[arg-type]


def test_registry_names_sorted():
    reg = SourceRegistry()
    reg.register("zen", BBCSource)
    reg.register("alpha", BBCSource)
    reg.register("mid", BBCSource)
    assert reg.names() == ["alpha", "mid", "zen"]


def test_registry_contains():
    reg = SourceRegistry()
    reg.register("bbc", BBCSource)
    assert "bbc" in reg
    assert "guardian" not in reg


# ---------------- NewsSource abstract class ----------------


def test_news_source_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        NewsSource()  # type: ignore[abstract]


def test_news_source_subclass_must_implement_methods():
    class IncompleteSource(NewsSource):
        name = "incomplete"

        async def fetch_urls(self, client, limit):
            return []

    with pytest.raises(TypeError):
        IncompleteSource()  # type: ignore[abstract]


# ---------------- BBCSource tests ----------------


def test_bbc_source_default_homepage():
    src = BBCSource()
    assert src.name == "bbc"
    assert src.homepage_url == "https://www.bbc.com/news"


def test_bbc_source_custom_homepage():
    src = BBCSource(homepage_url="https://www.bbc.com/sport")
    assert src.homepage_url == "https://www.bbc.com/sport"


def test_bbc_source_satisfies_protocol():
    """BBCSource should pass runtime protocol check."""
    src = BBCSource()
    assert isinstance(src, NewsSourceProtocol)


async def test_bbc_source_fetch_urls(bbc_homepage_html: str):
    """fetch_urls should delegate to the existing scraper function."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bbc_homepage_html.encode())

    transport = httpx.MockTransport(handler)

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            src = BBCSource()
            urls = await src.fetch_urls(client, limit=10)
            return urls

    urls = await go()
    assert len(urls) == 4
    for u in urls:
        assert "/news/articles/" in u


async def test_bbc_source_scrape_article_sets_source(article_html: str):
    """scrape_article should populate article.source with the source name."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=article_html.encode())

    transport = httpx.MockTransport(handler)

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            src = BBCSource()
            article = await src.scrape_article(client, "https://www.bbc.com/news/articles/abc")
            return article

    article = await go()
    assert article is not None
    assert article.source == "bbc"
    assert article.title == "Test Article Title"


async def test_bbc_source_scrape_article_returns_none_on_404():
    """scrape_article should return None for missing articles."""
    import httpx

    transport = httpx.MockTransport(lambda r: httpx.Response(404))

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            src = BBCSource()
            return await src.scrape_article(client, "https://www.bbc.com/news/articles/missing")

    article = await go()
    assert article is None


# ---------------- default registry tests ----------------


def test_default_registry_has_bbc():
    assert "bbc" in default_registry


def test_default_registry_get_bbc():
    src = default_registry.get("bbc")
    assert isinstance(src, BBCSource)


# ---------------- model source field tests ----------------


def test_scraped_article_source_field():
    article = ScrapedArticle(source="bbc", url="https://x.com", title="T", content="C")
    assert article.source == "bbc"


def test_scraped_article_source_defaults_to_empty():
    article = ScrapedArticle(url="https://x.com", title="T", content="C")
    assert article.source == ""

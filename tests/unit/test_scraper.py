"""Unit tests for the BBC scraper (with mocked HTTP)."""

from __future__ import annotations

import asyncio

import httpx
from bs4 import BeautifulSoup

from daily_bot.scraper import (
    _build_client,
    _extract_article_image,
    _extract_article_text,
    _normalize_url,
    get_top_story_urls_async,
    scrape_article_content_async,
    scrape_many,
)


def test_normalize_https():
    assert _normalize_url("https://example.com/x.jpg") == "https://example.com/x.jpg"


def test_normalize_protocol_relative():
    assert _normalize_url("//example.com/x.jpg") == "https://example.com/x.jpg"


def test_normalize_http_kept():
    assert _normalize_url("http://example.com/x.jpg") == "http://example.com/x.jpg"


def test_normalize_relative_returns_none():
    assert _normalize_url("/relative/path") is None


def test_normalize_empty():
    assert _normalize_url("") is None
    assert _normalize_url(None) is None


def test_normalize_list():
    assert _normalize_url(["https://a.com/x.jpg"]) == "https://a.com/x.jpg"
    assert _normalize_url([]) is None


def test_extract_text_from_text_blocks(bbc_homepage_html: str, article_html: str):
    soup = BeautifulSoup(article_html, "html.parser")
    text = _extract_article_text(soup)
    assert "First paragraph" in text
    assert "Second paragraph" in text
    assert "Third paragraph" in text
    # Junk lines should be in the raw text (we don't clean them here)
    assert "10:45 GMT" in text
    assert "Follow BBC" in text


def test_extract_text_falls_back_to_main():
    html = """<html><body><main>
        <p>Short p.</p>
        <p>A long enough paragraph to pass the filter.</p>
    </main></body></html>"""
    soup = BeautifulSoup(html, "html.parser")
    text = _extract_article_text(soup)
    # The fallback only includes paragraphs longer than 20 chars
    assert "Short p." not in text
    assert "A long enough paragraph" in text


def test_extract_image_from_og_tag(article_html: str):
    soup = BeautifulSoup(article_html, "html.parser")
    image = _extract_article_image(soup)
    assert image == "https://ichef.bbci.co.uk/news/1024/hero.jpg"


def test_extract_image_protocol_relative():
    html = """<html><head>
      <meta property="og:image" content="//cdn.example.com/img.jpg">
    </head></html>"""
    soup = BeautifulSoup(html, "html.parser")
    image = _extract_article_image(soup)
    assert image == "https://cdn.example.com/img.jpg"


def test_extract_image_fallback_to_figure():
    html = """<html><body>
      <main><figure><img src="https://x.com/fig.jpg"></figure></main>
    </body></html>"""
    soup = BeautifulSoup(html, "html.parser")
    image = _extract_article_image(soup)
    assert image == "https://x.com/fig.jpg"


def test_extract_image_srcset():
    html = """<html><body>
      <main><img srcset="https://x.com/small.jpg 480w, https://x.com/large.jpg 1024w"></main>
    </body></html>"""
    soup = BeautifulSoup(html, "html.parser")
    image = _extract_article_image(soup)
    # The largest (last) srcset entry should win
    assert image == "https://x.com/large.jpg"


def test_extract_image_none_when_missing():
    soup = BeautifulSoup("<html><body>no images</body></html>", "html.parser")
    assert _extract_article_image(soup) is None


async def test_get_top_story_urls_returns_unique_article_links(
    bbc_homepage_html: str,
):
    """URL extractor should find /news/articles/ links, dedupe, and limit."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=bbc_homepage_html.encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await get_top_story_urls_async(client, "https://www.bbc.com/news", limit=10)
    assert len(urls) == 4
    for url in urls:
        assert "/news/articles/" in url
        assert url.startswith("https://www.bbc.com/news/")


async def test_get_top_story_urls_respects_limit(bbc_homepage_html: str):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=bbc_homepage_html.encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await get_top_story_urls_async(client, "https://www.bbc.com/news", limit=2)
    assert len(urls) == 2


async def test_get_top_story_urls_handles_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        urls = await get_top_story_urls_async(client, "https://www.bbc.com/news", limit=5)
    assert urls == []


async def test_scrape_article_content_parses_title_and_image(article_html: str):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=article_html.encode())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        article = await scrape_article_content_async(
            client, "https://www.bbc.com/news/articles/abc"
        )
    assert article is not None
    assert article.title == "Test Article Title"
    assert "First paragraph" in article.content
    assert article.image_url == "https://ichef.bbci.co.uk/news/1024/hero.jpg"


async def test_scrape_article_content_returns_none_on_404():
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        article = await scrape_article_content_async(
            client, "https://www.bbc.com/news/articles/missing"
        )
    assert article is None


async def test_scrape_many_runs_concurrently_and_skips_failures(test_settings, article_html: str):
    """scrape_many should gather all URLs and drop None results."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "404" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, content=article_html.encode())

    transport = httpx.MockTransport(handler)

    # Monkey-patch _build_client to use the mock transport
    from daily_bot import scraper as scraper_mod

    def _mock_client(_: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    scraper_mod._build_client = _mock_client  # type: ignore[assignment]

    urls = [
        "https://www.bbc.com/news/articles/ok1",
        "https://www.bbc.com/news/articles/404page",
        "https://www.bbc.com/news/articles/ok2",
    ]
    results = await scrape_many(test_settings, urls, asyncio.Semaphore(2))
    assert len(results) == 2
    assert {r.url for r in results} == {
        "https://www.bbc.com/news/articles/ok1",
        "https://www.bbc.com/news/articles/ok2",
    }


async def test_build_client_has_timeout(test_settings):
    """The client factory should honor the timeout setting."""
    async with _build_client(test_settings) as client:
        assert client.timeout.connect == 15.0 or 15.0 in (
            client.timeout.connect,
            client.timeout.read,
            client.timeout.write,
            client.timeout.pool,
        )

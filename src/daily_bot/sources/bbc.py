"""BBC News source implementation.

Wraps the existing scraper functions into a NewsSource subclass.
This is the reference implementation; adding other sources means
writing analogous classes (e.g. GuardianSource, ReutersSource).
"""

from __future__ import annotations

import httpx

from ..models import ScrapedArticle
from ..scraper import (
    get_top_story_urls_async,
    scrape_article_content_async,
)
from .base import NewsSource


class BBCSource(NewsSource):
    """The BBC News homepage + article scraper."""

    DEFAULT_HOMEPAGE = "https://www.bbc.com/news"
    URL_HINT = "/news/articles/"

    def __init__(self, homepage_url: str | None = None) -> None:
        self._homepage_url = homepage_url or self.DEFAULT_HOMEPAGE

    @property
    def name(self) -> str:
        return "bbc"

    @property
    def homepage_url(self) -> str:
        return self._homepage_url

    async def fetch_urls(self, client: httpx.AsyncClient, limit: int) -> list[str]:
        return await get_top_story_urls_async(client, self._homepage_url, limit)

    async def scrape_article(self, client: httpx.AsyncClient, url: str) -> ScrapedArticle | None:
        article = await scrape_article_content_async(client, url)
        if article is not None:
            article.source = self.name
        return article

    def __repr__(self) -> str:
        return f"BBCSource(homepage_url={self._homepage_url!r})"

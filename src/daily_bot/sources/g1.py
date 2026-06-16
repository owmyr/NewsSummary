"""G1 (g1.globo.com) Brazilian news source implementation.

Discovers article URLs from the G1 homepage and extracts title, body and
lead image from individual articles. G1 article URLs end in ``.ghtml`` and
the homepage exposes them via ``a.feed-post-link`` anchors.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ..models import ScrapedArticle
from .base import NewsSource

logger = logging.getLogger(__name__)

_URL_SUFFIX = ".ghtml"
_TITLE_SELECTORS = ("h1", ".content-head__title")
_BODY_SELECTORS = ("article p", "main p", ".mc-article-body p")
_IMAGE_SELECTORS = ("article img", "main img")


def _normalize_url(url: str | list | None) -> str | None:
    """Coerce a BeautifulSoup attribute into an absolute https URL string."""
    if not url:
        return None
    if isinstance(url, list):
        if not url:
            return None
        url = url[0]

    url_str = str(url).strip()
    if url_str.startswith("//"):
        return "https:" + url_str
    if url_str.startswith(("http://", "https://")):
        return url_str
    return None


def _extract_article_text(soup: BeautifulSoup) -> str:
    """Extract the article body text using prioritized strategies."""
    for selector in _BODY_SELECTORS:
        paragraphs = soup.select(selector)
        texts = [p.get_text(strip=True) for p in paragraphs]
        texts = [t for t in texts if t]
        if texts:
            return "\n".join(texts)
    return "Could not find article content."


def _extract_article_image(soup: BeautifulSoup) -> str | None:
    """Extract the lead image, preferring og:image then article <img> tags."""
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image:
        url = _normalize_url(og_image.get("content"))
        if url:
            return url

    for selector in _IMAGE_SELECTORS:
        img = soup.select_one(selector)
        if not img:
            continue
        url = _normalize_url(img.get("src"))
        if url:
            return url
    return None


class G1Source(NewsSource):
    """The G1 (Globo) homepage + article scraper."""

    DEFAULT_HOMEPAGE = "https://g1.globo.com"

    def __init__(self, homepage_url: str | None = None) -> None:
        self._homepage_url = homepage_url or self.DEFAULT_HOMEPAGE

    @property
    def name(self) -> str:
        return "g1"

    @property
    def language(self) -> str:
        return "pt-BR"

    @property
    def homepage_url(self) -> str:
        return self._homepage_url

    async def fetch_urls(self, client: httpx.AsyncClient, limit: int) -> list[str]:
        """Return up to ``limit`` unique G1 article URLs from the homepage."""
        logger.info("Fetching G1 top stories from %s", self._homepage_url)
        try:
            response = await client.get(self._homepage_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Could not retrieve %s: %s", self._homepage_url, exc)
            return []

        soup = BeautifulSoup(response.content, "html.parser")
        anchors = soup.select("a.feed-post-link[href]")

        unique_urls: list[str] = []
        seen: set[str] = set()
        for tag in anchors:
            href = tag.get("href")
            if not href:
                continue
            href_str = str(href).strip()
            if _URL_SUFFIX not in href_str:
                continue

            absolute_url = urljoin(self._homepage_url, href_str)
            if "g1.globo.com" not in absolute_url:
                continue
            if absolute_url in seen:
                continue
            seen.add(absolute_url)
            unique_urls.append(absolute_url)

            if len(unique_urls) >= limit:
                break

        logger.info("Found %d G1 article URLs", len(unique_urls))
        return unique_urls

    async def scrape_article(self, client: httpx.AsyncClient, url: str) -> ScrapedArticle | None:
        """Scrape a single G1 article and return a ``ScrapedArticle`` or ``None``."""
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Network error scraping %s: %s", url, exc)
            return None
        except Exception:
            logger.exception("Unexpected error scraping %s", url)
            return None

        soup = BeautifulSoup(response.content, "html.parser")

        title_tag: BeautifulSoup | None = None
        for selector in _TITLE_SELECTORS:
            title_tag = soup.select_one(selector)
            if title_tag:
                break
        title = title_tag.get_text(strip=True) if title_tag else "No title found"

        content = _extract_article_text(soup)
        image_url = _extract_article_image(soup)

        return ScrapedArticle(
            url=url,
            title=title,
            content=content,
            image_url=image_url,
            source=self.name,
        )

    def __repr__(self) -> str:
        return f"G1Source(homepage_url={self._homepage_url!r})"

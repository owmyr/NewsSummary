"""Async web scraper for BBC News (and other sources in the future).

Refactored from the original MyNews.py with:
- httpx.AsyncClient (was: requests sync)
- asyncio.Semaphore-based concurrency control
- Logging instead of print statements
- Type hints using `str | None` syntax
- Cleaner separation between URL discovery and article extraction
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .models import ScrapedArticle

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36"
)


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


def _extract_article_image(soup: BeautifulSoup) -> str | None:
    """Extract the main article image using prioritized strategies."""
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image:
        url = _normalize_url(og_image.get("content"))
        if url:
            return url

    tw_image = soup.find("meta", attrs={"name": "twitter:image"})
    if tw_image:
        url = _normalize_url(tw_image.get("content"))
        if url:
            return url

    selectors = [
        "img[data-testid='hero-image']",
        "div[data-component='image-block'] img",
        "figure img",
        "main img",
    ]
    for sel in selectors:
        img = soup.select_one(sel)
        if not img:
            continue

        srcset = img.get("srcset")
        if srcset:
            parts = str(srcset).split(",")
            if parts:
                last_entry = parts[-1].strip().split(" ")[0]
                url = _normalize_url(last_entry)
                if url:
                    return url

        url = _normalize_url(img.get("src"))
        if url:
            return url

    return None


def _extract_article_text(soup: BeautifulSoup) -> str:
    """Extract the article body text using prioritized strategies."""
    blocks = soup.select("div[data-component='text-block']")
    if not blocks:
        blocks = soup.select("article p")

    paragraphs: list[str] = []
    if blocks:
        for block in blocks:
            if block.name == "p":
                text = block.get_text(strip=True)
                if text:
                    paragraphs.append(text)
            else:
                for p in block.find_all("p"):
                    text = p.get_text(strip=True)
                    if text:
                        paragraphs.append(text)

    if not paragraphs:
        main_content = soup.find("main")
        if main_content:
            for p in main_content.find_all("p"):
                text = p.get_text(strip=True)
                if text and len(text) > 20:
                    paragraphs.append(text)

    if paragraphs:
        return "\n".join(paragraphs)
    return "Could not find article content."


def _build_client(settings: Settings) -> httpx.AsyncClient:
    """Build a configured httpx AsyncClient with sensible defaults."""
    return httpx.AsyncClient(
        headers={"User-Agent": DEFAULT_USER_AGENT},
        timeout=httpx.Timeout(settings.http_timeout_seconds),
        limits=httpx.Limits(
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_connections,
        ),
        follow_redirects=True,
    )


async def get_top_story_urls_async(
    client: httpx.AsyncClient, homepage_url: str, limit: int
) -> list[str]:
    """Fetch the BBC News homepage and return top story article URLs."""
    logger.info("Fetching top stories from %s", homepage_url)
    try:
        response = await client.get(homepage_url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Could not retrieve %s: %s", homepage_url, exc)
        return []

    soup = BeautifulSoup(response.content, "html.parser")
    headlines = soup.find_all("a", href=True)

    unique_urls: list[str] = []
    seen: set[str] = set()

    for tag in headlines:
        raw_href = tag.get("href")
        if not raw_href:
            continue
        href = str(raw_href).strip()
        if "/news/articles/" not in href:
            continue

        absolute_url = urljoin(homepage_url, href)
        if absolute_url in seen:
            continue
        seen.add(absolute_url)
        unique_urls.append(absolute_url)

        if len(unique_urls) >= limit:
            break

    logger.info("Found %d article URLs", len(unique_urls))
    return unique_urls


async def scrape_article_content_async(
    client: httpx.AsyncClient, url: str
) -> ScrapedArticle | None:
    """Scrape a single article and return a ScrapedArticle, or None on failure."""
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

    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else "No title found"

    content = _extract_article_text(soup)
    image_url = _extract_article_image(soup)

    return ScrapedArticle(
        url=url,
        title=title,
        content=content,
        image_url=image_url,
    )


async def scrape_many(
    settings: Settings,
    urls: list[str],
    semaphore: asyncio.Semaphore,
) -> list[ScrapedArticle]:
    """Scrape many URLs concurrently, bounded by the semaphore.

    Returns a list of successfully scraped articles (failures are logged and skipped).
    Order is not preserved — call sites that care should re-sort by url if needed.
    """
    async with _build_client(settings) as client:

        async def _one(url: str) -> ScrapedArticle | None:
            async with semaphore:
                return await scrape_article_content_async(client, url)

        results = await asyncio.gather(*(_one(url) for url in urls), return_exceptions=False)
    return [r for r in results if r is not None]

import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from typing import Optional, List, Dict

BBC_NEWS_URL = "https://www.bbc.com/news"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}


# ============================================================
#  IMAGE EXTRACTION (full robust version)
# ============================================================

def extract_article_image(soup: BeautifulSoup) -> Optional[str]:
    """
    Extracts the main BBC article image from multiple fallback selectors.
    Works safely with BeautifulSoup 4.12+'s internal attribute types:
    _AttributeValue, _AttributeValueList, etc.
    """

    selectors = [
        "img[data-testid='image-component-image']",
        "img[data-component='image-block'] img",
        "figure img",
        "img[loading='lazy']",
        "picture img",
    ]

    for sel in selectors:
        img = soup.select_one(sel)
        if not img:
            continue

        # CASE 1 — Direct "src"
        src = img.get("src")
        if src:
            return str(src).strip()

        # CASE 2 — srcset (may be AttributeValue or list-like)
        srcset = img.get("srcset")
        if srcset:
            srcset_str = str(srcset)
            candidates = []

            # srcset looks like:
            # "https://...640.webp 640w, https://...1024.webp 1024w"
            for item in srcset_str.split(","):
                item = item.strip()
                if not item:
                    continue

                parts = item.split(" ")
                if parts:
                    candidates.append(parts[0])

            if candidates:
                # Usually last is highest-res
                return candidates[-1]

    return None


# ============================================================
#  URL EXTRACTION FOR TOP STORIES
# ============================================================

def get_top_story_urls(limit: int = 5) -> List[str]:
    """
    Scrapes BBC News homepage and returns top <limit> article URLs.
    Uses robust relative→absolute URL normalization.
    """

    print("📡 Fetching top stories from BBC News homepage...")

    try:
        response = requests.get(BBC_NEWS_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # BBC article pattern links
        headlines = soup.select('a[href*="/news/articles/"]')

        unique_urls: set[str] = set()

        for tag in headlines:
            if not isinstance(tag, Tag):
                continue

            raw_href = tag.get("href")
            if raw_href is None:
                continue

            # Normalize BeautifulSoup attribute → pure string
            href = str(raw_href).strip()
            if not href:
                continue

            absolute_url = urljoin(BBC_NEWS_URL, href)
            unique_urls.add(absolute_url)

            if len(unique_urls) >= limit:
                break

        print(f"✅ Found {len(unique_urls)} article URLs.")
        return list(unique_urls)

    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: Could not retrieve BBC News homepage. {e}")
        return []
    except Exception as e:
        print(f"❌ Unexpected error while fetching top stories: {e}")
        return []


# ============================================================
#  ARTICLE TEXT EXTRACTION — Robust Content Parsing
# ============================================================

def extract_article_text(soup: BeautifulSoup) -> str:
    """
    Extracts article text using multiple BBC fallback patterns.
    """

    # Primary BBC format
    blocks = soup.select("div[data-component='text-block']")

    paragraphs: List[str] = []
    for block in blocks:
        for p in block.find_all("p"):
            text = p.get_text(strip=True)
            if text:
                paragraphs.append(text)

    if paragraphs:
        return "\n".join(paragraphs)

    # Fallback — any <article> tag
    article = soup.find("article")
    if article:
        text = "\n".join(
            p.get_text(strip=True)
            for p in article.find_all("p")
            if p.get_text(strip=True)
        )
        if text:
            return text

    # Fallback — any <p> on the page
    fallback_text = "\n".join(
        p.get_text(strip=True)
        for p in soup.find_all("p")
        if p.get_text(strip=True)
    )
    if fallback_text:
        return fallback_text

    return "Could not find article content."


# ============================================================
#  FULL ARTICLE SCRAPER
# ============================================================

def scrape_article_content(url: str) -> Optional[dict[str, str | None]]:


    ...

    """
    Fetches a BBC article page, extracts:
    - Title
    - Content
    - Image URL
    """

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Title
        title_tag = soup.select_one("#main-heading") or soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else "No title found"

        # Text
        content = extract_article_text(soup)

        # Image
        image_url = extract_article_image(soup)

        return {
            "title": title,
            "content": content,
            "url": url,
            "image_url": image_url,
        }

    except requests.exceptions.RequestException as e:
        print(f"❌ Network error scraping article {url}: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error scraping article {url}: {e}")
        return None

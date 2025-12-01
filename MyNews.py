import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin

BBC_NEWS_URL = "https://www.bbc.com/news"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}


def get_top_story_urls(limit=5):
    """Scrapes BBC News homepage and returns up to <limit> unique article URLs."""

    print("Fetching top stories from BBC News homepage...")

    try:
        response = requests.get(BBC_NEWS_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # BBC article structure: anchor tags linking to /news/articles/<id>
        headlines = soup.select('a[href*="/news/articles/"]')

        unique_urls: set[str] = set()

        for headline in headlines:
            if not isinstance(headline, Tag):
                continue

            raw_href = headline.get("href")

            # Skip missing/None hrefs (prevents _AttributeValue warnings)
            if raw_href is None:
                continue

            # Convert BeautifulSoup _AttributeValue → real Python string
            href = str(raw_href).strip()

            if not href:
                continue

            # Convert relative paths to absolute URLs
            absolute_url = urljoin(BBC_NEWS_URL, href)
            unique_urls.add(absolute_url)

            if len(unique_urls) >= limit:
                break

        print(f"✅ Found {len(unique_urls)} unique article URLs.")
        return list(unique_urls)

    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: Could not retrieve BBC News homepage. Error: {e}")
        return []
    except Exception as e:
        print(f"❌ ERROR: Unexpected error while fetching top stories: {e}")
        return []


def scrape_article_content(url):

    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Extract title
        title_element = soup.select_one("#main-heading") or soup.find("h1")
        title = title_element.get_text(strip=True) if title_element else "No title found"

        # Extract paragraphs
        article_blocks = soup.select("div[data-component='text-block']")
        content = "\n".join(
            p.get_text(strip=True)
            for block in article_blocks
            for p in block.find_all("p")
        )

        if not content:
            content = "Could not find article content."

        # Extract image
        image_url = extract_article_image(soup)

        return {
            "title": title,
            "content": content,
            "url": url,
            "image_url": image_url,  # NEW
        }

    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: Could not retrieve article at {url}. Error: {e}")
        return None
    except Exception as e:
        print(f"❌ ERROR: Unexpected error while scraping {url}: {e}")
        return None


def extract_article_image(soup: BeautifulSoup) -> str | None:
    """
    Extracts the main BBC article image from multiple fallback selectors.
    Returns None if no image is found.
    """

    selectors = [
        "img[data-testid='image-component-image']",
        "img[data-component='image-block'] img",
        "figure img",
        "img[loading='lazy']",
    ]

    for sel in selectors:
        img = soup.select_one(sel)
        if img and img.get("src"):
            return str(img.get("src"))

        # Sometimes the URL is srcset; take the largest
        if img and img.get("srcset"):
            srcset = img.get("srcset")
            parts = [s.strip().split(" ")[0] for s in srcset.split(",")]
            if parts:
                return parts[-1]  # take largest resolution

    return None

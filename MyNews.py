import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin
from typing import Optional, List, Dict, Any

BBC_NEWS_URL = "https://www.bbc.com/news"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/91.0.4472.124 Safari/537.36"
    )
}


# ============================================================
#  IMAGE EXTRACTION (Fixed for Type Checkers)
# ============================================================

def extract_article_image(soup: BeautifulSoup) -> str | None:
    """
    Extracts the main article image using robust attribute dicts.
    """

    def normalize(url: Any) -> str | None:
        if not url:
            return None
        
        # Safety check: BS4 .get() can return a list
        if isinstance(url, list):
            if not url:
                return None
            url = url[0]

        url_str = str(url).strip()
        
        if url_str.startswith("//"):
            return "https:" + url_str
        if url_str.startswith("http://") or url_str.startswith("https://"):
            return url_str
        return None

    # --- PRIORITY 1: Open Graph (The Gold Standard) ---
    # We use attrs={} to avoid collision with reserved args
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image:
        url = normalize(og_image.get("content"))
        if url:
            return url

    # --- PRIORITY 2: Twitter Card ---
    # FIXED: Used attrs={"name": ...} to fix the type error
    tw_image = soup.find("meta", attrs={"name": "twitter:image"})
    if tw_image:
        url = normalize(tw_image.get("content"))
        if url:
            return url

    # --- PRIORITY 3: Manual Body Scraping (Fallback) ---
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
            srcset_str = str(srcset)
            parts = srcset_str.split(",")
            if parts:
                last_entry = parts[-1].strip().split(" ")[0]
                url = normalize(last_entry)
                if url:
                    return url
        
        url = normalize(img.get("src"))
        if url:
            return url

    return None


# ============================================================
#  URL EXTRACTION FOR TOP STORIES
# ============================================================

def get_top_story_urls(limit: int = 5) -> List[str]:
    print("📡 Fetching top stories from BBC News homepage...")

    try:
        response = requests.get(BBC_NEWS_URL, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        headlines = soup.find_all("a", href=True)
        
        unique_urls: set[str] = set()

        for tag in headlines:
            raw_href = tag.get("href")
            if not raw_href:
                continue

            href = str(raw_href).strip()
            
            if "/news/articles/" not in href:
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
#  ARTICLE TEXT EXTRACTION
# ============================================================

def extract_article_text(soup: BeautifulSoup) -> str:
    blocks = soup.select("div[data-component='text-block']")
    
    if not blocks:
        blocks = soup.select("article p")

    paragraphs: List[str] = []
    
    if blocks:
        for block in blocks:
            if block.name == 'p':
                 text = block.get_text(strip=True)
                 if text: paragraphs.append(text)
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


# ============================================================
#  FULL ARTICLE SCRAPER
# ============================================================

def scrape_article_content(url: str) -> Optional[dict[str, str | None]]:
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        title_tag = soup.find("h1")
        title = title_tag.get_text(strip=True) if title_tag else "No title found"

        content = extract_article_text(soup)
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
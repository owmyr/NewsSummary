import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BBC_NEWS_URL = "https://www.bbc.com/news"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_top_story_urls(limit=5):
    print("Fetching top stories from BBC News homepage...")
    try:
        response = requests.get(BBC_NEWS_URL, headers=HEADERS)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        headlines = soup.select('a[href*="/news/articles/"]')
        
        unique_urls = set()
        for headline in headlines:
            relative_url = headline.get('href')
            if relative_url:
                absolute_url = urljoin(BBC_NEWS_URL, relative_url)
                unique_urls.add(absolute_url)
            
            if len(unique_urls) >= limit:
                break
        
        print(f"✅ Found {len(unique_urls)} unique article URLs.")
        return list(unique_urls)

    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: Could not retrieve the BBC News homepage. An error occurred: {e}")
        return []
    except Exception as e:
        print(f"❌ ERROR: An unexpected error occurred while fetching top stories: {e}")
        return []

def scrape_article_content(url):

    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        title_element = soup.select_one("#main-heading")
        
        if not title_element:
            title_element = soup.find("h1")

        title = title_element.get_text(strip=True) if title_element else "No title found"

        article_blocks = soup.select("div[data-component='text-block']")
        content = "\n".join(p.get_text(strip=True) for block in article_blocks for p in block.find_all('p'))

        if not content:
            content = "Could not find article content."

        return {"title": title, "content": content, "url": url}

    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: Could not retrieve article at {url}. Error: {e}")
        return None
    except Exception as e:
        print(f"❌ ERROR: An unexpected error occurred while scraping {url}: {e}")
        return None
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Base URL for BBC News to resolve relative links
BBC_NEWS_URL = "https://www.bbc.com/news"

# A consistent User-Agent is good practice for web scraping
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_top_story_urls(limit=5):
    """
    Scrapes the BBC News homepage to find the URLs of the top stories.

    Args:
        limit (int): The maximum number of article URLs to return.

    Returns:
        list: A list of absolute URLs for the top news articles.
    """
    print("Fetching top stories from BBC News homepage...")
    try:
        response = requests.get(BBC_NEWS_URL, headers=HEADERS)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        
        # This selector targets links that are likely to be primary headlines.
        # Note: Website structures change! This may need updating in the future.
        # We look for links within a specific type of container often used for headlines.
        # We also filter for URLs that look like article links.
        headlines = soup.select('a[href*="/news/articles/"]')
        
        # Using a set to avoid duplicate URLs
        unique_urls = set()
        for headline in headlines:
            # Get the relative URL from the 'href' attribute
            relative_url = headline.get('href')
            if relative_url:
                # Convert the relative URL to an absolute URL
                absolute_url = urljoin(BBC_NEWS_URL, relative_url)
                unique_urls.add(absolute_url)
            
            # Stop once we've reached our limit
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
    """
    Scrapes the title and content of a single BBC News article from a given URL.

    Args:
        url (str): The full URL of the BBC article to scrape.

    Returns:
        dict: A dictionary containing the 'title' and 'content' of the article,
              or None if scraping fails.
    """
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # --- MODIFICATION START ---
        # Make title scraping more robust.
        # First, try the specific ID we used before.
        title_element = soup.select_one("#main-heading")
        
        # If that doesn't work (returns None), fall back to finding the first <h1> tag.
        # This is a more general but very reliable method for news articles.
        if not title_element:
            title_element = soup.find("h1")
        # --- MODIFICATION END ---

        title = title_element.get_text(strip=True) if title_element else "No title found"

        article_blocks = soup.select("div[data-component='text-block']")
        content = "\n".join(p.get_text(strip=True) for block in article_blocks for p in block.find_all('p'))

        if not content:
            # Fallback for different article structures
            content = "Could not find article content."

        return {"title": title, "content": content, "url": url}

    except requests.exceptions.RequestException as e:
        print(f"❌ ERROR: Could not retrieve article at {url}. Error: {e}")
        return None
    except Exception as e:
        print(f"❌ ERROR: An unexpected error occurred while scraping {url}: {e}")
        return None
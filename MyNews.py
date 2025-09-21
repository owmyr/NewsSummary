import requests
from bs4 import BeautifulSoup
import json

def getnews():
    url = input("Por favor, cole a URL completa da noticia do site G1\n").strip()

    if not url.startswith("https://g1.globo.com"):
        print("❌ ERRO: A URL fornecida não parece ser do G1. Por favor, use um link válido.")
        return # Exit the function

    response = requests.get(url)
    soup = BeautifulSoup(response.content, "html.parser")

    # Title
    title_element = soup.select_one("h1")
    title = title_element.get_text(strip=True) if title_element else "No title found"

    # Article content
    article_content_elements = soup.select("article p")
    content = "\n".join(p.get_text(strip=True) for p in article_content_elements)

    # Prepare JSON
    article = {"title": title, "content": content}

    with open("article.json", "w", encoding="utf-8") as f:
        json.dump(article, f, ensure_ascii=False, indent=4)

    print("Article saved successfully.")

if __name__ == "__main__":
    getnews()
import os
import google.generativeai as genai
from dotenv import load_dotenv
import json
from MyNews import getnews

def main():
    # --- AI CONFIG ---
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("API key not found.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')

    try:
        # --- Scrape and Read ---
        print("Scraping news...")
        getnews()

        print("Reading article from JSON...")
        with open('article.json', 'r', encoding="utf-8") as f:
            article_data = json.load(f)

        article_content = article_data.get("content", "")
        
        if not article_content:
            print("⚠️ Article content is empty. Cannot continue.")
            return

        # --- AI Prompt ---
        prompt = f"""
        Você é um assistente de notícias especializado.
        Crie um resumo claro e conciso baseado no seguinte artigo:
        ---
        {article_content} 
        ---
        """

        # --- AI Answer ---
        print("Generating summary...")
        response = model.generate_content(prompt)
        summary_text = response.text
        print("\n--- AI Summary ---")
        print(summary_text)
        print("--------------------\n")

        # --- Save summary to JSON ---
        print("Updating JSON file...")
        article_data['summary'] = summary_text
        
        with open("article.json", "w", encoding="utf-8") as f:
            json.dump(article_data, f, ensure_ascii=False, indent=4)
        
        print("✅ JSON updated successfully.")

    except FileNotFoundError:
        print("\n❌ ERROR: 'article.json' not found. The scraping step might have failed.")
    except json.JSONDecodeError:
        print("\n❌ ERROR: Could not read 'article.json'. The file might be empty or corrupted.")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
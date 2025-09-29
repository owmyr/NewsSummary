import os
import json
import google.generativeai as genai
from dotenv import load_dotenv
from MyNews import get_top_story_urls, scrape_article_content # Corrected import name
from email_sender import send_summary_email

# --- CONFIGURATION ---
NEWS_LIMIT = 5
OUTPUT_FILENAME = "daily_news_summary.json"

def initialize_ai():
    """Loads API key and configures the Generative AI model."""
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not found in environment variables.")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash') # Updated to a strong model
    return model

def get_ai_summary(model, content):
    """Generates a summary for the given content using the AI model."""
    if not content or content == "Could not find article content.":
        return "Could not generate summary because article content was empty."

    prompt = f"""
    You are an expert news editor. Your task is to provide a clear, concise, 
    and neutral summary of the following news article. Capture the main points
    and key information. The summary should be about 4-6 sentences long.
    ---
    ARTICLE:
    {content} 
    ---
    SUMMARY:
    """
    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ AI generation failed: {e}")
        return "Summary generation failed."

def main():
    """Main function to run the news summarization process."""
    print("🚀 Starting the daily news summarizer...")
    
    try:
        model = initialize_ai()
    except ValueError as e:
        print(f"❌ CONFIGURATION ERROR: {e}")
        return

    # Step 1: Get the URLs of the top stories
    article_urls = get_top_story_urls(limit=NEWS_LIMIT)

    if not article_urls:
        print("Could not fetch any article URLs. Exiting.")
        return

    # Step 2: Loop through each URL, scrape, and summarize
    all_summaries = []
    for i, url in enumerate(article_urls, 1):
        print(f"\n--- Processing article {i}/{len(article_urls)} ---")
        print(f"URL: {url}")

        article_data = scrape_article_content(url)
        
        if not article_data:
            print("Skipping this article due to a scraping error.")
            continue

        print(f"Scraped Title: {article_data['title']}")
        print("Generating summary...")
        
        summary = get_ai_summary(model, article_data['content'])
        
        print(f"AI Summary: {summary}")

        all_summaries.append({
            "title": article_data['title'],
            "summary": summary,
            "url": article_data['url']
        })

    # Step 3: Save the consolidated summaries to a new JSON file
    if all_summaries:
        print("\nSaving all summaries to JSON file...")
        with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
            json.dump(all_summaries, f, ensure_ascii=False, indent=4)
        print(f"✅ All summaries saved successfully to {OUTPUT_FILENAME}.")
    else:
        print("⚠️ No summaries were generated.")
        
    # Step 4: Send the email
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")
    recipient_email = os.getenv("RECIPIENT_EMAIL")

    if all([sender_email, sender_password, recipient_email]):
        send_summary_email(
            summaries=all_summaries,
            sender_email=sender_email,
            sender_password=sender_password,
            recipient_email=recipient_email
        )
    else:
        print("\n⚠️ Email credentials not found in .env file. Skipping email.")

if __name__ == "__main__":
    main()
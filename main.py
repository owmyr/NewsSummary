import os
import json
import time
import re
import google.generativeai as genai
from datetime import datetime, timezone
from MyNews import get_top_story_urls, scrape_article_content
from email_sender import send_summary_email

import firebase_admin
from firebase_admin import credentials, firestore


# ============================================================
#  FIRESTORE INITIALIZATION
# ============================================================

def initialize_firestore():
    """
    Initialize Firestore using the FIREBASE_CREDENTIALS environment variable.
    This must contain the FULL JSON service account as a string.
    """

    creds_json = os.getenv("FIREBASE_CREDENTIALS")
    if not creds_json:
        raise RuntimeError("❌ FIREBASE_CREDENTIALS env var is missing.")

    # Load credentials from JSON string
    creds_dict = json.loads(creds_json)
    cred = credentials.Certificate(creds_dict)

    firebase_admin.initialize_app(cred)
    return firestore.client()

# Firestore client
db = initialize_firestore()


# ============================================================
#  GEMINI SETUP (Gemini 2.5 Flash)
# ============================================================

GENAI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GENAI_API_KEY:
    raise RuntimeError("❌ Missing GEMINI_API_KEY environment variable.")

genai.configure(api_key=GENAI_API_KEY)


def call_gemini(prompt, model="gemini-2.5-flash", retries=4):
    """
    Robust Gemini call with retries and exponential backoff.
    """
    for attempt in range(retries):
        try:
            response = genai.GenerativeModel(model).generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"⚠️ Gemini Error: {e}. Retrying ({attempt + 1}/{retries})...")
            time.sleep(2 * (attempt + 1))
    return None


# ============================================================
#  SUMMARIZATION PROMPTS (Neutral newsroom tone)
# ============================================================

SUMMARY_PROMPT = """
You are a professional news summarizer.

Your task is to summarize a BBC News article into a concise, neutral,
objective news brief in **120–180 words**.

Rules:
- Maintain a factual, impartial tone.
- Do not add opinions or invented details.
- Do not add sentences like "This article says".
- Avoid filler or meta commentary.
- Focus on the key facts and major developments.
- No emojis.
- No hallucination.
- Output MUST be valid JSON.

JSON schema to produce:

{
  "title": "<string>",
  "summary": "<string>",
  "key_points": ["point1", "point2", "point3"],
  "category": "<politics | world | business | tech | science | health | uk | europe | other>"
}

Article text:
\"\"\"{article_text}\"\"\"
"""


CATEGORY_PROMPT = """
Classify this article into exactly one category from:

politics, world, business, tech, science, health, uk, europe, other.

Title: "{title}"
Summary: "{summary}"

Return ONLY the category word.
"""


# ============================================================
#  TEXT CLEANING + CHUNKING
# ============================================================

def clean_article_text(text: str) -> str:
    """
    Remove junk, timestamps, boilerplate, duplicates.
    """
    lines = text.split("\n")
    cleaned = []

    for line in lines:
        ln = line.strip()
        if not ln:
            continue

        # skip timestamps
        if re.match(r"^\d{1,2}:\d{2}(\s*(GMT|BST))?$", ln):
            continue

        # skip boilerplate
        if "Follow BBC" in ln or "Related Topics" in ln:
            continue

        cleaned.append(ln)

    # dedupe while preserving order
    final = list(dict.fromkeys(cleaned))
    return "\n".join(final)


def chunk_text(text: str, max_words=600):
    """
    Split into safe chunks for LLM.
    """
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i:i + max_words]))
    return chunks


# ============================================================
#  ARTICLE SUMMARIZATION (Full robust pipeline)
# ============================================================

def summarize_article(article_text: str, title: str):
    """
    Perform multi-step summarization:
    - Clean text
    - Chunk if necessary
    - Summarize chunks
    - Merge summaries
    - Classify category
    - Return final structured JSON
    """

    cleaned = clean_article_text(article_text)

    if len(cleaned.split()) < 80:
        cleaned += "\n(Note: Article is short; summary may be limited.)"

    chunks = chunk_text(cleaned)
    chunk_summaries = []

    # ---- Step 1: summarize each chunk ----
    for chunk in chunks:
        prompt = SUMMARY_PROMPT.format(article_text=chunk)
        response_text = call_gemini(prompt)

        if not response_text:
            continue

        try:
            parsed = json.loads(response_text)
            chunk_summaries.append(parsed["summary"])
        except Exception:
            print("⚠️ Invalid chunk JSON. Skipping.")
            continue

    if not chunk_summaries:
        print("❌ No partial summaries produced.")
        return None

    # ---- Step 2: Combine partial summaries ----
    combined_text = "\n".join(chunk_summaries)
    final_prompt = SUMMARY_PROMPT.format(article_text=combined_text)

    final_output = call_gemini(final_prompt)
    if not final_output:
        print("❌ Final summarization failed.")
        return None

    try:
        final_json = json.loads(final_output)
    except json.JSONDecodeError:
        print("❌ Final output not valid JSON.")
        return None

    # ---- Step 3: Category classification ----
    cat_prompt = CATEGORY_PROMPT.format(
        title=final_json["title"],
        summary=final_json["summary"]
    )
    category = call_gemini(cat_prompt)
    final_json["category"] = category.strip().lower() if category else "other"

    return final_json


# ============================================================
#  FIRESTORE SAVE
# ============================================================

def save_summaries_to_firestore(date_str: str, summaries: list[dict]):
    print("🗄 Saving summaries to Firestore...")
    try:
        db.collection("dailySummaries").document(date_str).set({
            "date": date_str,
            "articles": summaries
        })
        print("✅ Saved summaries to Firestore.")
    except Exception as e:
        print(f"❌ Failed to save summaries: {e}")


# ============================================================
#  MAIN EXECUTION
# ============================================================

def main():
    print("\n============================")
    print("🚀 Daily BBC Summary Runner")
    print("============================\n")

    from datetime import datetime, timezone

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")


    # FIX: initialize summary list here
    all_summaries = []

    # ---- Fetch article URLs ----
    urls = get_top_story_urls(limit=5)
    if not urls:
        print("❌ No URLs found. Exiting.")
        return

    for url in urls:
        print(f"\n📄 Scraping article:\n{url}")

        article = scrape_article_content(url)
        if not article:
            print("⚠️ Article scrape failed. Skipping.")
            continue

        # Summarize
        title = article["title"] or "No title"
        content = article["content"] or ""
        summary = summarize_article(content, title)



        if not summary:
            print("⚠️ Summary generation failed. Skipping.")
            continue

        summary["url"] = url
        summary["image_url"] = article.get("image_url")
        all_summaries.append(summary)

    if not all_summaries:
        print("❌ No summaries created. Exiting.")
        return

    # Save to Firestore
    save_summaries_to_firestore(today_str, all_summaries)

    # Send email digest
    print("\n📨 Sending email digest...")

    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")
    recipient_email = os.getenv("RECIPIENT_EMAIL")

    if not (sender_email and sender_password and recipient_email):
        print("⚠️ Missing email environment variables. Skipping email sending.")
    else:
        send_summary_email(all_summaries, sender_email, sender_password, recipient_email)

    print("✅ Email digest step complete.")




if __name__ == "__main__":
    main()
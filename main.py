import os
import json
import time
import re
from datetime import datetime, timezone

import google.generativeai as genai
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

    creds_dict = json.loads(creds_json)
    cred = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(cred)
    return firestore.client()


# Global Firestore client
db = initialize_firestore()


# ============================================================
#  GEMINI SETUP (Gemini 2.5 Flash)
# ============================================================

GENAI_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GENAI_API_KEY:
    raise RuntimeError("❌ Missing GOOGLE_API_KEY environment variable.")

genai.configure(api_key=GENAI_API_KEY)


def call_gemini(prompt: str, model: str = "gemini-2.5-flash", retries: int = 4) -> str | None:
    """
    Robust Gemini call with retries and exponential backoff.
    Returns the response text or None on failure.
    """
    for attempt in range(retries):
        try:
            response = genai.GenerativeModel(model).generate_content(prompt)
            text = getattr(response, "text", None)
            if text:
                return text.strip()
            return None
        except Exception as e:
            print(f"⚠️ Gemini Error: {e}. Retrying ({attempt + 1}/{retries})...")
            time.sleep(2 * (attempt + 1))
    return None



# ============================================================
#  TEXT CLEANING + CHUNKING
# ============================================================

def clean_article_text(text: str) -> str:
    """
    Remove junk, timestamps, boilerplate, duplicates.
    """
    lines = text.split("\n")
    cleaned: list[str] = []

    for line in lines:
        ln = line.strip()
        if not ln:
            continue

        # skip timestamps like "10:45" or "10:45 GMT"
        if re.match(r"^\d{1,2}:\d{2}(\s*(GMT|BST))?$", ln):
            continue

        # skip boilerplate
        if "Follow BBC" in ln or "Related Topics" in ln:
            continue

        cleaned.append(ln)

    # dedupe while preserving order
    final = list(dict.fromkeys(cleaned))
    return "\n".join(final)


def chunk_text(text: str, max_words: int = 600) -> list[str]:
    """
    Split into safe word chunks for LLM.
    """
    words = text.split()
    chunks: list[str] = []
    for i in range(0, len(words), max_words):
        chunks.append(" ".join(words[i:i + max_words]))
    return chunks


# ============================================================
#  ARTICLE SUMMARIZATION (Robust, NO .format/JSON parsing)
# ============================================================

def summarize_article(article_text: str, title: str) -> dict[str, str]:
    """
    Summarize an article using a two-step process:
      1) Summarize chunks if the article is long.
      2) Summarize the combined partial summaries into a final summary.
    Returns a dict with keys: title, summary, category.
    """

    cleaned = clean_article_text(article_text or "")
    if len(cleaned.split()) < 40:
        cleaned += "\n(Note: Article text is short; summary may be limited.)"

    chunks = chunk_text(cleaned, max_words=600)

    # --- Step 1: summarize each chunk (if multiple) ---
    partial_summaries: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        chunk_prompt = f"""
You are a professional BBC-style news summarizer.

Summarize the following portion of a BBC News article
in a neutral, objective newsroom tone in about 80–120 words.

PORTION {idx}:
{chunk}
"""
        chunk_summary = call_gemini(chunk_prompt)
        if chunk_summary:
            partial_summaries.append(chunk_summary)

    if not partial_summaries:
        # Try a single-shot summary as a fallback
        fallback_prompt = f"""
You are a professional BBC-style news summarizer.

Summarize the following BBC News article in a neutral, objective newsroom tone
in about 120–180 words. Focus on key facts, context, and major developments.
Avoid commentary, opinion, or meta text.

TITLE:
{title}

ARTICLE:
{cleaned}
"""
        fallback_summary = call_gemini(fallback_prompt)
        summary_text = fallback_summary or "Summary generation failed."
    else:
        # --- Step 2: combine chunk summaries into a final summary ---
        combined = "\n\n".join(partial_summaries)
        final_prompt = f"""
You are a professional BBC-style news summarizer.

You are given several partial summaries of a BBC News article.
Using them, write a single coherent summary of the entire article
in about 120–180 words, in a neutral, factual, newsroom tone.

TITLE:
{title}

PARTIAL SUMMARIES:
{combined}

Return only the final summary text, with no headings or bullet points.
"""
        final = call_gemini(final_prompt)
        summary_text = final or "Summary generation failed."

    # --- Step 3: simple category classification ---
    category_prompt = f"""
Classify this BBC News article into one of:
politics, world, business, tech, science, health, uk, europe, other.

Title: {title}
Summary: {summary_text}

Return ONLY the single category word.
"""
    category_raw = call_gemini(category_prompt)
    if category_raw:
        category = category_raw.strip().split()[0].lower()
    else:
        category = "other"

    return {
        "title": title,
        "summary": summary_text,
        "category": category,
    }



# ============================================================
#  FIRESTORE SAVE
# ============================================================

def save_summaries_to_firestore(date_str: str, summaries: list[dict]):
    print("🗄 Saving summaries to Firestore...")
    try:
        db.collection("dailySummaries").document(date_str).set({
            "date": date_str,
            "articles": summaries,
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

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    all_summaries: list[dict] = []

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

        title = article.get("title") or "No title"
        content = article.get("content") or ""

        summary = summarize_article(content, title)
        if not summary:
            print("⚠️ Summary generation failed. Skipping.")
            continue

        summary["url"] = url
        summary["image_url"] = article.get("image_url") or ""

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
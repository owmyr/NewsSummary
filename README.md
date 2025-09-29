# 🚀 BBC News AI Summarizer

This project is a simple script that automatically scrapes the top stories from BBC News, uses Google's AI to create a short summary for each, and then emails you a daily digest everyday.

![GitHub Actions Workflow Status](https://github.com/owmyr/NewsSummary/actions/workflows/daily-summary.yml/badge.svg)
---
### ‼️Not Finished

* Still working on implementing a (free) subscription service for anyone to receive the emails when they are sent out. 

---
### ✨ Features

* **Web Scraping**: Automatically fetches the latest top stories from the BBC News homepage.
* **AI-Powered Summaries**: Leverages the Google Gemini AI model to create clear and concise summaries of complex articles.
* **Email Digest**: Formats the summaries into a clean, readable HTML email.
* **Fully Automated**: Uses GitHub Actions for a "set it and forget it" daily execution.

---

## 🛠️ Getting Started

If you want to utilize this on your own before the subscription is available you can run the program by  renaming the ".env.example" to ".env" and replacing it's contents. You will need a Google API Key and App Password from your email.

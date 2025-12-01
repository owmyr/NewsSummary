# 🚀 BBC News AI Summarizer & Subscription Service
This project is a fully automated service that scrapes the top stories from BBC News, uses Google's Gemini AI to generate concise summaries, and emails a daily digest to all subscribers. It features a public-facing website for users to sign up and is deployed on a serverless architecture.

![GitHub Actions Workflow Status](https://github.com/owmyr/NewsSummary/actions/workflows/daily-summary.yml/badge.svg)
---

🌐 Live Demo & Subscription
You can subscribe to the daily email digest at the link below:

https://thedailybot.web.app/

---

### ✨ Features

* Automated Web Scraping: Fetches the latest top stories from the BBC News homepage daily.

* AI-Powered Summaries: Leverages Google's Gemini AI to create clear and neutral summaries of each article.

* Public Subscription System: A live website with a signup form for any user to subscribe to the daily emails.

* Secure & Scalable Backend: Uses Firebase Hosting, Cloud Functions, and a Firestore database to securely manage subscribers.

* Fully Automated: A "set it and forget it" daily workflow managed by GitHub Actions that requires no manual intervention.

---

### 🛠️ Tech Stack

* Backend & Scraping: Python, BeautifulSoup

* AI Summarization: Google Gemini AI

* Database & Web: Firebase (Hosting, Cloud Functions, Firestore)

* Automation: GitHub Actions

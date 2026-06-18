"""Firestore data access layer with lazy initialization.

All Firebase initialization happens on first call to `get_db()`,
not at module import time. This makes the package importable for
tests and tooling without real credentials.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from .models import Subscriber

if TYPE_CHECKING:
    from firebase_admin.firestore import Client

logger = logging.getLogger(__name__)

_db: Client | None = None


def _load_firebase_credentials() -> str:
    """Return FIREBASE_CREDENTIALS from the process env, or fall back to .env.

    Pydantic's BaseSettings reads .env internally but does not export values
    to os.environ. This helper provides a runtime fallback so that
    ``db.get_db()`` works whether the credential is supplied as a real
    environment variable (e.g. in CI / GitHub Actions) or only via .env
    (e.g. local development).
    """
    creds = os.getenv("FIREBASE_CREDENTIALS")
    if creds:
        return creds

    try:
        from dotenv import dotenv_values

        env_path = os.path.join(os.getcwd(), ".env")
        if os.path.isfile(env_path):
            values = dotenv_values(env_path)
            creds = values.get("FIREBASE_CREDENTIALS")
            if creds:
                logger.debug("Loaded FIREBASE_CREDENTIALS from %s", env_path)
                return creds
    except ImportError:
        pass

    return ""


def get_db() -> Client:
    """Return a process-wide Firestore client, initializing on first call."""
    global _db
    if _db is not None:
        return _db

    import firebase_admin
    from firebase_admin import credentials
    from firebase_admin import firestore as _firestore

    creds_json = _load_firebase_credentials()
    if not creds_json:
        raise RuntimeError(
            "FIREBASE_CREDENTIALS env var is missing. Set it in your shell "
            "or in the project root .env file as a JSON string."
        )

    creds_dict = json.loads(creds_json)
    cred = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(cred)
    _db = _firestore.client()
    logger.info("Firestore client initialized")
    return _db


def reset_db() -> None:
    """Reset the cached client. Useful for tests."""
    global _db
    _db = None


def get_existing_summaries(date_str: str) -> list[dict]:
    """Return summaries already stored for the given date, or [] if none."""
    db = get_db()
    doc = db.collection("dailySummaries").document(date_str).get()
    if not doc.exists:
        return []
    data = doc.to_dict() or {}
    return list(data.get("articles", []))


def save_summaries(date_str: str, summaries: list[dict]) -> None:
    """Persist the day's summaries to Firestore."""
    db = get_db()
    db.collection("dailySummaries").document(date_str).set(
        {
            "date": date_str,
            "articles": summaries,
        }
    )
    logger.info("Saved %d summaries to Firestore for %s", len(summaries), date_str)


def get_all_subscriber_emails() -> list[str]:
    """Return every subscriber's email address from Firestore."""
    db = get_db()
    docs = db.collection("subscribers").stream()
    emails: list[str] = []
    for doc in docs:
        data = doc.to_dict() or {}
        email = data.get("email")
        if email:
            emails.append(str(email))
    logger.info("Loaded %d subscribers from Firestore", len(emails))
    return emails


def get_all_subscribers() -> list[Subscriber]:
    """Return all subscribers from Firestore with their source preferences.

    Optional geolocation fields (``country``, ``city``, ``timezone``,
    ``browser_timezone``, ``lat``, ``lon``) are read if present. Legacy
    subscribers that signed up before geolocation was added will simply
    have these fields set to ``None`` and are loaded successfully.
    """
    db = get_db()
    docs = db.collection("subscribers").stream()
    subscribers: list[Subscriber] = []
    for doc in docs:
        data = doc.to_dict() or {}
        email = data.get("email")
        if not email:
            continue
        sources = data.get("sources", ["bbc"])
        if not isinstance(sources, list):
            sources = ["bbc"]
        subscribers.append(
            Subscriber(
                email=str(email),
                sources=[str(s) for s in sources],
                subscribed_at=data.get("subscribed_at"),
                country=data.get("country"),
                city=data.get("city"),
                timezone=data.get("timezone"),
                browser_timezone=data.get("browser_timezone"),
                lat=data.get("lat"),
                lon=data.get("lon"),
            )
        )
    logger.info("Loaded %d subscribers from Firestore", len(subscribers))
    return subscribers


def log_email_send(date_str: str, email: str, status: str, error: str | None) -> None:
    """Append a record of an individual email send to the email_log collection."""
    from firebase_admin import firestore as _firestore

    db = get_db()
    db.collection("email_log").add(
        {
            "date": date_str,
            "email": email,
            "status": status,
            "error": error,
            "sent_at": _firestore.SERVER_TIMESTAMP,
        }
    )


def get_latest_template() -> str | None:
    """Return the most recent rendered email template stored in Firestore."""
    db = get_db()
    doc = db.collection("emailTemplates").document("latest").get()
    if not doc.exists:
        return None
    return (doc.to_dict() or {}).get("html")


def save_latest_template(html: str) -> None:
    """Persist the latest rendered email template for the Cloud Function to use."""
    from firebase_admin import firestore as _firestore

    db = get_db()
    db.collection("emailTemplates").document("latest").set(
        {
            "html": html,
            "updated_at": _firestore.SERVER_TIMESTAMP,
        }
    )
    logger.info("Saved latest email template to Firestore")

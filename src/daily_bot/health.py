"""Health check / dead-man's-switch for the daily pipeline.

Writes a `last_run` document to Firestore with status, timing, and counts.
A monitoring system can alert if `last_run.status != "ok"` or if the
document hasn't been updated within a window.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from . import db

logger = logging.getLogger(__name__)


def record_run_start(date_str: str) -> None:
    """Mark the start of a run."""
    try:
        db.get_db().collection("health").document("last_run").set(
            {
                "date": date_str,
                "status": "running",
                "started_at": datetime.now(UTC),
            },
            merge=True,
        )
    except Exception:
        logger.exception("Failed to record run start")


def record_run_complete(
    date_str: str,
    scraped: int,
    summarized: int,
    sent: int,
    failed: int,
) -> None:
    """Mark successful completion of a run."""
    try:
        from firebase_admin import firestore as _firestore

        db.get_db().collection("health").document("last_run").set(
            {
                "date": date_str,
                "status": "ok",
                "articles_scraped": scraped,
                "articles_summarized": summarized,
                "emails_sent": sent,
                "emails_failed": failed,
                "completed_at": _firestore.SERVER_TIMESTAMP,
            }
        )
        logger.info("Health check recorded: ok")
    except Exception:
        logger.exception("Failed to record run completion")


def record_run_failure(date_str: str, error: str) -> None:
    """Mark a run as failed (still useful for monitoring)."""
    try:
        from firebase_admin import firestore as _firestore

        db.get_db().collection("health").document("last_run").set(
            {
                "date": date_str,
                "status": "error",
                "error": error,
                "failed_at": _firestore.SERVER_TIMESTAMP,
            }
        )
        logger.warning("Health check recorded: error - %s", error)
    except Exception:
        logger.exception("Failed to record run failure")

"""Data models for the daily news pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal[
    "politics",
    "world",
    "business",
    "tech",
    "science",
    "health",
    "uk",
    "europe",
    "other",
]

VALID_CATEGORIES: set[str] = {
    "politics",
    "world",
    "business",
    "tech",
    "science",
    "health",
    "uk",
    "europe",
    "other",
}


class ScrapedArticle(BaseModel):
    """An article fetched from a news source."""

    source: str = ""
    url: str
    title: str
    content: str
    image_url: str | None = None
    section: str = Field(
        default="",
        description=(
            "Source-specific section hint extracted from article metadata "
            "(e.g. BBC <meta property='article:section'>). Used by the "
            "classifier to map to a VALID_CATEGORIES value."
        ),
    )


class Summary(BaseModel):
    """A summarized article ready to be stored and emailed."""

    source: str = ""
    title: str
    summary: str
    category: str
    url: str = ""
    image_url: str = ""


class Subscriber(BaseModel):
    """An email subscriber from Firestore."""

    email: str
    sources: list[str] = Field(default_factory=lambda: ["bbc"])
    subscribed_at: datetime | None = None


class EmailSendResult(BaseModel):
    """Result of sending the daily digest to one subscriber."""

    email: str
    date: str
    status: Literal["sent", "failed"]
    error: str | None = None
    sent_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

"""Centralized configuration loaded from environment variables.

Uses Pydantic BaseSettings for validation and type safety.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_api_key: str = Field(..., description="Google Gemini API key")

    firebase_credentials: str = Field(
        ...,
        description="Full JSON service account as a string",
    )

    sender_email: str = Field(..., description="Sender email address")
    sender_password: str = Field(..., description="Sender email app password")

    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP server host")
    smtp_port: int = Field(default=465, description="SMTP server port")

    bbc_news_url: str = Field(
        default="https://www.bbc.com/news",
        description="BBC News homepage URL",
    )
    g1_homepage_url: str = Field(
        default="https://g1.globo.com",
        description="G1 homepage URL",
    )
    sources: str = Field(
        default="bbc",
        description="Comma-separated list of news source names to process",
    )
    article_limit: int = Field(default=5, description="Number of articles to process per source")

    gemini_model: str = Field(default="gemini-2.5-flash", description="Gemini model name")
    gemini_retries: int = Field(
        default=6,
        description="Gemini retry attempts. Higher values help survive 429 quota errors.",
    )

    chunk_max_words: int = Field(default=600, description="Max words per chunk")
    summary_min_words: int = Field(default=40, description="Min words to attempt summary")

    email_batch_size: int = Field(default=50, description="Subscribers per SMTP batch")
    email_batch_delay_seconds: int = Field(default=2, description="Delay between batches")

    scrape_concurrency: int = Field(default=5, description="Max concurrent article scrapes")
    summarize_concurrency: int = Field(
        default=1,
        description="Max concurrent Gemini calls. Keep low (1-2) to stay under the free-tier 5 req/min limit.",
    )
    http_timeout_seconds: float = Field(default=15.0, description="HTTP request timeout")
    http_max_connections: int = Field(default=10, description="Max concurrent HTTP connections")

    circuit_breaker_threshold: int = Field(
        default=3, description="Consecutive failures before short-circuiting"
    )
    circuit_breaker_cooldown_seconds: float = Field(
        default=30.0, description="Cooldown after circuit trips"
    )

    health_doc_id: str = Field(default="last_run", description="Firestore doc id for health check")

    log_level: str = Field(default="INFO", description="Logging level")


def load_settings() -> Settings:
    """Load settings from environment, with clear error on missing required vars."""
    return Settings()

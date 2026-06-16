"""Unit tests for the Pydantic config and data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daily_bot.config import Settings, load_settings
from daily_bot.models import VALID_CATEGORIES, ScrapedArticle, Subscriber, Summary


def test_settings_require_google_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path):
    # Clear env vars and use a class that doesn't read .env
    for var in ("GOOGLE_API_KEY", "FIREBASE_CREDENTIALS", "SENDER_EMAIL", "SENDER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    class NoEnvSettings(Settings):
        model_config = {"env_file": None, "extra": "ignore"}

    with pytest.raises(ValidationError):
        NoEnvSettings(  # type: ignore[call-arg]
            firebase_credentials="{}",
            sender_email="a@b.c",
            sender_password="password",
        )


def test_settings_require_firebase_credentials(monkeypatch: pytest.MonkeyPatch):
    for var in ("GOOGLE_API_KEY", "FIREBASE_CREDENTIALS", "SENDER_EMAIL", "SENDER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    class NoEnvSettings(Settings):
        model_config = {"env_file": None, "extra": "ignore"}

    with pytest.raises(ValidationError):
        NoEnvSettings(  # type: ignore[call-arg]
            google_api_key="k",
            sender_email="a@b.c",
            sender_password="password",
        )


def test_settings_require_sender_email(monkeypatch: pytest.MonkeyPatch):
    for var in ("GOOGLE_API_KEY", "FIREBASE_CREDENTIALS", "SENDER_EMAIL", "SENDER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    class NoEnvSettings(Settings):
        model_config = {"env_file": None, "extra": "ignore"}

    with pytest.raises(ValidationError):
        NoEnvSettings(  # type: ignore[call-arg]
            google_api_key="k",
            firebase_credentials="{}",
            sender_password="password",
        )


def test_settings_require_sender_password(monkeypatch: pytest.MonkeyPatch):
    for var in ("GOOGLE_API_KEY", "FIREBASE_CREDENTIALS", "SENDER_EMAIL", "SENDER_PASSWORD"):
        monkeypatch.delenv(var, raising=False)

    class NoEnvSettings(Settings):
        model_config = {"env_file": None, "extra": "ignore"}

    with pytest.raises(ValidationError):
        NoEnvSettings(  # type: ignore[call-arg]
            google_api_key="k",
            firebase_credentials="{}",
            sender_email="a@b.c",
        )


def test_settings_defaults(test_settings: Settings):
    """Defaults should be sensible for production use."""
    assert test_settings.smtp_host == "smtp.gmail.com"
    assert test_settings.smtp_port == 465
    assert test_settings.bbc_news_url == "https://www.bbc.com/news"
    assert test_settings.article_limit == 4
    assert test_settings.gemini_model == "gemini-2.5-flash"
    assert test_settings.gemini_retries == 6
    assert test_settings.scrape_concurrency == 5
    assert test_settings.summarize_concurrency == 1
    assert test_settings.circuit_breaker_threshold == 3
    assert test_settings.email_batch_size == 50
    assert test_settings.log_level == "INFO"


def test_settings_override_from_env(monkeypatch: pytest.MonkeyPatch):
    """Environment variables should override defaults."""
    monkeypatch.setenv("GOOGLE_API_KEY", "env-key")
    monkeypatch.setenv("FIREBASE_CREDENTIALS", "{}")
    monkeypatch.setenv("SENDER_EMAIL", "env@example.com")
    monkeypatch.setenv("SENDER_PASSWORD", "envpass")
    monkeypatch.setenv("ARTICLE_LIMIT", "20")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    s = load_settings()
    assert s.google_api_key == "env-key"
    assert s.article_limit == 20
    assert s.log_level == "DEBUG"


def test_summary_model_validates():
    s = Summary(
        title="T",
        summary="S",
        category="world",
        url="https://x.com",
        image_url="",
    )
    assert s.title == "T"
    assert s.image_url == ""


def test_scraped_article_optional_image():
    a = ScrapedArticle(
        url="https://x.com",
        title="T",
        content="C",
    )
    assert a.image_url is None


def test_valid_categories_completeness():
    """Make sure the allowlist has all 9 expected values."""
    expected = {
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
    assert expected == VALID_CATEGORIES


def test_subscriber_defaults_to_bbc():
    """A subscriber without explicit sources should default to ['bbc']."""
    sub = Subscriber(email="user@example.com")
    assert sub.email == "user@example.com"
    assert sub.sources == ["bbc"]
    assert sub.subscribed_at is None


def test_subscriber_accepts_custom_sources():
    """A subscriber may specify any list of source names."""
    sub = Subscriber(email="user@example.com", sources=["bbc", "g1"])
    assert sub.sources == ["bbc", "g1"]


def test_subscriber_empty_sources_allowed():
    """An empty source list is a valid (if unusual) configuration."""
    sub = Subscriber(email="user@example.com", sources=[])
    assert sub.sources == []


def test_subscriber_default_factory_is_independent():
    """Each subscriber's default sources list should be a fresh instance."""
    a = Subscriber(email="a@x.com")
    b = Subscriber(email="b@x.com")
    a.sources.append("g1")
    assert b.sources == ["bbc"]

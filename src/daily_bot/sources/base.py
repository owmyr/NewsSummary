"""NewsSource Protocol and SourceRegistry.

A NewsSource is anything that can:
  - Tell us its name (used as a stable identifier in stored data)
  - Provide a list of top story URLs
  - Scrape a single article from one of those URLs

Implementations subclass NewsSource and override the abstract methods.
The Protocol is provided so duck-typed objects (with the right methods) work too.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

import httpx

from ..models import ScrapedArticle


class NewsSource(ABC):
    """Abstract base class for all news sources.

    Concrete subclasses must implement `name`, `fetch_urls`, and
    `scrape_article`. The `__init__` may accept configuration kwargs
    (e.g. homepage URL) but should have safe defaults.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for this source (e.g. 'bbc', 'guardian')."""
        ...

    @property
    def language(self) -> str:
        """Output language for summaries from this source. Override in subclasses."""
        return "en"

    @abstractmethod
    async def fetch_urls(self, client: httpx.AsyncClient, limit: int) -> list[str]:
        """Return up to `limit` article URLs from this source's front page."""
        ...

    @abstractmethod
    async def scrape_article(self, client: httpx.AsyncClient, url: str) -> ScrapedArticle | None:
        """Scrape a single article. Return None on failure."""
        ...


@runtime_checkable
class NewsSourceProtocol(Protocol):
    """Duck-typed protocol version of NewsSource.

    Useful for type-checking when a callable is passed as a source.
    """

    name: str

    async def fetch_urls(self, client: httpx.AsyncClient, limit: int) -> list[str]: ...

    async def scrape_article(
        self, client: httpx.AsyncClient, url: str
    ) -> ScrapedArticle | None: ...


class SourceRegistry:
    """Maps source names to their concrete NewsSource classes.

    The registry pattern lets new sources be added without changing
    the orchestrator: register a class, then add its name to the
    `sources` setting in config.
    """

    def __init__(self) -> None:
        self._sources: dict[str, type[NewsSource]] = {}

    def register(self, name: str, source_class: type[NewsSource]) -> None:
        """Register a source class under a name.

        Raises ValueError if the class doesn't implement the required interface.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("Source name must be a non-empty string")
        if not issubclass(source_class, NewsSource):
            raise ValueError(f"{source_class.__name__} must subclass NewsSource")
        self._sources[name] = source_class

    def unregister(self, name: str) -> None:
        """Remove a source from the registry."""
        self._sources.pop(name, None)

    def get(self, name: str) -> NewsSource:
        """Instantiate and return the source registered under `name`.

        Raises KeyError if the name is not registered.
        """
        if name not in self._sources:
            raise KeyError(f"Unknown news source: '{name}'. Available: {sorted(self._sources)}")
        return self._sources[name]()

    def names(self) -> list[str]:
        """Return a sorted list of registered source names."""
        return sorted(self._sources)

    def __contains__(self, name: str) -> bool:
        return name in self._sources

    def __len__(self) -> int:
        return len(self._sources)


# Global default registry. Import this to add new sources.
default_registry = SourceRegistry()

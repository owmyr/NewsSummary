"""Pluggable news source architecture.

Adding a new source requires:
  1. Subclassing NewsSource (or implementing the Protocol) in a new module
  2. Registering it via `default_registry.register("name", MySource)`

The orchestrator iterates over the configured `sources` setting and asks
each registered source for its URLs and articles.
"""

from __future__ import annotations

from .base import NewsSource, SourceRegistry, default_registry
from .bbc import BBCSource
from .g1 import G1Source

__all__ = [
    "BBCSource",
    "G1Source",
    "NewsSource",
    "SourceRegistry",
    "default_registry",
]


# Register built-in sources
default_registry.register("bbc", BBCSource)
default_registry.register("g1", G1Source)

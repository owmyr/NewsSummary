"""Re-derive categories for all summaries in a given date's dailySummaries doc.

Useful as a one-shot maintenance tool when the classifier rule set expands
(e.g. a new title keyword is added) and you want to refresh the stored
``category`` field on past data without re-running the full pipeline.

Usage:
    python scripts/rederive_categories.py                # today
    python scripts/rederive_categories.py 2026-06-17     # specific date

The orchestrator already re-derives categories on every load (see
``__main__.run_async``), so this script is only needed if you want to
persist the re-derived values back to Firestore without running the rest
of the pipeline. The orchestrator will also write the re-derived list
back on the next normal run.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime

from daily_bot import db
from daily_bot.summarizer import classify_article


def rederive(date_str: str) -> None:
    existing = db.get_existing_summaries(date_str)
    if not existing:
        print(f"No summaries found in Firestore for {date_str}.")
        return

    re_derived: list[dict] = []
    for a in existing:
        if not a.get("title"):
            continue
        a["category"] = classify_article(
            title=a.get("title", ""),
            url=a.get("url", ""),
            section=a.get("section", ""),
        )
        re_derived.append(a)

    db.save_summaries(date_str, re_derived)
    print(f"Re-derived and saved {len(re_derived)} summaries for {date_str}.")
    print()

    by_source: dict[str, list[dict]] = {}
    for a in re_derived:
        by_source.setdefault(a.get("source", "?"), []).append(a)

    for source in sorted(by_source):
        print(f"--- {source} ---")
        for a in by_source[source]:
            print(f"  [{a['category']:10s}] {a['title'][:70]}")
        print()


def main() -> int:
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    rederive(date_str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

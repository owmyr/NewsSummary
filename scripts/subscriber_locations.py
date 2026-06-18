"""Print an approximate location breakdown of all subscribers.

Reads every subscriber from Firestore and groups them by the most
reliable location signal available, in this order:

    1. ``country`` from IP geolocation (set by the addSubscriber Cloud
       Function via ip-api.com)
    2. ``browser_timezone`` country inferred from the IANA timezone
       (e.g. "America/Sao_Paulo" -> "Brazil") when the IP lookup
       wasn't recorded
    3. "Unknown" for legacy subscribers with no geo data

Usage:
    python scripts/subscriber_locations.py

This is a read-only inspection tool. It does not write to Firestore.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from daily_bot import db

# Maps the leading region of an IANA timezone to a country label.
# Best-effort: the same timezone prefix can cover multiple countries
# (e.g. "America/Los_Angeles" is shared with Canada), so the labels
# here are approximate.
_TIMEZONE_TO_COUNTRY: dict[str, str] = {
    "America/Sao_Paulo": "Brazil",
    "America/Fortaleza": "Brazil",
    "America/Recife": "Brazil",
    "America/Bahia": "Brazil",
    "America/Belem": "Brazil",
    "America/Manaus": "Brazil",
    "America/Cuiaba": "Brazil",
    "America/Porto_Velho": "Brazil",
    "America/Boa_Vista": "Brazil",
    "America/Rio_Branco": "Brazil",
    "America/Araguaina": "Brazil",
    "America/Maceio": "Brazil",
    "America/Santarem": "Brazil",
    "America/Noronha": "Brazil",
    "America/Eirunepe": "Brazil",
    "America/Argentina": "Argentina",
    "America/Mexico": "Mexico",
    "America/New_York": "United States",
    "America/Chicago": "United States",
    "America/Denver": "United States",
    "America/Los_Angeles": "United States",
    "America/Phoenix": "United States",
    "America/Toronto": "Canada",
    "America/Vancouver": "Canada",
    "America/London": "United Kingdom",
    "Europe/London": "United Kingdom",
    "Europe/Lisbon": "Portugal",
    "Europe/Paris": "France",
    "Europe/Berlin": "Germany",
    "Europe/Madrid": "Spain",
    "Europe/Rome": "Italy",
}


def _resolve_location(sub) -> str:
    """Return the best-available country label for a subscriber."""
    if sub.country:
        return sub.country
    if sub.browser_timezone:
        if sub.browser_timezone in _TIMEZONE_TO_COUNTRY:
            return _TIMEZONE_TO_COUNTRY[sub.browser_timezone]
        # Fall back to the region prefix ("America" -> "?America")
        region = sub.browser_timezone.split("/")[0]
        return f"?{region}"
    if sub.timezone and sub.timezone in _TIMEZONE_TO_COUNTRY:
        return _TIMEZONE_TO_COUNTRY[sub.timezone]
    return "Unknown"


def main() -> int:
    subscribers = db.get_all_subscribers()
    if not subscribers:
        print("No subscribers found in Firestore.")
        return 0

    countries = Counter(_resolve_location(s) for s in subscribers)
    timezones = Counter(
        s.browser_timezone or s.timezone or "(none)" for s in subscribers
    )

    total = len(subscribers)
    with_country = sum(1 for s in subscribers if s.country)
    with_browser_tz = sum(1 for s in subscribers if s.browser_timezone)
    with_ip_tz = sum(1 for s in subscribers if s.timezone)
    no_geo = sum(
        1
        for s in subscribers
        if not s.country and not s.browser_timezone and not s.timezone
    )

    print(f"=== Subscriber Location Breakdown ({total} total) ===")
    print(f"  generated: {datetime.now(UTC).isoformat(timespec='seconds')}")
    print()
    print(f"  with country (IP):   {with_country:>3}  ({with_country * 100 // total}%)")
    print(f"  with browser tz:     {with_browser_tz:>3}  ({with_browser_tz * 100 // total}%)")
    print(f"  with IP timezone:    {with_ip_tz:>3}  ({with_ip_tz * 100 // total}%)")
    print(f"  with no geo data:    {no_geo:>3}  ({no_geo * 100 // total}%)")
    print()

    print("--- By country ---")
    for country, count in sorted(countries.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {count:>3}  {country}")
    print()

    print("--- By timezone (browser or IP) ---")
    for tz, count in sorted(timezones.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {count:>3}  {tz}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

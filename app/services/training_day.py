"""training_day — Phase 1 training day resolution via browser timezone.

Phase 1 fallback chain:
  1. valid browser IANA timezone  → training_timezone_source = "browser_iana"
  2. UTC fallback                 → training_timezone_source = "utc_fallback"

Phase 2 (separate approval):
  lat/lng → timezonefinder → IANA timezone → training_timezone_source = "lat_lng_derived"

Design note: ?tz= query param on the Card Studio page is a Phase 1 interim
solution only. Phase 2/3 will derive the training timezone from the stored
attempt location or a user-session-level timezone profile.
"""
from __future__ import annotations

from datetime import date, datetime, timezone as _tz_module
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_UTC = ZoneInfo("UTC")
_LOCATION_FRESHNESS_SECONDS = 300   # 5 minutes


# ── IANA timezone validation ───────────────────────────────────────────────────

def _valid_iana_tz(tz_str: str | None) -> str | None:
    """Return tz_str if it is a valid IANA timezone identifier, else None."""
    if not tz_str or not isinstance(tz_str, str) or len(tz_str) > 64:
        return None
    try:
        ZoneInfo(tz_str)
        return tz_str
    except (ZoneInfoNotFoundError, KeyError):
        return None


# ── Location source ────────────────────────────────────────────────────────────

def resolve_location_source(
    lat: float | None,
    lng: float | None,
    captured_at: datetime | None,
    now: datetime,
) -> str:
    """Classify the browser geolocation data quality.

    Returns:
        "browser_geolocation"        — fresh, valid GPS fix (≤5 min old)
        "stale_browser_geolocation"  — lat/lng present but older than 5 min
        "unavailable"                — no GPS data at all
    """
    if lat is None or lng is None or captured_at is None:
        return "unavailable"
    age = (now - captured_at).total_seconds()
    return "browser_geolocation" if age <= _LOCATION_FRESHNESS_SECONDS else "stale_browser_geolocation"


# ── Phase 1: timezone resolution ──────────────────────────────────────────────

def resolve_training_timezone(
    browser_timezone: str | None,
) -> tuple[str, str]:
    """Return (training_timezone, training_timezone_source).

    Phase 1 fallback chain:
      1. valid browser IANA tz → ("Europe/Budapest", "browser_iana")
      2. UTC                   → ("UTC", "utc_fallback")

    Phase 2 will add lat/lng → timezone before step 1 without changing
    this function's signature — a new param will be added with a default.
    """
    valid = _valid_iana_tz(browser_timezone)
    if valid:
        return valid, "browser_iana"
    return "UTC", "utc_fallback"


# ── Training local date ────────────────────────────────────────────────────────

def compute_training_local_date(
    completed_at: datetime,
    training_timezone: str,
) -> date:
    """Return the local calendar date of completed_at in training_timezone.

    Falls back to UTC date if the timezone string is invalid.
    """
    try:
        return completed_at.astimezone(ZoneInfo(training_timezone)).date()
    except Exception:
        return completed_at.astimezone(_UTC).date()


def current_training_date_utc() -> date:
    """Return today's UTC date — server-side fallback when no user tz is known."""
    return datetime.now(_tz_module.utc).date()

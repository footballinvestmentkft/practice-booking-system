"""training_day — Training day resolution via GPS coordinates or browser timezone.

Phase 1 fallback chain (browser_timezone only):
  1. valid browser IANA timezone  → training_timezone_source = "browser_iana"
  2. UTC fallback                 → training_timezone_source = "utc_fallback"

Phase 2 fallback chain (lat/lng takes priority):
  1. fresh GPS + accuracy ≤ 500m → timezonefinder → "lat_lng_derived"
  2. valid browser IANA timezone  → "browser_iana"
  3. UTC fallback                 → "utc_fallback"

Design note: ?tz= query param on the Card Studio page remains Phase 1 (browser_iana)
because the Card Studio does not collect GPS. Attempt submit routes use Phase 2.

Phase 3 (separate approval):
  Campus geofence, on-site challenge enforcement, location retention policy.
"""
from __future__ import annotations

from datetime import date, datetime, timezone as _tz_module
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_UTC = ZoneInfo("UTC")
_LOCATION_FRESHNESS_SECONDS = 300   # 5 minutes
_LOCATION_ACCURACY_MAX_M    = 500   # max accuracy_m for lat_lng_derived tz


# ── timezonefinder singleton (lazy init — avoids startup cost) ────────────────

_TZF = None  # type: ignore[assignment]


def _get_tzf():
    """Return module-level TimezoneFinder instance (lazy, thread-safe for reads)."""
    global _TZF
    if _TZF is None:
        from timezonefinder import TimezoneFinder  # noqa: PLC0415
        _TZF = TimezoneFinder()
    return _TZF


def _derive_tz_from_coords(lat: float, lng: float) -> str | None:
    """Look up IANA timezone from GPS coordinates. Returns None on any error."""
    try:
        return _get_tzf().timezone_at(lat=lat, lng=lng)
    except Exception:  # noqa: BLE001
        return None


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


# ── Phase 2: timezone resolution ──────────────────────────────────────────────

def resolve_training_timezone(
    browser_timezone: str | None,
    lat: float | None = None,
    lng: float | None = None,
    accuracy_m: int | None = None,
    captured_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Return (training_timezone, training_timezone_source).

    Phase 2 fallback chain:
      1. Fresh GPS (≤5 min, accuracy ≤500m) → timezonefinder → "lat_lng_derived"
      2. Valid browser IANA tz              → "browser_iana"
      3. UTC                               → "utc_fallback"

    Backward-compatible: resolve_training_timezone("Europe/Budapest") still returns
    ("Europe/Budapest", "browser_iana") unchanged — all Phase 1 callers unaffected.
    """
    # ── Step 1: lat/lng → timezonefinder (Phase 2) ───────────────────────────
    if lat is not None and lng is not None:
        _now = now or datetime.now(_tz_module.utc)
        _acc_ok    = (accuracy_m is None) or (accuracy_m <= _LOCATION_ACCURACY_MAX_M)
        _fresh_ok  = (captured_at is None) or (
            (_now - captured_at).total_seconds() <= _LOCATION_FRESHNESS_SECONDS
        )
        if _acc_ok and _fresh_ok:
            try:
                derived = _derive_tz_from_coords(lat, lng)
            except Exception:  # noqa: BLE001
                derived = None
            if derived and _valid_iana_tz(derived):
                return derived, "lat_lng_derived"

    # ── Step 2: browser IANA timezone (Phase 1) ──────────────────────────────
    valid = _valid_iana_tz(browser_timezone)
    if valid:
        return valid, "browser_iana"

    # ── Step 3: UTC fallback ─────────────────────────────────────────────────
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
    except Exception:  # noqa: BLE001
        return completed_at.astimezone(_UTC).date()


def current_training_date_utc() -> date:
    """Return today's UTC date — server-side fallback when no user tz is known."""
    return datetime.now(_tz_module.utc).date()

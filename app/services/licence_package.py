"""
licence_package — Shared constants and helpers for LFA Football Player licence unlock.

Single source of truth for:
  - Allowed unlock durations (months)
  - Credit cost per duration
  - Calendar-month expiry calculation (uses relativedelta, not day arithmetic)

All other code that needs duration/cost logic MUST import from here.
Never hardcode duration costs in endpoint handlers.
"""
from __future__ import annotations

from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta

# ── Allowed durations and costs ───────────────────────────────────────────────

# Maps duration_months → credit cost.
# Keys are the ONLY accepted values for duration_months across all unlock paths.
UNLOCK_DURATION_COST: dict[int, int] = {
    1:  100,
    3:  250,
    6:  450,
    12: 800,
}

ALLOWED_DURATIONS: tuple[int, ...] = tuple(UNLOCK_DURATION_COST.keys())  # (1, 3, 6, 12)

DEFAULT_DURATION_MONTHS: int = 1


# ── Validation ────────────────────────────────────────────────────────────────

def validate_duration_months(value: int) -> int:
    """
    Validate that value is an allowed duration.

    Returns the validated integer on success.
    Raises ValueError with a user-facing message on failure.
    """
    if value not in ALLOWED_DURATIONS:
        raise ValueError(
            f"duration_months must be one of {list(ALLOWED_DURATIONS)}, got {value!r}"
        )
    return value


def cost_for_duration(duration_months: int) -> int:
    """Return the credit cost for a given duration. Caller must validate first."""
    return UNLOCK_DURATION_COST[duration_months]


# ── Expiry calculation ────────────────────────────────────────────────────────

def calculate_expires_at(now: datetime, duration_months: int) -> datetime:
    """
    Calculate the licence expiry datetime using calendar-month arithmetic.

    Uses dateutil.relativedelta so that e.g. 1 month from 2026-01-31 → 2026-02-28,
    not 2026-03-02 (which timedelta(days=30) would produce).

    Args:
        now:             The unlock timestamp (should be timezone-aware UTC).
        duration_months: Validated integer in ALLOWED_DURATIONS.

    Returns:
        Timezone-aware UTC datetime representing the expiry.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now + relativedelta(months=duration_months)


# ── Expiry helpers ────────────────────────────────────────────────────────────

def is_licence_expired(licence, now: datetime | None = None) -> bool:
    """
    Return True if the licence has a set expires_at that is in the past.

    NULL expires_at → not expired (legacy perpetual behaviour).
    Handles both naive and timezone-aware expires_at stored in the DB.
    """
    if licence.expires_at is None:
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    exp = licence.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp <= now


def sync_active_on_expiry(licence, db, now: datetime | None = None) -> bool:
    """
    Lazily set licence.is_active = False if the licence is expired.

    Flushes (does NOT commit) so the caller can batch additional writes
    before committing. The caller is responsible for db.commit().

    Returns True if the licence is (or was just marked) expired.
    """
    if not is_licence_expired(licence, now):
        return False
    if licence.is_active:
        licence.is_active = False
        db.flush()
    return True

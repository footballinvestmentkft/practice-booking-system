"""
academy_id_service — lfa_academy_id generation, verify rate limiting, display labels.

lfa_academy_id format: LFA-{YYYY}-{SEQ5}
  YYYY = year of user.created_at
  SEQ5 = 5-digit zero-padded sequence within that year (00001–99999)

public_token: UUID v4, stored on the User model, used exclusively as the
  path segment in /verify/{token}.  Never written to logs.

Race-condition protection for lfa_academy_id:
  - The DB UNIQUE constraint is the authoritative guard.
  - The service retries up to _MAX_ASSIGN_ATTEMPTS times on IntegrityError.
  - With a small user base (< 10 000) collisions are extremely rare; the
    retry budget of 5 covers any burst of simultaneous registrations.
"""
from __future__ import annotations

import logging
import uuid
from collections import deque
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# ── Rate limit (verify page) ──────────────────────────────────────────────────

_VERIFY_MAX:    int   = 20   # max requests per window
_VERIFY_WINDOW: float = 60.0 # seconds

_verify_buckets: dict[str, deque] = {}
_verify_lock = Lock()


def check_verify_rate_limit(ip: str) -> bool:
    """
    Sliding-window rate limit for GET /verify/{token}.
    Returns True (allowed) or False (too many requests).
    Mirrors check_bg_removal_rate_limit() pattern.
    """
    now = datetime.now(timezone.utc).timestamp()
    with _verify_lock:
        bucket = _verify_buckets.setdefault(ip, deque())
        # evict entries outside the window
        while bucket and now - bucket[0] > _VERIFY_WINDOW:
            bucket.popleft()
        if len(bucket) >= _VERIFY_MAX:
            return False
        bucket.append(now)
        return True


# ── Specialization display labels ─────────────────────────────────────────────

_SPEC_LABELS: dict[str, str] = {
    "lfa_football_player": "LFA Football Player",
    "lfa_coach":           "LFA Coach",
    "gancuju_player":      "GānCuju Player",
    "internship":          "Internship",
}


def specialization_display_label(raw: str | None) -> str | None:
    """
    Map a SpecializationType raw value to a human-readable label.
    Returns None if raw is None or empty — callers hide the field.
    """
    if not raw:
        return None
    return _SPEC_LABELS.get(raw.lower(), raw.replace("_", " ").title())


# ── lfa_academy_id generation ─────────────────────────────────────────────────

_MAX_ASSIGN_ATTEMPTS = 5


def _next_seq_for_year(year: int, db: Session) -> int:
    """Count existing lfa_academy_id values for *year* and return count + 1."""
    row = db.execute(
        text("SELECT COUNT(*) FROM users WHERE lfa_academy_id LIKE :pat"),
        {"pat": f"LFA-{year}-%"},
    ).scalar()
    return (row or 0) + 1


def assign_lfa_academy_id(user, db: Session) -> str:
    """
    Generate and persist a unique lfa_academy_id for *user*.

    Uses COUNT(*)+1 as the candidate sequence number, then flushes to let the
    DB UNIQUE constraint detect collisions.  On IntegrityError the flush is
    rolled back and the next sequence number is tried (up to
    _MAX_ASSIGN_ATTEMPTS times).

    Raises RuntimeError if all attempts fail (should never happen in practice).
    """
    year = (user.created_at or datetime.now(timezone.utc)).year
    for attempt in range(1, _MAX_ASSIGN_ATTEMPTS + 1):
        seq       = _next_seq_for_year(year, db)
        candidate = f"LFA-{year}-{seq:05d}"
        try:
            user.lfa_academy_id = candidate
            db.flush()
            log.debug("Assigned lfa_academy_id=%s to user_id=%s", candidate, user.id)
            return candidate
        except IntegrityError:
            db.rollback()
            log.warning(
                "lfa_academy_id collision attempt %d/%d: %s (user_id=%s)",
                attempt, _MAX_ASSIGN_ATTEMPTS, candidate, user.id,
            )
    raise RuntimeError(
        f"Could not assign lfa_academy_id to user_id={user.id} "
        f"after {_MAX_ASSIGN_ATTEMPTS} attempts"
    )


def ensure_public_token(user, db: Session) -> None:
    """Assign a public_token UUID if the user does not have one yet."""
    if user.public_token is None:
        user.public_token = uuid.uuid4()
        db.flush()

"""
Instructor Eligibility Service

Rögzített domain policy (2026-05-07):

  Master Instructor:
    - User.role == INSTRUCTOR
    - User.is_active == True
    - UserLicense(specialization_type="LFA_COACH", is_active=True) létezik
    - expires_at IS NULL OR expires_at > now()   [timezone-safe]
    - current_level >= _required_level(age_groups)
    - payment_verified: OUT-OF-SCOPE (nem enforcelve)

  Field / Assistant Instructor:
    - Ugyanaz mint Master
    - Minimum level: max(1, _required_level(age_groups) - 1)

  Revoked / suspended:
    - is_active=False lefedi; nincs külön revoked/suspended mező ebben a PR-ban.

  Multi-age Promotion Event:
    - A legmagasabb (legszigorúbb) age_group minimumát kell teljesíteni.

Age group → minimum coach level (LFA scale 1–8):
    PRE    → 1   (COACH_LFA_PRE_ASSISTANT)
    YOUTH  → 3   (COACH_LFA_YOUTH_ASSISTANT)
    AMATEUR → 5  (COACH_LFA_AMATEUR_ASSISTANT)
    PRO    → 7   (COACH_LFA_PRO_ASSISTANT)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.license import UserLicense
from app.models.user import User, UserRole

if TYPE_CHECKING:
    from app.models.semester import Semester

# ── Age group → minimum coach level ──────────────────────────────────────────

_LEVEL_FOR_AGE_GROUP: dict[str, int] = {
    "PRE": 1,
    "YOUTH": 3,
    "AMATEUR": 5,
    "PRO": 7,
}


# ── Timezone-safe comparison ──────────────────────────────────────────────────

def _to_utc_aware(dt: datetime) -> datetime:
    """Normalize a datetime to UTC-aware.

    Treats naive datetimes as UTC (the convention used throughout this project).
    Safe to call on aware datetimes too — they are returned unchanged if already
    UTC, otherwise converted.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _required_level(age_groups: list[str]) -> int:
    """Return the highest minimum coach level required across all age groups.

    Empty list → 1 (most permissive fallback).
    Unknown age group string → treated as 1 (backward-compat).
    """
    return max((_LEVEL_FOR_AGE_GROUP.get(ag, 1) for ag in age_groups), default=1)


def _active_coach_license(db: Session, user_id: int) -> UserLicense | None:
    """Return the highest-level active, non-expired LFA_COACH license, or None.

    SQLAlchemy filter handles timezone comparison at DB level (PostgreSQL TIMESTAMPTZ).
    The Python-side _to_utc_aware() call ensures we pass a timezone-aware datetime
    regardless of the local system clock configuration.
    """
    now = _to_utc_aware(datetime.now(timezone.utc))
    return (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user_id,
            UserLicense.specialization_type == "LFA_COACH",
            UserLicense.is_active == True,  # noqa: E712
            or_(UserLicense.expires_at.is_(None), UserLicense.expires_at > now),
        )
        .order_by(UserLicense.current_level.desc())
        .first()
    )


def _get_master_instructor_id(db: Session, tournament_id: int) -> int | None:
    """Resolve the master instructor user-id for a tournament.

    Checks (in order):
    1. Semester.master_instructor_id  (legacy / wizard-assigned field)
    2. TournamentInstructorSlot with role=MASTER and status != ABSENT

    Returns None if no master assignment found — callers must handle this as
    "No master instructor assigned" (not "User not found").
    """
    from app.models.semester import Semester
    from app.models.tournament_instructor_slot import (
        TournamentInstructorSlot,
        SlotRole,
        SlotStatus,
    )

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return None

    if t.master_instructor_id:
        return t.master_instructor_id

    slot = (
        db.query(TournamentInstructorSlot)
        .filter(
            TournamentInstructorSlot.semester_id == tournament_id,
            TournamentInstructorSlot.role == SlotRole.MASTER.value,
            TournamentInstructorSlot.status != SlotStatus.ABSENT.value,
        )
        .first()
    )
    return slot.instructor_id if slot else None


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_tournament_age_groups(tournament: "Semester") -> list[str]:
    """Canonical age group resolution for any Semester/tournament object.

    Priority:
      1. age_groups  — JSONB list (multi-age Promotion Events)
      2. age_group   — scalar string (single-age tournaments)
      3. []          — neither set; callers use _required_level([]) → 1

    Shared by lifecycle.py, generation_validator.py, sponsors.py, and
    instructor_planning_service.py — do NOT duplicate this logic inline.
    """
    if tournament.age_groups:
        return list(tournament.age_groups)
    if tournament.age_group:
        return [tournament.age_group]
    return []


def is_eligible_master_instructor(
    db: Session,
    user_id: int,
    age_groups: list[str],
) -> tuple[bool, str]:
    """Check master instructor eligibility against the rögzített domain policy.

    Returns:
        (True, "")               — eligible
        (False, human_reason)    — not eligible; reason is English, suitable for
                                   HTTP error detail or redirect query param.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, f"User {user_id} not found"
    if user.role != UserRole.INSTRUCTOR:
        return False, f"User role is '{user.role.value}', expected 'instructor'"
    if not user.is_active:
        return False, "User account is inactive"

    lic = _active_coach_license(db, user_id)
    if not lic:
        return False, "No active, valid LFA_COACH license found (license may be missing, inactive, or expired)"

    req = _required_level(age_groups)
    if lic.current_level < req:
        return False, (
            f"Coach level {lic.current_level} is insufficient for age groups "
            f"{age_groups} (minimum required: {req})"
        )

    return True, ""


def is_eligible_field_instructor(
    db: Session,
    user_id: int,
    age_groups: list[str],
) -> tuple[bool, str]:
    """Check field / assistant instructor eligibility.

    Same as master but minimum level is max(1, _required_level(age_groups) - 1).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False, f"User {user_id} not found"
    if user.role != UserRole.INSTRUCTOR:
        return False, f"User role is '{user.role.value}', expected 'instructor'"
    if not user.is_active:
        return False, "User account is inactive"

    lic = _active_coach_license(db, user_id)
    if not lic:
        return False, "No active, valid LFA_COACH license found (license may be missing, inactive, or expired)"

    req = max(1, _required_level(age_groups) - 1)
    if lic.current_level < req:
        return False, (
            f"Coach level {lic.current_level} is insufficient for field role in "
            f"age groups {age_groups} (minimum required: {req})"
        )

    return True, ""


def check_tournament_master_instructor_eligible(
    db: Session,
    tournament_id: int,
) -> tuple[bool, str]:
    """Check that the tournament's assigned master instructor is eligible.

    Used by lifecycle.py (CHECK_IN_OPEN) and GenerationValidator.

    Returns:
        (True, "")                        — eligible
        (False, "No master instructor assigned")      — no assignment at all
        (False, human_reason_from_policy) — assigned but not eligible
    """
    from app.models.semester import Semester

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return False, "Tournament not found"

    master_id = _get_master_instructor_id(db, tournament_id)
    if master_id is None:
        return False, "No master instructor assigned"

    age_groups = resolve_tournament_age_groups(t)
    return is_eligible_master_instructor(db, master_id, age_groups)


def get_eligible_master_instructors(
    db: Session,
    age_groups: list[str] | None = None,
) -> list[User]:
    """Return all Users eligible as master instructor.

    age_groups=None → license + role filter only (level NOT filtered).
                      Used for wizard GET where age_groups are not yet known.
    age_groups set  → full level filter applied.

    Uses JOIN (not subquery) to avoid SQLAlchemy in_() subquery warnings.
    """
    now = _to_utc_aware(datetime.now(timezone.utc))

    license_conditions = and_(
        UserLicense.specialization_type == "LFA_COACH",
        UserLicense.is_active == True,  # noqa: E712
        or_(UserLicense.expires_at.is_(None), UserLicense.expires_at > now),
    )
    if age_groups:
        req = _required_level(age_groups)
        license_conditions = and_(license_conditions, UserLicense.current_level >= req)

    return (
        db.query(User)
        .join(UserLicense, and_(UserLicense.user_id == User.id, license_conditions))
        .filter(
            User.role == UserRole.INSTRUCTOR,
            User.is_active == True,  # noqa: E712
        )
        .order_by(User.name)
        .all()
    )


def get_instructor_license_levels(
    db: Session,
    user_ids: list[int],
) -> dict[int, int]:
    """Return {user_id: current_level} for the given instructor user IDs.

    Only returns entries where an active, non-expired LFA_COACH license exists.
    Used to populate the `instructor_license_levels` dict in template context
    without attaching dynamic attributes to ORM objects.
    """
    if not user_ids:
        return {}

    now = _to_utc_aware(datetime.now(timezone.utc))
    rows = (
        db.query(UserLicense.user_id, UserLicense.current_level)
        .filter(
            UserLicense.user_id.in_(user_ids),
            UserLicense.specialization_type == "LFA_COACH",
            UserLicense.is_active == True,  # noqa: E712
            or_(UserLicense.expires_at.is_(None), UserLicense.expires_at > now),
        )
        .all()
    )
    return {row.user_id: row.current_level for row in rows}

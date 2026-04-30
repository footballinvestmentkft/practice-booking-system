#!/usr/bin/env python3
"""
Phase 6.3 Load Test Seed Script
================================

Creates 100 student accounts (load-user-0001..0100@lfa.com) with:
  - role: STUDENT
  - credit_balance: 50,000
  - UserLicense(LFA_FOOTBALL_PLAYER, is_active=True)
  - Realistic profile fields (nationality, gender, country) for visual QA
  - motivation_scores with position/goals/motivation/average_skill_level
  - football_skills baseline at 60 for all 29 skill keys

Also creates (if not already present):
  - 1 MINI_SEASON ONGOING semester for enrollment tasks
  - 5 public tournament events for browse tasks (uses existing Semester records
    if available, to avoid FK complexity)

Idempotent: safe to run multiple times — skips existing records.
Backfills profile/skill fields on existing load users if they are missing.

Outputs (printed to stdout for use by run_phase63_load.sh):
  LOAD_SEMESTER_ID={id}
  LOAD_EVENT_IDS={id1},{id2},...

Usage:
  python scripts/seed_load_test_users.py
  # Or with custom count:
  LOAD_USERS_COUNT=10 python scripts/seed_load_test_users.py
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import get_password_hash
from app.database import SessionLocal
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.user import User, UserRole

# ── Configuration ────────────────────────────────────────────────────────────

USERS_COUNT   = int(os.getenv("LOAD_USERS_COUNT", "100"))
USER_PASSWORD = "LoadTest1234!"
CREDIT_BALANCE = 50_000    # enough for 500 enrollments at cost=100 each
ENROLL_COST    = 100

# Realistic profile defaults — used for QA-visible player card rendering.
# All load users get the same values; differentiation is not needed for load testing.
_NATIONALITY = "Hungarian"
_GENDER      = "Male"
_COUNTRY     = "Hungary"
_POSITION    = "MIDFIELDER"

# 29 skill keys — baseline at 60 for all.
# Mirrors SKILL_CATEGORIES key list; kept inline to avoid circular import risk.
_SKILL_KEYS = [
    "ball_control", "dribbling", "finishing", "shot_power", "long_shots",
    "volleys", "crossing", "passing", "heading", "tackle", "marking",
    "free_kicks", "corners", "penalties",
    "positioning_off", "positioning_def", "vision", "aggression",
    "reactions", "composure", "consistency", "tactical_awareness",
    "acceleration", "sprint_speed", "agility", "jumping",
    "strength", "stamina", "balance",
]
_SKILL_BASELINE = 60.0

SEMESTER_CODE = "LOAD-TEST-MINI-01"
SEMESTER_NAME = "Load Test MINI_SEASON (Phase 6.3)"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_user(db, i: int) -> User:
    email = f"load-user-{i:04d}@lfa.com"
    user  = db.query(User).filter(User.email == email).first()
    if user:
        # Ensure sufficient credits for the load test
        if user.credit_balance < CREDIT_BALANCE // 2:
            user.credit_balance = CREDIT_BALANCE
        # Backfill profile fields if missing (prior seed runs omitted these)
        if not user.nationality:
            user.nationality = _NATIONALITY
        if not user.gender:
            user.gender = _GENDER
        if not user.country:
            user.country = _COUNTRY
        db.flush()
        return user

    user = User(
        email          = email,
        name           = f"Load User {i:04d}",
        password_hash  = get_password_hash(USER_PASSWORD),
        role           = UserRole.STUDENT,
        credit_balance = CREDIT_BALANCE,
        is_active      = True,
        specialization = "LFA_FOOTBALL_PLAYER",
        date_of_birth  = date(2000, 1, 1),  # skip age-verification on login
        nationality    = _NATIONALITY,
        gender         = _GENDER,
        country        = _COUNTRY,
    )
    db.add(user)
    db.flush()
    return user


def _build_motivation_scores() -> dict:
    return {
        "position":               _POSITION,
        "goals":                  "improve_skills",
        "motivation":             "",
        "average_skill_level":    _SKILL_BASELINE,
        "onboarding_completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_football_skills() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        key: {
            "baseline":         _SKILL_BASELINE,
            "current_level":    _SKILL_BASELINE,
            "tournament_delta": 0.0,
            "total_delta":      0.0,
            "tournament_count": 0,
            "last_updated":     now,
        }
        for key in _SKILL_KEYS
    }


def _ensure_license(db, user: User) -> UserLicense:
    lic = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        )
        .first()
    )
    if lic:
        if not lic.is_active:
            lic.is_active = True
        # Backfill motivation_scores + football_skills if missing
        if not lic.motivation_scores:
            lic.motivation_scores = _build_motivation_scores()
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(lic, "motivation_scores")
        if not lic.football_skills:
            lic.football_skills = _build_football_skills()
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(lic, "football_skills")
        db.flush()
        return lic

    lic = UserLicense(
        user_id              = user.id,
        specialization_type  = "LFA_FOOTBALL_PLAYER",
        is_active            = True,
        started_at           = datetime.now(timezone.utc),
        onboarding_completed = True,
        payment_verified     = True,
        credit_balance       = 0,
        motivation_scores    = _build_motivation_scores(),
        football_skills      = _build_football_skills(),
    )
    db.add(lic)
    db.flush()
    return lic


def _ensure_semester(db) -> Semester:
    sem = (
        db.query(Semester)
        .filter(Semester.code == SEMESTER_CODE)
        .first()
    )
    if sem:
        # Ensure it stays open
        if sem.status != SemesterStatus.ONGOING:
            sem.status = SemesterStatus.ONGOING
            db.flush()
        return sem

    today = date.today()
    sem = Semester(
        code              = SEMESTER_CODE,
        name              = SEMESTER_NAME,
        semester_category = SemesterCategory.MINI_SEASON,
        status            = SemesterStatus.ONGOING,
        specialization_type = "LFA_FOOTBALL_PLAYER",
        start_date        = today,
        end_date          = today + timedelta(days=90),
        enrollment_cost   = ENROLL_COST,
    )
    db.add(sem)
    db.flush()
    return sem


def _find_browse_events(db) -> list[int]:
    """Find existing IN_PROGRESS tournament semesters for browse tasks."""
    rows = (
        db.query(Semester.id)
        .filter(
            Semester.tournament_status == "IN_PROGRESS",
            Semester.semester_category.in_([
                SemesterCategory.ACADEMY_SEASON,
                SemesterCategory.MINI_SEASON,
            ]),
        )
        .limit(10)
        .all()
    )
    ids = [r.id for r in rows]

    # Fallback: any non-CANCELLED semester
    if not ids:
        rows = (
            db.query(Semester.id)
            .filter(Semester.tournament_status != "CANCELLED")
            .limit(5)
            .all()
        )
        ids = [r.id for r in rows]

    # Last resort: use the load-test semester itself
    if not ids:
        sem = _ensure_semester(db)
        ids = [sem.id]

    return ids[:10]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    db = SessionLocal()
    try:
        print(f"[seed] Creating {USERS_COUNT} load-test student accounts...", flush=True)

        created = 0
        skipped = 0
        for i in range(1, USERS_COUNT + 1):
            user = _ensure_user(db, i)
            _ensure_license(db, user)
            if user.id is None or getattr(user, "_sa_instance_state").pending:
                created += 1
            else:
                skipped += 1

        db.commit()
        print(f"[seed] Users: {created} created, {skipped} already existed", flush=True)

        # Semester for enrollment
        sem = _ensure_semester(db)
        db.commit()
        print(f"[seed] Enrollment semester: id={sem.id}  '{sem.name}'", flush=True)

        # Events for browse
        event_ids = _find_browse_events(db)
        print(f"[seed] Browse events: {event_ids}", flush=True)

        # Output env-var style for run_phase63_load.sh
        print()
        print(f"LOAD_SEMESTER_ID={sem.id}")
        print(f"LOAD_SEMESTER_IDS={sem.id}")
        print(f"LOAD_EVENT_IDS={','.join(str(i) for i in event_ids)}")

    except Exception as exc:
        db.rollback()
        print(f"[seed] ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

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
_HEIGHT_CM   = 180
_WEIGHT_KG   = 75
_RIGHT_FOOT  = 68.0   # dominant badge: right-footed ("Rl")
_LEFT_FOOT   = 32.0

# 29 skill keys — varied baseline values for realistic card rendering.
# Values represent a competent MIDFIELDER profile (OVR ≈ 67).
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

# Realistic per-skill values — all bars visible, varied heights, no identical rows.
_SKILL_VALUES: dict[str, float] = {
    # Outfield (11)
    "ball_control":  68.0,
    "dribbling":     71.0,
    "finishing":     59.0,
    "shot_power":    63.0,
    "long_shots":    65.0,
    "volleys":       58.0,
    "crossing":      72.0,
    "passing":       80.0,
    "heading":       62.0,
    "tackle":        68.0,
    "marking":       65.0,
    # Set Pieces (3)
    "free_kicks":    66.0,
    "corners":       74.0,
    "penalties":     70.0,
    # Mental (8)
    "positioning_off":    72.0,
    "positioning_def":    69.0,
    "vision":             76.0,
    "aggression":         64.0,
    "reactions":          71.0,
    "composure":          68.0,
    "consistency":        66.0,
    "tactical_awareness": 73.0,
    # Physical Fitness (7)
    "acceleration":  70.0,
    "sprint_speed":  68.0,
    "agility":       73.0,
    "jumping":       65.0,
    "strength":      67.0,
    "stamina":       72.0,
    "balance":       70.0,
}
_SKILL_BASELINE = 60.0  # kept for legacy reference

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
        "height_cm":              _HEIGHT_CM,
        "weight_kg":              _WEIGHT_KG,
        "onboarding_completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_football_skills() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        key: {
            "baseline":         _SKILL_VALUES.get(key, _SKILL_BASELINE),
            "current_level":    _SKILL_VALUES.get(key, _SKILL_BASELINE),
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
        from sqlalchemy.orm.attributes import flag_modified
        if not lic.is_active:
            lic.is_active = True
        # Backfill motivation_scores — add missing height_cm / weight_kg
        if not lic.motivation_scores:
            lic.motivation_scores = _build_motivation_scores()
            flag_modified(lic, "motivation_scores")
        else:
            ms = dict(lic.motivation_scores)
            changed = False
            if not ms.get("height_cm"):
                ms["height_cm"] = _HEIGHT_CM
                changed = True
            if not ms.get("weight_kg"):
                ms["weight_kg"] = _WEIGHT_KG
                changed = True
            if changed:
                lic.motivation_scores = ms
                flag_modified(lic, "motivation_scores")
        # Backfill football_skills with varied values (replaces flat 60.0 baselines)
        if not lic.football_skills:
            lic.football_skills = _build_football_skills()
            flag_modified(lic, "football_skills")
        else:
            # Update any skill whose value is still at the old flat baseline (60.0)
            fs = dict(lic.football_skills)
            changed = False
            for key, target in _SKILL_VALUES.items():
                entry = fs.get(key)
                if entry is None:
                    fs[key] = {
                        "baseline": target, "current_level": target,
                        "tournament_delta": 0.0, "total_delta": 0.0,
                        "tournament_count": 0,
                        "last_updated": datetime.now(timezone.utc).isoformat(),
                    }
                    changed = True
                elif isinstance(entry, dict) and entry.get("current_level") == _SKILL_BASELINE:
                    entry["baseline"]      = target
                    entry["current_level"] = target
                    fs[key] = entry
                    changed = True
                elif isinstance(entry, (int, float)) and float(entry) == _SKILL_BASELINE:
                    fs[key] = target
                    changed = True
            if changed:
                lic.football_skills = fs
                flag_modified(lic, "football_skills")
        # Backfill foot scores for dominant badge
        if lic.right_foot_score is None:
            lic.right_foot_score = _RIGHT_FOOT
        if lic.left_foot_score is None:
            lic.left_foot_score = _LEFT_FOOT
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
        right_foot_score     = _RIGHT_FOOT,
        left_foot_score      = _LEFT_FOOT,
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

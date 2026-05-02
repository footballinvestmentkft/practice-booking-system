"""
DEV/DEMO SEED — Laterality tournament history for UI validation.

Creates persistent tournament history for the 4 laterality control users by
running the real distribute_rewards_for_user() pipeline.  After this seed:

  - Each control user has ≥ 1 TournamentParticipation row with foot_context set
  - UserLicense.football_skills contains lateral_components on finishing / crossing
  - user.onboarding_completed = True → /skills page is accessible

NOT for production use.  Does NOT run in CI.
Only explicit execution creates persistent data.

Usage
-----
  # Seed (idempotent — safe to re-run):
  python scripts/seed_laterality_dev_history.py

  # Clean DEV-LAT-* tournaments only, then re-seed:
  python scripts/seed_laterality_dev_history.py --clean

  # Clean only (no re-seed):
  python scripts/seed_laterality_dev_history.py --clean --no-reseed
"""

import sys
import os
import argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.tournament_achievement import TournamentParticipation, TournamentSkillMapping
from app.models.user import User
from app.models.license import UserLicense
from app.services.tournament.tournament_reward_orchestrator import distribute_rewards_for_user
from scripts.seed_laterality_test_fixtures import seed_laterality_fixtures

# ── Sentinel prefix ────────────────────────────────────────────────────────────
# All semesters created by this script carry this prefix.  --clean removes ONLY
# these records and their cascaded children — never production data, never users.

_DEV_PREFIX = "DEV-LAT-"

# ── Scenario: which preset to run for each control user ───────────────────────
# Two tournaments per user → right + non-right bucket always populated.
# lat.unmeasured gets one tournament (exercises NULL foot_score fallback path).

_SCENARIOS = [
    # (user_email, preset_code, skills, placement)
    ("lat.right@lfa-test.com",     "lat_shooting_right",   ["finishing", "shot_power"], 1),
    ("lat.right@lfa-test.com",     "lat_crossing_neutral", ["crossing"],                2),
    ("lat.left@lfa-test.com",      "lat_shooting_left",    ["finishing", "shot_power"], 1),
    ("lat.left@lfa-test.com",      "lat_crossing_left",    ["crossing"],                1),
    ("lat.balanced@lfa-test.com",  "lat_shooting_neutral", ["finishing", "shot_power"], 3),
    ("lat.balanced@lfa-test.com",  "lat_crossing_right",   ["crossing"],                1),
    ("lat.unmeasured@lfa-test.com","lat_shooting_right",   ["finishing", "shot_power"], 2),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sem_code(email: str, preset_code: str) -> str:
    """Deterministic Semester.code so idempotency check works by code."""
    safe_email = email.split("@")[0].replace(".", "-")
    return f"{_DEV_PREFIX}{safe_email}--{preset_code}"


def _ensure_tournament(db: Session, sem_code: str, preset_id: int, skills: list) -> Semester:
    """Return existing Semester or create a new one; flush but do NOT commit."""
    sem = db.query(Semester).filter(Semester.code == sem_code).first()
    if sem:
        return sem

    sem = Semester(
        code=sem_code,
        name=f"[DEV] Laterality History — {sem_code[len(_DEV_PREFIX):]}",
        start_date=date.today() - timedelta(days=14),
        end_date=date.today() + timedelta(days=16),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()

    for skill in skills:
        db.add(TournamentSkillMapping(
            semester_id=sem.id,
            skill_name=skill,
            skill_category="football_skill",
            weight=1.0,
        ))

    db.add(GameConfiguration(
        semester_id=sem.id,
        game_preset_id=preset_id,
    ))

    db.flush()
    db.refresh(sem)
    return sem


def _has_participation(db: Session, user_id: int, semester_id: int) -> bool:
    return db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.semester_id == semester_id,
    ).first() is not None


# ── Cleanup ───────────────────────────────────────────────────────────────────

def clean_dev_history(db: Session) -> int:
    """Delete all DEV-LAT-* semesters (CASCADE removes linked rows).
    Returns count of deleted semesters.  Never touches users or presets."""
    sems = db.query(Semester).filter(Semester.code.like(f"{_DEV_PREFIX}%")).all()
    count = len(sems)
    for sem in sems:
        db.delete(sem)
    db.commit()
    return count


# ── Main seed ─────────────────────────────────────────────────────────────────

def seed_laterality_dev_history(db: Session) -> dict:
    """
    Idempotent seed: run the real distribute_rewards_for_user() pipeline for
    each (user, preset) scenario.  Skips scenarios where TournamentParticipation
    already exists.

    Side effects (all committed):
      - Semester records (code DEV-LAT-*)
      - TournamentParticipation rows with foot_context
      - UserLicense.football_skills updated with lateral_components
      - user.onboarding_completed = True for all 4 control users

    Returns summary dict for CLI output.
    """
    # 1. Ensure presets + users exist
    fixtures = seed_laterality_fixtures(db)

    summary = {}

    for email, preset_code, skills, placement in _SCENARIOS:
        user_id = fixtures["users"].get(email)
        preset_id = fixtures["presets"].get(preset_code)

        if not user_id or not preset_id:
            print(f"  WARN: missing fixture for {email} / {preset_code} — skipping")
            continue

        sem_code = _sem_code(email, preset_code)
        sem = _ensure_tournament(db, sem_code, preset_id, skills)
        db.commit()  # commit so distribute_rewards_for_user sees the semester

        if _has_participation(db, user_id, sem.id):
            label = f"{email} × {preset_code}"
            summary.setdefault(email, {"skipped": 0, "created": 0})
            summary[email]["skipped"] += 1
            print(f"  SKIP  {label} (participation exists)")
            continue

        distribute_rewards_for_user(
            db=db,
            user_id=user_id,
            tournament_id=sem.id,
            placement=placement,
            total_participants=4,
        )

        summary.setdefault(email, {"skipped": 0, "created": 0})
        summary[email]["created"] += 1
        print(f"  OK    {email} × {preset_code}  (placement={placement})")

    # 2. Set onboarding_completed = True for all 4 control users
    onboarding_updated = 0
    for email in fixtures["users"]:
        user = db.query(User).filter(User.email == email).first()
        if user and not user.onboarding_completed:
            user.onboarding_completed = True
            onboarding_updated += 1

    db.commit()
    print(f"\n  Onboarding flag set for {onboarding_updated} user(s).")

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="DEV seed: laterality tournament history for UI validation."
    )
    p.add_argument("--clean", action="store_true",
                   help="Delete all DEV-LAT-* semesters before seeding.")
    p.add_argument("--no-reseed", action="store_true",
                   help="With --clean: only clean, do not re-seed afterward.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    db: Session = SessionLocal()

    try:
        if args.clean:
            print(f"🧹 Cleaning DEV-LAT-* tournaments…")
            n = clean_dev_history(db)
            print(f"   Deleted {n} semester(s) and their cascaded rows.")

            if args.no_reseed:
                print("   --no-reseed set, done.")
                sys.exit(0)

        print("\n🦵 Seeding laterality dev history (idempotent)…")
        print("=" * 70)
        summary = seed_laterality_dev_history(db)

        print("\nSummary:")
        total_created = total_skipped = 0
        for email, counts in summary.items():
            print(f"  {email}: {counts['created']} created, {counts['skipped']} skipped")
            total_created += counts["created"]
            total_skipped += counts["skipped"]

        print(f"\n  Total: {total_created} created, {total_skipped} skipped")
        print("\n✅ Done. Run the /skills page as any lat.* user to validate.")

    except Exception:
        db.rollback()
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

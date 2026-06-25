"""
DEV/DEMO SEED — Laterality tournament history for UI validation.

Creates a fully valid, user-facing LFA Player state for the 4 laterality control
users: persistent tournament history via the real distribute_rewards_for_user()
pipeline, and a complete onboarding state that mirrors what the production
onboarding flow produces.

After this seed:
  - Login succeeds without DOB redirect  (user.date_of_birth set)
  - /skills, /progress, /achievements accessible  (user.onboarding_completed = True)
  - LFA dashboard accessible  (UserLicense.onboarding_completed = True)
  - Tournament history shows foot badges  (TournamentParticipation.foot_context set)
  - lateral_components present on finishing + crossing  (EMA pipeline ran)

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
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.tournament_achievement import TournamentParticipation, TournamentSkillMapping
from app.models.user import User
from app.models.license import UserLicense
from app.models.specialization import SpecializationType
from app.services.tournament.tournament_reward_orchestrator import distribute_rewards_for_user
from app.services.onboarding_service import complete_lfa_player_onboarding
from scripts.seed_laterality_test_fixtures import seed_laterality_fixtures, _LAT_PRESETS, _CONTROL_USERS

# ── Sentinel prefix ────────────────────────────────────────────────────────────
# All semesters created by this script carry this prefix.  --clean removes ONLY
# these records and their cascaded children — never production data, never users.

_DEV_PREFIX = "DEV-LAT-"

# ── Per-user onboarding metadata ───────────────────────────────────────────────
# Mirrors what the real LFA player onboarding questionnaire collects.
# date_of_birth: valid adult (18+), unique per user.

_ONBOARDING_META = {
    "lat.right@lfa-test.com": {
        "date_of_birth": date(1992, 3, 15),
        "position":      "STRIKER",
    },
    "lat.left@lfa-test.com": {
        "date_of_birth": date(1994, 7, 22),
        "position":      "MIDFIELDER",
    },
    "lat.balanced@lfa-test.com": {
        "date_of_birth": date(1998, 11, 5),
        "position":      "MIDFIELDER",
    },
    "lat.unmeasured@lfa-test.com": {
        "date_of_birth": date(1990, 1, 20),
        "position":      "DEFENDER",
    },
}

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


def _apply_full_onboarding_state(db: Session, user: User, meta: dict) -> None:
    """
    Set a fully valid LFA Player onboarding state on user + license.

    Mirrors what the production onboarding flow (POST /specialization/lfa-player/
    onboarding-web) produces.  Uses complete_lfa_player_onboarding() as the
    canonical service call so the exact same fields are set.

    Idempotent: only writes fields that are currently NULL/False.
    Does NOT call db.commit() — caller owns the transaction.
    """
    now = datetime.now(timezone.utc)

    # 1. User-level prerequisite: date_of_birth (login gate)
    if user.date_of_birth is None:
        user.date_of_birth = meta["date_of_birth"]

    # 2. User-level: specialization (dashboard spec context)
    if user.specialization is None:
        user.specialization = SpecializationType.LFA_FOOTBALL_PLAYER

    # 3. Load license fresh so we have the post-pipeline football_skills
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if lic is None:
        return
    db.refresh(lic)

    # 4. motivation_scores (license questionnaire data)
    if lic.motivation_scores is None:
        skills_map = lic.football_skills or {}
        levels = [
            v.get("current_level", 60.0) if isinstance(v, dict) else float(v)
            for v in skills_map.values()
        ]
        avg_skill = round(sum(levels) / len(levels), 1) if levels else 60.0

        lic.motivation_scores = {
            "position":                meta["position"],
            "goals":                   "competitive",
            "motivation":              "[DEV] Control user — laterality validation seed",
            "average_skill_level":     avg_skill,
            "onboarding_completed_at": now.isoformat(),
        }
        lic.average_motivation_score      = avg_skill
        lic.motivation_last_assessed_at   = now
        lic.motivation_assessed_by        = user.id

    # 5. Call the canonical onboarding service — sets:
    #      license.onboarding_completed = True
    #      license.onboarding_completed_at = now
    #      user.onboarding_completed = True
    #    Passes the current football_skills so the pipeline output is preserved.
    complete_lfa_player_onboarding(db, user, lic, lic.football_skills)


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

def _load_or_create_fixtures(db: Session) -> dict:
    """
    Look up preset IDs + user IDs without resetting football_skills.

    If any fixture is missing, fall through to seed_laterality_fixtures() to
    create the missing records.  We avoid calling it unconditionally because
    seed_laterality_fixtures() resets football_skills to flat floats on every
    run (designed for test isolation — destructive in a dev context).
    """
    preset_ids = {d["code"]: None for d in _LAT_PRESETS}
    user_ids   = {u["email"]: None for u in _CONTROL_USERS}

    for code in list(preset_ids):
        p = db.query(GamePreset).filter(GamePreset.code == code).first()
        if p:
            preset_ids[code] = p.id

    for email in list(user_ids):
        u = db.query(User).filter(User.email == email).first()
        if u:
            user_ids[email] = u.id

    if None in preset_ids.values() or None in user_ids.values():
        # Some fixtures are missing — create them (first-time run)
        return seed_laterality_fixtures(db)

    return {"presets": preset_ids, "users": user_ids}


def seed_laterality_dev_history(db: Session) -> dict:
    """
    Idempotent seed: builds a fully valid LFA Player state for all 4 control users.

    Step 1 — Tournament history:
      Runs distribute_rewards_for_user() for each (user, preset) scenario.
      Skips scenarios where TournamentParticipation already exists.

    Step 2 — Full onboarding state:
      Sets all fields required for an unblocked user-facing experience:
        user.date_of_birth, user.specialization, user.onboarding_completed
        UserLicense.onboarding_completed, onboarding_completed_at, motivation_scores
      Uses complete_lfa_player_onboarding() — the same service as the real flow.

    Returns summary dict for CLI output.
    """
    # 1. Look up presets + users — do NOT reset football_skills if they exist
    fixtures = _load_or_create_fixtures(db)

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
            summary.setdefault(email, {"skipped": 0, "created": 0})
            summary[email]["skipped"] += 1
            print(f"  SKIP  {email} × {preset_code} (participation exists)")
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

    # 2. Apply full onboarding state for all 4 control users
    print()
    onboarding_updated = 0
    for email, meta in _ONBOARDING_META.items():
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            print(f"  WARN: user {email!r} not found — skipping onboarding state")
            continue

        _apply_full_onboarding_state(db, user, meta)
        onboarding_updated += 1
        print(f"  ONBOARD  {email}  dob={meta['date_of_birth']}  pos={meta['position']}")

    db.commit()
    print(f"\n  Full onboarding state applied for {onboarding_updated} user(s).")

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="DEV seed: laterality tournament history + full onboarding state."
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
            print("🧹 Cleaning DEV-LAT-* tournaments…")
            n = clean_dev_history(db)
            print(f"   Deleted {n} semester(s) and their cascaded rows.")

            if args.no_reseed:
                print("   --no-reseed set, done.")
                sys.exit(0)

        print("\n🦵 Seeding laterality dev history + full onboarding state (idempotent)…")
        print("=" * 70)
        summary = seed_laterality_dev_history(db)

        print("\nSummary:")
        total_created = total_skipped = 0
        for email, counts in summary.items():
            print(f"  {email}: {counts['created']} created, {counts['skipped']} skipped")
            total_created += counts["created"]
            total_skipped += counts["skipped"]

        print(f"\n  Total tournaments: {total_created} created, {total_skipped} skipped")
        print("\n✅ Done.")
        print("   Login as any lat.* user → no DOB redirect → /skills loads directly.")

    except Exception:
        db.rollback()
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

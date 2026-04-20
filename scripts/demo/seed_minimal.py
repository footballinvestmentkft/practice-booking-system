#!/usr/bin/env python3
"""
Seed Minimum Playable System — activates 4 report_* test users and
creates 3 ENROLLMENT_OPEN tournaments + 2 camps so the full game loop
can be exercised without running seed_events_demo.py first.

ADDITIVE and IDEMPOTENT: safe to run multiple times.
Does NOT truncate any table.

Actions:
  1. Fix report_* user licenses:
       - football_skills JSON (all 29 skills, flat format, per-player baselines)
       - onboarding_completed = True
       - credit_balance = 900 on the User row
  2. Ensure a usable Location + Campus exist (reuses existing if found).
  3. Create 3 ENROLLMENT_OPEN tournaments (league H2H · score_based · time_based)
     with required TournamentConfiguration + TournamentRewardConfig + GameConfiguration.
  4. Create 2 ENROLLMENT_OPEN camps.

Run: python scripts/seed_minimum_playable.py
"""
import sys
import os
import random
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.game_configuration import GameConfiguration
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.game_preset import GamePreset

# ── Constants ──────────────────────────────────────────────────────────────────

TARGET_EMAILS = [
    "report_490c3e64@t.com",
    "report_940c5c73@t.com",
    "report_7b85cdfa@t.com",
    "report_9ab12d42@t.com",
]
CREDIT_BALANCE = 900

# All 29 skills (flat format) per player — source of truth: app/skills_config.py
# Keys: outfield(11) + set_pieces(3) + mental(8) + physical(7)
# get_baseline_skills() in skill_progression_service.py reads both flat and rich formats.
_PLAYER_SKILLS = {
    # All-rounder with technical bias
    "report_490c3e64@t.com": {
        # Outfield
        "ball_control": 70.0, "dribbling": 73.0, "finishing": 75.0, "shot_power": 70.0,
        "long_shots": 62.0, "volleys": 58.0, "crossing": 62.0, "passing": 71.0,
        "heading": 68.0, "tackle": 55.0, "marking": 52.0,
        # Set pieces
        "free_kicks": 60.0, "corners": 63.0, "penalties": 65.0,
        # Mental
        "positioning_off": 63.0, "positioning_def": 60.0, "vision": 65.0,
        "aggression": 55.0, "reactions": 68.0, "composure": 65.0,
        "consistency": 62.0, "tactical_awareness": 64.0,
        # Physical
        "acceleration": 68.0, "sprint_speed": 70.0, "agility": 72.0,
        "jumping": 65.0, "strength": 60.0, "stamina": 67.0, "balance": 68.0,
    },
    # Attacking/shooting specialist
    "report_940c5c73@t.com": {
        # Outfield
        "ball_control": 74.0, "dribbling": 77.0, "finishing": 81.0, "shot_power": 78.0,
        "long_shots": 68.0, "volleys": 65.0, "crossing": 65.0, "passing": 69.0,
        "heading": 72.0, "tackle": 52.0, "marking": 48.0,
        # Set pieces
        "free_kicks": 70.0, "corners": 62.0, "penalties": 72.0,
        # Mental
        "positioning_off": 70.0, "positioning_def": 55.0, "vision": 68.0,
        "aggression": 65.0, "reactions": 72.0, "composure": 62.0,
        "consistency": 68.0, "tactical_awareness": 65.0,
        # Physical
        "acceleration": 74.0, "sprint_speed": 78.0, "agility": 76.0,
        "jumping": 68.0, "strength": 65.0, "stamina": 72.0, "balance": 73.0,
    },
    # Playmaker/passer
    "report_7b85cdfa@t.com": {
        # Outfield
        "ball_control": 68.0, "dribbling": 65.0, "finishing": 67.0, "shot_power": 60.0,
        "long_shots": 65.0, "volleys": 55.0, "crossing": 70.0, "passing": 80.0,
        "heading": 61.0, "tackle": 60.0, "marking": 58.0,
        # Set pieces
        "free_kicks": 72.0, "corners": 75.0, "penalties": 65.0,
        # Mental
        "positioning_off": 72.0, "positioning_def": 62.0, "vision": 78.0,
        "aggression": 48.0, "reactions": 68.0, "composure": 75.0,
        "consistency": 72.0, "tactical_awareness": 76.0,
        # Physical
        "acceleration": 62.0, "sprint_speed": 60.0, "agility": 65.0,
        "jumping": 58.0, "strength": 55.0, "stamina": 70.0, "balance": 68.0,
    },
    # Developing player (beginner)
    "report_9ab12d42@t.com": {
        # Outfield
        "ball_control": 60.0, "dribbling": 62.0, "finishing": 60.0, "shot_power": 58.0,
        "long_shots": 52.0, "volleys": 48.0, "crossing": 58.0, "passing": 63.0,
        "heading": 55.0, "tackle": 55.0, "marking": 52.0,
        # Set pieces
        "free_kicks": 52.0, "corners": 50.0, "penalties": 55.0,
        # Mental
        "positioning_off": 52.0, "positioning_def": 55.0, "vision": 55.0,
        "aggression": 60.0, "reactions": 58.0, "composure": 55.0,
        "consistency": 52.0, "tactical_awareness": 52.0,
        # Physical
        "acceleration": 60.0, "sprint_speed": 62.0, "agility": 58.0,
        "jumping": 55.0, "strength": 58.0, "stamina": 62.0, "balance": 58.0,
    },
}

# All 29 skill keys (used as fallback for unknown emails)
_SKILL_KEYS = list(_PLAYER_SKILLS["report_490c3e64@t.com"].keys())

# Reward policy used for all seed tournaments
_STANDARD_REWARD = {
    "template_name": "Standard Football",
    "custom_config": False,
    "skill_mappings": [
        {"skill": "Dribbling", "weight": 1.5, "category": "TECHNICAL", "enabled": True},
        {"skill": "Shooting",  "weight": 1.3, "category": "TECHNICAL", "enabled": True},
        {"skill": "Passing",   "weight": 1.0, "category": "TECHNICAL", "enabled": True},
    ],
    "first_place":   {"credits": 500, "xp_multiplier": 2.0, "badges": []},
    "second_place":  {"credits": 250, "xp_multiplier": 1.5, "badges": []},
    "third_place":   {"credits": 100, "xp_multiplier": 1.2, "badges": []},
    "participation": {"credits":  50, "xp_multiplier": 1.0, "badges": []},
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_or_create_location(db: Session) -> Location:
    """Reuse the first active CENTER location, or create a minimal one."""
    loc = db.query(Location).filter(Location.location_type == LocationType.CENTER, Location.is_active == True).first()
    if loc:
        return loc
    loc = Location(
        name="LFA Budapest Education Center",
        city="Budapest",
        country="Hungary",
        country_code="HU",
        location_code="BDPST-MIN",
        postal_code="1146",
        address="Istvánmezei út 1-3, Budapest",
        location_type=LocationType.CENTER,
        is_active=True,
    )
    db.add(loc)
    db.flush()
    print(f"   ✓ Created location: {loc.name} (id={loc.id})")
    return loc


def _get_or_create_campus(db: Session, location: Location) -> Campus:
    """Reuse first campus at this location, or create one."""
    campus = db.query(Campus).filter(Campus.location_id == location.id, Campus.is_active == True).first()
    if campus:
        return campus
    campus = Campus(
        location_id=location.id,
        name="Main Training Campus",
        venue="Outdoor fields + gym",
        address="Istvánmezei út 1-3, Budapest",
        is_active=True,
    )
    db.add(campus)
    db.flush()
    print(f"   ✓ Created campus: {campus.name} (id={campus.id})")
    return campus


def _get_preset(db: Session, code: str) -> GamePreset | None:
    return db.query(GamePreset).filter(GamePreset.code == code, GamePreset.is_active == True).first()


def _get_tt(db: Session, code: str) -> TournamentType | None:
    return db.query(TournamentType).filter(TournamentType.code == code).first()


def _ensure_tournament(
    db: Session,
    *,
    code: str,
    name: str,
    tt_code: str,
    scoring_type: str | None,
    measurement_unit: str | None,
    ranking_direction: str,
    preset_code: str,
    location: Location,
    campus: Campus,
    today: date,
    age_group: str = "YOUTH",
    max_players: int = 32,
) -> Semester | None:
    """Create a READY_FOR_ENROLLMENT tournament if it doesn't exist yet (idempotent by code)."""
    existing = db.query(Semester).filter(Semester.code == code).first()
    if existing:
        print(f"   ⏭️  Already exists: {code}")
        return existing

    t = Semester(
        code=code,
        name=name,
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        tournament_status="ENROLLMENT_OPEN",
        age_group=age_group,
        location_id=location.id,
        campus_id=campus.id,
        start_date=today + timedelta(days=14),
        end_date=today + timedelta(days=28),
        enrollment_cost=0,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    db.add(t)
    db.flush()

    tt = _get_tt(db, tt_code)
    db.add(TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt.id if tt else None,
        scoring_type=scoring_type,
        measurement_unit=measurement_unit,
        ranking_direction=ranking_direction,
        participant_type="INDIVIDUAL",
        is_multi_day=False,
        max_players=max_players,
        parallel_fields=1,
        sessions_generated=False,
    ))

    preset = _get_preset(db, preset_code)
    game_cfg = {
        "metadata": {"min_players": 4, "game_type": "football"},
        "match_rules": {"scoring": "goals", "overtime": False},
    }
    db.add(GameConfiguration(
        semester_id=t.id,
        game_preset_id=preset.id if preset else None,
        game_config=game_cfg,
    ))

    db.add(TournamentRewardConfig(
        semester_id=t.id,
        reward_policy_name="Standard Football",
        reward_config=_STANDARD_REWARD,
    ))

    print(f"   ✅ Created tournament: {name!r} ({code})")
    return t


def _ensure_camp(
    db: Session,
    *,
    code: str,
    name: str,
    location: Location,
    campus: Campus,
    today: date,
    age_group: str = "YOUTH",
    enrollment_cost: int = 500,
) -> Semester | None:
    existing = db.query(Semester).filter(Semester.code == code).first()
    if existing:
        print(f"   ⏭️  Already exists: {code}")
        return existing

    c = Semester(
        code=code,
        name=name,
        semester_category=SemesterCategory.CAMP,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        age_group=age_group,
        location_id=location.id,
        campus_id=campus.id,
        start_date=today + timedelta(days=45),
        end_date=today + timedelta(days=52),
        enrollment_cost=enrollment_cost,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    db.add(c)
    db.flush()
    print(f"   ✅ Created camp: {name!r} ({code})")
    return c


# ── Main ───────────────────────────────────────────────────────────────────────

def seed():
    db = SessionLocal()
    try:
        today = date.today()
        now = datetime.now(timezone.utc)

        # ── 1. Fix report_* user licenses ──────────────────────────────────────
        print("👤 Fixing report_* test user licenses…")
        for email in TARGET_EMAILS:
            user = db.query(User).filter(User.email == email).first()
            if not user:
                print(f"   SKIP {email} — not found in DB (run seed_report_users_login.py first?)")
                continue

            # Set credit_balance
            if user.credit_balance != CREDIT_BALANCE:
                user.credit_balance = CREDIT_BALANCE

            license = db.query(UserLicense).filter(
                UserLicense.user_id == user.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                UserLicense.is_active == True,
            ).first()

            if not license:
                print(f"   SKIP {email} — no active LFA_FOOTBALL_PLAYER license found")
                continue

            # Ensure started_at is set (required NOT NULL)
            if license.started_at is None:
                license.started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

            # Set football_skills baseline if not already set
            if not license.football_skills:
                license.football_skills = _PLAYER_SKILLS.get(email, {
                    k: 65.0 for k in _SKILL_KEYS
                })
                print(f"   ✅ Set football_skills for {email}")
            else:
                print(f"   ⏭️  football_skills already set for {email}")

            # Ensure onboarding completed
            if not license.onboarding_completed:
                license.onboarding_completed = True
                print(f"   ✅ Set onboarding_completed for {email}")

            db.flush()

        db.commit()
        print()

        # ── 2. Ensure a usable location + campus ───────────────────────────────
        print("📍 Ensuring location + campus…")
        location = _get_or_create_location(db)
        campus = _get_or_create_campus(db, location)
        db.commit()
        print(f"   Location: {location.name} (id={location.id})")
        print(f"   Campus:   {campus.name} (id={campus.id})")
        print()

        # ── 3. Create 3 ENROLLMENT_OPEN tournaments ────────────────────────────
        print("🏆 Ensuring 3 ENROLLMENT_OPEN tournaments…")

        _ensure_tournament(
            db,
            code="PLAYABLE-TOURN-H2H-LEAGUE-2026",
            name="YOUTH League Tournament 2026 — Enrollment Open",
            tt_code="league",
            scoring_type=None,
            measurement_unit=None,
            ranking_direction="DESC",
            preset_code="outfield_default",
            location=location,
            campus=campus,
            today=today,
            age_group="YOUTH",
            max_players=32,
        )

        _ensure_tournament(
            db,
            code="PLAYABLE-TOURN-SCORE-2026",
            name="YOUTH Score Challenge 2026 — Enrollment Open",
            tt_code="score_based",
            scoring_type="SCORE_BASED",
            measurement_unit="goals",
            ranking_direction="DESC",
            preset_code="shooting_focus",
            location=location,
            campus=campus,
            today=today,
            age_group="YOUTH",
            max_players=32,
        )

        _ensure_tournament(
            db,
            code="PLAYABLE-TOURN-TIME-2026",
            name="YOUTH Time Trial Series 2026 — Enrollment Open",
            tt_code="time_based",
            scoring_type="TIME_BASED",
            measurement_unit="seconds",
            ranking_direction="ASC",
            preset_code="passing_focus",
            location=location,
            campus=campus,
            today=today,
            age_group="YOUTH",
            max_players=24,
        )

        db.commit()
        print()

        # ── 4. Create 2 ENROLLMENT_OPEN camps ─────────────────────────────────
        print("⛺ Ensuring 2 ENROLLMENT_OPEN camps…")

        _ensure_camp(
            db,
            code="PLAYABLE-CAMP-SUMMER-2026-YOUTH",
            name="Summer Football Camp 2026 — YOUTH",
            location=location,
            campus=campus,
            today=today,
            age_group="YOUTH",
            enrollment_cost=500,
        )

        _ensure_camp(
            db,
            code="PLAYABLE-CAMP-AUTUMN-2026-YOUTH",
            name="Autumn Development Camp 2026 — YOUTH",
            location=location,
            campus=campus,
            today=today,
            age_group="YOUTH",
            enrollment_cost=350,
        )

        db.commit()
        print()

        # ── Summary ────────────────────────────────────────────────────────────
        open_tourneys = db.query(Semester).filter(
            Semester.semester_category == SemesterCategory.TOURNAMENT,
            Semester.tournament_status == "ENROLLMENT_OPEN",
        ).count()
        open_camps = db.query(Semester).filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.status == SemesterStatus.READY_FOR_ENROLLMENT,
        ).count()

        print("=" * 70)
        print("✅ MINIMUM PLAYABLE SYSTEM READY")
        print(f"   ENROLLMENT_OPEN tournaments : {open_tourneys}")
        print(f"   ENROLLMENT_OPEN camps       : {open_camps}")
        print()
        print("   Test users (password: Player1234!):")
        for email in TARGET_EMAILS:
            u = db.query(User).filter(User.email == email).first()
            if u:
                lic = db.query(UserLicense).filter(
                    UserLicense.user_id == u.id,
                    UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                    UserLicense.is_active == True,
                ).first()
                fs = "✅" if (lic and lic.football_skills) else "❌"
                oc = "✅" if (lic and lic.onboarding_completed) else "❌"
                cr = u.credit_balance or 0
                print(f"   [{email}]  football_skills={fs}  onboarding={oc}  credits={cr}")
        print()
        print("   Login: report_490c3e64@t.com / Player1234!")
        print("   Journey: /dashboard → ENTER → /dashboard/lfa-football-player")
        print("            → Available Events (Tournaments + Camps tabs)")
        print("            → [Enroll] → /api/v1/tournaments/{id}/enroll")
        print("=" * 70)

    except Exception as e:
        db.rollback()
        print(f"\n❌ SEED FAILED: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()

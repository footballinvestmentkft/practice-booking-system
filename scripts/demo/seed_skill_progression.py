#!/usr/bin/env python3
"""
Full Playable System Seed — creates everything from scratch.

Use this script after a DB reset to restore the full playable state:
  1. Game presets  (outfield_default, shooting_focus, passing_focus)
  2. Tournament types  (league + 4 IR variants via JSON)
  3. 4 report_* test users + LFA_FOOTBALL_PLAYER licenses
  4. 3 COMPLETED tournaments with EMA skill history
  5. 3 ENROLLMENT_OPEN tournaments + 2 camps (current enrollment)

Placement matrix (creates realistic EMA progression per player):
  T1 league (Jan 2026):      940→1st  7b85→2nd  490c→3rd  9ab1→NULL
  T2 score_based (Feb 2026): 7b85→1st  490c→2nd  9ab1→3rd  940→NULL
  T3 time_based (Mar 2026):  490c→1st  9ab1→2nd  940→3rd  7b85→NULL

Result after run:
  - Login: report_490c3e64@t.com / Player1234!
  - /skills/history shows 3-tournament EMA chart
  - 3 tournaments + 2 camps open for enrollment

IDEMPOTENT: checks before creating, safe to re-run.

Run: python scripts/seed_full_playable.py
"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.specialization import SpecializationType
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.game_configuration import GameConfiguration
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.game_preset import GamePreset
from app.core.security import get_password_hash
from app.services.tournament.tournament_participation_service import (
    calculate_skill_points_for_placement,
    record_tournament_participation,
)

# ── Constants ──────────────────────────────────────────────────────────────────

PASSWORD = "Player1234!"

# email → display name
PLAYERS = {
    "report_940c5c73@t.com": "P1 Report (940c)",   # Attacking specialist
    "report_7b85cdfa@t.com": "P2 Report (7b85)",   # Playmaker/passer
    "report_490c3e64@t.com": "P3 Report (490c)",   # All-rounder (best arc)
    "report_9ab12d42@t.com": "P4 Report (9ab1)",   # Developing player
}

# Football skills baselines — flat format (29 keys from skills_config.py)
_PLAYER_SKILLS = {
    # Attacking/shooting specialist
    "report_940c5c73@t.com": {
        "ball_control": 74.0, "dribbling": 77.0, "finishing": 81.0, "shot_power": 78.0,
        "long_shots": 68.0, "volleys": 65.0, "crossing": 65.0, "passing": 69.0,
        "heading": 72.0, "tackle": 52.0, "marking": 48.0,
        "free_kicks": 70.0, "corners": 62.0, "penalties": 72.0,
        "positioning_off": 70.0, "positioning_def": 55.0, "vision": 68.0,
        "aggression": 65.0, "reactions": 72.0, "composure": 62.0,
        "consistency": 68.0, "tactical_awareness": 65.0,
        "acceleration": 74.0, "sprint_speed": 78.0, "agility": 76.0,
        "jumping": 68.0, "strength": 65.0, "stamina": 72.0, "balance": 73.0,
    },
    # Playmaker/passer
    "report_7b85cdfa@t.com": {
        "ball_control": 68.0, "dribbling": 65.0, "finishing": 67.0, "shot_power": 60.0,
        "long_shots": 65.0, "volleys": 55.0, "crossing": 70.0, "passing": 80.0,
        "heading": 61.0, "tackle": 60.0, "marking": 58.0,
        "free_kicks": 72.0, "corners": 75.0, "penalties": 65.0,
        "positioning_off": 72.0, "positioning_def": 62.0, "vision": 78.0,
        "aggression": 48.0, "reactions": 68.0, "composure": 75.0,
        "consistency": 72.0, "tactical_awareness": 76.0,
        "acceleration": 62.0, "sprint_speed": 60.0, "agility": 65.0,
        "jumping": 58.0, "strength": 55.0, "stamina": 70.0, "balance": 68.0,
    },
    # All-rounder — best development arc (3rd → 2nd → 1st)
    "report_490c3e64@t.com": {
        "ball_control": 70.0, "dribbling": 73.0, "finishing": 75.0, "shot_power": 70.0,
        "long_shots": 62.0, "volleys": 58.0, "crossing": 62.0, "passing": 71.0,
        "heading": 68.0, "tackle": 55.0, "marking": 52.0,
        "free_kicks": 60.0, "corners": 63.0, "penalties": 65.0,
        "positioning_off": 63.0, "positioning_def": 60.0, "vision": 65.0,
        "aggression": 55.0, "reactions": 68.0, "composure": 65.0,
        "consistency": 62.0, "tactical_awareness": 64.0,
        "acceleration": 68.0, "sprint_speed": 70.0, "agility": 72.0,
        "jumping": 65.0, "strength": 60.0, "stamina": 67.0, "balance": 68.0,
    },
    # Developing player (beginner)
    "report_9ab12d42@t.com": {
        "ball_control": 60.0, "dribbling": 62.0, "finishing": 60.0, "shot_power": 58.0,
        "long_shots": 52.0, "volleys": 48.0, "crossing": 58.0, "passing": 63.0,
        "heading": 55.0, "tackle": 55.0, "marking": 52.0,
        "free_kicks": 52.0, "corners": 50.0, "penalties": 55.0,
        "positioning_off": 52.0, "positioning_def": 55.0, "vision": 55.0,
        "aggression": 60.0, "reactions": 58.0, "composure": 55.0,
        "consistency": 52.0, "tactical_awareness": 52.0,
        "acceleration": 60.0, "sprint_speed": 62.0, "agility": 58.0,
        "jumping": 55.0, "strength": 58.0, "stamina": 62.0, "balance": 58.0,
    },
}

# Placement matrix: tournament_index → { email: placement (None = participant) }
# Chronological order is critical for correct EMA delta computation.
PLACEMENTS = [
    # T1: league — Jan 2026
    {
        "report_940c5c73@t.com": 1,
        "report_7b85cdfa@t.com": 2,
        "report_490c3e64@t.com": 3,
        "report_9ab12d42@t.com": None,
    },
    # T2: score_based — Feb 2026
    {
        "report_7b85cdfa@t.com": 1,
        "report_490c3e64@t.com": 2,
        "report_9ab12d42@t.com": 3,
        "report_940c5c73@t.com": None,
    },
    # T3: time_based — Mar 2026
    {
        "report_490c3e64@t.com": 1,
        "report_9ab12d42@t.com": 2,
        "report_940c5c73@t.com": 3,
        "report_7b85cdfa@t.com": None,
    },
]

# Reward policy for historical tournaments.
# IMPORTANT: "skill" values must match get_all_skill_keys() (lowercase snake_case)
# so that _extract_tournament_skills() can match them against the canonical skill set.
_STANDARD_REWARD = {
    "template_name": "Standard Football",
    "custom_config": False,
    "skill_mappings": [
        {"skill": "dribbling", "weight": 1.5, "category": "TECHNICAL", "enabled": True},
        {"skill": "finishing",  "weight": 1.3, "category": "TECHNICAL", "enabled": True},
        {"skill": "passing",   "weight": 1.0, "category": "TECHNICAL", "enabled": True},
    ],
    "first_place":   {"credits": 500, "xp_multiplier": 2.0, "badges": []},
    "second_place":  {"credits": 250, "xp_multiplier": 1.5, "badges": []},
    "third_place":   {"credits": 100, "xp_multiplier": 1.2, "badges": []},
    "participation": {"credits":  50, "xp_multiplier": 1.0, "badges": []},
}

_HIST_TOURN_CONFIGS = [
    {
        "code": "HIST-TOURN-LEAGUE-2026-01",
        "name": "YOUTH League Cup — January 2026",
        "tt_code": "league",
        "scoring_type": None,
        "ranking_direction": "DESC",
        "preset_code": "outfield_default",
        "achieved_at": datetime(2026, 1, 25, 15, 0, 0, tzinfo=timezone.utc),
    },
    {
        "code": "HIST-TOURN-SCORE-2026-02",
        "name": "YOUTH Score Challenge — February 2026",
        "tt_code": "score_based",
        "scoring_type": "SCORE_BASED",
        "ranking_direction": "DESC",
        "preset_code": "shooting_focus",
        "achieved_at": datetime(2026, 2, 20, 14, 0, 0, tzinfo=timezone.utc),
    },
    {
        "code": "HIST-TOURN-TIME-2026-03",
        "name": "YOUTH Time Trial — March 2026",
        "tt_code": "time_based",
        "scoring_type": "TIME_BASED",
        "ranking_direction": "ASC",
        "preset_code": "passing_focus",
        "achieved_at": datetime(2026, 3, 15, 13, 0, 0, tzinfo=timezone.utc),
    },
]


# ── Phase 1: Game presets ──────────────────────────────────────────────────────

GAME_PRESETS = [
    {
        "code": "outfield_default",
        "name": "Outfield Football (Default)",
        "is_recommended": True,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {"game_category": "FOOTBALL", "difficulty_level": "intermediate", "min_players": 4},
            "skill_config": {
                "skills_tested": ["ball_control", "dribbling", "finishing", "passing", "vision",
                                  "positioning_off", "sprint_speed", "agility", "stamina"],
                "skill_weights": {
                    "ball_control": 1.2, "dribbling": 1.5, "finishing": 1.4, "passing": 1.3,
                    "vision": 1.1, "positioning_off": 1.1, "sprint_speed": 1.0,
                    "agility": 1.0, "stamina": 0.9,
                },
            },
            "format_config": {}, "simulation_config": {},
        },
    },
    {
        "code": "passing_focus",
        "name": "Passing & Vision Focus",
        "is_recommended": False,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {"game_category": "FOOTBALL", "difficulty_level": "intermediate", "min_players": 4},
            "skill_config": {
                "skills_tested": ["passing", "vision", "ball_control", "positioning_off", "agility", "stamina"],
                "skill_weights": {"passing": 1.8, "vision": 1.6, "ball_control": 1.4,
                                  "positioning_off": 1.3, "agility": 1.0, "stamina": 0.8},
            },
            "format_config": {}, "simulation_config": {},
        },
    },
    {
        "code": "shooting_focus",
        "name": "Finishing & Shooting Focus",
        "is_recommended": False,
        "is_locked": False,
        "game_config": {
            "version": "1.0",
            "metadata": {"game_category": "FOOTBALL", "difficulty_level": "intermediate", "min_players": 4},
            "skill_config": {
                "skills_tested": ["finishing", "sprint_speed", "dribbling", "ball_control",
                                  "agility", "positioning_off"],
                "skill_weights": {"finishing": 2.0, "sprint_speed": 1.6, "dribbling": 1.5,
                                  "ball_control": 1.2, "agility": 1.1, "positioning_off": 0.9},
            },
            "format_config": {}, "simulation_config": {},
        },
    },
]


def _seed_game_presets(db: Session) -> None:
    print("\n🎮 Phase 1: Game presets…")
    for defn in GAME_PRESETS:
        if db.query(GamePreset).filter(GamePreset.code == defn["code"]).first():
            print(f"   ⏭️  {defn['code']} already exists")
            continue
        db.add(GamePreset(
            code=defn["code"],
            name=defn["name"],
            game_config=defn["game_config"],
            is_active=True,
            is_recommended=defn["is_recommended"],
            is_locked=defn["is_locked"],
        ))
        print(f"   ✅ Created preset: {defn['code']}")
    db.flush()


# ── Phase 2: Tournament types ──────────────────────────────────────────────────

def _seed_tournament_types(db: Session) -> None:
    print("\n🏆 Phase 2: Tournament types…")
    configs_dir = Path(__file__).resolve().parent.parent / "app" / "tournament_types"
    json_files = sorted(configs_dir.glob("*.json"))
    for json_file in json_files:
        config = json.loads(json_file.read_text())
        code = config["code"]
        if db.query(TournamentType).filter(TournamentType.code == code).first():
            print(f"   ⏭️  {code} already exists")
            continue
        db.add(TournamentType(
            code=code,
            display_name=config["display_name"],
            description=config.get("description", ""),
            format=config.get("format", "INDIVIDUAL_RANKING"),
            min_players=config["min_players"],
            max_players=config.get("max_players"),
            requires_power_of_two=config["requires_power_of_two"],
            session_duration_minutes=config["session_duration_minutes"],
            break_between_sessions_minutes=config["break_between_sessions_minutes"],
            config=config,
        ))
        print(f"   ✅ Created type: {code}")
    db.flush()


# ── Phase 3: Users + licenses ──────────────────────────────────────────────────

def _seed_users(db: Session) -> dict[str, User]:
    print("\n👤 Phase 3: Test users + licenses…")
    password_hash = get_password_hash(PASSWORD)
    now = datetime.now(timezone.utc)
    users = {}

    for email, name in PLAYERS.items():
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                name=name,
                password_hash=password_hash,
                role=UserRole.STUDENT,
                is_active=True,
                onboarding_completed=True,
                specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
                credit_balance=900,
                payment_verified=True,
                payment_verified_at=now,
            )
            db.add(user)
            db.flush()
            print(f"   ✅ Created user: {email} (id={user.id})")
        else:
            # Ensure password is correct and attributes are set
            user.password_hash = password_hash
            user.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
            user.onboarding_completed = True
            user.credit_balance = 900
            db.flush()
            print(f"   ⏭️  User exists: {email} (id={user.id}) — updated password+attrs")

        # Ensure license
        license = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        ).first()
        if not license:
            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                is_active=True,
                onboarding_completed=True,
                onboarding_completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payment_verified=True,
                payment_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                football_skills=_PLAYER_SKILLS.get(email, {}),
            )
            db.add(license)
            db.flush()
            print(f"      ✅ Created license for {email} (id={license.id}, 29 skills set)")
        else:
            # Refresh the license attributes
            license.is_active = True
            license.onboarding_completed = True
            if license.started_at is None:
                license.started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            license.football_skills = _PLAYER_SKILLS.get(email, {})
            db.flush()
            print(f"      ⏭️  License exists for {email} — refreshed skills")

        users[email] = user

    return users


# ── Phase 4: Historical tournaments + EMA history ──────────────────────────────

def _get_preset(db: Session, code: str) -> GamePreset | None:
    return db.query(GamePreset).filter(GamePreset.code == code, GamePreset.is_active == True).first()


def _get_tt(db: Session, code: str) -> TournamentType | None:
    return db.query(TournamentType).filter(TournamentType.code == code).first()


def _ensure_historical_tournament(
    db: Session,
    *,
    code: str,
    name: str,
    tt_code: str,
    scoring_type: str | None,
    ranking_direction: str,
    preset_code: str,
    start_dt: datetime,
) -> Semester:
    """Create a COMPLETED tournament for historical EMA data."""
    existing = db.query(Semester).filter(Semester.code == code).first()
    if existing:
        print(f"   ⏭️  Tournament exists: {code}")
        return existing

    start_date = start_dt.date() - timedelta(days=7)
    end_date = start_dt.date()

    t = Semester(
        code=code,
        name=name,
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.COMPLETED,
        tournament_status="FINALIZED",
        age_group="YOUTH",
        location_id=None,   # will be set after location is created
        campus_id=None,
        start_date=start_date,
        end_date=end_date,
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
        ranking_direction=ranking_direction,
        participant_type="INDIVIDUAL",
        is_multi_day=False,
        max_players=32,
        parallel_fields=1,
        sessions_generated=False,
    ))

    preset = _get_preset(db, preset_code)
    db.add(GameConfiguration(
        semester_id=t.id,
        game_preset_id=preset.id if preset else None,
        game_config={"metadata": {"min_players": 4, "game_type": "football"}, "match_rules": {}},
    ))

    db.add(TournamentRewardConfig(
        semester_id=t.id,
        reward_policy_name="Standard Football",
        reward_config=_STANDARD_REWARD,
    ))

    db.flush()
    print(f"   ✅ Created historical tournament: {name!r} (id={t.id})")
    return t


def _seed_history(db: Session, users: dict[str, User]) -> None:
    print("\n📈 Phase 4: Historical tournaments + EMA skill history…")
    print("   (calling record_tournament_participation in chronological order)")

    for idx, hist_cfg in enumerate(_HIST_TOURN_CONFIGS):
        tourn = _ensure_historical_tournament(
            db,
            code=hist_cfg["code"],
            name=hist_cfg["name"],
            tt_code=hist_cfg["tt_code"],
            scoring_type=hist_cfg.get("scoring_type"),
            ranking_direction=hist_cfg["ranking_direction"],
            preset_code=hist_cfg["preset_code"],
            start_dt=hist_cfg["achieved_at"],
        )
        db.commit()  # Commit so next call can query prior participations for EMA

        placement_map = PLACEMENTS[idx]
        print(f"\n   [{hist_cfg['code']}]")

        for email, placement in placement_map.items():
            user = users.get(email)
            if not user:
                print(f"      SKIP {email} — not in users dict")
                continue

            skill_pts = calculate_skill_points_for_placement(db, tourn.id, placement)
            plabel = f"{placement}st/nd/rd" if placement else "participant"

            # record_tournament_participation upserts + computes EMA delta
            record_tournament_participation(
                db=db,
                user_id=user.id,
                tournament_id=tourn.id,
                placement=placement,
                skill_points=skill_pts,
                base_xp=0,     # not awarding XP retroactively
                credits=0,     # not awarding credits retroactively
            )

            total_pts = round(sum(skill_pts.values()), 1)
            print(f"      {email}  placement={plabel}  skill_pts={total_pts}")

        db.commit()

    print("\n   ✅ EMA history written for all 3 tournaments.")


# ── Phase 5: Enrollment-open tournaments + camps ───────────────────────────────

def _get_or_create_location(db: Session) -> Location:
    loc = (
        db.query(Location)
        .filter(Location.location_type == LocationType.CENTER, Location.is_active == True)
        .first()
    )
    if loc:
        return loc
    loc = Location(
        name="LFA Budapest Education Center",
        city="Budapest",
        country="Hungary",
        country_code="HU",
        location_code="BDPST-MIN",
        postal_code="1146",
        address="Istvánmezei út 1-3",
        location_type=LocationType.CENTER,
        is_active=True,
    )
    db.add(loc)
    db.flush()
    print(f"   ✅ Created location: {loc.name}")
    return loc


def _get_or_create_campus(db: Session, location: Location) -> Campus:
    campus = (
        db.query(Campus)
        .filter(Campus.location_id == location.id, Campus.is_active == True)
        .first()
    )
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
    print(f"   ✅ Created campus: {campus.name}")
    return campus


def _ensure_open_tournament(
    db: Session,
    *,
    code: str,
    name: str,
    tt_code: str,
    scoring_type: str | None,
    ranking_direction: str,
    preset_code: str,
    location: Location,
    campus: Campus,
    today: date,
) -> None:
    if db.query(Semester).filter(Semester.code == code).first():
        print(f"   ⏭️  {code} already exists")
        return

    t = Semester(
        code=code,
        name=name,
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        tournament_status="ENROLLMENT_OPEN",
        age_group="YOUTH",
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
        ranking_direction=ranking_direction,
        participant_type="INDIVIDUAL",
        is_multi_day=False,
        max_players=32,
        parallel_fields=1,
        sessions_generated=False,
    ))
    preset = _get_preset(db, preset_code)
    db.add(GameConfiguration(
        semester_id=t.id,
        game_preset_id=preset.id if preset else None,
        game_config={"metadata": {"min_players": 4, "game_type": "football"}, "match_rules": {}},
    ))
    db.add(TournamentRewardConfig(
        semester_id=t.id,
        reward_policy_name="Standard Football",
        reward_config=_STANDARD_REWARD,
    ))
    db.flush()
    print(f"   ✅ Created open tournament: {name!r}")


def _ensure_camp(db: Session, *, code: str, name: str, location: Location,
                 campus: Campus, today: date, cost: int = 500) -> None:
    if db.query(Semester).filter(Semester.code == code).first():
        print(f"   ⏭️  {code} already exists")
        return
    c = Semester(
        code=code,
        name=name,
        semester_category=SemesterCategory.CAMP,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        age_group="YOUTH",
        location_id=location.id,
        campus_id=campus.id,
        start_date=today + timedelta(days=45),
        end_date=today + timedelta(days=52),
        enrollment_cost=cost,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    db.add(c)
    db.flush()
    print(f"   ✅ Created camp: {name!r}")


def _seed_open_events(db: Session) -> None:
    print("\n🎯 Phase 5: ENROLLMENT_OPEN tournaments + camps…")
    today = date.today()
    location = _get_or_create_location(db)
    campus = _get_or_create_campus(db, location)

    _ensure_open_tournament(
        db, code="PLAYABLE-TOURN-H2H-LEAGUE-2026", name="YOUTH League Tournament 2026 — Enrollment Open",
        tt_code="league", scoring_type=None, ranking_direction="DESC",
        preset_code="outfield_default", location=location, campus=campus, today=today,
    )
    _ensure_open_tournament(
        db, code="PLAYABLE-TOURN-SCORE-2026", name="YOUTH Score Challenge 2026 — Enrollment Open",
        tt_code="score_based", scoring_type="SCORE_BASED", ranking_direction="DESC",
        preset_code="shooting_focus", location=location, campus=campus, today=today,
    )
    _ensure_open_tournament(
        db, code="PLAYABLE-TOURN-TIME-2026", name="YOUTH Time Trial Series 2026 — Enrollment Open",
        tt_code="time_based", scoring_type="TIME_BASED", ranking_direction="ASC",
        preset_code="passing_focus", location=location, campus=campus, today=today,
    )
    _ensure_camp(
        db, code="PLAYABLE-CAMP-SUMMER-2026-YOUTH", name="Summer Football Camp 2026 — YOUTH",
        location=location, campus=campus, today=today, cost=500,
    )
    _ensure_camp(
        db, code="PLAYABLE-CAMP-AUTUMN-2026-YOUTH", name="Autumn Development Camp 2026 — YOUTH",
        location=location, campus=campus, today=today, cost=350,
    )
    db.commit()


# ── Summary ────────────────────────────────────────────────────────────────────

def _print_summary(db: Session) -> None:
    from app.models.tournament_achievement import TournamentParticipation

    print("\n" + "=" * 70)
    print("✅ FULL PLAYABLE SYSTEM READY")

    open_t = db.query(Semester).filter(
        Semester.semester_category == SemesterCategory.TOURNAMENT,
        Semester.tournament_status == "ENROLLMENT_OPEN",
    ).count()
    open_c = db.query(Semester).filter(
        Semester.semester_category == SemesterCategory.CAMP,
        Semester.status == SemesterStatus.READY_FOR_ENROLLMENT,
    ).count()
    total_tp = db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id.in_(
            [db.query(User.id).filter(User.email == e).scalar() for e in PLAYERS]
        )
    ).count()

    print(f"   Game presets in DB        : {db.query(GamePreset).count()}")
    print(f"   Tournament types in DB    : {db.query(TournamentType).count()}")
    print(f"   ENROLLMENT_OPEN tournaments: {open_t}")
    print(f"   ENROLLMENT_OPEN camps     : {open_c}")
    print(f"   TournamentParticipation rows: {total_tp}")
    print()
    print("   Test users (password: Player1234!):")
    for email in PLAYERS:
        u = db.query(User).filter(User.email == email).first()
        if u:
            lic = db.query(UserLicense).filter(
                UserLicense.user_id == u.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            ).first()
            fs = "✅" if (lic and lic.football_skills) else "❌"
            tp_count = db.query(TournamentParticipation).filter(
                TournamentParticipation.user_id == u.id
            ).count()
            ema = db.query(TournamentParticipation).filter(
                TournamentParticipation.user_id == u.id,
                TournamentParticipation.skill_rating_delta.isnot(None),
            ).count()
            print(f"   [{email}]  skills={fs}  participations={tp_count}  EMA_deltas={ema}")
    print()
    print("   Journey:")
    print("     /login  →  report_490c3e64@t.com / Player1234!")
    print("     /dashboard  →  ENTER  →  /dashboard/lfa-football-player")
    print("     /skills/history  →  3-tournament EMA chart")
    print("     Available Events tab  →  3 tournaments + 2 camps")
    print("=" * 70)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    print("🚀 seed_full_playable.py — Full Playable System Seed")
    print("=" * 70)

    db = SessionLocal()
    try:
        _seed_game_presets(db)
        db.commit()

        _seed_tournament_types(db)
        db.commit()

        users = _seed_users(db)
        db.commit()

        _seed_history(db, users)
        # _seed_history already commits after each tournament

        _seed_open_events(db)
        # _seed_open_events commits at the end

        _print_summary(db)

    except Exception as e:
        db.rollback()
        print(f"\n❌ SEED FAILED: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

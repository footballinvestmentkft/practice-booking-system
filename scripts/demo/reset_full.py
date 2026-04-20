#!/usr/bin/env python3
"""
Full Database Reset & Seed — Frontend Validation
=================================================

RESET: drops all tables, recreates schema via Base.metadata.create_all.

SEED (LFA_FOOTBALL_PLAYER, YOUTH):
  Locations:
    Budapest  (CENTER)  — 7 events
    Debrecen  (PARTNER) — 6 events

  Event types per location:
    TOURNAMENT league    — COMPLETED  (placement + skill delta)
    TOURNAMENT score     — ENROLLMENT_OPEN
    TOURNAMENT time      — ENROLLMENT_OPEN
    CAMP summer          — READY_FOR_ENROLLMENT
    CAMP autumn          — READY_FOR_ENROLLMENT
    MINI_SEASON          — READY_FOR_ENROLLMENT
    ACADEMY_SEASON       — READY_FOR_ENROLLMENT  (Budapest CENTER only)

  Users (10 total):
    1 admin, 1 instructor, 4 Budapest students, 4 Debrecen students
    — full profiles, all 29 football skills, onboarding complete

  Enrollments:
    APPROVED + payment_verified for every event-user pair

  Completed-tournament results:
    TournamentParticipation with placements (1/2/3/NULL) + skill_rating_delta
    Updated license.football_skills to reflect post-tournament levels

  Invitation codes:
    1 event-specific code per event   (unredeemed)
    2 general codes                   (unredeemed)
    2 sample redeemed codes           (used by bdpst_1 and debr_1)

Run:
    python scripts/reset_and_seed_full.py
"""

import sys
import uuid
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, engine, Base
from app.models import *  # noqa: F401,F403 — registers all SQLAlchemy models
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.game_configuration import GameConfiguration
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.game_preset import GamePreset
from app.models.tournament_achievement import TournamentParticipation
from app.models.credit_transaction import CreditTransaction
from app.models.invitation_code import InvitationCode
from sqlalchemy import text
from app.core.security import get_password_hash

NOW = datetime.now(timezone.utc)
TODAY = date.today()

SPEC = "LFA_FOOTBALL_PLAYER"
AGE_GROUP = "YOUTH"
DEFAULT_PASSWORD = "Player1234!"
ADMIN_PASSWORD = "Admin1234!"

# ── 29 football skills (all keys must be lowercase snake_case) ─────────────────

def _skills(
    *,
    ball_control=65, dribbling=65, finishing=65, shot_power=65,
    long_shots=60, volleys=58, crossing=62, passing=65,
    heading=62, tackle=60, marking=58,
    free_kicks=58, corners=60, penalties=62,
    positioning_off=62, positioning_def=60, vision=63,
    aggression=58, reactions=65, composure=63,
    consistency=60, tactical_awareness=62,
    acceleration=65, sprint_speed=65, agility=65,
    jumping=62, strength=62, stamina=65, balance=63,
):
    return {
        "ball_control": float(ball_control), "dribbling": float(dribbling),
        "finishing": float(finishing), "shot_power": float(shot_power),
        "long_shots": float(long_shots), "volleys": float(volleys),
        "crossing": float(crossing), "passing": float(passing),
        "heading": float(heading), "tackle": float(tackle), "marking": float(marking),
        "free_kicks": float(free_kicks), "corners": float(corners),
        "penalties": float(penalties),
        "positioning_off": float(positioning_off), "positioning_def": float(positioning_def),
        "vision": float(vision), "aggression": float(aggression),
        "reactions": float(reactions), "composure": float(composure),
        "consistency": float(consistency), "tactical_awareness": float(tactical_awareness),
        "acceleration": float(acceleration), "sprint_speed": float(sprint_speed),
        "agility": float(agility), "jumping": float(jumping),
        "strength": float(strength), "stamina": float(stamina), "balance": float(balance),
    }


# ── Student definitions ────────────────────────────────────────────────────────

_STUDENTS = [
    # ── Budapest players ──────────────────────────────────────────────────
    {
        "key": "bdpst_1",
        "first_name": "Péter",  "last_name": "Kovács",
        "email": "kovacs.peter@lfa-bdpst.hu",
        "dob": date(2008, 3, 15),
        "position": "midfielder",
        "nationality": "Hungarian",
        "location_key": "budapest",
        "skills": _skills(
            passing=80, vision=78, ball_control=74, dribbling=72, finishing=68,
            tactical_awareness=76, composure=73, reactions=70, sprint_speed=68,
        ),
    },
    {
        "key": "bdpst_2",
        "first_name": "Balázs",  "last_name": "Nagy",
        "email": "nagy.balazs@lfa-bdpst.hu",
        "dob": date(2007, 8, 22),
        "position": "forward",
        "nationality": "Hungarian",
        "location_key": "budapest",
        "skills": _skills(
            finishing=82, shot_power=79, dribbling=76, ball_control=74,
            acceleration=78, sprint_speed=80, agility=74, volleys=68,
            positioning_off=75,
        ),
    },
    {
        "key": "bdpst_3",
        "first_name": "Dániel",  "last_name": "Horváth",
        "email": "horvath.daniel@lfa-bdpst.hu",
        "dob": date(2008, 11, 5),
        "position": "defender",
        "nationality": "Hungarian",
        "location_key": "budapest",
        "skills": _skills(
            tackle=80, marking=78, heading=77, strength=76, jumping=74,
            positioning_def=78, aggression=72, composure=68,
        ),
    },
    {
        "key": "bdpst_4",
        "first_name": "Ádám",  "last_name": "Szabó",
        "email": "szabo.adam@lfa-bdpst.hu",
        "dob": date(2009, 6, 18),
        "position": "forward",
        "nationality": "Hungarian",
        "location_key": "budapest",
        "skills": _skills(
            sprint_speed=82, acceleration=80, dribbling=74, crossing=72,
            agility=78, stamina=76, ball_control=70,
        ),
    },
    # ── Debrecen players ─────────────────────────────────────────────────
    {
        "key": "debr_1",
        "first_name": "Tamás",  "last_name": "Fekete",
        "email": "fekete.tamas@lfa-debr.hu",
        "dob": date(2007, 2, 10),
        "position": "midfielder",
        "nationality": "Hungarian",
        "location_key": "debrecen",
        "skills": _skills(
            passing=74, stamina=78, tactical_awareness=72, ball_control=70,
            reactions=70, consistency=68, vision=68,
        ),
    },
    {
        "key": "debr_2",
        "first_name": "László",  "last_name": "Varga",
        "email": "varga.laszlo@lfa-debr.hu",
        "dob": date(2008, 5, 29),
        "position": "midfielder",
        "nationality": "Hungarian",
        "location_key": "debrecen",
        "skills": _skills(
            vision=80, passing=78, free_kicks=74, corners=72, composure=76,
            ball_control=74, tactical_awareness=75, crossing=70,
        ),
    },
    {
        "key": "debr_3",
        "first_name": "Gábor",  "last_name": "Kiss",
        "email": "kiss.gabor@lfa-debr.hu",
        "dob": date(2009, 9, 3),
        "position": "midfielder",
        "nationality": "Hungarian",
        "location_key": "debrecen",
        "skills": _skills(
            tackle=76, marking=72, aggression=70, strength=74,
            positioning_def=74, stamina=72, consistency=68,
        ),
    },
    {
        "key": "debr_4",
        "first_name": "Bence",  "last_name": "Tóth",
        "email": "toth.bence@lfa-debr.hu",
        "dob": date(2010, 1, 20),
        "position": "forward",
        "nationality": "Hungarian",
        "location_key": "debrecen",
        "skills": _skills(
            agility=76, dribbling=72, acceleration=74, sprint_speed=73,
            crossing=68, ball_control=68,
        ),
    },
]

# ── Football reward config ─────────────────────────────────────────────────────

_FOOTBALL_REWARD = {
    "template_name": "LFA Football Standard",
    "custom_config": False,
    "skill_mappings": [
        {"skill": "passing",   "weight": 1.0, "category": "TECHNICAL", "enabled": True},
        {"skill": "dribbling", "weight": 1.5, "category": "TECHNICAL", "enabled": True},
        {"skill": "finishing", "weight": 1.3, "category": "TECHNICAL", "enabled": True},
    ],
    "first_place":   {"credits": 500, "xp_multiplier": 2.0, "badges": []},
    "second_place":  {"credits": 250, "xp_multiplier": 1.5, "badges": []},
    "third_place":   {"credits": 100, "xp_multiplier": 1.2, "badges": []},
    "participation": {"credits":  50, "xp_multiplier": 1.0, "badges": []},
}

_GAME_CONFIG = {
    "version": "1.0",
    "metadata": {"min_players": 4, "game_category": "FOOTBALL", "difficulty_level": "INTERMEDIATE"},
    "skill_config": {
        "skills_tested": ["passing", "dribbling", "finishing"],
        "weights": {"passing": 1.0, "dribbling": 1.5, "finishing": 1.3},
    },
    "simulation_config": {"variation": 0.15},
    "format_config": {},
    "match_rules": {"scoring": "goals", "overtime": False},
}


# ═══════════════════════════════════════════════════════════════════════════════
# Seed functions
# ═══════════════════════════════════════════════════════════════════════════════

def _create_game_presets(db) -> dict:
    presets = {}
    for code, name, tested_skills in [
        ("outfield_default", "Outfield Football (Default)", ["passing", "dribbling", "finishing", "ball_control", "sprint_speed"]),
        ("passing_focus",    "Passing & Vision Focus",     ["passing", "vision", "ball_control", "crossing", "tactical_awareness", "free_kicks"]),
        ("shooting_focus",   "Finishing & Shooting Focus", ["finishing", "sprint_speed", "dribbling", "shot_power", "long_shots", "volleys"]),
    ]:
        cfg = dict(_GAME_CONFIG)
        cfg["skill_config"] = {"skills_tested": tested_skills, "weights": {s: 1.0 for s in tested_skills}}
        p = GamePreset(code=code, name=name, description=f"{name} game configuration",
                       game_config=cfg, is_active=True, is_recommended=(code == "outfield_default"))
        db.add(p)
        db.flush()
        presets[code] = p
    print(f"   ✅ {len(presets)} game presets")
    return presets


def _create_tournament_types(db) -> dict:
    types = {}
    _default_cfg = {"rounds": None, "group_size": None, "third_place_match": False}
    for code, display, fmt, min_p, max_p in [
        ("league",      "League (Round Robin)",        "HEAD_TO_HEAD",        2, 1024),
        ("score_based", "Score-Based Ranking",         "INDIVIDUAL_RANKING",  2, None),
        ("time_based",  "Time-Based Ranking",          "INDIVIDUAL_RANKING",  2, None),
        ("placement",   "Placement Ranking",           "INDIVIDUAL_RANKING",  2, None),
    ]:
        t = TournamentType(
            code=code, display_name=display, format=fmt,
            min_players=min_p, max_players=max_p,
            requires_power_of_two=False,
            session_duration_minutes=90,
            break_between_sessions_minutes=15,
            config=_default_cfg,
        )
        db.add(t)
        db.flush()
        types[code] = t
    print(f"   ✅ {len(types)} tournament types")
    return types


def _create_admin_and_instructor(db) -> tuple:
    admin = User(
        name="LFA Admin",
        first_name="LFA", last_name="Admin",
        email="admin@lfa.com",
        password_hash=get_password_hash(ADMIN_PASSWORD),
        role=UserRole.ADMIN,
        is_active=True, onboarding_completed=True,
        nationality="Hungarian", country="Hungary",
    )
    db.add(admin)

    instructor = User(
        name="Nagymester Ferenc",
        first_name="Ferenc", last_name="Nagymester",
        email="grandmaster@lfa.com",
        password_hash=get_password_hash(ADMIN_PASSWORD),
        role=UserRole.INSTRUCTOR,
        is_active=True, onboarding_completed=True,
        nationality="Hungarian", country="Hungary",
    )
    db.add(instructor)
    db.flush()
    print(f"   ✅ admin (id={admin.id})  instructor (id={instructor.id})")
    return admin, instructor


def _create_locations(db) -> dict:
    locs = {}
    campuses = {}

    budapest = Location(
        name="LFA IK Education Center Budapest",
        city="Budapest", country="Hungary", country_code="HU",
        location_code="BDPST-IK", postal_code="1146",
        address="Istvánmezei út 1-3, Budapest",
        location_type=LocationType.CENTER, is_active=True,
    )
    db.add(budapest)
    db.flush()
    locs["budapest"] = budapest

    bdpst_campus = Campus(
        location_id=budapest.id, name="IK Main Campus",
        venue="Outdoor fields + indoor gym",
        address="Istvánmezei út 1-3, Budapest", is_active=True,
    )
    db.add(bdpst_campus)
    db.flush()
    campuses["budapest"] = bdpst_campus

    debrecen = Location(
        name="LFA Debrecen Partner Center",
        city="Debrecen", country="Hungary", country_code="HU",
        location_code="DEBR-01", postal_code="4032",
        address="Nagyerdei körút 98, Debrecen",
        location_type=LocationType.PARTNER, is_active=True,
    )
    db.add(debrecen)
    db.flush()
    locs["debrecen"] = debrecen

    debr_campus = Campus(
        location_id=debrecen.id, name="Debrecen Main Campus",
        venue="Outdoor pitch + changing rooms",
        address="Nagyerdei körút 98, Debrecen", is_active=True,
    )
    db.add(debr_campus)
    db.flush()
    campuses["debrecen"] = debr_campus

    print(f"   ✅ Budapest CENTER (id={budapest.id}, campus={bdpst_campus.id})")
    print(f"   ✅ Debrecen PARTNER (id={debrecen.id}, campus={debr_campus.id})")
    return locs, campuses


def _create_students(db, admin) -> dict:
    users = {}
    for s in _STUDENTS:
        age = TODAY.year - s["dob"].year - ((TODAY.month, TODAY.day) < (s["dob"].month, s["dob"].day))
        u = User(
            name=f"{s['first_name']} {s['last_name']}",
            first_name=s["first_name"], last_name=s["last_name"],
            email=s["email"],
            password_hash=get_password_hash(DEFAULT_PASSWORD),
            role=UserRole.STUDENT,
            is_active=True, onboarding_completed=True,
            date_of_birth=datetime.combine(s["dob"], datetime.min.time()),
            nationality=s["nationality"],
            position=s.get("position"),
            country="Hungary",
            credit_balance=2000, credit_purchased=2000,
            payment_verified=True,
            created_by=admin.id,
        )
        db.add(u)
        db.flush()

        age_cat = "YOUTH" if 14 <= age <= 18 else ("AMATEUR" if age > 18 else "PRE")

        lic = UserLicense(
            user_id=u.id,
            specialization_type=SPEC,
            current_level=1,
            is_active=True,
            onboarding_completed=True,
            onboarding_completed_at=NOW - timedelta(days=30),
            payment_verified=True,
            payment_verified_at=NOW - timedelta(days=35),
            started_at=NOW - timedelta(days=90),
            football_skills=s["skills"],
            skills_last_updated_at=NOW - timedelta(days=10),
            credit_balance=0,
            credit_purchased=0,
        )
        db.add(lic)
        db.flush()

        users[s["key"]] = {"user": u, "license": lic, "age_cat": age_cat}
        print(f"   ✅ {u.name} [{s['key']}]  email={u.email}  age={age}({age_cat})  lic={lic.id}")

    return users


def _make_semester(
    db, *, code, name, category, loc, campus, instructor,
    start_offset, duration,
    t_status=None, status=SemesterStatus.READY_FOR_ENROLLMENT,
    enrollment_cost=0,
) -> Semester:
    s = Semester(
        code=code, name=name,
        semester_category=category,
        status=status,
        tournament_status=t_status,
        age_group=AGE_GROUP,
        location_id=loc.id, campus_id=campus.id,
        master_instructor_id=instructor.id,
        start_date=TODAY + timedelta(days=start_offset) if start_offset >= 0 else TODAY - timedelta(days=-start_offset),
        end_date=TODAY + timedelta(days=start_offset + duration) if start_offset >= 0 else TODAY - timedelta(days=-start_offset - duration),
        enrollment_cost=enrollment_cost,
        specialization_type=SPEC,
    )
    db.add(s)
    db.flush()
    return s


def _add_tournament_config(db, semester, tt_code, tt_map, scoring_type=None, measurement_unit=None, ranking_direction="DESC", preset=None):
    # HEAD_TO_HEAD (e.g. league) → link tournament_type; INDIVIDUAL_RANKING → NULL (format derived implicitly)
    tt = tt_map[tt_code]
    is_h2h = (tt.format == "HEAD_TO_HEAD")
    db.add(TournamentConfiguration(
        semester_id=semester.id,
        tournament_type_id=tt.id if is_h2h else None,
        scoring_type=scoring_type if scoring_type else "PLACEMENT",
        measurement_unit=measurement_unit,
        ranking_direction=ranking_direction,
        participant_type="INDIVIDUAL",
        is_multi_day=False,
        max_players=32, parallel_fields=1,
        sessions_generated=False,
    ))
    db.add(GameConfiguration(
        semester_id=semester.id,
        game_preset_id=preset.id if preset else None,
        game_config=_GAME_CONFIG,
    ))
    db.add(TournamentRewardConfig(
        semester_id=semester.id,
        reward_policy_name=_FOOTBALL_REWARD["template_name"],
        reward_config=_FOOTBALL_REWARD,
    ))


def _create_events(db, locs, campuses, instructor, tt_map, presets) -> dict:
    """Create all events for both locations. Returns dict of code → Semester."""
    events = {}
    preset = presets["outfield_default"]

    # ── Budapest ──────────────────────────────────────────────────────────────
    loc, campus = locs["budapest"], campuses["budapest"]

    # TOURNAMENT league — COMPLETED (past)
    s = _make_semester(db, code="BDPST-TOURN-LEAGUE-2026",
        name="Budapest League Tournament 2026 — COMPLETED",
        category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.COMPLETED,
        t_status="COMPLETED",
        loc=loc, campus=campus, instructor=instructor,
        start_offset=-60, duration=14)
    _add_tournament_config(db, s, "league", tt_map, ranking_direction="DESC", preset=preset)
    events["bdpst_league"] = s

    # TOURNAMENT score — ENROLLMENT_OPEN
    s = _make_semester(db, code="BDPST-TOURN-SCORE-2026",
        name="Budapest Score Challenge 2026",
        category=SemesterCategory.TOURNAMENT,
        t_status="ENROLLMENT_OPEN",
        loc=loc, campus=campus, instructor=instructor,
        start_offset=14, duration=14)
    _add_tournament_config(db, s, "score_based", tt_map, scoring_type="SCORE_BASED",
                           measurement_unit="goals", ranking_direction="DESC", preset=presets["shooting_focus"])
    events["bdpst_score"] = s

    # TOURNAMENT time — ENROLLMENT_OPEN
    s = _make_semester(db, code="BDPST-TOURN-TIME-2026",
        name="Budapest Time Trial Series 2026",
        category=SemesterCategory.TOURNAMENT,
        t_status="ENROLLMENT_OPEN",
        loc=loc, campus=campus, instructor=instructor,
        start_offset=21, duration=14)
    _add_tournament_config(db, s, "time_based", tt_map, scoring_type="TIME_BASED",
                           measurement_unit="seconds", ranking_direction="ASC", preset=presets["passing_focus"])
    events["bdpst_time"] = s

    # CAMP summer
    s = _make_semester(db, code="BDPST-CAMP-SUMMER-2026",
        name="Budapest Summer Football Camp 2026 — YOUTH",
        category=SemesterCategory.CAMP,
        loc=loc, campus=campus, instructor=instructor,
        start_offset=30, duration=7, enrollment_cost=500)
    events["bdpst_camp_summer"] = s

    # CAMP autumn
    s = _make_semester(db, code="BDPST-CAMP-AUTUMN-2026",
        name="Budapest Autumn Football Camp 2026 — YOUTH",
        category=SemesterCategory.CAMP,
        loc=loc, campus=campus, instructor=instructor,
        start_offset=120, duration=7, enrollment_cost=500)
    events["bdpst_camp_autumn"] = s

    # MINI_SEASON
    s = _make_semester(db, code="BDPST-MINI-2026",
        name="Budapest Mini Season YOUTH 2026",
        category=SemesterCategory.MINI_SEASON,
        loc=loc, campus=campus, instructor=instructor,
        start_offset=45, duration=60, enrollment_cost=500)
    events["bdpst_mini"] = s

    # ACADEMY_SEASON (CENTER only)
    s = _make_semester(db, code="BDPST-ACADEMY-2026",
        name="Budapest Academy Season YOUTH 2026/27",
        category=SemesterCategory.ACADEMY_SEASON,
        loc=loc, campus=campus, instructor=instructor,
        start_offset=90, duration=300, enrollment_cost=1000)
    events["bdpst_academy"] = s

    print(f"   ✅ Budapest: {len([k for k in events if k.startswith('bdpst')])} events")

    # ── Debrecen ──────────────────────────────────────────────────────────────
    loc, campus = locs["debrecen"], campuses["debrecen"]

    # TOURNAMENT league — COMPLETED (past)
    s = _make_semester(db, code="DEBR-TOURN-LEAGUE-2026",
        name="Debrecen League Tournament 2026 — COMPLETED",
        category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.COMPLETED,
        t_status="COMPLETED",
        loc=loc, campus=campus, instructor=instructor,
        start_offset=-45, duration=14)
    _add_tournament_config(db, s, "league", tt_map, ranking_direction="DESC", preset=preset)
    events["debr_league"] = s

    # TOURNAMENT score — ENROLLMENT_OPEN
    s = _make_semester(db, code="DEBR-TOURN-SCORE-2026",
        name="Debrecen Score Challenge 2026",
        category=SemesterCategory.TOURNAMENT,
        t_status="ENROLLMENT_OPEN",
        loc=loc, campus=campus, instructor=instructor,
        start_offset=14, duration=14)
    _add_tournament_config(db, s, "score_based", tt_map, scoring_type="SCORE_BASED",
                           measurement_unit="goals", ranking_direction="DESC", preset=presets["shooting_focus"])
    events["debr_score"] = s

    # TOURNAMENT time — ENROLLMENT_OPEN
    s = _make_semester(db, code="DEBR-TOURN-TIME-2026",
        name="Debrecen Time Trial Series 2026",
        category=SemesterCategory.TOURNAMENT,
        t_status="ENROLLMENT_OPEN",
        loc=loc, campus=campus, instructor=instructor,
        start_offset=21, duration=14)
    _add_tournament_config(db, s, "time_based", tt_map, scoring_type="TIME_BASED",
                           measurement_unit="seconds", ranking_direction="ASC", preset=presets["passing_focus"])
    events["debr_time"] = s

    # CAMP summer
    s = _make_semester(db, code="DEBR-CAMP-SUMMER-2026",
        name="Debrecen Summer Football Camp 2026 — YOUTH",
        category=SemesterCategory.CAMP,
        loc=loc, campus=campus, instructor=instructor,
        start_offset=30, duration=7, enrollment_cost=500)
    events["debr_camp_summer"] = s

    # CAMP autumn
    s = _make_semester(db, code="DEBR-CAMP-AUTUMN-2026",
        name="Debrecen Autumn Football Camp 2026 — YOUTH",
        category=SemesterCategory.CAMP,
        loc=loc, campus=campus, instructor=instructor,
        start_offset=120, duration=7, enrollment_cost=500)
    events["debr_camp_autumn"] = s

    # MINI_SEASON (no ACADEMY for PARTNER)
    s = _make_semester(db, code="DEBR-MINI-2026",
        name="Debrecen Mini Season YOUTH 2026",
        category=SemesterCategory.MINI_SEASON,
        loc=loc, campus=campus, instructor=instructor,
        start_offset=45, duration=60, enrollment_cost=500)
    events["debr_mini"] = s

    print(f"   ✅ Debrecen: {len([k for k in events if k.startswith('debr')])} events")
    return events


def _enroll(db, admin, user_data, semester, event_key) -> SemesterEnrollment:
    """Create APPROVED + payment_verified enrollment."""
    u = user_data["user"]
    lic = user_data["license"]
    age_cat = user_data["age_cat"]

    ref_code = f"ENRL-{u.id:04d}-{semester.id:04d}-{uuid.uuid4().hex[:6].upper()}"

    enr = SemesterEnrollment(
        user_id=u.id,
        semester_id=semester.id,
        user_license_id=lic.id,
        request_status=EnrollmentStatus.APPROVED,
        requested_at=NOW - timedelta(days=20),
        approved_at=NOW - timedelta(days=18),
        approved_by=admin.id,
        payment_reference_code=ref_code,
        payment_verified=True,
        payment_verified_at=NOW - timedelta(days=17),
        payment_verified_by=admin.id,
        is_active=True,
        enrolled_at=NOW - timedelta(days=18),
        age_category=age_cat,
    )
    db.add(enr)
    db.flush()

    # Deduct enrollment cost if > 0
    cost = semester.enrollment_cost or 0
    if cost > 0:
        u.credit_balance -= cost
        db.add(CreditTransaction(
            user_license_id=lic.id,   # enrollment is a license-level op (XOR constraint)
            transaction_type="ENROLLMENT",
            amount=-cost,
            balance_after=u.credit_balance,
            description=f"Enrollment: {semester.name}",
            idempotency_key=f"enroll-{enr.id}",
            semester_id=semester.id,
            enrollment_id=enr.id,
            performed_by_user_id=admin.id,
        ))
        db.flush()

    return enr


def _create_enrollments(db, admin, users, events) -> dict:
    """Create APPROVED enrollments for every event-user pair."""
    # event_key → [student_keys]
    matrix = {
        # Budapest
        "bdpst_league":       ["bdpst_1", "bdpst_2", "bdpst_3", "bdpst_4"],
        "bdpst_score":        ["bdpst_1", "bdpst_2", "bdpst_3", "debr_1"],
        "bdpst_time":         ["bdpst_2", "bdpst_3", "bdpst_4", "debr_2"],
        "bdpst_camp_summer":  ["bdpst_1", "bdpst_3"],
        "bdpst_camp_autumn":  ["bdpst_2", "bdpst_4"],
        "bdpst_mini":         ["bdpst_1", "bdpst_2", "bdpst_3", "bdpst_4"],
        "bdpst_academy":      ["bdpst_1", "bdpst_2"],
        # Debrecen
        "debr_league":        ["debr_1", "debr_2", "debr_3", "debr_4"],
        "debr_score":         ["debr_1", "debr_2", "debr_3", "bdpst_3"],
        "debr_time":          ["debr_2", "debr_3", "debr_4", "bdpst_4"],
        "debr_camp_summer":   ["debr_1", "debr_3"],
        "debr_camp_autumn":   ["debr_2", "debr_4"],
        "debr_mini":          ["debr_1", "debr_2", "debr_3", "debr_4"],
    }

    enrollments = {}
    total = 0
    for event_key, student_keys in matrix.items():
        semester = events[event_key]
        for sk in student_keys:
            enr = _enroll(db, admin, users[sk], semester, event_key)
            enrollments[f"{event_key}__{sk}"] = enr
            total += 1
    print(f"   ✅ {total} enrollments (all APPROVED + payment_verified)")
    return enrollments


def _create_tournament_results(db, admin, users, events):
    """Create TournamentParticipation for COMPLETED tournaments + update skills."""

    # placement → (credits_reward, skill_delta_factor)
    REWARD = {1: (500, +4.0), 2: (250, +2.0), 3: (100, +1.0), None: (50, 0.0)}

    for tourn_key, placement_map in [
        ("bdpst_league", {
            "bdpst_1": 1,     # 1st place → skill up
            "bdpst_2": 2,     # 2nd place
            "bdpst_3": 3,     # 3rd place
            "bdpst_4": None,  # participant only
        }),
        ("debr_league", {
            "debr_1": 1,
            "debr_2": 2,
            "debr_3": 3,
            "debr_4": None,
        }),
    ]:
        semester = events[tourn_key]
        for sk, placement in placement_map.items():
            u = users[sk]["user"]
            lic = users[sk]["license"]
            credits_reward, delta_factor = REWARD[placement]

            # Skill delta (top 3 skills from preset)
            skill_delta = {}
            if delta_factor > 0:
                skill_delta = {
                    "passing":   round(delta_factor * 1.0, 2),
                    "dribbling": round(delta_factor * 1.5, 2),
                    "finishing": round(delta_factor * 0.8, 2),
                }
                # Apply delta to football_skills on license
                new_skills = dict(lic.football_skills)
                for skill_key, delta in skill_delta.items():
                    new_skills[skill_key] = round(new_skills.get(skill_key, 65.0) + delta, 2)
                lic.football_skills = new_skills
                lic.skills_last_updated_at = NOW - timedelta(days=5)
                db.flush()

            # skill_points_awarded — aggregate raw points
            skill_points = {k: round(abs(v) * 10, 1) for k, v in skill_delta.items()} if skill_delta else {}

            tp = TournamentParticipation(
                user_id=u.id,
                semester_id=semester.id,
                placement=placement,
                skill_points_awarded=skill_points,
                skill_rating_delta=skill_delta,
                xp_awarded=credits_reward,
                credits_awarded=credits_reward,
                achieved_at=semester.end_date,
            )
            db.add(tp)
            db.flush()

            # Credit reward transaction
            u.credit_balance += credits_reward
            db.add(CreditTransaction(
                user_id=u.id,
                transaction_type="TOURNAMENT_REWARD",
                amount=credits_reward,
                balance_after=u.credit_balance,
                description=f"Placement reward: {semester.name} — {'#' + str(placement) if placement else 'Participant'}",
                idempotency_key=f"tourney-reward-{semester.id}-{u.id}",
                semester_id=semester.id,
                performed_by_user_id=admin.id,
            ))
            db.flush()

            place_str = f"#{placement}" if placement else "participant"
            print(f"      {place_str:12s}  {u.name}  +{credits_reward} cr  delta={skill_delta}")

    print(f"   ✅ Tournament results recorded")


def _create_invitation_codes(db, admin, users, events):
    """Create event-specific + general + sample-redeemed invitation codes."""
    codes = []

    # 1) One entry code per event
    for event_key, semester in events.items():
        loc_prefix = "BDPST" if event_key.startswith("bdpst") else "DEBR"
        type_suffix = event_key.upper().replace("_", "-")
        code_str = f"LFA-{type_suffix}-ENTRY"
        c = InvitationCode(
            code=code_str,
            invited_name=f"Entry pass — {semester.name}",
            bonus_credits=100,
            is_used=False,
            created_by_admin_id=admin.id,
            notes=f"Event entry code for: {semester.code}",
        )
        db.add(c)
        codes.append(code_str)

    # 2) Two general codes per location (unredeemed)
    for loc, bonus in [("BDPST-GENERAL-A", 200), ("BDPST-GENERAL-B", 300),
                        ("DEBR-GENERAL-A", 200),  ("DEBR-GENERAL-B", 300)]:
        c = InvitationCode(
            code=f"LFA-{loc}-2026",
            invited_name=f"General entry — {loc.split('-')[0]}",
            bonus_credits=bonus,
            is_used=False,
            created_by_admin_id=admin.id,
            notes="General invitation code — location wide",
        )
        db.add(c)
        codes.append(f"LFA-{loc}-2026")

    # 3) Two sample redeemed codes (already used by bdpst_1 and debr_1)
    for redeemed_by_key, code_str, bonus in [
        ("bdpst_1", "LFA-WELCOME-BDPST-001", 300),
        ("debr_1",  "LFA-WELCOME-DEBR-001",  300),
    ]:
        redeemed_user = users[redeemed_by_key]["user"]
        c = InvitationCode(
            code=code_str,
            invited_name=f"{redeemed_user.name} — welcome bonus",
            bonus_credits=bonus,
            is_used=True,
            used_by_user_id=redeemed_user.id,
            used_at=NOW - timedelta(days=60),
            created_by_admin_id=admin.id,
            notes="Welcome bonus code — already redeemed",
        )
        db.add(c)
        codes.append(code_str)
        # Credit transaction for the redemption
        redeemed_user.credit_balance += bonus
        db.add(CreditTransaction(
            user_id=redeemed_user.id,
            transaction_type="ADMIN_ADJUSTMENT",
            amount=bonus,
            balance_after=redeemed_user.credit_balance,
            description=f"Invitation code bonus: {code_str}",
            idempotency_key=f"invitation-code-{code_str}-user-{redeemed_user.id}",
            performed_by_user_id=admin.id,
        ))
        db.flush()

    db.flush()
    print(f"   ✅ {len(codes)} invitation codes ({len(events)} event-specific, 4 general, 2 redeemed)")


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 70)
    print("  Full Database Reset & Seed — Frontend Validation")
    print("=" * 70)

    # ── 1. Reset DB ───────────────────────────────────────────────────────────
    print("\n🗑️  Dropping schema (CASCADE) and recreating…")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO postgres"))
        conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        conn.commit()
    print("📐 Creating tables…")
    Base.metadata.create_all(bind=engine)
    print("✅ Clean schema ready\n")

    db = SessionLocal()
    try:
        # ── 2. Game presets + tournament types ────────────────────────────────
        print("🎮 Game presets & tournament types")
        presets = _create_game_presets(db)
        tt_map = _create_tournament_types(db)
        db.commit()

        # ── 3. Admin + Instructor ─────────────────────────────────────────────
        print("\n👤 Admin & Instructor")
        admin, instructor = _create_admin_and_instructor(db)
        db.commit()

        # ── 4. Locations + Campuses ───────────────────────────────────────────
        print("\n📍 Locations & Campuses")
        locs, campuses = _create_locations(db)
        db.commit()

        # ── 5. Students ───────────────────────────────────────────────────────
        print("\n👥 Students (8)")
        users = _create_students(db, admin)
        db.commit()

        # ── 6. Events ─────────────────────────────────────────────────────────
        print("\n📅 Events (13 total)")
        events = _create_events(db, locs, campuses, instructor, tt_map, presets)
        db.commit()

        # ── 7. Enrollments ────────────────────────────────────────────────────
        print("\n📝 Enrollments")
        _create_enrollments(db, admin, users, events)
        db.commit()

        # ── 8. Tournament results ─────────────────────────────────────────────
        print("\n🏆 Completed tournament results")
        print("   Budapest League:")
        _create_tournament_results.__wrapped__ = False  # prevent wrapping
        _create_tournament_results(db, admin, users, events)
        db.commit()

        # ── 9. Invitation codes ───────────────────────────────────────────────
        print("\n🔑 Invitation codes")
        _create_invitation_codes(db, admin, users, events)
        db.commit()

        # ── Final summary ─────────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("  ✅ Seed complete — Final state")
        print("=" * 70)

        print(f"""
  Credentials:
    admin@lfa.com            {ADMIN_PASSWORD}
    grandmaster@lfa.com      {ADMIN_PASSWORD}

  Budapest students (password: {DEFAULT_PASSWORD}):
    kovacs.peter@lfa-bdpst.hu   — midfielder
    nagy.balazs@lfa-bdpst.hu    — forward
    horvath.daniel@lfa-bdpst.hu — defender
    szabo.adam@lfa-bdpst.hu     — forward

  Debrecen students (password: {DEFAULT_PASSWORD}):
    fekete.tamas@lfa-debr.hu    — midfielder
    varga.laszlo@lfa-debr.hu    — midfielder
    kiss.gabor@lfa-debr.hu      — midfielder
    toth.bence@lfa-debr.hu      — forward

  Budapest events (7):
    BDPST-TOURN-LEAGUE-2026    TOURNAMENT  league     COMPLETED
    BDPST-TOURN-SCORE-2026     TOURNAMENT  score      ENROLLMENT_OPEN
    BDPST-TOURN-TIME-2026      TOURNAMENT  time       ENROLLMENT_OPEN
    BDPST-CAMP-SUMMER-2026     CAMP                   READY_FOR_ENROLLMENT
    BDPST-CAMP-AUTUMN-2026     CAMP                   READY_FOR_ENROLLMENT
    BDPST-MINI-2026            MINI_SEASON            READY_FOR_ENROLLMENT
    BDPST-ACADEMY-2026         ACADEMY_SEASON         READY_FOR_ENROLLMENT

  Debrecen events (6):
    DEBR-TOURN-LEAGUE-2026     TOURNAMENT  league     COMPLETED
    DEBR-TOURN-SCORE-2026      TOURNAMENT  score      ENROLLMENT_OPEN
    DEBR-TOURN-TIME-2026       TOURNAMENT  time       ENROLLMENT_OPEN
    DEBR-CAMP-SUMMER-2026      CAMP                   READY_FOR_ENROLLMENT
    DEBR-CAMP-AUTUMN-2026      CAMP                   READY_FOR_ENROLLMENT
    DEBR-MINI-2026             MINI_SEASON            READY_FOR_ENROLLMENT

  Invitation codes:
    13 event-specific  (1 per event, unredeemed)
     4 general         (2 Budapest, 2 Debrecen, unredeemed)
     2 redeemed        (LFA-WELCOME-BDPST-001, LFA-WELCOME-DEBR-001)
""")

    except Exception as exc:
        db.rollback()
        print(f"\n❌ Error during seed: {exc}")
        import traceback; traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()

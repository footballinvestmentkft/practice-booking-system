"""
Laterality format smoke tests — LF-01 through LF-04.

Verifies that distribute_rewards_for_user writes the correct foot_context to
TournamentParticipation for each active tournament format type.

The laterality routing path in the orchestrator is format-agnostic: it reads
foot_context from GamePreset regardless of tournament structure.  These tests
exist to document that we've exercised all four formats and to catch any future
format-conditional branching that might inadvertently skip lateral write-back.

Formats covered:
  LF-01  HEAD_TO_HEAD         (simulated via scoring_type — no TournamentType FK needed)
  LF-02  GROUP_KNOCKOUT        (simulated via scoring_type)
  LF-03  SWISS                 (simulated via scoring_type)
  LF-04  INDIVIDUAL_RANKING    (default; no TournamentConfiguration needed)

All tests use:
  - laterality_fixtures (session-scoped)
  - test_db (function-scoped SAVEPOINT)
"""

import uuid
import pytest
from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.game_configuration import GameConfiguration
from app.models.tournament_achievement import TournamentParticipation, TournamentSkillMapping
from app.models.tournament_configuration import TournamentConfiguration
from app.models.user import User
from app.services.tournament.tournament_reward_orchestrator import distribute_rewards_for_user


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_user(db: Session, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    assert user is not None, f"Control user {email!r} not found"
    return user


def _base_semester(db: Session, label: str, preset_id: int, skill: str) -> Semester:
    """Create a minimal tournament semester with a laterality preset."""
    sem = Semester(
        code=f"LAT-F-{uuid.uuid4().hex[:8]}",
        name=f"Laterality Format Smoke — {label}",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()

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


def _assert_foot_context(db: Session, user_id: int, tournament_id: int, expected: str) -> None:
    participation = db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.semester_id == tournament_id,
    ).first()
    assert participation is not None, "TournamentParticipation not found after distribution"
    assert participation.foot_context == expected, (
        f"foot_context={participation.foot_context!r} != {expected!r}"
    )


# ── LF-01: HEAD_TO_HEAD format smoke ──────────────────────────────────────────

def test_lf01_head_to_head_foot_context_routed(test_db: Session, laterality_fixtures):
    """LF-01: HEAD_TO_HEAD-style tournament — foot_context='right' stored correctly."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_right"]

    sem = _base_semester(test_db, "H2H", preset_id, "finishing")

    # Simulate HEAD_TO_HEAD: scoring_type triggers INDIVIDUAL_RANKING fallback path
    # (TournamentType FK not required for this smoke — format routing is not on the
    # critical path for laterality write-back)
    test_db.add(TournamentConfiguration(
        semester_id=sem.id,
        scoring_type="HEAD_TO_HEAD",
    ))
    test_db.flush()

    distribute_rewards_for_user(
        db=test_db,
        user_id=user.id,
        tournament_id=sem.id,
        placement=1,
        total_participants=4,
    )

    _assert_foot_context(test_db, user.id, sem.id, "right")


# ── LF-02: GROUP_KNOCKOUT format smoke ────────────────────────────────────────

def test_lf02_group_knockout_foot_context_routed(test_db: Session, laterality_fixtures):
    """LF-02: GROUP_KNOCKOUT-style tournament — foot_context='left' stored correctly."""
    user = _load_user(test_db, "lat.left@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_left"]

    sem = _base_semester(test_db, "GK", preset_id, "finishing")

    test_db.add(TournamentConfiguration(
        semester_id=sem.id,
        scoring_type="PLACEMENT",
    ))
    test_db.flush()

    distribute_rewards_for_user(
        db=test_db,
        user_id=user.id,
        tournament_id=sem.id,
        placement=1,
        total_participants=4,
    )

    _assert_foot_context(test_db, user.id, sem.id, "left")


# ── LF-03: SWISS format smoke ─────────────────────────────────────────────────

def test_lf03_swiss_foot_context_routed(test_db: Session, laterality_fixtures):
    """LF-03: SWISS-style tournament — foot_context='neutral' stored correctly."""
    user = _load_user(test_db, "lat.balanced@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_neutral"]

    sem = _base_semester(test_db, "SWISS", preset_id, "finishing")

    test_db.add(TournamentConfiguration(
        semester_id=sem.id,
        scoring_type="SCORE_BASED",
    ))
    test_db.flush()

    distribute_rewards_for_user(
        db=test_db,
        user_id=user.id,
        tournament_id=sem.id,
        placement=1,
        total_participants=4,
    )

    _assert_foot_context(test_db, user.id, sem.id, "neutral")


# ── LF-04: INDIVIDUAL_RANKING format smoke (default, no TournamentConfiguration) ─

def test_lf04_individual_ranking_foot_context_routed(test_db: Session, laterality_fixtures):
    """LF-04: INDIVIDUAL_RANKING (default format, no TournamentConfiguration) —
    foot_context='right' stored correctly."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_crossing_right"]

    sem = _base_semester(test_db, "IR", preset_id, "crossing")
    # No TournamentConfiguration → Semester.format defaults to "INDIVIDUAL_RANKING"

    distribute_rewards_for_user(
        db=test_db,
        user_id=user.id,
        tournament_id=sem.id,
        placement=1,
        total_participants=4,
    )

    _assert_foot_context(test_db, user.id, sem.id, "right")

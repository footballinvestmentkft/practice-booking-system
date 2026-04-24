"""
Integration tests for Domain B — tournament EMA delta → FootballSkillAssessment propagation.

Uses real PostgreSQL with SAVEPOINT isolation (test_db fixture from
tests/integration/conftest.py).

What is tested end-to-end:
  record_tournament_participation(placement=1, ...)
    → compute_single_tournament_skill_delta   (Phase 2, real EMA replay)
    → update_skill_assessments                (Phase 3, real DB writes)
      → UserLicense lookup (order by id DESC)
      → FootballSkillAssessment archive + insert

PROP-I-01  Fresh flow: 1st-place → delta computed → new ASSESSED row created
PROP-I-02  Pre-existing ASSESSED → archived, new row at prior_pct + delta
PROP-I-03  No active LFA_FOOTBALL_PLAYER license → no assessment written
PROP-I-04  ENABLE_TOURNAMENT_SKILL_PROPAGATION=False → no assessment written

Expected delta calculation (solo tournament, default inputs):
  total_players=1 → percentile=0.0 (guarded)
  placement_skill = 100.0 - 0.0 × (100.0 - 40.0) = 100.0
  step  = 0.20 × log(1+1.0) / log(2.0) = 0.20
  raw_δ = 0.20 × (100.0 - 60.0) = 8.0
  opp_factor=1.0 (no opponents), match_modifier=0.0 (no game sessions)
  new_val = clamp(60.0 + 8.0, 40, 99) = 68.0
  delta = round(68.0 - 60.0, 1) = 8.0
"""
import uuid
import pytest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus
from app.models.tournament_achievement import TournamentParticipation, TournamentSkillMapping
from app.models.license import UserLicense
from app.models.football_skill_assessment import FootballSkillAssessment
from app.services.tournament.tournament_participation_service import record_tournament_participation
from app.services.skill_progression_service import get_skill_profile
from app.core.security import get_password_hash


_BASE = "app.services.tournament.tournament_participation_service"

_EXPECTED_DELTA = 8.0    # DEFAULT_BASELINE=60.0; step=0.20; placement_skill=100; delta=0.20*(100-60)=8
_EXPECTED_NEW_PCT = 68.0  # baseline 60.0 + delta 8.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _player(test_db: Session) -> User:
    user = User(
        email=f"prop-player+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Propagation Test Player",
        password_hash=get_password_hash("pass"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _license(test_db: Session, user: User) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
        is_active=True,
        # football_skills=None → get_baseline_skills returns DEFAULT_BASELINE=60.0
    )
    test_db.add(lic)
    test_db.commit()
    test_db.refresh(lic)
    return lic


def _tournament(test_db: Session) -> Semester:
    sem = Semester(
        code=f"PROP-{uuid.uuid4().hex[:8]}",
        name="Propagation Test Tournament",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=90),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.commit()
    test_db.refresh(sem)
    return sem


def _skill_mapping(
    test_db: Session,
    tournament: Semester,
    skill: str = "dribbling",
    weight: float = 1.0,
) -> TournamentSkillMapping:
    mapping = TournamentSkillMapping(
        semester_id=tournament.id,
        skill_name=skill,
        skill_category="football_skill",
        weight=weight,
    )
    test_db.add(mapping)
    test_db.commit()
    return mapping


# ── PROP-I-01: Fresh flow ─────────────────────────────────────────────────────

def test_prop_i01_fresh_flow_creates_assessment(test_db: Session):
    """
    Full pipeline: player 1st place → EMA delta computed → FootballSkillAssessment created.

    With default inputs (solo tournament, no prior tournaments, no game sessions):
      delta = +8.0, new_pct = 68.0
    """
    player = _player(test_db)
    lic = _license(test_db, player)
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")

    participation = record_tournament_participation(
        db=test_db,
        user_id=player.id,
        tournament_id=tournament.id,
        placement=1,
        skill_points={},
        base_xp=0,
        credits=0,
    )
    test_db.flush()

    # TournamentParticipation.skill_rating_delta should be populated
    assert participation.skill_rating_delta is not None, "EMA delta must be computed"
    assert "dribbling" in participation.skill_rating_delta
    assert participation.skill_rating_delta["dribbling"] == pytest.approx(
        _EXPECTED_DELTA, abs=0.15
    )

    # Exactly one FootballSkillAssessment should be created
    assessments = (
        test_db.query(FootballSkillAssessment)
        .filter(
            FootballSkillAssessment.user_license_id == lic.id,
            FootballSkillAssessment.skill_name == "dribbling",
        )
        .all()
    )
    assert len(assessments) == 1

    a = assessments[0]
    assert a.status == "ASSESSED"
    assert a.percentage == pytest.approx(_EXPECTED_NEW_PCT, abs=0.15)
    assert a.points_earned == round(a.percentage)
    assert a.points_total == 100
    assert a.assessed_by == player.id   # falls back to user_id (no assessed_by_id given)
    assert a.requires_validation is False
    assert a.notes is not None
    assert "+8.0" in a.notes or "8.0" in a.notes


# ── PROP-I-02: Pre-existing ASSESSED → archived, new created ─────────────────

def test_prop_i02_existing_assessment_archived_and_replaced(test_db: Session):
    """
    Pre-existing ASSESSED assessment at 65.0% should be archived.
    New assessment: new_pct = clamp(65.0 + 8.0, 40, 99) = 73.0  (delta=8.0 from DEFAULT_BASELINE=60).
    """
    player = _player(test_db)
    lic = _license(test_db, player)
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")

    # Seed a prior assessment
    prior = FootballSkillAssessment(
        user_license_id=lic.id,
        skill_name="dribbling",
        points_earned=65,
        points_total=100,
        percentage=65.0,
        assessed_by=player.id,
        status="ASSESSED",
        requires_validation=False,
    )
    test_db.add(prior)
    test_db.commit()
    test_db.refresh(prior)
    prior_id = prior.id

    record_tournament_participation(
        db=test_db,
        user_id=player.id,
        tournament_id=tournament.id,
        placement=1,
        skill_points={},
        base_xp=0,
        credits=0,
    )
    test_db.flush()

    # The prior assessment must be archived
    test_db.refresh(prior)
    assert prior.status == "ARCHIVED"
    assert prior.previous_status == "ASSESSED"
    assert prior.archived_reason == "tournament_progression_delta=+8.0"
    assert prior.archived_at is not None
    assert prior.archived_by == player.id

    # A new ASSESSED assessment should have been created
    new_assessment = (
        test_db.query(FootballSkillAssessment)
        .filter(
            FootballSkillAssessment.user_license_id == lic.id,
            FootballSkillAssessment.skill_name == "dribbling",
            FootballSkillAssessment.status == "ASSESSED",
        )
        .first()
    )
    assert new_assessment is not None
    assert new_assessment.id != prior_id
    assert new_assessment.percentage == pytest.approx(73.0, abs=0.15)  # 65.0 + 8.0


# ── PROP-I-03: No license → no assessment ────────────────────────────────────

def test_prop_i03_no_license_skips_assessment(test_db: Session):
    """
    Player without LFA_FOOTBALL_PLAYER license:
    - EMA delta IS still computed and stored on TournamentParticipation
    - But no FootballSkillAssessment is written (no license to link to)
    """
    player = _player(test_db)
    # Deliberately no UserLicense created for this player
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")

    participation = record_tournament_participation(
        db=test_db,
        user_id=player.id,
        tournament_id=tournament.id,
        placement=1,
        skill_points={},
        base_xp=0,
        credits=0,
    )
    test_db.flush()

    # EMA delta is computed regardless (get_baseline_skills falls back to defaults)
    assert participation.skill_rating_delta is not None

    # No FootballSkillAssessment rows for this player
    count = (
        test_db.query(FootballSkillAssessment)
        .join(UserLicense, FootballSkillAssessment.user_license_id == UserLicense.id)
        .filter(UserLicense.user_id == player.id)
        .count()
    )
    assert count == 0


# ── PROP-I-04: Flag disabled → no assessment ─────────────────────────────────

def test_prop_i04_flag_disabled_skips_assessment(test_db: Session):
    """
    ENABLE_TOURNAMENT_SKILL_PROPAGATION=False:
    - EMA delta is computed and stored (Phase 2 runs independently)
    - But no FootballSkillAssessment is written (update_skill_assessments exits early)
    """
    player = _player(test_db)
    lic = _license(test_db, player)
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = False
        participation = record_tournament_participation(
            db=test_db,
            user_id=player.id,
            tournament_id=tournament.id,
            placement=1,
            skill_points={},
            base_xp=0,
            credits=0,
        )
    test_db.flush()

    # EMA delta still computed (Phase 2 is not guarded by the flag)
    assert participation.skill_rating_delta is not None

    # No assessment written (Phase 3 exited early due to flag)
    count = (
        test_db.query(FootballSkillAssessment)
        .filter(FootballSkillAssessment.user_license_id == lic.id)
        .count()
    )
    assert count == 0


# ── PROP-I-05: End-to-end — Phase 3 → get_skill_profile ──────────────────────

def test_prop_i05_skill_profile_reflects_propagated_delta(test_db: Session):
    """
    PROP-I-05: After Phase 3 runs, get_skill_profile() shows non-zero
    assessment_delta and total_assessments ≥ 1 for the mapped skill.

    This verifies the full pipeline:
      record_tournament_participation (Phase 3 writes FootballSkillAssessment)
        → get_skill_profile (reads assessment rows from DB)
    """
    player = _player(test_db)
    lic = _license(test_db, player)
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")

    record_tournament_participation(
        db=test_db,
        user_id=player.id,
        tournament_id=tournament.id,
        placement=1,
        skill_points={},
        base_xp=0,
        credits=0,
    )
    test_db.flush()

    profile = get_skill_profile(test_db, player.id)

    # After Phase 3, there should be at least 1 assessment written
    assert profile["total_assessments"] >= 1, (
        "get_skill_profile should count the FootballSkillAssessment row written by Phase 3"
    )

    # The mapped skill must show a non-zero assessment_delta
    dribbling = profile["skills"].get("dribbling")
    assert dribbling is not None, "dribbling must be present in the skill profile"
    assert dribbling["assessment_count"] >= 1
    assert dribbling["assessment_delta"] != 0.0, (
        "assessment_delta must reflect the EMA delta written by Phase 3 (not hardcoded 0)"
    )

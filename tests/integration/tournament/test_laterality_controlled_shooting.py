"""
Laterality-controlled shooting trials — LS-01 through LS-08.

Validates that the tournament reward pipeline writes EMA deltas to the correct
lateral bucket (right / left / neutral) in UserLicense.football_skills when
foot_context is driven by a GamePreset.

Scope boundary: _lateral.py, EMA engine, orchestrator, badge logic are NOT
modified by these tests — they exercise the existing pipeline only.

All tests use:
  - laterality_fixtures (session-scoped) — committed seed; visible through test_db
  - test_db (function-scoped SAVEPOINT) — all test-local writes rolled back
"""

import uuid
import pytest
from datetime import date, timedelta
from sqlalchemy.orm import Session

from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.game_configuration import GameConfiguration
from app.models.tournament_achievement import TournamentParticipation, TournamentSkillMapping
from app.models.user import User
from app.models.license import UserLicense
from app.services.tournament.tournament_reward_orchestrator import distribute_rewards_for_user
from app.services.skill_progression._lateral import aggregate_lateral_components


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _load_user(db: Session, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    assert user is not None, f"Control user {email!r} not found — run seed_laterality_test_fixtures"
    return user


def _load_license(db: Session, user_id: int) -> UserLicense:
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    assert lic is not None, f"Active LFA license not found for user_id={user_id}"
    return lic


def _make_tournament(db: Session, preset_id: int, skills: list) -> Semester:
    """Minimal Semester + skill mappings + GameConfiguration for controlled trials."""
    sem = Semester(
        code=f"LAT-S-{uuid.uuid4().hex[:8]}",
        name="Laterality Shooting Trial",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
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


def _distribute(db: Session, user: User, tournament: Semester, placement: int = 1) -> None:
    distribute_rewards_for_user(
        db=db,
        user_id=user.id,
        tournament_id=tournament.id,
        placement=placement,
        total_participants=4,
    )


def _get_lateral_components(db: Session, user_id: int, skill: str) -> dict:
    """Read lateral_components for a skill, refreshing the license first."""
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    db.refresh(lic)
    entry = lic.football_skills.get(skill, {})
    return entry.get("lateral_components", {}) if isinstance(entry, dict) else {}


# ── LS-01: Seed validation ─────────────────────────────────────────────────────

def test_ls01_seed_populated_correctly(test_db: Session, laterality_fixtures):
    """LS-01: All 6 presets and 4 control users are present after seeding."""
    from app.models.game_preset import GamePreset

    expected_presets = [
        "lat_shooting_right", "lat_shooting_left", "lat_shooting_neutral",
        "lat_crossing_right", "lat_crossing_left", "lat_crossing_neutral",
    ]
    for code in expected_presets:
        preset = test_db.query(GamePreset).filter(GamePreset.code == code).first()
        assert preset is not None, f"Preset {code!r} missing from DB"
        assert preset.foot_context in ("right", "left", "neutral"), (
            f"foot_context={preset.foot_context!r} unexpected for {code}"
        )

    expected_emails = [
        "lat.right@lfa-test.com",
        "lat.left@lfa-test.com",
        "lat.balanced@lfa-test.com",
        "lat.unmeasured@lfa-test.com",
    ]
    for email in expected_emails:
        user = test_db.query(User).filter(User.email == email).first()
        assert user is not None, f"Control user {email!r} missing"

        lic = _load_license(test_db, user.id)
        assert lic.football_skills is not None, f"football_skills is None for {email}"
        assert "finishing" in lic.football_skills
        assert "crossing" in lic.football_skills

    # Foot-score spot-checks
    right_user = _load_user(test_db, "lat.right@lfa-test.com")
    lic_r = _load_license(test_db, right_user.id)
    assert lic_r.right_foot_score == 80.0
    assert lic_r.left_foot_score == 20.0

    unmeasured_user = _load_user(test_db, "lat.unmeasured@lfa-test.com")
    lic_u = _load_license(test_db, unmeasured_user.id)
    assert lic_u.right_foot_score is None
    assert lic_u.left_foot_score is None


# ── LS-02: Right preset → right lateral component created ─────────────────────

def test_ls02_right_preset_creates_right_component(test_db: Session, laterality_fixtures):
    """LS-02: foot_context=right preset → finishing.lateral_components['right'] written."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_right"]
    tournament = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])

    _distribute(test_db, user, tournament)

    components = _get_lateral_components(test_db, user.id, "finishing")
    assert "right" in components, (
        f"'right' bucket missing; lateral_components={components}"
    )
    assert components["right"]["tournament_count"] == 1
    assert components["right"]["level"] > 0


# ── LS-03: Left preset → left lateral component created ───────────────────────

def test_ls03_left_preset_creates_left_component(test_db: Session, laterality_fixtures):
    """LS-03: foot_context=left preset → finishing.lateral_components['left'] written."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_left"]
    tournament = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])

    _distribute(test_db, user, tournament)

    components = _get_lateral_components(test_db, user.id, "finishing")
    assert "left" in components, (
        f"'left' bucket missing; lateral_components={components}"
    )
    assert components["left"]["tournament_count"] == 1


# ── LS-04: Neutral preset → neutral lateral component created ─────────────────

def test_ls04_neutral_preset_creates_neutral_component(test_db: Session, laterality_fixtures):
    """LS-04: foot_context=neutral preset → finishing.lateral_components['neutral'] written."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_neutral"]
    tournament = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])

    _distribute(test_db, user, tournament)

    components = _get_lateral_components(test_db, user.id, "finishing")
    assert "neutral" in components, (
        f"'neutral' bucket missing; lateral_components={components}"
    )
    assert components["neutral"]["tournament_count"] == 1


# ── LS-05: foot_context stored in TournamentParticipation ─────────────────────

def test_ls05_foot_context_stored_in_participation(test_db: Session, laterality_fixtures):
    """LS-05: TournamentParticipation.foot_context == preset's foot_context."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_right"]
    tournament = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])

    _distribute(test_db, user, tournament)

    participation = test_db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == user.id,
        TournamentParticipation.semester_id == tournament.id,
    ).first()
    assert participation is not None
    assert participation.foot_context == "right"


# ── LS-06: current_level consistent with aggregate_lateral_components ──────────

def test_ls06_current_level_matches_aggregation(test_db: Session, laterality_fixtures):
    """LS-06: current_level written by orchestrator == aggregate_lateral_components result."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_right"]
    tournament = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])

    _distribute(test_db, user, tournament)

    lic = _load_license(test_db, user.id)
    test_db.refresh(lic)

    entry = lic.football_skills.get("finishing")
    assert isinstance(entry, dict), "finishing entry should be dict after write-back"

    expected = aggregate_lateral_components(entry, lic.right_foot_score, lic.left_foot_score)
    assert entry["current_level"] == pytest.approx(expected, abs=0.1), (
        f"current_level={entry['current_level']} != aggregate={expected}"
    )


# ── LS-07: Cumulative — two sequential right-foot tournaments ──────────────────

def test_ls07_cumulative_two_tournaments_increment_count(test_db: Session, laterality_fixtures):
    """LS-07: Two sequential right-foot tournaments → right.tournament_count == 2."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_right"]

    t1 = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])
    _distribute(test_db, user, t1)

    t2 = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])
    _distribute(test_db, user, t2)

    components = _get_lateral_components(test_db, user.id, "finishing")
    assert "right" in components
    assert components["right"]["tournament_count"] == 2, (
        f"Expected 2 tournaments in right bucket, got {components['right']['tournament_count']}"
    )


# ── LS-08: Idempotency — second call to same tournament doesn't double-write ───

def test_ls08_idempotency_same_tournament_no_double_write(test_db: Session, laterality_fixtures):
    """LS-08: Calling distribute_rewards_for_user twice for the same tournament
    returns the existing summary — lateral_components count remains 1."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_shooting_right"]
    tournament = _make_tournament(test_db, preset_id, ["finishing", "shot_power"])

    _distribute(test_db, user, tournament)
    _distribute(test_db, user, tournament)  # second call — idempotency guard fires

    components = _get_lateral_components(test_db, user.id, "finishing")
    assert "right" in components
    assert components["right"]["tournament_count"] == 1, (
        f"Expected 1 (idempotent), got {components['right']['tournament_count']}"
    )

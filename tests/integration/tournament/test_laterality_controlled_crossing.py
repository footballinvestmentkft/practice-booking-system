"""
Laterality-controlled crossing trials — LC-01 through LC-09.

Mirrors the shooting suite but uses the `crossing` skill group.
Adds cross-skill isolation (LC-06) and player-dominance divergence (LC-07).

Scope boundary: _lateral.py, EMA engine, orchestrator, badge logic unchanged.

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


# ── Shared helpers (identical pattern to test_laterality_controlled_shooting) ─

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
    sem = Semester(
        code=f"LAT-C-{uuid.uuid4().hex[:8]}",
        name="Laterality Crossing Trial",
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


def _get_entry(db: Session, user_id: int, skill: str) -> dict:
    """Return the full football_skills entry for a skill (dict after write-back)."""
    lic = _load_license(db, user_id)
    db.refresh(lic)
    entry = lic.football_skills.get(skill, {})
    return entry if isinstance(entry, dict) else {}


def _get_lateral_components(db: Session, user_id: int, skill: str) -> dict:
    return _get_entry(db, user_id, skill).get("lateral_components", {})


# ── LC-01: Right preset → right component created for crossing ────────────────

def test_lc01_right_preset_creates_right_component(test_db: Session, laterality_fixtures):
    """LC-01: crossing preset foot_context=right → crossing.lateral_components['right']."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_crossing_right"]
    tournament = _make_tournament(test_db, preset_id, ["crossing"])

    _distribute(test_db, user, tournament)

    components = _get_lateral_components(test_db, user.id, "crossing")
    assert "right" in components, f"'right' bucket missing; components={components}"
    assert components["right"]["tournament_count"] == 1


# ── LC-02: Left preset → left component ───────────────────────────────────────

def test_lc02_left_preset_creates_left_component(test_db: Session, laterality_fixtures):
    """LC-02: crossing preset foot_context=left → crossing.lateral_components['left']."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_crossing_left"]
    tournament = _make_tournament(test_db, preset_id, ["crossing"])

    _distribute(test_db, user, tournament)

    components = _get_lateral_components(test_db, user.id, "crossing")
    assert "left" in components, f"'left' bucket missing; components={components}"
    assert components["left"]["tournament_count"] == 1


# ── LC-03: Neutral preset → neutral component ─────────────────────────────────

def test_lc03_neutral_preset_creates_neutral_component(test_db: Session, laterality_fixtures):
    """LC-03: crossing preset foot_context=neutral → crossing.lateral_components['neutral']."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_crossing_neutral"]
    tournament = _make_tournament(test_db, preset_id, ["crossing"])

    _distribute(test_db, user, tournament)

    components = _get_lateral_components(test_db, user.id, "crossing")
    assert "neutral" in components, f"'neutral' bucket missing; components={components}"
    assert components["neutral"]["tournament_count"] == 1


# ── LC-04: Right + neutral → two buckets after two sequential tournaments ──────

def test_lc04_right_then_neutral_builds_two_buckets(test_db: Session, laterality_fixtures):
    """LC-04: right preset tournament then neutral preset tournament →
    crossing has both 'right' and 'neutral' buckets."""
    user = _load_user(test_db, "lat.right@lfa-test.com")

    t_right = _make_tournament(test_db,
                               laterality_fixtures["presets"]["lat_crossing_right"],
                               ["crossing"])
    _distribute(test_db, user, t_right)

    t_neutral = _make_tournament(test_db,
                                 laterality_fixtures["presets"]["lat_crossing_neutral"],
                                 ["crossing"])
    _distribute(test_db, user, t_neutral)

    components = _get_lateral_components(test_db, user.id, "crossing")
    assert "right" in components, "right bucket missing after right-foot tournament"
    assert "neutral" in components, "neutral bucket missing after neutral tournament"
    assert components["right"]["tournament_count"] == 1
    assert components["neutral"]["tournament_count"] == 1


# ── LC-05: Unmeasured user (NULL foot scores) — aggregation falls back ─────────

def test_lc05_unmeasured_user_null_scores_no_crash(test_db: Session, laterality_fixtures):
    """LC-05: User with NULL right/left scores → right component written, no crash.
    aggregate_lateral_components falls back to (R=0.5, L=0.5)."""
    user = _load_user(test_db, "lat.unmeasured@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_crossing_right"]
    tournament = _make_tournament(test_db, preset_id, ["crossing"])

    _distribute(test_db, user, tournament)  # must not raise

    lic = _load_license(test_db, user.id)
    test_db.refresh(lic)
    assert lic.right_foot_score is None  # scores unchanged
    assert lic.left_foot_score is None

    entry = _get_entry(test_db, user.id, "crossing")
    assert isinstance(entry, dict), "entry must be dict after write-back"
    # current_level is set (no crash, aggregation ran with balanced fallback)
    assert "current_level" in entry
    assert entry["current_level"] > 0


# ── LC-06: Cross-skill isolation — crossing tournament delivers zero delta to finishing ─

def test_lc06_crossing_tournament_zero_delta_for_finishing(test_db: Session, laterality_fixtures):
    """LC-06: A crossing-only tournament routes a non-zero EMA delta to crossing
    but a zero delta to finishing.

    The orchestrator iterates ALL skills in the profile, calling
    update_lateral_component with delta=0.0 for unmapped skills.  True isolation
    means the lateral component for an unmapped skill has total_delta == 0.0,
    not that the component is absent.
    """
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_crossing_right"]
    tournament = _make_tournament(test_db, preset_id, ["crossing"])  # crossing only

    _distribute(test_db, user, tournament)

    # Crossing — should have a positive EMA delta in the right bucket
    crossing_components = _get_lateral_components(test_db, user.id, "crossing")
    assert "right" in crossing_components, "right bucket missing for mapped skill (crossing)"
    assert crossing_components["right"]["total_delta"] > 0, (
        f"crossing.total_delta={crossing_components['right']['total_delta']} should be > 0"
    )

    # Finishing — orchestrator writes a zero-delta component (by design: all skills iterated)
    finishing_entry = _get_entry(test_db, user.id, "finishing")
    finishing_lat = finishing_entry.get("lateral_components", {})
    if finishing_lat and "right" in finishing_lat:
        # If the bucket was written, its delta MUST be zero (no contamination)
        assert finishing_lat["right"]["total_delta"] == 0.0, (
            f"finishing.total_delta={finishing_lat['right']['total_delta']} "
            "must be 0.0 for an unmapped skill"
        )


# ── LC-07: Foot dominance changes aggregated current_level ────────────────────

def test_lc07_foot_dominance_changes_aggregation(
    test_db: Session, laterality_fixtures
):
    """LC-07: Both players have identical tournament history (neutral then right).

    Neutral runs first → neutral.level is seeded from 62.0 (low).
    Right runs second  → right.level is seeded from the already-elevated current_level,
    so right.level > neutral.level.

    Aggregation:
      user_r (R=0.8):  weights right more  → higher aggregate
      user_l (R=0.2):  weights right less  → lower aggregate

    This proves foot_score affects aggregated current_level.
    """
    user_r = _load_user(test_db, "lat.right@lfa-test.com")   # R=0.8 L=0.2
    user_l = _load_user(test_db, "lat.left@lfa-test.com")    # R=0.2 L=0.8

    pid_right   = laterality_fixtures["presets"]["lat_crossing_right"]
    pid_neutral = laterality_fixtures["presets"]["lat_crossing_neutral"]

    # Both players: NEUTRAL first (seeds neutral.level from 62.0),
    # then RIGHT (seeds right.level from already-elevated current_level)
    for user in (user_r, user_l):
        t_neutral = _make_tournament(test_db, pid_neutral, ["crossing"])
        _distribute(test_db, user, t_neutral)
        t_right = _make_tournament(test_db, pid_right, ["crossing"])
        _distribute(test_db, user, t_right)

    lic_r = _load_license(test_db, user_r.id)
    lic_l = _load_license(test_db, user_l.id)
    test_db.refresh(lic_r)
    test_db.refresh(lic_l)

    entry_r = lic_r.football_skills.get("crossing", {})
    entry_l = lic_l.football_skills.get("crossing", {})

    assert isinstance(entry_r, dict) and "lateral_components" in entry_r
    assert isinstance(entry_l, dict) and "lateral_components" in entry_l

    comps_r = entry_r["lateral_components"]
    comps_l = entry_l["lateral_components"]
    assert "right" in comps_r and "neutral" in comps_r, f"user_r components: {comps_r.keys()}"
    assert "right" in comps_l and "neutral" in comps_l, f"user_l components: {comps_l.keys()}"

    # Both players have identical bucket levels (same tournament deltas in same order)
    right_level   = comps_r["right"]["level"]
    neutral_level = comps_r["neutral"]["level"]

    # After neutral→right order: right.level > neutral.level
    assert right_level > neutral_level, (
        f"right.level={right_level} should be > neutral.level={neutral_level} "
        "after neutral→right ordering (right seeded from elevated current_level)"
    )

    agg_r = aggregate_lateral_components(entry_r, 80.0, 20.0)  # weights right more
    agg_l = aggregate_lateral_components(entry_l, 20.0, 80.0)  # weights neutral more

    # Right-dominant player weights the higher bucket (right) more → higher aggregate
    assert agg_r > agg_l, (
        f"Right-dominant agg={agg_r} should be > left-dominant agg={agg_l} "
        f"(right.level={right_level} > neutral.level={neutral_level})"
    )


# ── LC-08: current_level consistent with aggregate_lateral_components ──────────

def test_lc08_current_level_matches_aggregation(test_db: Session, laterality_fixtures):
    """LC-08: current_level written by orchestrator == aggregate_lateral_components result."""
    user = _load_user(test_db, "lat.right@lfa-test.com")
    preset_id = laterality_fixtures["presets"]["lat_crossing_right"]
    tournament = _make_tournament(test_db, preset_id, ["crossing"])

    _distribute(test_db, user, tournament)

    lic = _load_license(test_db, user.id)
    test_db.refresh(lic)

    entry = lic.football_skills.get("crossing")
    assert isinstance(entry, dict), "crossing entry should be dict after write-back"

    expected = aggregate_lateral_components(entry, lic.right_foot_score, lic.left_foot_score)
    assert entry["current_level"] == pytest.approx(expected, abs=0.1)


# ── LC-09: Balanced player (50/50) — symmetric aggregation ───────────────────

def test_lc09_balanced_player_symmetric_aggregation(test_db: Session, laterality_fixtures):
    """LC-09: 50/50 player with right preset then left preset has R=L=0.5 in aggregate.
    After both tournaments, current_level = (0.5 * right.level + 0.5 * left.level) / 1.0."""
    user = _load_user(test_db, "lat.balanced@lfa-test.com")   # R=50, L=50

    t_right = _make_tournament(test_db,
                               laterality_fixtures["presets"]["lat_crossing_right"],
                               ["crossing"])
    _distribute(test_db, user, t_right)

    t_left = _make_tournament(test_db,
                              laterality_fixtures["presets"]["lat_crossing_left"],
                              ["crossing"])
    _distribute(test_db, user, t_left)

    lic = _load_license(test_db, user.id)
    test_db.refresh(lic)

    entry = lic.football_skills.get("crossing", {})
    assert isinstance(entry, dict)
    components = entry.get("lateral_components", {})
    assert "right" in components and "left" in components, (
        f"Expected both right and left components, got: {components}"
    )

    r_level = components["right"]["level"]
    l_level = components["left"]["level"]
    expected_agg = (0.5 * r_level + 0.5 * l_level) / 1.0  # equal weights

    assert entry["current_level"] == pytest.approx(expected_agg, abs=0.2), (
        f"current_level={entry['current_level']} != symmetric avg={expected_agg}"
    )

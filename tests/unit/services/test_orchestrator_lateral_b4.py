"""
Orchestrator per-skill lateral write-back — B4.

FC-SKILL-06  Mixed preset (crossing=right, finishing=left, passing→neutral fallback):
             distribute_rewards_for_user calls update_lateral_component with per-skill
             foot_context on the real production code path.

FC-SKILL-07  Lat_* preset (no skill_foot_contexts, foot_context="left"):
             distribute_rewards_for_user calls update_lateral_component with "left" for
             every skill — regression proof that pre-B4 lat_* behaviour is unchanged.

_preset is None fallback:
             When game_preset_id is absent the Semester.game_preset property returns None,
             so _preset is None and _foot_ctx (from participation_record.foot_context) is
             used for every skill.  This preserves the pre-B4 behaviour for tournaments
             that were created without a preset.  Covered implicitly by the guard on the
             new _skill_fc line; no separate test required.

Patch target:
             update_lateral_component is imported INSIDE the function body via
               from app.services.skill_progression import update_lateral_component
             Python resolves that import against app.services.skill_progression.__dict__
             at call-time.  Patching at that path replaces the attribute before the
             function-local import runs, so the mock is what the loop executes.

DB isolation: SAVEPOINT-nested test_db (commits are SAVEPOINT-only; outer tx rolls back).
"""

import uuid
import pytest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.game_preset import GamePreset
from app.models.specialization import SpecializationType
from app.services.tournament.core import create_tournament_semester
from app.services.tournament.tournament_reward_orchestrator import distribute_rewards_for_user
from app.core.security import get_password_hash

_ORC = "app.services.tournament.tournament_reward_orchestrator"
_LAT = "app.services.skill_progression"

# ── Shared test data ──────────────────────────────────────────────────────────

_SKILL_KEYS = ["crossing", "finishing", "passing"]

_SKILLS = {
    sk: {"current_level": 50.0, "dominant_foot": None, "non_dominant_foot": None}
    for sk in _SKILL_KEYS
}

_PROFILE = {
    "skills": {
        "crossing":  {"current_level": 52.0, "tournament_delta": 2.0, "total_delta": 2.0, "tournament_count": 1},
        "finishing": {"current_level": 41.0, "tournament_delta": 1.0, "total_delta": 1.0, "tournament_count": 1},
        "passing":   {"current_level": 61.0, "tournament_delta": 1.0, "total_delta": 1.0, "tournament_count": 1},
    }
}

# Unique deltas per skill — used to correlate call_args to skill_key
_DELTAS = {"crossing": 1.0, "finishing": 0.8, "passing": 0.5}


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _make_preset(db: Session, *, foot_context: str, skill_foot_contexts: dict | None = None) -> GamePreset:
    sc: dict = {
        "foot_context": foot_context,
        "skills_tested": list(_SKILL_KEYS),
        "skill_weights": {"crossing": 0.4, "finishing": 0.4, "passing": 0.2},
        "skill_impact_on_matches": True,
    }
    if skill_foot_contexts:
        sc["skill_foot_contexts"] = skill_foot_contexts
    gp = GamePreset(
        code=f"b4_{uuid.uuid4().hex[:6]}",
        name=f"B4 Preset {uuid.uuid4().hex[:4]}",
        is_active=True,
        game_config={
            "version": "1.0",
            "format_config": {},
            "skill_config": sc,
            "simulation_config": {},
            "metadata": {"game_category": "FOOTBALL", "difficulty_level": None, "min_players": 2},
        },
    )
    db.add(gp)
    db.commit()
    db.refresh(gp)
    return gp


def _make_tournament(db: Session, *, preset_id: int):
    """Create a tournament Semester + GameConfiguration via the production factory."""
    return create_tournament_semester(
        db=db,
        tournament_date=date.today(),
        name=f"B4 Tourn {uuid.uuid4().hex[:6]}",
        specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER,
        format="INDIVIDUAL_RANKING",   # tournament_type_id must be NULL → simple setup
        game_preset_id=preset_id,
    )


def _make_user_with_license(db: Session) -> User:
    uid = uuid.uuid4().hex[:8]
    u = User(
        email=f"b4-{uid}@lfa.com",
        name=f"B4 Player {uid}",
        password_hash=get_password_hash("Test123!"),
        role=UserRole.STUDENT,
        xp_balance=0,
        credit_balance=0,
    )
    db.add(u)
    db.flush()
    lic = UserLicense(
        user_id=u.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        started_at=datetime.now(ZoneInfo("UTC")),
        football_skills=dict(_SKILLS),
        right_foot_score=0.7,
        left_foot_score=0.3,
    )
    db.add(lic)
    db.commit()
    return u


def _fake_participation() -> MagicMock:
    m = MagicMock()
    m.skill_rating_delta = dict(_DELTAS)
    m.foot_context = "neutral"
    m.placement = 1
    return m


def _dummy_badge() -> MagicMock:
    b = MagicMock()
    b.badge_type = "PARTICIPATION"
    b.badge_category = "TOURNAMENT"
    b.title = "Test"
    b.description = "Test"
    b.icon = ""
    b.rarity = "COMMON"
    b.badge_metadata = {}
    return b


def _run_and_capture(db: Session, *, user_id: int, tournament_id: int) -> MagicMock:
    """
    Call the real distribute_rewards_for_user with the service layer mocked.
    Returns the MagicMock for update_lateral_component so call_args can be inspected.
    """
    with (
        patch(f"{_ORC}.participation_service.calculate_skill_points_for_placement",
              return_value=dict(_DELTAS)),
        patch(f"{_ORC}.participation_service.convert_skill_points_to_xp", return_value=100),
        patch(f"{_ORC}.participation_service.record_tournament_participation",
              return_value=_fake_participation()),
        patch(f"{_ORC}.skill_progression_service.get_skill_profile", return_value=_PROFILE),
        patch(f"{_ORC}.badge_service.award_placement_badges", return_value=[]),
        patch(f"{_ORC}.badge_service.award_participation_badge", return_value=_dummy_badge()),
        patch(f"{_ORC}.badge_service.check_and_award_milestone_badges", return_value=[]),
        patch(f"{_LAT}.update_lateral_component",
              side_effect=lambda entry, fc, delta: entry) as mock_ulc,
        patch(f"{_LAT}.aggregate_lateral_components", return_value=50.0),
    ):
        distribute_rewards_for_user(
            db=db,
            user_id=user_id,
            tournament_id=tournament_id,
            placement=1,
            total_participants=5,
            is_sandbox_mode=False,
        )
    return mock_ulc


# ── FC-SKILL-06 ───────────────────────────────────────────────────────────────

class TestFcSkill06PerSkillLateralWriteBack:
    """FC-SKILL-06: real F4b loop passes per-skill foot_context to update_lateral_component."""

    def test_fc_skill_06_mixed_preset_per_skill_fc(self, test_db: Session):
        preset = _make_preset(
            test_db,
            foot_context="neutral",
            skill_foot_contexts={"crossing": "right", "finishing": "left"},
        )
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user = _make_user_with_license(test_db)

        mock_ulc = _run_and_capture(test_db, user_id=user.id, tournament_id=tournament.id)

        assert mock_ulc.call_count == 3, (
            f"Expected 3 update_lateral_component calls (one per skill), got {mock_ulc.call_count}"
        )

        # Correlate each call to its skill_key via the unique delta value.
        # call signature: update_lateral_component(entry, foot_context, delta)
        calls_by_delta = {c.args[2]: c.args[1] for c in mock_ulc.call_args_list}

        assert calls_by_delta[1.0] == "right", (
            f"crossing (delta=1.0) must use foot_context='right', got {calls_by_delta[1.0]!r}"
        )
        assert calls_by_delta[0.8] == "left", (
            f"finishing (delta=0.8) must use foot_context='left', got {calls_by_delta[0.8]!r}"
        )
        assert calls_by_delta[0.5] == "neutral", (
            f"passing (delta=0.5) must fallback to preset foot_context='neutral', got {calls_by_delta[0.5]!r}"
        )


# ── FC-SKILL-07 ───────────────────────────────────────────────────────────────

class TestFcSkill07LatPresetRegression:
    """FC-SKILL-07: lat_* preset (no overrides) — all skills receive the preset foot_context."""

    def test_fc_skill_07_lat_preset_all_skills_same_fc(self, test_db: Session):
        preset = _make_preset(test_db, foot_context="left")  # no skill_foot_contexts
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user = _make_user_with_license(test_db)

        mock_ulc = _run_and_capture(test_db, user_id=user.id, tournament_id=tournament.id)

        assert mock_ulc.call_count == 3, (
            f"Expected 3 calls, got {mock_ulc.call_count}"
        )

        actual_fcs = {c.args[1] for c in mock_ulc.call_args_list}
        assert actual_fcs == {"left"}, (
            f"All lat_* skills must use preset foot_context='left', got {actual_fcs!r}"
        )

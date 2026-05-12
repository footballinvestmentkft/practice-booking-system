"""
Orchestrator laterality domain guard (Option B).

Tests that the F4b loop correctly gates foot-lateral bucket writes based on
SKILL_LATERALITY — only foot-domain skills call update_lateral_component /
aggregate_lateral_components.  hand and none skills preserve the EMA
current_level directly.  Unknown skill keys trigger a warning and no bucket.

LD-GUARD-01  foot skill (crossing) → update_lateral_component IS called
LD-GUARD-02  foot skill (crossing) → aggregate_lateral_components IS called
LD-GUARD-03  throwing (hand)       → update_lateral_component NOT called
LD-GUARD-04  throwing (hand)       → aggregate_lateral_components NOT called
LD-GUARD-05  throwing current_level → EMA value preserved (not lateral-aggregated)
LD-GUARD-06  stamina (none)        → update_lateral_component NOT called
LD-GUARD-07  stamina current_level → EMA value preserved (not lateral-aggregated)
LD-GUARD-08  unknown skill key     → no lateral call; logger.warning emitted

Patch strategy (same as test_orchestrator_lateral_b4.py):
  _LAT = "app.services.skill_progression" — module where lateral fns live
  _ORC = "app.services.tournament.tournament_reward_orchestrator"

DB isolation: SAVEPOINT-nested test_db (commits are SAVEPOINT-only).
"""

import logging
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

# Skill keys under test — one per domain, plus one unknown
_FOOT_KEY    = "crossing"             # laterality_domain = "foot"
_HAND_KEY    = "throwing"             # laterality_domain = "hand"
_NONE_KEY    = "stamina"              # laterality_domain = "none"
_UNKNOWN_KEY = "unknown_fake_skill"   # not in SKILL_LATERALITY → warning path

# EMA-derived values returned by the mocked skill profile
_EMA_FOOT    = 55.0
_EMA_HAND    = 62.0
_EMA_NONE    = 70.0
_EMA_UNKNOWN = 48.0

_DELTAS = {
    _FOOT_KEY:    1.0,
    _HAND_KEY:    0.5,
    _NONE_KEY:    0.3,
    _UNKNOWN_KEY: 0.2,
}

# Mock profile returned by skill_progression_service.get_skill_profile
_PROFILE = {
    "skills": {
        _FOOT_KEY:    {"current_level": _EMA_FOOT,    "tournament_delta": 1.0, "total_delta": 1.0, "tournament_count": 1},
        _HAND_KEY:    {"current_level": _EMA_HAND,    "tournament_delta": 0.5, "total_delta": 0.5, "tournament_count": 1},
        _NONE_KEY:    {"current_level": _EMA_NONE,    "tournament_delta": 0.3, "total_delta": 0.3, "tournament_count": 1},
        _UNKNOWN_KEY: {"current_level": _EMA_UNKNOWN, "tournament_delta": 0.2, "total_delta": 0.2, "tournament_count": 1},
    }
}

# Initial football_skills on the license.
# throwing carries a stale neutral bucket (from before the domain guard existed)
# to verify it is not extended by new tournament runs.
_INITIAL_SKILLS = {
    _FOOT_KEY:    {"current_level": 50.0},
    _HAND_KEY:    {
        "current_level": 61.5,
        "lateral_components": {
            "neutral": {
                "level": 60.0,
                "total_delta": -1.5,
                "tournament_count": 2,
                "last_delta": -0.5,
            }
        },
    },
    _NONE_KEY:    {"current_level": 69.7},
    _UNKNOWN_KEY: {"current_level": 47.8},
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_preset(db: Session) -> GamePreset:
    gp = GamePreset(
        code=f"ldg_{uuid.uuid4().hex[:6]}",
        name=f"LDGuard {uuid.uuid4().hex[:4]}",
        is_active=True,
        game_config={
            "version": "1.0",
            "format_config": {},
            "skill_config": {
                "foot_context": "neutral",
                "skills_tested":    [_FOOT_KEY, _HAND_KEY, _NONE_KEY, _UNKNOWN_KEY],
                "skill_weights":    {_FOOT_KEY: 0.4, _HAND_KEY: 0.3, _NONE_KEY: 0.2, _UNKNOWN_KEY: 0.1},
                "skill_impact_on_matches": True,
            },
            "simulation_config": {},
            "metadata": {"game_category": "FOOTBALL", "difficulty_level": None, "min_players": 2},
        },
    )
    db.add(gp)
    db.commit()
    db.refresh(gp)
    return gp


def _make_tournament(db: Session, *, preset_id: int):
    return create_tournament_semester(
        db=db,
        tournament_date=date.today(),
        name=f"LDG {uuid.uuid4().hex[:6]}",
        specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER,
        format="INDIVIDUAL_RANKING",
        game_preset_id=preset_id,
    )


def _make_user_with_license(db: Session) -> tuple[User, UserLicense]:
    uid = uuid.uuid4().hex[:8]
    u = User(
        email=f"ldg-{uid}@lfa.com",
        name=f"LDGuard {uid}",
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
        football_skills=dict(_INITIAL_SKILLS),
        right_foot_score=0.7,
        left_foot_score=0.3,
    )
    db.add(lic)
    db.commit()
    return u, lic


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


def _run(db: Session, *, user_id: int, tournament_id: int):
    """
    Run distribute_rewards_for_user with the service layer mocked.
    Returns (mock_ulc, mock_agg) for call inspection.
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
        patch(f"{_LAT}.aggregate_lateral_components", return_value=50.0) as mock_agg,
    ):
        distribute_rewards_for_user(
            db=db,
            user_id=user_id,
            tournament_id=tournament_id,
            placement=1,
            total_participants=5,
            is_sandbox_mode=False,
        )
    return mock_ulc, mock_agg


def _reload(db: Session, lic: UserLicense) -> UserLicense:
    """Force a fresh load of the license from the DB (post-SAVEPOINT commit)."""
    db.expire(lic)
    return db.query(UserLicense).filter(UserLicense.id == lic.id).first()


# ── LD-GUARD-01 / 02 — foot skill ────────────────────────────────────────────

class TestLateralDomainGuardFoot:
    """crossing (foot) must go through the full lateral update path."""

    def test_foot_skill_calls_update_lateral_component(self, test_db: Session):
        """LD-GUARD-01."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, _    = _make_user_with_license(test_db)

        mock_ulc, _ = _run(test_db, user_id=user.id, tournament_id=tournament.id)

        foot_calls = [c for c in mock_ulc.call_args_list if c.args[2] == _DELTAS[_FOOT_KEY]]
        assert foot_calls, (
            f"update_lateral_component was not called for '{_FOOT_KEY}' (foot skill). "
            f"Total calls: {mock_ulc.call_count}"
        )

    def test_foot_skill_calls_aggregate_lateral_components(self, test_db: Session):
        """LD-GUARD-02."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, _    = _make_user_with_license(test_db)

        _, mock_agg = _run(test_db, user_id=user.id, tournament_id=tournament.id)

        assert mock_agg.call_count >= 1, (
            f"aggregate_lateral_components was never called "
            f"(expected at least 1 call for foot skill '{_FOOT_KEY}')"
        )


# ── LD-GUARD-03 / 04 / 05 — hand skill (throwing) ────────────────────────────

class TestLateralDomainGuardHand:
    """throwing (hand) must skip foot-lateral tracking entirely."""

    def test_throwing_skips_update_lateral_component(self, test_db: Session):
        """LD-GUARD-03."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, _    = _make_user_with_license(test_db)

        mock_ulc, _ = _run(test_db, user_id=user.id, tournament_id=tournament.id)

        throwing_calls = [c for c in mock_ulc.call_args_list if c.args[2] == _DELTAS[_HAND_KEY]]
        assert not throwing_calls, (
            f"update_lateral_component was called for '{_HAND_KEY}' (hand skill) — "
            f"must not create foot buckets. Calls: {throwing_calls}"
        )

    def test_throwing_skips_aggregate_lateral_components(self, test_db: Session):
        """LD-GUARD-04: aggregate is only called once per foot skill, not for throwing."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, lic  = _make_user_with_license(test_db)

        _, mock_agg = _run(test_db, user_id=user.id, tournament_id=tournament.id)

        # Only 1 foot skill in the set (_FOOT_KEY = crossing); aggregate called once.
        # If throwing were aggregated, call_count would be 2.
        assert mock_agg.call_count == 1, (
            f"aggregate_lateral_components call_count={mock_agg.call_count}; "
            f"expected 1 (only for '{_FOOT_KEY}'). "
            f"throwing must not trigger aggregation."
        )

    def test_throwing_current_level_from_ema(self, test_db: Session):
        """LD-GUARD-05: throwing.current_level is the EMA value, not lateral-aggregated."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, lic  = _make_user_with_license(test_db)

        _run(test_db, user_id=user.id, tournament_id=tournament.id)

        updated = _reload(test_db, lic)
        actual  = updated.football_skills[_HAND_KEY]["current_level"]
        assert actual == _EMA_HAND, (
            f"throwing current_level={actual!r}; expected EMA value {_EMA_HAND!r}. "
            f"lateral aggregation must NOT run for hand skills."
        )

    def test_throwing_stale_neutral_bucket_not_extended(self, test_db: Session):
        """Stale neutral bucket in throwing.lateral_components must not gain new entries."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, lic  = _make_user_with_license(test_db)

        _run(test_db, user_id=user.id, tournament_id=tournament.id)

        updated = _reload(test_db, lic)
        bucket  = updated.football_skills[_HAND_KEY].get("lateral_components", {}).get("neutral", {})
        assert bucket.get("tournament_count") == 2, (
            f"Stale neutral bucket tournament_count changed from 2 to "
            f"{bucket.get('tournament_count')!r} — bucket must remain frozen."
        )


# ── LD-GUARD-06 / 07 — none skill (stamina) ───────────────────────────────────

class TestLateralDomainGuardNone:
    """stamina (none) must skip foot-lateral tracking entirely."""

    def test_none_skill_skips_update_lateral_component(self, test_db: Session):
        """LD-GUARD-06."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, _    = _make_user_with_license(test_db)

        mock_ulc, _ = _run(test_db, user_id=user.id, tournament_id=tournament.id)

        stamina_calls = [c for c in mock_ulc.call_args_list if c.args[2] == _DELTAS[_NONE_KEY]]
        assert not stamina_calls, (
            f"update_lateral_component was called for '{_NONE_KEY}' (none skill) — "
            f"must not create foot buckets. Calls: {stamina_calls}"
        )

    def test_none_skill_current_level_from_ema(self, test_db: Session):
        """LD-GUARD-07: stamina.current_level is the EMA value, not lateral-aggregated."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, lic  = _make_user_with_license(test_db)

        _run(test_db, user_id=user.id, tournament_id=tournament.id)

        updated = _reload(test_db, lic)
        actual  = updated.football_skills[_NONE_KEY]["current_level"]
        assert actual == _EMA_NONE, (
            f"stamina current_level={actual!r}; expected EMA value {_EMA_NONE!r}. "
            f"lateral aggregation must NOT run for none skills."
        )


# ── LD-GUARD-08 — unknown skill key ───────────────────────────────────────────

class TestLateralDomainGuardUnknown:
    """Skills absent from SKILL_LATERALITY must not create foot buckets and must log a warning."""

    def test_unknown_skill_no_lateral_call_and_warning_logged(self, test_db: Session, caplog):
        """LD-GUARD-08."""
        preset     = _make_preset(test_db)
        tournament = _make_tournament(test_db, preset_id=preset.id)
        user, _    = _make_user_with_license(test_db)

        with caplog.at_level(
            logging.WARNING,
            logger="app.services.tournament.tournament_reward_orchestrator",
        ):
            mock_ulc, mock_agg = _run(test_db, user_id=user.id, tournament_id=tournament.id)

        unknown_lateral_calls = [
            c for c in mock_ulc.call_args_list if c.args[2] == _DELTAS[_UNKNOWN_KEY]
        ]
        assert not unknown_lateral_calls, (
            f"update_lateral_component was called for unknown key '{_UNKNOWN_KEY}'. "
            f"Must not silently create a foot bucket for unknown skills."
        )

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(_UNKNOWN_KEY in msg for msg in warning_messages), (
            f"Expected a WARNING mentioning '{_UNKNOWN_KEY}' for unknown laterality_domain. "
            f"Warnings emitted: {warning_messages}"
        )

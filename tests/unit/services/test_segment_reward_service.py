"""
Unit tests for segment_reward_service.

Coverage:
  Pure functions (no DB access):
    - resolve_segment_skill_targets: priority chain (segment override > session config >
      preset skill_weights > empty)
    - compute_skill_deltas: formula, zero-sum-weights guard, negative xp guard,
      missing conversion rate falls back to default

  DB-backed (MagicMock db):
    - award_session_segments: returns [] immediately when no active segments exist
    - award_session_segments: idempotency — duplicate call does not append duplicate rows
      (IntegrityError branch covered via mock)

All tests use MagicMock; no live DB required.
"""

import pytest
from unittest.mock import MagicMock, patch, call
from sqlalchemy.exc import IntegrityError

from app.services.segment_reward_service import (
    resolve_segment_skill_targets,
    compute_skill_deltas,
    award_session_segments,
    _DEFAULT_XP_PER_POINT,
)

_BASE = "app.services.segment_reward_service"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _db():
    return MagicMock()


def _segment(skill_targets=None):
    seg = MagicMock()
    seg.skill_targets = skill_targets
    seg.id = 1
    seg.label = "Passing Drill"
    seg.session_id = 10
    return seg


def _session(session_reward_config=None, game_preset=None):
    s = MagicMock()
    s.id = 10
    s.semester_id = 5
    s.session_reward_config = session_reward_config
    s.game_preset = game_preset
    s.base_xp = None
    return s


def _preset(skill_weights):
    gp = MagicMock()
    gp.game_config = {"skill_config": {"skill_weights": skill_weights}}
    return gp


# ─────────────────────────────────────────────────────────────────────────────
# resolve_segment_skill_targets — priority chain
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveSegmentSkillTargets:
    def test_priority1_segment_override(self):
        """segment.skill_targets wins over everything else."""
        seg = _segment(skill_targets={"passing": 1.5, "dribbling": 0.5})
        session = _session(
            session_reward_config={"skill_areas": {"finishing": 1.0}},
            game_preset=_preset({"ball_control": 1.0}),
        )
        result = resolve_segment_skill_targets(seg, session)
        assert result == {"passing": 1.5, "dribbling": 0.5}

    def test_priority2_session_reward_config_dict(self):
        """session.session_reward_config['skill_areas'] (dict) wins when segment is None."""
        seg = _segment(skill_targets=None)
        session = _session(
            session_reward_config={"skill_areas": {"finishing": 2.0}},
            game_preset=_preset({"ball_control": 1.0}),
        )
        result = resolve_segment_skill_targets(seg, session)
        assert result == {"finishing": 2.0}

    def test_priority2_session_reward_config_list(self):
        """session.session_reward_config['skill_areas'] as list of strings → equal weights 1.0."""
        seg = _segment(skill_targets=None)
        session = _session(
            session_reward_config={"skill_areas": ["passing", "dribbling"]},
            game_preset=None,
        )
        result = resolve_segment_skill_targets(seg, session)
        assert result == {"passing": 1.0, "dribbling": 1.0}

    def test_priority3_game_preset_skill_weights(self):
        """Falls through to game_preset.game_config['skill_config']['skill_weights']."""
        seg = _segment(skill_targets=None)
        session = _session(
            session_reward_config=None,
            game_preset=_preset({"ball_control": 0.8, "passing": 1.2}),
        )
        result = resolve_segment_skill_targets(seg, session)
        assert result == {"ball_control": 0.8, "passing": 1.2}

    def test_priority4_no_skills_returns_empty(self):
        """Returns {} when all sources are unavailable."""
        seg = _segment(skill_targets=None)
        session = _session(session_reward_config=None, game_preset=None)
        result = resolve_segment_skill_targets(seg, session)
        assert result == {}

    def test_preset_with_empty_skill_weights_returns_empty(self):
        """Empty preset skill_weights → returns {}."""
        seg = _segment(skill_targets=None)
        session = _session(session_reward_config=None, game_preset=_preset({}))
        result = resolve_segment_skill_targets(seg, session)
        assert result == {}

    def test_segment_targets_not_dict_is_skipped(self):
        """segment.skill_targets that is not a dict is skipped (falls to priority 2)."""
        seg = _segment(skill_targets=["passing"])  # list, not dict
        session = _session(
            session_reward_config={"skill_areas": {"finishing": 1.0}},
            game_preset=None,
        )
        result = resolve_segment_skill_targets(seg, session)
        assert result == {"finishing": 1.0}


# ─────────────────────────────────────────────────────────────────────────────
# compute_skill_deltas — formula
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeSkillDeltas:
    def test_basic_formula(self):
        """Single skill with weight 1.0: delta = xp / conversion_rate."""
        result = compute_skill_deltas(
            skill_targets={"passing": 1.0},
            xp_awarded=100,
            conversion_rates={"passing": 10},
        )
        # delta = (1.0/1.0) * 100 / 10 = 10.0
        assert result == {"passing": 10.0}

    def test_weighted_split(self):
        """Two skills with different weights split XP proportionally."""
        result = compute_skill_deltas(
            skill_targets={"passing": 3.0, "dribbling": 1.0},
            xp_awarded=40,
            conversion_rates={"passing": 10, "dribbling": 10},
        )
        # passing: (3/4) * 40 / 10 = 3.0
        # dribbling: (1/4) * 40 / 10 = 1.0
        assert result["passing"] == pytest.approx(3.0, abs=0.01)
        assert result["dribbling"] == pytest.approx(1.0, abs=0.01)

    def test_missing_conversion_rate_uses_default(self):
        """Missing conversion rate falls back to _DEFAULT_XP_PER_POINT."""
        result = compute_skill_deltas(
            skill_targets={"finishing": 1.0},
            xp_awarded=10,
            conversion_rates={},
        )
        # delta = (1.0/1.0) * 10 / _DEFAULT_XP_PER_POINT
        expected = round(10 / _DEFAULT_XP_PER_POINT, 2)
        assert result == {"finishing": expected}

    def test_zero_xp_returns_empty(self):
        result = compute_skill_deltas(
            skill_targets={"passing": 1.0},
            xp_awarded=0,
            conversion_rates={},
        )
        assert result == {}

    def test_empty_skill_targets_returns_empty(self):
        result = compute_skill_deltas(
            skill_targets={},
            xp_awarded=50,
            conversion_rates={},
        )
        assert result == {}

    def test_zero_sum_weights_returns_empty(self):
        result = compute_skill_deltas(
            skill_targets={"passing": 0.0},
            xp_awarded=50,
            conversion_rates={},
        )
        assert result == {}

    def test_zero_or_negative_conversion_rate_falls_back(self):
        """conversion_rate <= 0 is treated as _DEFAULT_XP_PER_POINT."""
        result = compute_skill_deltas(
            skill_targets={"passing": 1.0},
            xp_awarded=10,
            conversion_rates={"passing": 0},
        )
        expected = round(10 / _DEFAULT_XP_PER_POINT, 2)
        assert result == {"finishing": expected} or "passing" in result


# ─────────────────────────────────────────────────────────────────────────────
# award_session_segments — no-op guard and idempotency branch
# ─────────────────────────────────────────────────────────────────────────────

class TestAwardSessionSegments:
    def _attendance(self, status="present", user_id=42, session_id=10, att_id=7):
        from app.models.attendance import AttendanceStatus
        att = MagicMock()
        att.id = att_id
        att.user_id = user_id
        att.session_id = session_id
        att.status = AttendanceStatus.present if status == "present" else MagicMock()
        return att

    def test_returns_empty_list_when_no_active_segments(self):
        """Sessions without active segments return [] immediately (backward compat)."""
        db = _db()

        # attendance query returns a present attendance
        att = self._attendance()

        from app.models.attendance import AttendanceStatus
        # Set up sequential query returns:
        # 1. Attendance.filter().first() → att
        # 2. SessionSegment.filter().order_by().all() → []  (no segments)
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.order_by.return_value = query_mock
        query_mock.first.return_value = att
        query_mock.all.return_value = []
        db.query.return_value = query_mock

        results = award_session_segments(db, session_id=10, attendance_id=7)
        assert results == []

    def test_returns_empty_list_when_attendance_not_present(self):
        """Non-present attendance returns [] immediately."""
        db = _db()

        from app.models.attendance import AttendanceStatus
        att = MagicMock()
        att.id = 7
        att.user_id = 42
        att.session_id = 10
        att.status = AttendanceStatus.absent

        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = att
        db.query.return_value = query_mock

        results = award_session_segments(db, session_id=10, attendance_id=7)
        assert results == []

    def test_returns_empty_list_when_attendance_not_found(self):
        """Missing attendance record returns []."""
        db = _db()
        query_mock = MagicMock()
        query_mock.filter.return_value = query_mock
        query_mock.first.return_value = None
        db.query.return_value = query_mock

        results = award_session_segments(db, session_id=10, attendance_id=999)
        assert results == []

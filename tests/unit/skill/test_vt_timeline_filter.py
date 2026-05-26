"""
SKILL-TL-01  xp_awarded=0 + non-empty skill_deltas → appears in timeline
SKILL-TL-02  xp_awarded=0 + empty skill_deltas → NOT in timeline
SKILL-TL-03  xp_awarded>0 + non-empty skill_deltas → appears (existing behaviour)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

_VIEWS = "app.services.skill_progression._views"
_NOW = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_attempt(
    *,
    aid: int = 1,
    user_id: int = 42,
    xp_awarded: int = 0,
    skill_deltas: dict | None = None,
    started_at: datetime | None = None,
) -> MagicMock:
    a = MagicMock()
    a.id               = aid
    a.user_id          = user_id
    a.is_valid         = True
    a.xp_awarded       = xp_awarded
    a.skill_deltas     = skill_deltas if skill_deltas is not None else {}
    a.started_at       = started_at or _NOW
    a.score_normalized = 55.0
    a.attempt_index_today = 1
    a.stimuli_count    = 36
    a.correct_count    = 30
    a.wrong_click_count = 6
    a.error_count      = 6
    a.avg_reaction_ms  = 400.0
    a.min_reaction_ms  = 220.0
    a.duration_seconds = 90

    game = MagicMock()
    game.code = "memory_sequence"
    game.name = "Memory Sequence"
    a.game = game

    return a


def _make_db(attempts: list) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = attempts
    return db


def _call(db, user_id=42, skill_key="reactions"):
    from app.services.skill_progression._views import _collect_vt_timeline_events
    return _collect_vt_timeline_events(db, user_id, skill_key)


# ══════════════════════════════════════════════════════════════════════════════
# SKILL-TL-01  challenge cap-bypass attempt (xp=0, skill_deltas non-empty)
# ══════════════════════════════════════════════════════════════════════════════

class TestVtTimelineFilter:

    def test_tl01_zero_xp_nonempty_deltas_included(self):
        """Virtual Challenge attempt with xp_awarded=0 but skill_deltas must appear."""
        attempt = _mock_attempt(
            xp_awarded=0,
            skill_deltas={"reactions": 0.09, "anticipation": -0.14},
        )
        db = _make_db([attempt])

        events = _call(db)

        assert len(events) == 1
        assert events[0]["_vt_delta"] == pytest.approx(0.09)
        assert events[0]["event_type"] == "virtual_training"
        assert events[0]["xp_awarded"] == 0

    # ── SKILL-TL-02 ──────────────────────────────────────────────────────────

    def test_tl02_zero_xp_empty_deltas_excluded(self):
        """Solo attempt hitting daily cap: xp_awarded=0, skill_deltas={} → not shown."""
        attempt = _mock_attempt(
            xp_awarded=0,
            skill_deltas={},
        )
        db = _make_db([attempt])

        events = _call(db)

        # The Python-level filter skips because skill_key not in {}
        assert events == []

    def test_tl02_zero_xp_delta_for_different_skill_excluded(self):
        """Cap-bypass with deltas for a different skill → not in this skill's timeline."""
        attempt = _mock_attempt(
            xp_awarded=0,
            skill_deltas={"decision_making": 0.05},  # not "reactions"
        )
        db = _make_db([attempt])

        events = _call(db, skill_key="reactions")

        assert events == []

    # ── SKILL-TL-03 ──────────────────────────────────────────────────────────

    def test_tl03_positive_xp_nonempty_deltas_included(self):
        """Standard attempt (xp_awarded>0) still appears — existing behaviour unchanged."""
        attempt = _mock_attempt(
            xp_awarded=12,
            skill_deltas={"reactions": 0.05},
        )
        db = _make_db([attempt])

        events = _call(db)

        assert len(events) == 1
        assert events[0]["_vt_delta"] == pytest.approx(0.05)
        assert events[0]["xp_awarded"] == 12

    def test_tl03_delta_value_stored_as_float(self):
        """_vt_delta is cast to float regardless of stored type."""
        attempt = _mock_attempt(
            xp_awarded=8,
            skill_deltas={"reactions": "0.07"},  # string in JSONB
        )
        db = _make_db([attempt])

        events = _call(db)

        assert len(events) == 1
        assert isinstance(events[0]["_vt_delta"], float)
        assert events[0]["_vt_delta"] == pytest.approx(0.07)

    def test_tl01_event_fields_populated(self):
        """Timeline event dict has all required fields for skill_history.html."""
        attempt = _mock_attempt(
            xp_awarded=0,
            skill_deltas={"reactions": 0.09},
        )
        db = _make_db([attempt])

        events = _call(db)
        ev = events[0]

        assert ev["event_type"]      == "virtual_training"
        assert ev["game_code"]       == "memory_sequence"
        assert ev["attempt_id"]      == attempt.id
        assert ev["score_normalized"] == attempt.score_normalized
        assert ev["xp_awarded"]      == 0
        assert "event_name" in ev
        assert "achieved_at" in ev

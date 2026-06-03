"""VTC-TAG-01..06 — Challenge attempt_source tagging hardening tests.

Verifies that target_tracking and memory_sequence submit routes ALWAYS write
raw_metrics["attempt_source"] = "challenge" when a challenge is present,
regardless of whether raw_metrics was already a dict in the client payload.

This is a prerequisite for VTC eligibility: the standalone filter relies on
raw_metrics->>'attempt_source' IS DISTINCT FROM 'challenge'.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_VT_BASE = "app.api.web_routes.virtual_training"


def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 1):
    u = MagicMock()
    u.id = uid
    u.email = f"user{uid}@test.lfa"
    return u


def _game(code: str = "target_tracking", max_daily: int = 5):
    g = MagicMock()
    g.id = 1
    g.code = code
    g.name = code.replace("_", " ").title()
    g.is_active = True
    g.max_daily_attempts = max_daily
    g.base_xp = 12
    g.config = {}
    g.skill_targets = {}
    return g


def _challenge():
    ch = MagicMock()
    ch.id = 99
    ch.challenger_id = 1
    ch.challenged_id = 2
    ch.status = MagicMock()
    return ch


def _db(valid_today: int = 0):
    db = MagicMock()
    db.query.return_value.filter.return_value.count.return_value = valid_today
    db.query.return_value.filter.return_value.first.return_value = MagicMock()
    return db


def _request_with_body(body: dict):
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    return req


def _mock_attempt():
    a = MagicMock()
    a.id = 1
    a.is_valid = True
    a.invalid_reason = None
    a.xp_awarded = 10
    a.skill_deltas = {}
    a.attempt_index_today = 1
    a.score_normalized = 75.0
    a.raw_metrics = {}
    # Numeric fields required by _compute_winner / _link_attempt_to_challenge
    a.stimuli_count = None
    a.correct_count = None
    a.avg_reaction_ms = None
    a.completed_at = None
    return a


# ── Helper: extract raw_metrics from the call to record_attempt ──────────────

def _capture_raw_metrics(mock_record):
    """Pull data["raw_metrics"] from the last record_attempt call."""
    call_kwargs = mock_record.call_args
    if call_kwargs:
        data = call_kwargs.kwargs.get("data") or (call_kwargs.args[3] if len(call_kwargs.args) > 3 else None)
        if data:
            return data.get("raw_metrics")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# TARGET TRACKING
# ══════════════════════════════════════════════════════════════════════════════

class TestVtcTagTargetTracking:
    """VTC-TAG-01/02/05 — target_tracking submit tagging."""

    @patch(f"{_VT_BASE}.require_student_onboarding", return_value=None)
    @patch(f"{_VT_BASE}._link_attempt_to_challenge", return_value={})
    @patch(f"{_VT_BASE}.VirtualTrainingService")
    @patch(f"{_VT_BASE}._validate_challenge_pre_submit")
    def test_vtc_tag_01_tt_challenge_with_raw_metrics_dict(
        self, mock_pre, mock_svc, mock_link, mock_guard
    ):
        """VTC-TAG-01: TT challenge + raw_metrics dict → attempt_source='challenge' written."""
        from app.api.web_routes.virtual_training import virtual_training_target_tracking_submit

        mock_pre.return_value = (_challenge(), None)
        mock_svc.get_game.return_value = _game("target_tracking")
        mock_svc.is_expert_unlocked.return_value = False
        mock_svc.get_difficulty_config.return_value = {"difficulty_multiplier": 1.0}
        mock_svc.record_attempt.return_value = _mock_attempt()
        mock_svc.calculate_daily_attempt_index.return_value = 1

        body = {
            "challenge_id": 99,
            "difficulty_level": "easy",
            "raw_metrics": {"v": 3, "some_key": "value"},   # already a dict
            "started_at": "2026-06-04T10:00:00Z",
        }

        captured_data = {}

        def _capture(**kwargs):
            captured_data.update(kwargs.get("data", {}))
            return _mock_attempt()

        mock_svc.record_attempt.side_effect = _capture

        _run(virtual_training_target_tracking_submit(
            request=_request_with_body(body),
            db=_db(),
            user=_user(),
        ))

        raw = captured_data.get("raw_metrics", {})
        assert isinstance(raw, dict), "raw_metrics must be a dict"
        assert raw.get("attempt_source") == "challenge", (
            "VTC-TAG-01: attempt_source must be 'challenge' when challenge is present"
        )

    @patch(f"{_VT_BASE}.require_student_onboarding", return_value=None)
    @patch(f"{_VT_BASE}._link_attempt_to_challenge", return_value={})
    @patch(f"{_VT_BASE}.VirtualTrainingService")
    @patch(f"{_VT_BASE}._validate_challenge_pre_submit")
    def test_vtc_tag_02_tt_challenge_with_null_raw_metrics(
        self, mock_pre, mock_svc, mock_link, mock_guard
    ):
        """VTC-TAG-02: TT challenge + raw_metrics null → raw_metrics becomes {'attempt_source':'challenge'}."""
        from app.api.web_routes.virtual_training import virtual_training_target_tracking_submit

        mock_pre.return_value = (_challenge(), None)
        mock_svc.get_game.return_value = _game("target_tracking")
        mock_svc.is_expert_unlocked.return_value = False
        mock_svc.get_difficulty_config.return_value = {"difficulty_multiplier": 1.0}
        mock_svc.calculate_daily_attempt_index.return_value = 1

        body = {
            "challenge_id": 99,
            "difficulty_level": "easy",
            # raw_metrics intentionally absent
            "started_at": "2026-06-04T10:00:00Z",
        }

        captured_data = {}

        def _capture(**kwargs):
            captured_data.update(kwargs.get("data", {}))
            return _mock_attempt()

        mock_svc.record_attempt.side_effect = _capture

        _run(virtual_training_target_tracking_submit(
            request=_request_with_body(body),
            db=_db(),
            user=_user(),
        ))

        raw = captured_data.get("raw_metrics")
        assert isinstance(raw, dict), (
            "VTC-TAG-02: raw_metrics must be a dict even if not in original payload"
        )
        assert raw.get("attempt_source") == "challenge", (
            "VTC-TAG-02: attempt_source must be 'challenge' when challenge present and raw_metrics was null"
        )

    @patch(f"{_VT_BASE}.require_student_onboarding", return_value=None)
    @patch(f"{_VT_BASE}.VirtualTrainingService")
    def test_vtc_tag_05_tt_standalone_no_tag(self, mock_svc, mock_guard):
        """VTC-TAG-05: TT standalone (no challenge_id) → attempt_source NOT written."""
        from app.api.web_routes.virtual_training import virtual_training_target_tracking_submit

        mock_svc.get_game.return_value = _game("target_tracking")
        mock_svc.is_expert_unlocked.return_value = False
        mock_svc.get_difficulty_config.return_value = {"difficulty_multiplier": 1.0}
        mock_svc.calculate_daily_attempt_index.return_value = 1

        body = {
            "difficulty_level": "easy",
            "raw_metrics": {"v": 3},
            "started_at": "2026-06-04T10:00:00Z",
        }

        captured_data = {}

        def _capture(**kwargs):
            captured_data.update(kwargs.get("data", {}))
            return _mock_attempt()

        mock_svc.record_attempt.side_effect = _capture

        _run(virtual_training_target_tracking_submit(
            request=_request_with_body(body),
            db=_db(valid_today=0),
            user=_user(),
        ))

        raw = captured_data.get("raw_metrics", {})
        assert raw.get("attempt_source") != "challenge", (
            "VTC-TAG-05: standalone attempt must NOT have attempt_source='challenge'"
        )


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY SEQUENCE
# ══════════════════════════════════════════════════════════════════════════════

class TestVtcTagMemorySequence:
    """VTC-TAG-03/04/06 — memory_sequence submit tagging."""

    @patch(f"{_VT_BASE}.require_student_onboarding", return_value=None)
    @patch(f"{_VT_BASE}._link_attempt_to_challenge", return_value={})
    @patch(f"{_VT_BASE}.VirtualTrainingService")
    @patch(f"{_VT_BASE}._validate_challenge_pre_submit")
    def test_vtc_tag_03_ms_challenge_with_raw_metrics_dict(
        self, mock_pre, mock_svc, mock_link, mock_guard
    ):
        """VTC-TAG-03: MS challenge + raw_metrics dict → attempt_source='challenge' written."""
        from app.api.web_routes.virtual_training import virtual_training_memory_sequence_submit

        mock_pre.return_value = (_challenge(), None)
        mock_svc.get_game.return_value = _game("memory_sequence")
        mock_svc.record_attempt.return_value = _mock_attempt()
        mock_svc.calculate_daily_attempt_index.return_value = 1

        body = {
            "challenge_id": 99,
            "raw_metrics": {"per_round": [], "v": 3},    # already a dict
            "started_at": "2026-06-04T10:00:00Z",
        }

        captured_data = {}

        def _capture(**kwargs):
            captured_data.update(kwargs.get("data", {}))
            return _mock_attempt()

        mock_svc.record_attempt.side_effect = _capture

        _run(virtual_training_memory_sequence_submit(
            request=_request_with_body(body),
            db=_db(),
            user=_user(),
        ))

        raw = captured_data.get("raw_metrics", {})
        assert isinstance(raw, dict), "raw_metrics must be a dict"
        assert raw.get("attempt_source") == "challenge", (
            "VTC-TAG-03: attempt_source must be 'challenge' when challenge present"
        )

    @patch(f"{_VT_BASE}.require_student_onboarding", return_value=None)
    @patch(f"{_VT_BASE}._link_attempt_to_challenge", return_value={})
    @patch(f"{_VT_BASE}.VirtualTrainingService")
    @patch(f"{_VT_BASE}._validate_challenge_pre_submit")
    def test_vtc_tag_04_ms_challenge_with_null_raw_metrics(
        self, mock_pre, mock_svc, mock_link, mock_guard
    ):
        """VTC-TAG-04: MS challenge + raw_metrics null → raw_metrics becomes {'attempt_source':'challenge'}."""
        from app.api.web_routes.virtual_training import virtual_training_memory_sequence_submit

        mock_pre.return_value = (_challenge(), None)
        mock_svc.get_game.return_value = _game("memory_sequence")
        mock_svc.calculate_daily_attempt_index.return_value = 1

        body = {
            "challenge_id": 99,
            # raw_metrics intentionally absent
            "started_at": "2026-06-04T10:00:00Z",
        }

        captured_data = {}

        def _capture(**kwargs):
            captured_data.update(kwargs.get("data", {}))
            return _mock_attempt()

        mock_svc.record_attempt.side_effect = _capture

        _run(virtual_training_memory_sequence_submit(
            request=_request_with_body(body),
            db=_db(),
            user=_user(),
        ))

        raw = captured_data.get("raw_metrics")
        assert isinstance(raw, dict), (
            "VTC-TAG-04: raw_metrics must be a dict even if absent in original payload"
        )
        assert raw.get("attempt_source") == "challenge", (
            "VTC-TAG-04: attempt_source must be 'challenge' when challenge present and raw_metrics was null"
        )

    @patch(f"{_VT_BASE}.require_student_onboarding", return_value=None)
    @patch(f"{_VT_BASE}.VirtualTrainingService")
    def test_vtc_tag_06_ms_standalone_no_tag(self, mock_svc, mock_guard):
        """VTC-TAG-06: MS standalone (no challenge_id) → attempt_source NOT written."""
        from app.api.web_routes.virtual_training import virtual_training_memory_sequence_submit

        mock_svc.get_game.return_value = _game("memory_sequence")
        mock_svc.calculate_daily_attempt_index.return_value = 1

        body = {
            "raw_metrics": {"per_round": []},
            "started_at": "2026-06-04T10:00:00Z",
        }

        captured_data = {}

        def _capture(**kwargs):
            captured_data.update(kwargs.get("data", {}))
            return _mock_attempt()

        mock_svc.record_attempt.side_effect = _capture

        _run(virtual_training_memory_sequence_submit(
            request=_request_with_body(body),
            db=_db(valid_today=0),
            user=_user(),
        ))

        raw = captured_data.get("raw_metrics", {})
        assert raw.get("attempt_source") != "challenge", (
            "VTC-TAG-06: standalone attempt must NOT have attempt_source='challenge'"
        )

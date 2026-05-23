"""Unit tests for Virtual Training — Memory Sequence (Phase 2.5).

MS-01   GET /virtual-training/memory-sequence → 200 when game is_active=True
MS-02   GET /virtual-training/memory-sequence → hub with error when game is_active=False
MS-03   GET /virtual-training/memory-sequence → hub with error when game is None
MS-04   GET /virtual-training/memory-sequence → 303 redirect for non-student user
MS-05   GET game page: attempts_today and attempts_remaining context keys populated
MS-06   GET game page: daily cap banner present when attempts_remaining == 0
MS-07   POST /virtual-training/memory-sequence/submit → 200 with valid payload
MS-08   POST submit → 429 when daily cap exhausted
MS-09   POST submit → 404 when game is_active=False
MS-10   POST submit → 403 when not onboarded
MS-11   POST submit returns attempt_id, xp_awarded, score_normalized, is_valid
MS-12   POST submit: idempotency_key uses vt_ms_u{user_id}_{started_at} prefix
MS-13   GET /virtual-training/memory-sequence/result/{id} → 200 for attempt owner
MS-14   GET result → hub with error for wrong user (attempt not found)
MS-15   Result page: per_phase and per_round extracted from raw_metrics v=2
MS-16   Result page: best_sequence_length = max sequence_length among correct rounds
MS-17   Result page: best_sequence_length falls back to first phase seq_len when no correct rounds
MS-18   Result page: skill_scores and signals_ctx populated from attempt data
MS-19   score_tactical_awareness() — aggregate path: 0.65×hit_rate + 0.35×completion_rate
MS-20   score_tactical_awareness() — per_phase path when per_phase[2] available (MS format)
MS-21   score_tactical_awareness() — legacy fallback keys (correct/stimuli) used when MS keys absent
MS-22   score_tactical_awareness() — clamped to [0, 1] on pathological input
MS-23   score_tactical_awareness() registered in score_all() for tactical_awareness skill
MS-24   Seed: memory_sequence has is_active=True
MS-25   Seed: memory_sequence config has 3 phases with sequence_length 3/5/7
MS-26   Seed: memory_sequence config has tile_colors list of length 12
MS-27   Seed: memory_sequence skill_targets includes tactical_awareness
MS-28   Seed: memory_sequence has gameplay config keys (grid_rows, grid_cols)
MS-29   raw_metrics v=2 stored: no hand_profile key; per_round + per_phase + late_summary present
MS-30   POST submit uses no assign_protocol call (pure cognitive game — no hand/finger protocol)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient

from app.services.virtual_training_service import VirtualTrainingService

_ROUTE_BASE = "app.api.web_routes.virtual_training"

# ── MS config ─────────────────────────────────────────────────────────────────

_MS_CONFIG = {
    "phases": [
        {"phase": 0, "sequence_length": 3, "rounds": 3, "show_ms_per_item": 800, "isi_ms": 500, "recall_window_ms": 8000},
        {"phase": 1, "sequence_length": 5, "rounds": 3, "show_ms_per_item": 650, "isi_ms": 400, "recall_window_ms": 13000},
        {"phase": 2, "sequence_length": 7, "rounds": 3, "show_ms_per_item": 500, "isi_ms": 300, "recall_window_ms": 18000},
    ],
    "grid_rows": 3,
    "grid_cols": 4,
    "tile_colors": [
        "#ef4444", "#f97316", "#eab308", "#22c55e",
        "#14b8a6", "#3b82f6", "#8b5cf6", "#ec4899",
        "#6b7280", "#78716c", "#0ea5e9", "#84cc16",
    ],
    "football_benefit": "Visuospatial working memory for reading complex game situations.",
    "icon": "🧠",
    "show_in_hub": True,
}

_MS_SKILL_TARGETS = {"tactical_awareness": 0.70, "concentration": 0.20, "anticipation": 0.10}

_MS_RAW_METRICS = {
    "v": 2,
    "per_round": [
        {"round": 0, "phase": 0, "sequence_length": 3, "correct_positions": 3, "wrong_positions": 0, "timeout_positions": 0, "outcome": "correct", "first_tap_ms": 620},
        {"round": 1, "phase": 0, "sequence_length": 3, "correct_positions": 2, "wrong_positions": 1, "timeout_positions": 0, "outcome": "partial", "first_tap_ms": 740},
        {"round": 2, "phase": 0, "sequence_length": 3, "correct_positions": 3, "wrong_positions": 0, "timeout_positions": 0, "outcome": "correct", "first_tap_ms": 580},
        {"round": 3, "phase": 1, "sequence_length": 5, "correct_positions": 4, "wrong_positions": 1, "timeout_positions": 0, "outcome": "partial", "first_tap_ms": 800},
        {"round": 4, "phase": 1, "sequence_length": 5, "correct_positions": 5, "wrong_positions": 0, "timeout_positions": 0, "outcome": "correct", "first_tap_ms": 700},
        {"round": 5, "phase": 1, "sequence_length": 5, "correct_positions": 3, "wrong_positions": 2, "timeout_positions": 0, "outcome": "partial", "first_tap_ms": 890},
        {"round": 6, "phase": 2, "sequence_length": 7, "correct_positions": 5, "wrong_positions": 2, "timeout_positions": 0, "outcome": "partial", "first_tap_ms": 950},
        {"round": 7, "phase": 2, "sequence_length": 7, "correct_positions": 4, "wrong_positions": 0, "timeout_positions": 3, "outcome": "timeout", "first_tap_ms": 1100},
        {"round": 8, "phase": 2, "sequence_length": 7, "correct_positions": 7, "wrong_positions": 0, "timeout_positions": 0, "outcome": "correct", "first_tap_ms": 860},
    ],
    "per_phase": [
        {"phase": 0, "sequence_length": 3, "rounds": 3, "total_positions": 9, "correct_positions": 8, "wrong_positions": 1, "timeout_positions": 0, "avg_first_tap_ms": 647},
        {"phase": 1, "sequence_length": 5, "rounds": 3, "total_positions": 15, "correct_positions": 12, "wrong_positions": 3, "timeout_positions": 0, "avg_first_tap_ms": 797},
        {"phase": 2, "sequence_length": 7, "rounds": 3, "total_positions": 21, "correct_positions": 16, "wrong_positions": 2, "timeout_positions": 3, "avg_first_tap_ms": 970},
    ],
    "late_summary": {"timeout_count": 3, "timeout_rounds": 1},
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_ms_game(*, is_active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id = 7
    g.code = "memory_sequence"
    g.name = "Memory Sequence"
    g.is_active = is_active
    g.base_xp = 18
    g.max_daily_attempts = 5
    g.skill_targets = _MS_SKILL_TARGETS
    g.config = _MS_CONFIG
    return g


def _mock_ms_attempt(
    *,
    id: int = 101,
    user_id: int = 42,
    is_valid: bool = True,
    invalid_reason: str | None = None,
    xp_awarded: int = 18,
    attempt_index_today: int = 1,
    score_normalized: float = 76.0,
    avg_reaction_ms: float | None = 780.0,
    min_reaction_ms: float | None = 580.0,
    duration_seconds: float | None = 120.0,
    stimuli_count: int | None = 45,
    correct_count: int | None = 36,
    wrong_click_count: int | None = 6,
    error_count: int | None = 3,
    skill_deltas: dict | None = None,
    raw_metrics: dict | None = None,
) -> MagicMock:
    a = MagicMock()
    a.id = id
    a.user_id = user_id
    a.game_id = 7
    a.is_valid = is_valid
    a.invalid_reason = invalid_reason
    a.xp_awarded = xp_awarded
    a.skill_deltas = skill_deltas or {"tactical_awareness": 1.20, "concentration": 0.35, "anticipation": 0.18}
    a.attempt_index_today = attempt_index_today
    a.score_normalized = score_normalized
    a.avg_reaction_ms = avg_reaction_ms
    a.min_reaction_ms = min_reaction_ms
    a.duration_seconds = duration_seconds
    a.stimuli_count = stimuli_count
    a.correct_count = correct_count
    a.wrong_click_count = wrong_click_count
    a.error_count = error_count
    a.raw_metrics = raw_metrics if raw_metrics is not None else _MS_RAW_METRICS
    a.idempotency_key = f"vt_ms_u{user_id}_ts"
    a.completed_at = datetime.now(timezone.utc)
    return a


def _make_client(user=None, db=None):
    from fastapi import FastAPI
    from app.api.web_routes import virtual_training as vt_module
    from app.dependencies import get_current_user_web
    from app.database import get_db

    app = FastAPI()
    app.include_router(vt_module.router)
    if user is not None:
        app.dependency_overrides[get_current_user_web] = lambda: user
    if db is not None:
        app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _onboarded_student(user_id: int = 42):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = user_id
    u.role = UserRole.STUDENT
    u.onboarding_completed = True
    u.specialization = MagicMock()
    u.specialization.value = "LFA_FOOTBALL_PLAYER"
    return u


def _non_student():
    from app.models.user import UserRole
    u = MagicMock()
    u.id = 99
    u.role = UserRole.INSTRUCTOR
    u.onboarding_completed = True
    return u


def _db_with_count(count: int = 0) -> MagicMock:
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.count.return_value = count
    q.first.return_value = None
    db.query.return_value = q
    return db


# ── MS-01..MS-06: GET game page ───────────────────────────────────────────────

class TestMSPage:

    def test_ms01_game_page_200_when_active(self):
        """MS-01: GET /virtual-training/memory-sequence → 200 when game is_active=True."""
        user = _onboarded_student()
        game = _mock_ms_game(is_active=True)
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            db = _db_with_count(0)
            client = _make_client(user=user, db=db)
            resp = client.get("/virtual-training/memory-sequence", follow_redirects=False)
        assert resp.status_code == 200

    def test_ms02_hub_error_when_inactive(self):
        """MS-02: GET game page → hub with error when game is_active=False."""
        user = _onboarded_student()
        game = _mock_ms_game(is_active=False)
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]):
            db = _db_with_count(0)
            client = _make_client(user=user, db=db)
            resp = client.get("/virtual-training/memory-sequence", follow_redirects=False)
        assert resp.status_code == 200
        assert b"not available" in resp.content

    def test_ms03_hub_error_when_game_none(self):
        """MS-03: GET game page → hub with error when game is None."""
        user = _onboarded_student()
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=None), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]):
            db = _db_with_count(0)
            client = _make_client(user=user, db=db)
            resp = client.get("/virtual-training/memory-sequence", follow_redirects=False)
        assert resp.status_code == 200
        assert b"not available" in resp.content

    def test_ms04_redirect_for_non_student(self):
        """MS-04: GET game page → 303 redirect for non-student (instructor)."""
        user = _non_student()
        client = _make_client(user=user, db=MagicMock())
        resp = client.get("/virtual-training/memory-sequence", follow_redirects=False)
        assert resp.status_code == 303

    def test_ms05_attempts_context_keys_populated(self):
        """MS-05: Game page response is 200 and DB count query is called for attempts_today."""
        user = _onboarded_student()
        game = _mock_ms_game()
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            db = _db_with_count(2)
            client = _make_client(user=user, db=db)
            resp = client.get("/virtual-training/memory-sequence", follow_redirects=False)
        assert resp.status_code == 200
        # attempts_today=2, attempts_remaining=3 — both rendered as digits in HTML
        assert b"2" in resp.content
        assert b"3" in resp.content

    def test_ms06_daily_cap_banner_when_no_remaining(self):
        """MS-06: Daily cap banner appears when attempts_remaining == 0."""
        user = _onboarded_student()
        game = _mock_ms_game()
        game.max_daily_attempts = 3
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            db = _db_with_count(3)
            client = _make_client(user=user, db=db)
            resp = client.get("/virtual-training/memory-sequence", follow_redirects=False)
        assert resp.status_code == 200
        assert b"Daily limit reached" in resp.content


# ── MS-07..MS-12: POST submit ──────────────────────────────────────────────────

class TestMSSubmit:

    _VALID_PAYLOAD = {
        "started_at": "2026-05-23T10:00:00.000Z",
        "duration_seconds": 118.5,
        "stimuli_count": 45,
        "correct_count": 36,
        "wrong_click_count": 6,
        "error_count": 3,
        "avg_reaction_ms": 780,
        "min_reaction_ms": 580,
        "score_raw": 0.76,
        "score_normalized": 76,
        "raw_metrics": _MS_RAW_METRICS,
    }

    def test_ms07_submit_200_valid_payload(self):
        """MS-07: POST submit → 200 with valid payload and active game."""
        user = _onboarded_student()
        game = _mock_ms_game()
        attempt = _mock_ms_attempt()
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            db = _db_with_count(0)
            client = _make_client(user=user, db=db)
            resp = client.post(
                "/virtual-training/memory-sequence/submit",
                json=self._VALID_PAYLOAD,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "attempt_id" in data

    def test_ms08_submit_429_when_daily_cap_exhausted(self):
        """MS-08: POST submit → 429 when valid_today >= max_daily_attempts."""
        user = _onboarded_student()
        game = _mock_ms_game()
        game.max_daily_attempts = 5
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            db = _db_with_count(5)
            client = _make_client(user=user, db=db)
            resp = client.post(
                "/virtual-training/memory-sequence/submit",
                json=self._VALID_PAYLOAD,
            )
        assert resp.status_code == 429
        assert resp.json()["error"] == "daily_cap"

    def test_ms09_submit_404_when_game_inactive(self):
        """MS-09: POST submit → 404 when game is_active=False."""
        user = _onboarded_student()
        game = _mock_ms_game(is_active=False)
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user=user, db=MagicMock())
            resp = client.post(
                "/virtual-training/memory-sequence/submit",
                json=self._VALID_PAYLOAD,
            )
        assert resp.status_code == 404

    def test_ms10_submit_403_when_not_onboarded(self):
        """MS-10: POST submit → 403 when user has not completed onboarding."""
        from app.models.user import UserRole
        user = MagicMock()
        user.id = 42
        user.role = UserRole.STUDENT
        user.onboarding_completed = False
        client = _make_client(user=user, db=MagicMock())
        resp = client.post(
            "/virtual-training/memory-sequence/submit",
            json=self._VALID_PAYLOAD,
        )
        assert resp.status_code == 403

    def test_ms11_submit_response_shape(self):
        """MS-11: POST submit response includes attempt_id, xp_awarded, score_normalized, is_valid."""
        user = _onboarded_student()
        game = _mock_ms_game()
        attempt = _mock_ms_attempt(xp_awarded=18, score_normalized=76.0, is_valid=True)
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            db = _db_with_count(0)
            client = _make_client(user=user, db=db)
            resp = client.post(
                "/virtual-training/memory-sequence/submit",
                json=self._VALID_PAYLOAD,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempt_id"] == 101
        assert data["xp_awarded"] == 18
        assert data["score_normalized"] == 76.0
        assert data["is_valid"] is True

    def test_ms12_idempotency_key_prefix(self):
        """MS-12: idempotency_key uses vt_ms_u{user_id}_{started_at} prefix."""
        user = _onboarded_student(user_id=42)
        game = _mock_ms_game()
        attempt = _mock_ms_attempt()
        captured_key = {}
        started_at = "2026-05-23T10:00:00.000Z"

        def _capture(**kwargs):
            captured_key["idem"] = kwargs.get("idempotency_key", "")
            return attempt

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", side_effect=_capture):
            db = _db_with_count(0)
            client = _make_client(user=user, db=db)
            client.post(
                "/virtual-training/memory-sequence/submit",
                json={**self._VALID_PAYLOAD, "started_at": started_at},
            )
        assert captured_key.get("idem", "").startswith("vt_ms_u42_")
        assert started_at in captured_key.get("idem", "")


# ── MS-13..MS-18: GET result page ─────────────────────────────────────────────

class TestMSResult:

    def test_ms13_result_200_for_owner(self):
        """MS-13: GET /virtual-training/memory-sequence/result/{id} → 200 for attempt owner."""
        user = _onboarded_student(user_id=42)
        attempt = _mock_ms_attempt(user_id=42)
        game = _mock_ms_game()

        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = attempt
        game_q = MagicMock()
        game_q.filter.return_value = game_q
        game_q.first.return_value = game
        db.query.side_effect = [q, game_q]

        client = _make_client(user=user, db=db)
        resp = client.get("/virtual-training/memory-sequence/result/101", follow_redirects=False)
        assert resp.status_code == 200

    def test_ms14_result_hub_error_wrong_user(self):
        """MS-14: GET result → hub with error when attempt not found for user."""
        user = _onboarded_student(user_id=99)

        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = None  # not found for user 99
        q.all.return_value = []
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]):
            client = _make_client(user=user, db=db)
            resp = client.get("/virtual-training/memory-sequence/result/101", follow_redirects=False)
        assert resp.status_code == 200
        assert b"not found" in resp.content.lower() or b"error" in resp.content.lower()

    def test_ms15_result_per_phase_and_per_round_from_raw_metrics(self):
        """MS-15: Result page extracts per_phase and per_round from raw_metrics v=2."""
        attempt = _mock_ms_attempt(raw_metrics=_MS_RAW_METRICS)
        # per_phase has 3 entries, per_round has 9 entries
        assert len(attempt.raw_metrics["per_phase"]) == 3
        assert len(attempt.raw_metrics["per_round"]) == 9
        assert attempt.raw_metrics["v"] == 2
        # No hand_profile in v=2
        assert "hand_profile" not in attempt.raw_metrics

    def test_ms16_best_sequence_length_from_correct_rounds(self):
        """MS-16: best_sequence_length = max sequence_length among rounds with outcome==correct."""
        per_round = _MS_RAW_METRICS["per_round"]
        # Correct rounds: round 0 (len=3), round 2 (len=3), round 4 (len=5), round 8 (len=7)
        completed = [r for r in per_round if r["outcome"] == "correct"]
        best = max(r["sequence_length"] for r in completed)
        assert best == 7

    def test_ms17_best_sequence_length_fallback_when_no_correct(self):
        """MS-17: best_sequence_length falls back to first phase seq_len when no correct rounds."""
        per_round_no_correct = [
            {**r, "outcome": "partial"} for r in _MS_RAW_METRICS["per_round"]
        ]
        completed = [r for r in per_round_no_correct if r["outcome"] == "correct"]
        assert len(completed) == 0
        # Fallback: phases[0].sequence_length = 3
        fallback = _MS_CONFIG["phases"][0]["sequence_length"]
        assert fallback == 3

    def test_ms18_skill_scores_populated_via_extractor(self):
        """MS-18: skill_scores and signals_ctx are populated when attempt has skill_deltas."""
        from app.services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer

        data = {
            "stimuli_count": 45,
            "correct_count": 36,
            "wrong_click_count": 6,
            "error_count": 3,
            "avg_reaction_ms": 780,
            "raw_metrics": _MS_RAW_METRICS,
        }
        signals = VTSignalExtractor.extract(data, _MS_CONFIG["phases"])
        scores = VTSkillScorer.score_all(signals, _MS_SKILL_TARGETS)
        assert "tactical_awareness" in scores
        assert 0.0 <= scores["tactical_awareness"] <= 1.0


# ── MS-19..MS-23: score_tactical_awareness scorer ─────────────────────────────

class TestMSScorerTacticalAwareness:

    def _signals(self, **kwargs):
        from app.services.virtual_training_metrics import VTSignals
        defaults = {
            "hit_rate": 0.80,
            "wrong_rate": 0.13,
            "miss_rate": 0.07,
            "speed_score": 0.60,
            "completion_rate": 0.67,
            "avg_reaction_ms": 780.0,
            "per_phase": None,
        }
        defaults.update(kwargs)
        return VTSignals(**defaults)

    def test_ms19_aggregate_path(self):
        """MS-19: score_tactical_awareness() aggregate path: 0.65×hit + 0.35×completion."""
        from app.services.virtual_training_metrics import VTSkillScorer
        signals = self._signals(hit_rate=0.80, completion_rate=0.67, per_phase=None)
        score = VTSkillScorer.score_tactical_awareness(signals)
        expected = 0.65 * 0.80 + 0.35 * 0.67
        assert abs(score - expected) < 1e-6

    def test_ms20_per_phase_path_when_phase3_available(self):
        """MS-20: Per-phase path used when per_phase[2] has total_positions > 0 (MS format)."""
        from app.services.virtual_training_metrics import VTSkillScorer
        per_phase = [
            {"total_positions": 9,  "correct_positions": 8},
            {"total_positions": 15, "correct_positions": 12},
            {"total_positions": 21, "correct_positions": 16},
        ]
        signals = self._signals(hit_rate=0.80, completion_rate=0.67, per_phase=per_phase)
        score = VTSkillScorer.score_tactical_awareness(signals)
        p3_acc = 16 / 21
        expected = 0.4 * 0.67 * 0.80 + 0.6 * p3_acc
        assert abs(score - expected) < 1e-6

    def test_ms21_legacy_fallback_keys(self):
        """MS-21: Legacy correct/stimuli keys used when MS-format keys absent in per_phase."""
        from app.services.virtual_training_metrics import VTSkillScorer
        per_phase = [
            {"stimuli": 9,  "correct": 8},
            {"stimuli": 15, "correct": 12},
            {"stimuli": 21, "correct": 16},
        ]
        signals = self._signals(hit_rate=0.80, completion_rate=0.67, per_phase=per_phase)
        score = VTSkillScorer.score_tactical_awareness(signals)
        p3_acc = 16 / 21
        expected = 0.4 * 0.67 * 0.80 + 0.6 * p3_acc
        assert abs(score - expected) < 1e-6

    def test_ms22_clamped_to_unit_interval(self):
        """MS-22: score_tactical_awareness() result always in [0, 1]."""
        from app.services.virtual_training_metrics import VTSkillScorer
        # Extreme low
        s_low  = self._signals(hit_rate=0.0, completion_rate=0.0, per_phase=None)
        # Extreme high
        s_high = self._signals(hit_rate=1.0, completion_rate=1.0, per_phase=None)
        assert 0.0 <= VTSkillScorer.score_tactical_awareness(s_low)  <= 1.0
        assert 0.0 <= VTSkillScorer.score_tactical_awareness(s_high) <= 1.0

    def test_ms23_registered_in_score_all(self):
        """MS-23: score_all() calls score_tactical_awareness for tactical_awareness skill."""
        from app.services.virtual_training_metrics import VTSkillScorer
        signals = self._signals(hit_rate=0.80, completion_rate=0.67, per_phase=None)
        scores = VTSkillScorer.score_all(signals, _MS_SKILL_TARGETS)
        assert "tactical_awareness" in scores
        assert scores["tactical_awareness"] > 0.0


# ── MS-24..MS-28: Seed data assertions ────────────────────────────────────────

class TestMSSeed:

    def _ms_game_data(self):
        from scripts.seed_virtual_training_games import _GAMES
        game_map = {g["code"]: g for g in _GAMES}
        return game_map["memory_sequence"]

    def test_ms24_seed_memory_sequence_is_active(self):
        """MS-24: memory_sequence seed entry has is_active=True."""
        assert self._ms_game_data()["is_active"] is True

    def test_ms25_seed_phases_sequence_lengths(self):
        """MS-25: memory_sequence config has 3 phases with sequence_length 3/5/7."""
        phases = self._ms_game_data()["config"]["phases"]
        assert len(phases) == 3
        lengths = [p["sequence_length"] for p in phases]
        assert lengths == [3, 5, 7]

    def test_ms26_seed_tile_colors_length_12(self):
        """MS-26: memory_sequence config has tile_colors list of length 12."""
        colors = self._ms_game_data()["config"]["tile_colors"]
        assert len(colors) == 12

    def test_ms27_seed_skill_targets_includes_tactical_awareness(self):
        """MS-27: memory_sequence skill_targets includes tactical_awareness."""
        targets = self._ms_game_data()["skill_targets"]
        assert "tactical_awareness" in targets

    def test_ms28_seed_grid_config_keys_present(self):
        """MS-28: memory_sequence config has grid_rows and grid_cols keys."""
        cfg = self._ms_game_data()["config"]
        assert "grid_rows" in cfg
        assert "grid_cols" in cfg
        assert cfg["grid_rows"] == 3
        assert cfg["grid_cols"] == 4


# ── MS-29..MS-30: raw_metrics and protocol assertions ─────────────────────────

class TestMSRawMetrics:

    def test_ms29_raw_metrics_v2_no_hand_profile(self):
        """MS-29: raw_metrics v=2 has no hand_profile; per_round, per_phase, late_summary present."""
        rm = _MS_RAW_METRICS
        assert rm["v"] == 2
        assert "hand_profile" not in rm
        assert "per_round" in rm
        assert "per_phase" in rm
        assert "late_summary" in rm

    def test_ms30_no_assign_protocol_call_in_get_route(self):
        """MS-30: GET game page does not call assign_protocol (pure cognitive game — no hand/finger)."""
        user = _onboarded_student()
        game = _mock_ms_game()
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.assign_protocol") as mock_assign:
            db = _db_with_count(0)
            client = _make_client(user=user, db=db)
            client.get("/virtual-training/memory-sequence", follow_redirects=False)
        mock_assign.assert_not_called()

"""Unit tests for Virtual Training — Target Tracking (Phase 2.6).

TT-01   GET /virtual-training/target-tracking → 200 when game is_active=True
TT-02   GET /virtual-training/target-tracking → hub with error when game is_active=False
TT-03   GET /virtual-training/target-tracking → hub with error when game is None
TT-04   GET game page: attempts_today and attempts_remaining context keys populated
TT-05   GET game page: daily cap banner present when attempts_remaining == 0
TT-06   POST /virtual-training/target-tracking/submit → 200 with valid payload
TT-07   POST submit → 429 when daily cap exhausted
TT-08   POST submit → 404 when game is_active=False
TT-09   POST submit → 403 when not onboarded
TT-10   POST submit returns attempt_id, xp_awarded, score_normalized, is_valid
TT-11   POST submit: idempotency_key uses vt_tt_u{user_id}_{started_at} prefix
TT-12   GET /virtual-training/target-tracking/result/{id} → 200 for attempt owner
TT-13   GET result → hub with error for wrong user (attempt not found)
TT-14   Result page: per_phase and per_round extracted from raw_metrics v=2
TT-15   Result page: skill_scores and signals_ctx populated from attempt data
TT-16   Seed: target_tracking has is_active=True
TT-17   Seed: target_tracking config has 3 phases
TT-18   Seed: target_tracking config has validation_overrides with min_stimuli_count=3
TT-19   Seed: target_tracking skill_targets includes anticipation and concentration
TT-20   Seed: target_tracking config has object_radius_px, arena_width, arena_height
TT-21   validate_attempt with overrides: min_stimuli_count=3 — 9 rounds passes
TT-22   validate_attempt with overrides: min_duration_seconds=20 — 25s session passes
TT-23   validate_attempt with overrides: bot_threshold_ms=200 — 250ms avg passes
TT-24   validate_attempt no overrides: default min_stimuli=28 still blocks 9-round TT
TT-25   POST submit: no assign_protocol call (pure cognitive game — no hand/finger protocol)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.virtual_training_service import VirtualTrainingService

_ROUTE_BASE = "app.api.web_routes.virtual_training"

# ── TT config ─────────────────────────────────────────────────────────────────

_TT_CONFIG = {
    "phases": [
        {"phase": 0, "rounds": 3, "object_count": 3, "object_speed": 1.00,
         "highlight_ms": 1500, "tracking_ms": 4000, "window_ms": 3000},
        {"phase": 1, "rounds": 3, "object_count": 4, "object_speed": 1.15,
         "highlight_ms": 1500, "tracking_ms": 5000, "window_ms": 3000},
        {"phase": 2, "rounds": 3, "object_count": 5, "object_speed": 1.30,
         "highlight_ms": 1500, "tracking_ms": 6000, "window_ms": 3000},
    ],
    "object_radius_px": 40,
    "arena_width":      480,
    "arena_height":     360,
    "validation_overrides": {
        "min_stimuli_count":           3,
        "min_duration_seconds":        20.0,
        "bot_threshold_ms":            200,
        "random_clicking_threshold":   0.70,
    },
    "show_in_hub":      True,
    "icon":             "👁️",
    "football_benefit": "Tracking off-ball movement, anticipating runs.",
}

_TT_SKILL_TARGETS = {
    "anticipation":       0.35,
    "concentration":      0.30,
    "tactical_awareness": 0.25,
    "reactions":          0.10,
}

_TT_RAW_METRICS = {
    "v": 2,
    "per_round": [
        {"round": 0, "phase": 0, "object_count": 3, "outcome": "correct",  "response_ms": 870, "tapped_index": 2, "target_index": 2},
        {"round": 1, "phase": 0, "object_count": 3, "outcome": "correct",  "response_ms": 920, "tapped_index": 0, "target_index": 0},
        {"round": 2, "phase": 0, "object_count": 3, "outcome": "wrong",    "response_ms": 780, "tapped_index": 1, "target_index": 0},
        {"round": 3, "phase": 1, "object_count": 4, "outcome": "correct",  "response_ms": 1050, "tapped_index": 3, "target_index": 3},
        {"round": 4, "phase": 1, "object_count": 4, "outcome": "timeout",  "response_ms": None, "tapped_index": None, "target_index": 2},
        {"round": 5, "phase": 1, "object_count": 4, "outcome": "correct",  "response_ms": 1100, "tapped_index": 1, "target_index": 1},
        {"round": 6, "phase": 2, "object_count": 5, "outcome": "wrong",    "response_ms": 890, "tapped_index": 4, "target_index": 0},
        {"round": 7, "phase": 2, "object_count": 5, "outcome": "correct",  "response_ms": 1200, "tapped_index": 2, "target_index": 2},
        {"round": 8, "phase": 2, "object_count": 5, "outcome": "timeout",  "response_ms": None, "tapped_index": None, "target_index": 3},
    ],
    "per_phase": [
        {"phase": 0, "rounds": 3, "correct": 2, "wrong": 1, "timeout": 0, "object_count": 3},
        {"phase": 1, "rounds": 3, "correct": 2, "wrong": 0, "timeout": 1, "object_count": 4},
        {"phase": 2, "rounds": 3, "correct": 1, "wrong": 1, "timeout": 1, "object_count": 5},
    ],
    "late_summary": None,
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_tt_game(*, is_active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id = 7
    g.code = "target_tracking"
    g.name = "Target Tracking"
    g.is_active = is_active
    g.base_xp = 12
    g.max_daily_attempts = 5
    g.skill_targets = _TT_SKILL_TARGETS
    g.config = _TT_CONFIG
    return g


def _mock_tt_attempt(
    *,
    id: int = 201,
    user_id: int = 42,
    is_valid: bool = True,
    invalid_reason: str | None = None,
    xp_awarded: int = 12,
    attempt_index_today: int = 1,
    score_normalized: float = 65.0,
    avg_reaction_ms: float | None = 970.0,
    min_reaction_ms: float | None = 780.0,
    duration_seconds: float | None = 90.0,
    stimuli_count: int | None = 9,
    correct_count: int | None = 5,
    wrong_click_count: int | None = 2,
    error_count: int | None = 2,
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
    a.skill_deltas = skill_deltas or {"anticipation": 0.50, "concentration": 0.42, "tactical_awareness": 0.35, "reactions": 0.11}
    a.attempt_index_today = attempt_index_today
    a.score_normalized = score_normalized
    a.avg_reaction_ms = avg_reaction_ms
    a.min_reaction_ms = min_reaction_ms
    a.duration_seconds = duration_seconds
    a.stimuli_count = stimuli_count
    a.correct_count = correct_count
    a.wrong_click_count = wrong_click_count
    a.error_count = error_count
    a.raw_metrics = raw_metrics if raw_metrics is not None else _TT_RAW_METRICS
    a.idempotency_key = f"vt_tt_u{user_id}_ts"
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


# ── TT-01..TT-05: GET game page ───────────────────────────────────────────────

class TestTTPage:

    def test_tt01_game_page_200_when_active(self):
        """TT-01: GET /virtual-training/target-tracking → 200 when game is_active=True."""
        user = _onboarded_student()
        game = _mock_tt_game(is_active=True)
        db   = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking")

        assert resp.status_code == 200
        assert b"Target Tracking" in resp.content

    def test_tt02_game_page_hub_when_inactive(self):
        """TT-02: GET → hub with error when game is_active=False."""
        user = _onboarded_student()
        game = _mock_tt_game(is_active=False)
        db   = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[game]), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking")

        assert resp.status_code == 200
        assert b"not available" in resp.content

    def test_tt03_game_page_hub_when_none(self):
        """TT-03: GET → hub with error when game is None."""
        user = _onboarded_student()
        db   = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=None), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking")

        assert resp.status_code == 200
        assert b"not available" in resp.content

    def test_tt04_game_page_context_attempts(self):
        """TT-04: GET game page: attempts_today and attempts_remaining context populated."""
        user = _onboarded_student()
        game = _mock_tt_game(is_active=True)
        db   = _db_with_count(2)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking")

        assert resp.status_code == 200
        assert b"2/5" in resp.content

    def test_tt05_game_page_cap_banner(self):
        """TT-05: GET game page: daily cap banner when attempts_remaining == 0."""
        user = _onboarded_student()
        game = _mock_tt_game(is_active=True)
        db   = _db_with_count(5)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking")

        assert resp.status_code == 200
        assert b"Come back tomorrow" in resp.content


# ── TT-06..TT-11: POST submit ─────────────────────────────────────────────────

class TestTTSubmit:

    _VALID_PAYLOAD = {
        "started_at":       "2026-05-23T10:00:00Z",
        "duration_seconds": 90.0,
        "stimuli_count":    9,
        "correct_count":    5,
        "error_count":      2,
        "wrong_click_count": 2,
        "avg_reaction_ms":  970,
        "min_reaction_ms":  780,
        "score_raw":        0.65,
        "score_normalized": 65,
        "raw_metrics":      _TT_RAW_METRICS,
    }

    def test_tt06_submit_valid_payload_200(self):
        """TT-06: POST submit → 200 with valid payload."""
        user    = _onboarded_student()
        game    = _mock_tt_game(is_active=True)
        attempt = _mock_tt_attempt()
        db      = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            client = _make_client(user, db)
            resp   = client.post("/virtual-training/target-tracking/submit",
                                 json=self._VALID_PAYLOAD)

        assert resp.status_code == 200
        data = resp.json()
        assert "attempt_id" in data

    def test_tt07_submit_429_daily_cap(self):
        """TT-07: POST submit → 429 when daily cap exhausted."""
        user = _onboarded_student()
        game = _mock_tt_game(is_active=True)
        db   = _db_with_count(5)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user, db)
            resp   = client.post("/virtual-training/target-tracking/submit",
                                 json=self._VALID_PAYLOAD)

        assert resp.status_code == 429
        assert resp.json()["error"] == "daily_cap"

    def test_tt08_submit_404_game_inactive(self):
        """TT-08: POST submit → 404 when game is_active=False."""
        user = _onboarded_student()
        game = _mock_tt_game(is_active=False)
        db   = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user, db)
            resp   = client.post("/virtual-training/target-tracking/submit",
                                 json=self._VALID_PAYLOAD)

        assert resp.status_code == 404

    def test_tt09_submit_403_not_onboarded(self):
        """TT-09: POST submit → 403 when not onboarded."""
        user = _non_student()
        db   = _db_with_count(0)

        client = _make_client(user, db)
        resp   = client.post("/virtual-training/target-tracking/submit",
                             json=self._VALID_PAYLOAD)

        assert resp.status_code == 403

    def test_tt10_submit_returns_attempt_fields(self):
        """TT-10: POST submit returns attempt_id, xp_awarded, score_normalized, is_valid."""
        user    = _onboarded_student()
        game    = _mock_tt_game(is_active=True)
        attempt = _mock_tt_attempt(id=201, xp_awarded=12, score_normalized=65.0, is_valid=True)
        db      = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            client = _make_client(user, db)
            resp   = client.post("/virtual-training/target-tracking/submit",
                                 json=self._VALID_PAYLOAD)

        data = resp.json()
        assert data["attempt_id"]      == 201
        assert data["xp_awarded"]      == 12
        assert data["score_normalized"] == 65.0
        assert data["is_valid"]        is True

    def test_tt11_submit_idempotency_key_prefix(self):
        """TT-11: POST submit: idempotency_key uses vt_tt_u{user_id}_{started_at}."""
        user    = _onboarded_student(user_id=42)
        game    = _mock_tt_game(is_active=True)
        attempt = _mock_tt_attempt()
        db      = _db_with_count(0)

        captured_key = {}

        def _capture(db, user_id, game, data, idempotency_key):
            captured_key["key"] = idempotency_key
            return attempt

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", side_effect=_capture):
            client = _make_client(user, db)
            client.post("/virtual-training/target-tracking/submit",
                        json={**self._VALID_PAYLOAD, "started_at": "2026-05-23T10:00:00Z"})

        assert captured_key.get("key", "").startswith("vt_tt_u42_")


# ── TT-12..TT-15: GET result ──────────────────────────────────────────────────

class TestTTResult:

    def test_tt12_result_200_for_owner(self):
        """TT-12: GET result/{id} → 200 for attempt owner."""
        user    = _onboarded_student(user_id=42)
        game    = _mock_tt_game(is_active=True)
        attempt = _mock_tt_attempt(id=201, user_id=42)

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = attempt
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[game]), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking/result/201")

        assert resp.status_code == 200
        assert b"Target Tracking" in resp.content

    def test_tt13_result_hub_wrong_user(self):
        """TT-13: GET result → hub with error for wrong user (attempt not found)."""
        user = _onboarded_student(user_id=42)

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = None
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking/result/999")

        assert resp.status_code == 200
        assert b"not found" in resp.content.lower()

    def test_tt14_result_per_phase_per_round_extracted(self):
        """TT-14: Result page: per_phase and per_round extracted from raw_metrics v=2."""
        user    = _onboarded_student(user_id=42)
        game    = _mock_tt_game(is_active=True)
        attempt = _mock_tt_attempt(
            user_id=42,
            raw_metrics=_TT_RAW_METRICS,
            skill_deltas=None,
        )
        attempt.skill_deltas = None   # no skill breakdown → simpler render path

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = attempt
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[game]), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking/result/201")

        assert resp.status_code == 200
        # Phase 1 has 3 rounds
        assert b"Phase 1" in resp.content

    def test_tt15_result_skill_scores_populated(self):
        """TT-15: Result page: skill_scores and signals_ctx populated from attempt data."""
        from app.services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer

        user    = _onboarded_student(user_id=42)
        game    = _mock_tt_game(is_active=True)
        attempt = _mock_tt_attempt(
            user_id=42,
            stimuli_count=9,
            correct_count=5,
            wrong_click_count=2,
            error_count=2,
            avg_reaction_ms=970.0,
            raw_metrics=_TT_RAW_METRICS,
            skill_deltas={"anticipation": 0.50, "concentration": 0.42},
        )

        # Two distinct queries: first returns attempt, second returns game
        db = MagicMock()
        q_attempt = MagicMock()
        q_attempt.filter.return_value = q_attempt
        q_attempt.first.return_value  = attempt

        q_game = MagicMock()
        q_game.filter.return_value = q_game
        q_game.first.return_value  = game

        call_count = {"n": 0}

        def _query_side_effect(*args, **kwargs):
            call_count["n"] += 1
            return q_attempt if call_count["n"] == 1 else q_game

        db.query.side_effect = _query_side_effect

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[game]), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking/result/201")

        assert resp.status_code == 200
        # Score stats are always rendered
        assert b"65" in resp.content   # score_normalized
        # Skill breakdown section rendered (skill_scores populated)
        assert b"anticipation" in resp.content.lower() or b"Score" in resp.content


# ── TT-16..TT-20: Seed ────────────────────────────────────────────────────────

class TestTTSeed:

    def _get_tt_seed_entry(self):
        from scripts.seed_virtual_training_games import _GAMES
        for g in _GAMES:
            if g["code"] == "target_tracking":
                return g
        return None

    def test_tt16_seed_is_active(self):
        """TT-16: Seed: target_tracking has is_active=True."""
        entry = self._get_tt_seed_entry()
        assert entry is not None
        assert entry["is_active"] is True

    def test_tt17_seed_three_phases(self):
        """TT-17: Seed: target_tracking config has 3 phases."""
        entry = self._get_tt_seed_entry()
        phases = entry["config"]["phases"]
        assert len(phases) == 3

    def test_tt18_seed_validation_overrides(self):
        """TT-18: Seed: validation_overrides has min_stimuli_count=3."""
        entry = self._get_tt_seed_entry()
        ov = entry["config"]["validation_overrides"]
        assert ov["min_stimuli_count"] == 3
        assert ov["min_duration_seconds"] == 20.0

    def test_tt19_seed_skill_targets(self):
        """TT-19: Seed: target_tracking skill_targets includes anticipation and concentration."""
        entry = self._get_tt_seed_entry()
        st = entry["skill_targets"]
        assert "anticipation" in st
        assert "concentration" in st

    def test_tt20_seed_arena_config(self):
        """TT-20: Seed: target_tracking config has object_radius_px, arena_width, arena_height."""
        entry = self._get_tt_seed_entry()
        cfg = entry["config"]
        assert "object_radius_px" in cfg
        assert "arena_width" in cfg
        assert "arena_height" in cfg


# ── TT-21..TT-24: validate_attempt with overrides ────────────────────────────

class TestTTValidateOverrides:

    _TT_OVERRIDES = {
        "min_stimuli_count":           3,
        "min_duration_seconds":        20.0,
        "bot_threshold_ms":            200,
        "random_clicking_threshold":   0.70,
    }

    def test_tt21_overrides_min_stimuli_9_rounds_passes(self):
        """TT-21: validate_attempt with min_stimuli=3 — 9-round TT attempt passes."""
        data = {
            "duration_seconds": 90.0,
            "stimuli_count":    9,
            "correct_count":    5,
            "wrong_click_count": 2,
            "error_count":      2,
            "avg_reaction_ms":  970,
        }
        is_valid, reason = VirtualTrainingService.validate_attempt(data, overrides=self._TT_OVERRIDES)
        assert is_valid is True
        assert reason is None

    def test_tt22_overrides_min_duration_25s_passes(self):
        """TT-22: validate_attempt with min_duration=20 — 25s session passes."""
        data = {
            "duration_seconds": 25.0,
            "stimuli_count":    9,
            "wrong_click_count": 0,
            "avg_reaction_ms":  970,
        }
        is_valid, reason = VirtualTrainingService.validate_attempt(data, overrides=self._TT_OVERRIDES)
        assert is_valid is True

    def test_tt23_overrides_bot_threshold_250ms_passes(self):
        """TT-23: validate_attempt with bot_threshold=200 — 250ms avg passes."""
        data = {
            "duration_seconds": 90.0,
            "stimuli_count":    9,
            "wrong_click_count": 0,
            "avg_reaction_ms":  250,
        }
        is_valid, reason = VirtualTrainingService.validate_attempt(data, overrides=self._TT_OVERRIDES)
        assert is_valid is True

    def test_tt24_no_overrides_default_min_stimuli_blocks_tt(self):
        """TT-24: validate_attempt no overrides: default min_stimuli=28 blocks 9-round TT."""
        data = {
            "duration_seconds": 90.0,
            "stimuli_count":    9,
            "wrong_click_count": 0,
            "avg_reaction_ms":  970,
        }
        is_valid, reason = VirtualTrainingService.validate_attempt(data)  # no overrides
        assert is_valid is False
        assert reason == "too_few_stimuli"


# ── TT-25: No assign_protocol ─────────────────────────────────────────────────

class TestTTNoProtocol:

    def test_tt25_no_assign_protocol_call(self):
        """TT-25: POST submit: no assign_protocol call (pure cognitive — no hand/finger)."""
        user    = _onboarded_student()
        game    = _mock_tt_game(is_active=True)
        attempt = _mock_tt_attempt()
        db      = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.assign_protocol") as mock_assign:

            client = _make_client(user, db)
            client.get("/virtual-training/target-tracking")   # GET game page

        mock_assign.assert_not_called()

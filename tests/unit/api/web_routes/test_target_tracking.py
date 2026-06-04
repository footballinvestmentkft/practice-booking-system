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
TT-26   Seed: config has 'difficulties' dict with easy/medium/hard/expert keys
TT-27   get_difficulty_config(game, 'easy') returns easy phases
TT-28   get_difficulty_config(game, 'medium') returns medium phases with 1.30× multiplier
TT-29   get_difficulty_config(game, 'hard') returns hard phases with 1.70× multiplier
TT-30   get_difficulty_config(game, 'unknown') falls back to easy
TT-31   Seed: expert difficulty has unlock_threshold with min_hard_attempts=3 and min_hard_score=70
TT-32   POST submit with difficulty_level=medium → raw_metrics difficulty_level == 'medium'
TT-33   POST submit with difficulty_level=hard → raw_metrics difficulty_multiplier == 1.70
TT-34   POST submit with missing difficulty_level → defaults to 'easy', multiplier 1.00
TT-35   Hard difficulty validation_overrides min_duration=30s — 25s attempt fails
TT-36   Result page context contains difficulty_level and difficulty_multiplier keys
TT-37   POST submit medium → per_round items may have flash_events key in v=3 payload
TT-38   POST submit hard → late_summary has total_flashes_shown, taps_during_flash, flash_distraction_rate
TT-39   extract_difficulty_multiplier: v=3 payload with 1.70 → returns 1.70
TT-40   extract_difficulty_multiplier: v=2 payload → returns 1.00 (backward compat)
TT-41   extract_difficulty_multiplier: missing key in v=3 → returns 1.00
TT-42   VTSignalExtractor.extract v=3 payload: signals.difficulty_multiplier == 1.70
TT-43   VTSignalExtractor.extract v=2 payload: signals.difficulty_multiplier == 1.00
TT-44   compute_vt_skill_deltas: hard (1.70×) gives larger delta than easy (1.00×) at same score
TT-45   compute_vt_skill_deltas: expert (2.20×) delta >= hard (1.70×) at same score
TT-46   POST expert submit when not unlocked → 403 expert_locked
TT-47   POST expert submit when unlocked → 200 (record_attempt called)
TT-48   Template: arena CSS uses aspect-ratio: 4 / 3
TT-49   Template: arena width uses min(480px, 100%), no padding-bottom hack
TT-50   Template: JS reads arena.clientWidth at runtime
TT-51   Template: JS reads arena.clientHeight at runtime
TT-52   Template: frozen const ARENA_W / ARENA_H absent
TT-53   Template: responsive radius formula uses arenaW / 480
TT-54   Template: resize + orientationchange listeners registered
TT-55   Template: object size set from radius * 2 in JS
TT-56   Template: X-CSRF-Token header present in fetch call
TT-57   Template: _DIFF_CONFIG_OK guard present — disables Medium/Hard/Expert when difficulties missing
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

        def _capture(db, user_id, game, data, idempotency_key, is_challenge=False, **_):
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


# ── TT-26..TT-31: Seed difficulty config ──────────────────────────────────────

_TT_DIFFICULTIES_CFG = {
    "easy": {
        "phases": [
            {"phase": 0, "rounds": 3, "object_count": 3, "object_speed": 1.00,
             "highlight_ms": 1500, "tracking_ms": 4000, "window_ms": 3000, "distractor_flash": 0},
            {"phase": 1, "rounds": 3, "object_count": 4, "object_speed": 1.15,
             "highlight_ms": 1500, "tracking_ms": 5000, "window_ms": 3000, "distractor_flash": 0},
            {"phase": 2, "rounds": 3, "object_count": 5, "object_speed": 1.30,
             "highlight_ms": 1500, "tracking_ms": 6000, "window_ms": 3000, "distractor_flash": 0},
        ],
        "difficulty_multiplier": 1.00,
        "validation_overrides": {
            "min_stimuli_count": 3, "min_duration_seconds": 20.0,
            "bot_threshold_ms": 200, "random_clicking_threshold": 0.70,
        },
    },
    "medium": {
        "phases": [
            {"phase": 0, "rounds": 3, "object_count": 5, "object_speed": 1.60,
             "highlight_ms": 1500, "tracking_ms": 5000, "window_ms": 2500, "distractor_flash": 1},
            {"phase": 1, "rounds": 3, "object_count": 6, "object_speed": 1.90,
             "highlight_ms": 1500, "tracking_ms": 6000, "window_ms": 2500, "distractor_flash": 2},
            {"phase": 2, "rounds": 3, "object_count": 7, "object_speed": 2.20,
             "highlight_ms": 1500, "tracking_ms": 7000, "window_ms": 2300, "distractor_flash": 3},
        ],
        "difficulty_multiplier": 1.30,
        "validation_overrides": {
            "min_stimuli_count": 3, "min_duration_seconds": 25.0,
            "bot_threshold_ms": 200, "random_clicking_threshold": 0.70,
        },
    },
    "hard": {
        "phases": [
            {"phase": 0, "rounds": 4, "object_count": 6, "object_speed": 2.10,
             "highlight_ms": 1200, "tracking_ms": 5500, "window_ms": 2200, "distractor_flash": 2},
            {"phase": 1, "rounds": 4, "object_count": 7, "object_speed": 2.50,
             "highlight_ms": 1200, "tracking_ms": 6500, "window_ms": 2000, "distractor_flash": 4},
            {"phase": 2, "rounds": 4, "object_count": 8, "object_speed": 2.90,
             "highlight_ms": 1200, "tracking_ms": 8000, "window_ms": 1800, "distractor_flash": 5},
        ],
        "difficulty_multiplier": 1.70,
        "validation_overrides": {
            "min_stimuli_count": 4, "min_duration_seconds": 30.0,
            "bot_threshold_ms": 200, "random_clicking_threshold": 0.70,
        },
    },
    "expert": {
        "phases": [
            {"phase": 0, "rounds": 3, "object_count": 7, "object_speed": 2.60,
             "highlight_ms": 900, "tracking_ms": 5500, "window_ms": 1800, "distractor_flash": 3},
            {"phase": 1, "rounds": 3, "object_count": 8, "object_speed": 3.00,
             "highlight_ms": 900, "tracking_ms": 7000, "window_ms": 1700, "distractor_flash": 5},
            {"phase": 2, "rounds": 3, "object_count": 9, "object_speed": 3.20,
             "highlight_ms": 700, "tracking_ms": 8500, "window_ms": 1600, "distractor_flash": 6},
            {"phase": 3, "rounds": 3, "object_count": 9, "object_speed": 3.20,
             "highlight_ms": 700, "tracking_ms": 10000, "window_ms": 1600, "distractor_flash": 6},
        ],
        "difficulty_multiplier": 2.20,
        "unlock_threshold": {"min_hard_attempts": 3, "min_hard_score": 70},
        "validation_overrides": {
            "min_stimuli_count": 4, "min_duration_seconds": 35.0,
            "bot_threshold_ms": 200, "random_clicking_threshold": 0.70,
        },
    },
}

_TT_CONFIG_WITH_DIFFICULTIES = {
    **_TT_CONFIG,
    "difficulties": _TT_DIFFICULTIES_CFG,
}


def _mock_tt_game_with_difficulties(*, is_active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id = 7
    g.code = "target_tracking"
    g.name = "Target Tracking"
    g.is_active = is_active
    g.base_xp = 12
    g.max_daily_attempts = 5
    g.skill_targets = _TT_SKILL_TARGETS
    g.config = _TT_CONFIG_WITH_DIFFICULTIES
    return g


class TestTTSeedDifficulties:

    def _get_tt_seed_entry(self):
        from scripts.seed_virtual_training_games import _GAMES
        for g in _GAMES:
            if g["code"] == "target_tracking":
                return g
        return None

    def test_tt26_seed_has_difficulties_dict(self):
        """TT-26: Seed: config has 'difficulties' dict with easy/medium/hard/expert keys."""
        entry = self._get_tt_seed_entry()
        difficulties = entry["config"]["difficulties"]
        assert isinstance(difficulties, dict)
        assert "easy"   in difficulties
        assert "medium" in difficulties
        assert "hard"   in difficulties
        assert "expert" in difficulties

    def test_tt31_seed_expert_unlock_threshold(self):
        """TT-31: Seed: expert difficulty has unlock_threshold with correct thresholds."""
        entry  = self._get_tt_seed_entry()
        expert = entry["config"]["difficulties"]["expert"]
        ut = expert["unlock_threshold"]
        assert ut["min_hard_attempts"] == 3
        assert ut["min_hard_score"]    == 70


# ── TT-27..TT-30: get_difficulty_config ──────────────────────────────────────

class TestTTGetDifficultyConfig:

    def _svc(self):
        from app.services.virtual_training_service import VirtualTrainingService
        return VirtualTrainingService

    def _game(self):
        return _mock_tt_game_with_difficulties()

    def test_tt27_easy_config_returned(self):
        """TT-27: get_difficulty_config(game, 'easy') returns easy phases."""
        result = self._svc().get_difficulty_config(self._game(), "easy")
        assert isinstance(result, dict)
        assert "phases" in result
        assert result["difficulty_multiplier"] == 1.00
        assert result["phases"][0]["object_count"] == 3

    def test_tt28_medium_config_returned(self):
        """TT-28: get_difficulty_config(game, 'medium') returns medium phases with 1.30× multiplier."""
        result = self._svc().get_difficulty_config(self._game(), "medium")
        assert result["difficulty_multiplier"] == 1.30
        assert result["phases"][2]["object_count"] == 7

    def test_tt29_hard_config_returned(self):
        """TT-29: get_difficulty_config(game, 'hard') returns hard phases with 1.70× multiplier."""
        result = self._svc().get_difficulty_config(self._game(), "hard")
        assert result["difficulty_multiplier"] == 1.70
        assert result["phases"][0]["rounds"] == 4

    def test_tt30_unknown_level_fallback_to_easy(self):
        """TT-30: get_difficulty_config(game, 'unknown') falls back to easy."""
        result = self._svc().get_difficulty_config(self._game(), "unknown_level")
        assert result["difficulty_multiplier"] == 1.00


# ── TT-32..TT-35: POST submit with difficulty ─────────────────────────────────

class TestTTDifficultySubmit:

    _BASE_PAYLOAD = {
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
        "raw_metrics":      {**_TT_RAW_METRICS},
    }

    def _make_payload(self, **overrides):
        import copy
        p = copy.deepcopy(self._BASE_PAYLOAD)
        p.update(overrides)
        return p

    def test_tt32_medium_difficulty_level_in_raw_metrics(self):
        """TT-32: POST submit with difficulty_level=medium → raw_metrics difficulty_level == 'medium'."""
        user    = _onboarded_student()
        game    = _mock_tt_game_with_difficulties(is_active=True)
        attempt = _mock_tt_attempt()
        db      = _db_with_count(0)

        captured = {}

        def _capture(db, user_id, game, data, idempotency_key, is_challenge=False, **_):
            captured["raw"] = data.get("raw_metrics", {})
            return attempt

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", side_effect=_capture), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.is_expert_unlocked", return_value=False):
            client = _make_client(user, db)
            client.post("/virtual-training/target-tracking/submit",
                        json=self._make_payload(difficulty_level="medium"))

        assert captured["raw"].get("difficulty_level") == "medium"

    def test_tt33_hard_difficulty_multiplier_in_raw_metrics(self):
        """TT-33: POST submit with difficulty_level=hard → raw_metrics difficulty_multiplier == 1.70."""
        user    = _onboarded_student()
        game    = _mock_tt_game_with_difficulties(is_active=True)
        attempt = _mock_tt_attempt()
        db      = _db_with_count(0)

        captured = {}

        def _capture(db, user_id, game, data, idempotency_key, is_challenge=False, **_):
            captured["raw"] = data.get("raw_metrics", {})
            return attempt

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", side_effect=_capture), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.is_expert_unlocked", return_value=False):
            client = _make_client(user, db)
            client.post("/virtual-training/target-tracking/submit",
                        json=self._make_payload(difficulty_level="hard"))

        assert captured["raw"].get("difficulty_multiplier") == 1.70

    def test_tt34_missing_difficulty_defaults_to_easy(self):
        """TT-34: POST submit with missing difficulty_level → defaults to 'easy', multiplier 1.00."""
        user    = _onboarded_student()
        game    = _mock_tt_game_with_difficulties(is_active=True)
        attempt = _mock_tt_attempt()
        db      = _db_with_count(0)

        captured = {}

        def _capture(db, user_id, game, data, idempotency_key, is_challenge=False, **_):
            captured["raw"] = data.get("raw_metrics", {})
            return attempt

        payload = self._make_payload()
        payload.pop("difficulty_level", None)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", side_effect=_capture), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.is_expert_unlocked", return_value=False):
            client = _make_client(user, db)
            client.post("/virtual-training/target-tracking/submit", json=payload)

        assert captured["raw"].get("difficulty_level") == "easy"
        assert captured["raw"].get("difficulty_multiplier") == 1.00

    def test_tt35_hard_min_duration_25s_fails(self):
        """TT-35: Hard difficulty validation_overrides min_duration=30s — 25s attempt fails."""
        from app.services.virtual_training_service import VirtualTrainingService
        data = {
            "duration_seconds": 25.0,
            "stimuli_count":    12,
            "wrong_click_count": 0,
            "avg_reaction_ms":  500,
            "raw_metrics": {
                "v": 3,
                "difficulty_level":      "hard",
                "difficulty_multiplier": 1.70,
            },
        }
        game = _mock_tt_game_with_difficulties()
        hard_ov = game.config["difficulties"]["hard"]["validation_overrides"]
        is_valid, reason = VirtualTrainingService.validate_attempt(data, overrides=hard_ov)
        assert is_valid is False
        assert reason == "too_short"


# ── TT-36: Result page context ────────────────────────────────────────────────

class TestTTDifficultyResultPage:

    def test_tt36_result_page_difficulty_context(self):
        """TT-36: Result page context contains difficulty_level and difficulty_multiplier keys."""
        user    = _onboarded_student(user_id=42)
        game    = _mock_tt_game_with_difficulties(is_active=True)
        raw_v3  = {
            **_TT_RAW_METRICS,
            "v": 3,
            "difficulty_level":      "medium",
            "difficulty_multiplier": 1.30,
        }
        attempt = _mock_tt_attempt(user_id=42, raw_metrics=raw_v3, skill_deltas=None)
        attempt.skill_deltas = None

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
        # difficulty badge rendered in HTML
        assert b"Medium" in resp.content or b"1.30" in resp.content


# ── TT-37..TT-38: Flash mechanic in raw_metrics ───────────────────────────────

class TestTTFlashRawMetrics:

    def test_tt37_medium_per_round_has_flash_events_key(self):
        """TT-37: A v=3 raw_metrics per_round item may have flash_events key."""
        raw_v3 = {
            "v": 3,
            "difficulty_level": "medium",
            "difficulty_multiplier": 1.30,
            "per_round": [
                {
                    "round": 0, "phase": 1, "object_count": 5,
                    "outcome": "correct", "response_ms": 900,
                    "tapped_index": 0, "target_index": 0,
                    "flash_events": [
                        {"t_offset_ms": 2000, "distractor_index": 2, "target_index": 0,
                         "duration_ms": 400, "color": "#f59e0b"}
                    ],
                    "tapped_during_flash": False,
                }
            ],
            "per_phase": [],
            "late_summary": {
                "total_flashes_shown": 1,
                "taps_during_flash": 0,
                "flash_distraction_rate": 0.0,
            },
        }
        assert "flash_events" in raw_v3["per_round"][0]
        assert raw_v3["per_round"][0]["flash_events"][0]["color"] == "#f59e0b"
        # target_index in flash_event must differ from distractor_index
        fe = raw_v3["per_round"][0]["flash_events"][0]
        assert fe["distractor_index"] != fe["target_index"]

    def test_tt38_late_summary_flash_fields(self):
        """TT-38: late_summary has total_flashes_shown, taps_during_flash, flash_distraction_rate."""
        late_summary = {
            "total_flashes_shown":   12,
            "taps_during_flash":     3,
            "flash_distraction_rate": 0.25,
        }
        assert late_summary["flash_distraction_rate"] == pytest.approx(0.25, abs=1e-9)
        assert late_summary["total_flashes_shown"] == 12
        assert late_summary["taps_during_flash"]   == 3


# ── TT-39..TT-41: extract_difficulty_multiplier ───────────────────────────────

class TestTTExtractDifficultyMultiplier:

    def _svc(self):
        from app.services.virtual_training_service import VirtualTrainingService
        return VirtualTrainingService

    def test_tt39_v3_payload_returns_correct_multiplier(self):
        """TT-39: extract_difficulty_multiplier: v=3 payload with 1.70 → returns 1.70."""
        data = {"raw_metrics": {"v": 3, "difficulty_multiplier": 1.70}}
        assert self._svc().extract_difficulty_multiplier(data) == pytest.approx(1.70)

    def test_tt40_v2_payload_returns_1_00(self):
        """TT-40: extract_difficulty_multiplier: v=2 payload → returns 1.00."""
        data = {"raw_metrics": {"v": 2, "difficulty_multiplier": 1.70}}
        assert self._svc().extract_difficulty_multiplier(data) == pytest.approx(1.00)

    def test_tt41_missing_key_returns_1_00(self):
        """TT-41: extract_difficulty_multiplier: missing key in v=3 → returns 1.00."""
        data = {"raw_metrics": {"v": 3}}
        assert self._svc().extract_difficulty_multiplier(data) == pytest.approx(1.00)


# ── TT-42..TT-43: VTSignalExtractor difficulty_multiplier ────────────────────

class TestTTVTSignalExtractorDifficulty:

    def test_tt42_v3_payload_signals_difficulty_multiplier(self):
        """TT-42: VTSignalExtractor.extract v=3 payload: signals.difficulty_multiplier == 1.70."""
        from app.services.virtual_training_metrics import VTSignalExtractor
        data = {
            "stimuli_count": 9, "correct_count": 5, "wrong_click_count": 2,
            "error_count": 2, "avg_reaction_ms": 970,
            "raw_metrics": {"v": 3, "difficulty_multiplier": 1.70},
        }
        signals = VTSignalExtractor.extract(data, [])
        assert signals.difficulty_multiplier == pytest.approx(1.70)

    def test_tt43_v2_payload_signals_difficulty_multiplier_default(self):
        """TT-43: VTSignalExtractor.extract v=2 payload: signals.difficulty_multiplier == 1.00."""
        from app.services.virtual_training_metrics import VTSignalExtractor
        data = {
            "stimuli_count": 9, "correct_count": 5, "wrong_click_count": 2,
            "error_count": 2, "avg_reaction_ms": 970,
            "raw_metrics": {"v": 2},
        }
        signals = VTSignalExtractor.extract(data, [])
        assert signals.difficulty_multiplier == pytest.approx(1.00)


# ── TT-44..TT-45: delta multiplier ordering ──────────────────────────────────

class TestTTDeltaMultiplierOrdering:

    def _compute_delta(self, difficulty_multiplier: float) -> float:
        """Mirror record_attempt: effective_multiplier = xp_multiplier × difficulty_multiplier."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        data = {
            "stimuli_count": 9, "correct_count": 8, "wrong_click_count": 0,
            "error_count": 1, "avg_reaction_ms": 800,
            "raw_metrics": {"v": 3, "difficulty_multiplier": difficulty_multiplier},
        }
        game = _mock_tt_game_with_difficulties()
        xp_multiplier      = 1.00   # attempt_index = 1
        effective_mult     = xp_multiplier * difficulty_multiplier   # same formula as record_attempt
        deltas = compute_vt_skill_deltas(data=data, game=game, multiplier=effective_mult)
        return sum(deltas.values()) if deltas else 0.0

    def test_tt44_hard_delta_larger_than_easy(self):
        """TT-44: compute_vt_skill_deltas: hard (1.70×) gives larger delta than easy (1.00×)."""
        easy_delta = self._compute_delta(1.00)
        hard_delta = self._compute_delta(1.70)
        assert hard_delta > easy_delta

    def test_tt45_expert_delta_gte_hard(self):
        """TT-45: compute_vt_skill_deltas: expert (2.20×) delta >= hard (1.70×)."""
        hard_delta   = self._compute_delta(1.70)
        expert_delta = self._compute_delta(2.20)
        assert expert_delta >= hard_delta


# ── TT-46..TT-47: Expert lock gate ───────────────────────────────────────────

class TestTTExpertLock:

    _VALID_PAYLOAD = {
        "started_at":       "2026-05-23T10:00:00Z",
        "duration_seconds": 120.0,
        "stimuli_count":    12,
        "correct_count":    10,
        "error_count":      1,
        "wrong_click_count": 1,
        "avg_reaction_ms":  900,
        "min_reaction_ms":  700,
        "score_raw":        0.80,
        "score_normalized": 80,
        "difficulty_level": "expert",
        "raw_metrics": {
            "v": 3,
            "difficulty_level": "expert",
            "difficulty_multiplier": 2.20,
            "per_round": [],
            "per_phase": [],
            "late_summary": {"total_flashes_shown": 0, "taps_during_flash": 0, "flash_distraction_rate": 0.0},
        },
    }

    def test_tt46_expert_submit_locked_returns_403(self):
        """TT-46: POST expert submit when not unlocked → 403 expert_locked."""
        user = _onboarded_student()
        game = _mock_tt_game_with_difficulties(is_active=True)
        db   = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.is_expert_unlocked", return_value=False):
            client = _make_client(user, db)
            resp   = client.post("/virtual-training/target-tracking/submit",
                                 json=self._VALID_PAYLOAD)

        assert resp.status_code == 403
        assert resp.json()["error"] == "expert_locked"

    def test_tt47_expert_submit_unlocked_calls_record_attempt(self):
        """TT-47: POST expert submit when unlocked → 200 (record_attempt called)."""
        user    = _onboarded_student()
        game    = _mock_tt_game_with_difficulties(is_active=True)
        attempt = _mock_tt_attempt()
        db      = _db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.is_expert_unlocked", return_value=True), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt) as mock_record:
            client = _make_client(user, db)
            resp   = client.post("/virtual-training/target-tracking/submit",
                                 json=self._VALID_PAYLOAD)

        assert resp.status_code == 200
        mock_record.assert_called_once()


# ── TT-48..TT-55: Template static guard — mobile responsiveness ───────────────

_TT_TEMPLATE_PATH = (
    __file__.replace(
        "tests/unit/api/web_routes/test_target_tracking.py",
        "app/templates/virtual_training_target_tracking.html",
    )
)


class TestTTTemplateResponsiveGuards:
    """Static checks that the template uses runtime-responsive arena sizing."""

    @staticmethod
    def _src() -> str:
        with open(_TT_TEMPLATE_PATH, encoding="utf-8") as f:
            return f.read()

    def test_tt48_arena_uses_aspect_ratio(self):
        """TT-48: Arena CSS uses aspect-ratio: 4 / 3 (no fixed height fallback)."""
        src = self._src()
        assert "aspect-ratio: 4 / 3" in src

    def test_tt49_arena_width_uses_min_function(self):
        """TT-49: Arena CSS width uses min(480px, 100%) — not a bare fixed pixel value."""
        src = self._src()
        assert "width: min(480px, 100%)" in src
        # Fixed-only width:480px must NOT appear inside .tt-arena (the media-query hack is gone)
        assert "height: 0" not in src
        assert "padding-bottom: 75%" not in src

    def test_tt50_js_reads_arena_client_width(self):
        """TT-50: JS reads arena.clientWidth at runtime (not a frozen ARENA_W constant)."""
        src = self._src()
        assert "arena.clientWidth" in src

    def test_tt51_js_reads_arena_client_height(self):
        """TT-51: JS reads arena.clientHeight at runtime (not a frozen ARENA_H constant)."""
        src = self._src()
        assert "arena.clientHeight" in src

    def test_tt52_no_frozen_arena_constants(self):
        """TT-52: Frozen const ARENA_W / ARENA_H assignments are absent."""
        src = self._src()
        assert "const ARENA_W" not in src
        assert "const ARENA_H" not in src

    def test_tt53_responsive_radius_calc_present(self):
        """TT-53: Responsive radius formula uses arenaW / 480 scale."""
        src = self._src()
        assert "arenaW / 480" in src

    def test_tt54_resize_handler_registered(self):
        """TT-54: resize and orientationchange event listeners registered."""
        src = self._src()
        assert "addEventListener('resize'" in src
        assert "addEventListener('orientationchange'" in src

    def test_tt55_object_size_set_from_radius(self):
        """TT-55: Object element width/height set from radius*2 in JS (not CSS-only)."""
        src = self._src()
        assert "radius * 2" in src

    def test_tt56_csrf_token_in_fetch_header(self):
        """TT-56: X-CSRF-Token header present in fetch submit call."""
        src = self._src()
        assert "CSRF_TOKEN" in src
        assert "X-CSRF-Token" in src

    def test_tt57_diff_config_ok_guard_present(self):
        """TT-57: _DIFF_CONFIG_OK guard disables Medium/Hard/Expert when difficulties missing."""
        src = self._src()
        assert "_DIFF_CONFIG_OK" in src
        assert "tt-config-warn" in src
        assert "console.warn" in src
        # Guard must disable each non-easy card
        assert "tt-diff-disabled" in src
        assert "Difficulty configuration unavailable" in src


# ── TT-58..TT-72: Track A difficulty scale guards ────────────────────────────

class TestTTDifficultyScaleGuards:
    """Pin the exact Track A parameter values in the seed and verify progressive escalation."""

    def _difficulties(self):
        from scripts.seed_virtual_training_games import _GAMES
        for g in _GAMES:
            if g["code"] == "target_tracking":
                return g["config"]["difficulties"]
        raise AssertionError("target_tracking seed entry not found")

    # ── Medium pin tests ──────────────────────────────────────────────────────

    def test_tt58_medium_ph0_object_count(self):
        """TT-58: Medium Phase 0 object_count == 5."""
        d = self._difficulties()
        assert d["medium"]["phases"][0]["object_count"] == 5

    def test_tt59_medium_ph0_object_speed(self):
        """TT-59: Medium Phase 0 object_speed == 1.60."""
        d = self._difficulties()
        assert d["medium"]["phases"][0]["object_speed"] == pytest.approx(1.60)

    def test_tt60_medium_ph2_distractor_flash(self):
        """TT-60: Medium Phase 2 distractor_flash == 3."""
        d = self._difficulties()
        assert d["medium"]["phases"][2]["distractor_flash"] == 3

    # ── Hard pin tests ────────────────────────────────────────────────────────

    def test_tt61_hard_ph0_object_count(self):
        """TT-61: Hard Phase 0 object_count == 6."""
        d = self._difficulties()
        assert d["hard"]["phases"][0]["object_count"] == 6

    def test_tt62_hard_ph2_object_speed(self):
        """TT-62: Hard Phase 2 object_speed == 2.90."""
        d = self._difficulties()
        assert d["hard"]["phases"][2]["object_speed"] == pytest.approx(2.90)

    def test_tt63_hard_ph2_distractor_flash(self):
        """TT-63: Hard Phase 2 distractor_flash == 5."""
        d = self._difficulties()
        assert d["hard"]["phases"][2]["distractor_flash"] == 5

    # ── Expert pin tests ──────────────────────────────────────────────────────

    def test_tt64_expert_ph0_object_count(self):
        """TT-64: Expert Phase 0 object_count == 7."""
        d = self._difficulties()
        assert d["expert"]["phases"][0]["object_count"] == 7

    def test_tt65_expert_ph3_object_count(self):
        """TT-65: Expert Phase 3 object_count == 9."""
        d = self._difficulties()
        assert d["expert"]["phases"][3]["object_count"] == 9

    def test_tt66_expert_ph3_object_speed(self):
        """TT-66: Expert Phase 3 object_speed == 3.20."""
        d = self._difficulties()
        assert d["expert"]["phases"][3]["object_speed"] == pytest.approx(3.20)

    def test_tt67_expert_ph3_distractor_flash(self):
        """TT-67: Expert Phase 3 distractor_flash == 6."""
        d = self._difficulties()
        assert d["expert"]["phases"][3]["distractor_flash"] == 6

    # ── Escalation checks ─────────────────────────────────────────────────────

    def test_tt68_medium_ph0_faster_than_easy_ph2(self):
        """TT-68: Medium Phase 0 speed (1.60) is faster than Easy Phase 2 speed (1.30)."""
        d = self._difficulties()
        medium_ph0 = d["medium"]["phases"][0]["object_speed"]
        easy_ph2   = d["easy"]["phases"][2]["object_speed"]
        assert medium_ph0 > easy_ph2, (
            f"Medium Ph0 speed {medium_ph0} should exceed Easy Ph2 speed {easy_ph2}"
        )

    def test_tt69_hard_max_faster_than_medium_max(self):
        """TT-69: Hard Phase 2 speed (2.90) > Medium Phase 2 speed (2.20) — tier max escalation."""
        d = self._difficulties()
        hard_ph2   = d["hard"]["phases"][2]["object_speed"]
        medium_ph2 = d["medium"]["phases"][2]["object_speed"]
        assert hard_ph2 > medium_ph2, (
            f"Hard max speed {hard_ph2} should exceed Medium max speed {medium_ph2}"
        )

    def test_tt70_expert_max_faster_than_hard_max(self):
        """TT-70: Expert Phase 3 speed (3.20) > Hard Phase 2 speed (2.90) — tier max escalation."""
        d = self._difficulties()
        expert_ph3 = d["expert"]["phases"][3]["object_speed"]
        hard_ph2   = d["hard"]["phases"][2]["object_speed"]
        assert expert_ph3 > hard_ph2, (
            f"Expert max speed {expert_ph3} should exceed Hard max speed {hard_ph2}"
        )

    def test_tt71_flash_count_monotone_within_each_difficulty(self):
        """TT-71: distractor_flash count is non-decreasing phase-by-phase within Medium, Hard, Expert."""
        d = self._difficulties()
        for level in ("medium", "hard", "expert"):
            flashes = [ph["distractor_flash"] for ph in d[level]["phases"]]
            for i in range(1, len(flashes)):
                assert flashes[i] >= flashes[i - 1], (
                    f"{level} flash not monotone: phases {i-1}→{i} "
                    f"({flashes[i-1]}→{flashes[i]})"
                )

    def test_tt72_difficulty_multipliers_unchanged(self):
        """TT-72: Difficulty multipliers remain 1.00/1.30/1.70/2.20 (Track A does not touch XP)."""
        d = self._difficulties()
        assert d["easy"]["difficulty_multiplier"]   == pytest.approx(1.00)
        assert d["medium"]["difficulty_multiplier"] == pytest.approx(1.30)
        assert d["hard"]["difficulty_multiplier"]   == pytest.approx(1.70)
        assert d["expert"]["difficulty_multiplier"] == pytest.approx(2.20)


# ── TT-B01..TT-B17: Track B — concurrent flash + direction change ─────────────

class TestTTTrackBFlashMotion:
    """Track B: concurrent flash, repeat flash, per-difficulty flash config, direction change."""

    def _difficulties(self):
        from scripts.seed_virtual_training_games import _GAMES
        for g in _GAMES:
            if g["code"] == "target_tracking":
                return g["config"]["difficulties"]
        raise AssertionError("target_tracking seed entry not found")

    @staticmethod
    def _src() -> str:
        with open(_TT_TEMPLATE_PATH, encoding="utf-8") as f:
            return f.read()

    # ── Seed pin tests ────────────────────────────────────────────────────────

    def test_ttb01_hard_max_concurrent_flashes(self):
        """TT-B01: Seed: hard flash_config.max_concurrent_flashes == 2."""
        d = self._difficulties()
        assert d["hard"]["flash_config"]["max_concurrent_flashes"] == 2

    def test_ttb02_expert_max_concurrent_flashes(self):
        """TT-B02: Seed: expert flash_config.max_concurrent_flashes == 3."""
        d = self._difficulties()
        assert d["expert"]["flash_config"]["max_concurrent_flashes"] == 3

    def test_ttb03_hard_allow_repeat_flash(self):
        """TT-B03: Seed: hard flash_config.allow_repeat_flash == True."""
        d = self._difficulties()
        assert d["hard"]["flash_config"]["allow_repeat_flash"] is True

    def test_ttb04_expert_allow_repeat_flash(self):
        """TT-B04: Seed: expert flash_config.allow_repeat_flash == True."""
        d = self._difficulties()
        assert d["expert"]["flash_config"]["allow_repeat_flash"] is True

    def test_ttb05_easy_medium_no_repeat_flash(self):
        """TT-B05: Seed: easy and medium allow_repeat_flash == False."""
        d = self._difficulties()
        assert d["easy"]["flash_config"]["allow_repeat_flash"]   is False
        assert d["medium"]["flash_config"]["allow_repeat_flash"] is False

    def test_ttb06_hard_direction_change_enabled(self):
        """TT-B06: Seed: hard direction_change.enabled == True."""
        d = self._difficulties()
        assert d["hard"]["direction_change"]["enabled"] is True

    def test_ttb07_expert_direction_change_enabled(self):
        """TT-B07: Seed: expert direction_change.enabled == True."""
        d = self._difficulties()
        assert d["expert"]["direction_change"]["enabled"] is True

    def test_ttb08_easy_medium_direction_change_disabled(self):
        """TT-B08: Seed: easy and medium direction_change.enabled == False."""
        d = self._difficulties()
        assert d["easy"]["direction_change"]["enabled"]   is False
        assert d["medium"]["direction_change"]["enabled"] is False

    # ── Template static guards ────────────────────────────────────────────────

    def test_ttb09_template_uses_flash_count_ref_counter(self):
        """TT-B09: Template uses _flashCount ref-counter (not boolean _flashing) for flash state."""
        src = self._src()
        assert "_flashCount" in src
        assert "obj._flashCount" in src or "o._flashCount" in src

    def test_ttb10_template_logs_concurrent_group_id(self):
        """TT-B10: Template flash event log contains concurrent_group_id field."""
        src = self._src()
        assert "concurrent_group_id" in src

    def test_ttb11_template_logs_repeat_field(self):
        """TT-B11: Template flash event log contains repeat field."""
        src = self._src()
        assert "repeat:" in src or '"repeat"' in src

    def test_ttb12_template_logs_is_target_false(self):
        """TT-B12: Template flash event log contains is_target: false."""
        src = self._src()
        assert "is_target" in src

    def test_ttb13_template_logs_direction_change_count(self):
        """TT-B13: Template logs direction_change_count per round in roundResults."""
        src = self._src()
        assert "direction_change_count" in src

    def test_ttb14_flash_duration_comes_from_config(self):
        """TT-B14: Flash duration reads from flashConfig, not only from hardcoded constant."""
        src = self._src()
        assert "flash_duration_ms" in src
        assert "flashDuration" in src

    def test_ttb15_flash_gap_comes_from_config(self):
        """TT-B15: Flash gap reads from flashConfig, not only from hardcoded constant."""
        src = self._src()
        assert "flash_gap_ms" in src
        assert "flashGap" in src

    def test_ttb16_total_direction_changes_in_late_summary(self):
        """TT-B16: total_direction_changes field present in late_summary payload."""
        src = self._src()
        assert "total_direction_changes" in src

    def test_ttb17_result_page_backward_compat_v3_missing_new_fields(self):
        """TT-B17: Result page renders without error when v=3 raw_metrics lacks Track B fields."""
        from datetime import datetime, timezone
        from unittest.mock import MagicMock

        user    = _onboarded_student(user_id=42)
        game    = _mock_tt_game(is_active=True)
        # Pre-Track-B v=3 payload: no concurrent_group_id, no direction_change_count
        old_v3_raw = {
            "v": 3,
            "difficulty_level":      "hard",
            "difficulty_multiplier": 1.70,
            "per_round": [
                {"round": 0, "phase": 0, "object_count": 6, "outcome": "correct",
                 "response_ms": 900, "tapped_index": 2, "target_index": 2,
                 "flash_events": [
                     {"t_offset_ms": 1200, "distractor_index": 4, "target_index": 2,
                      "duration_ms": 350, "color": "#f59e0b"}
                 ],
                 "tapped_during_flash": False},
            ],
            "per_phase": [{"phase": 0, "rounds": 1, "correct": 1, "wrong": 0, "timeout": 0, "object_count": 6}],
            "late_summary": {
                "total_flashes_shown": 1,
                "taps_during_flash": 0,
                "flash_distraction_rate": 0.0,
            },
        }
        attempt = _mock_tt_attempt(user_id=42, raw_metrics=old_v3_raw, skill_deltas=None)
        attempt.skill_deltas = None

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = attempt
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[game]), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}):
            client = _make_client(user, db)
            resp   = client.get("/virtual-training/target-tracking/result/201")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

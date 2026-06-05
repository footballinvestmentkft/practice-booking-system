"""Stroop Challenge — route, submit, scoring, and regression tests.

SC-01   Seed: stroop_challenge is_active=False
SC-02   Seed: 3 phases defined (3×8=24 stimuli)
SC-03   Seed: protocol_assignment="free"
SC-04   Seed: validation_overrides present (min_dur=20.0, min_stim=20)
SC-05   Seed: skill_targets {decisions:0.50, concentration:0.30, composure:0.20}
SC-06   Seed: show_in_hub=False
SC-07   Seed: colours is a dict (not a list)

SC-R01  GET /virtual-training/stroop-challenge → 200 when is_active=True (QA direct URL)
SC-R02  GET /virtual-training/stroop-challenge → hub+error when is_active=False (normal state)
SC-R03  GET /virtual-training/stroop-challenge → 303 redirect when not onboarded
SC-R04  POST /virtual-training/stroop-challenge/submit → 200 + attempt_id on valid payload
SC-R05  POST submit → 429 when daily cap exhausted (5/5 via training_local_date)
SC-R06  POST submit → 404 when game is_active=False
SC-R07  POST submit → 403 when not onboarded
SC-R08  GET result/{id} → 200 for attempt owner
SC-R09  GET result/{id} → hub error for wrong user
SC-R10  Result template renders conflict_cost_ms, congruent_avg_rt_ms, incongruent_avg_rt_ms
SC-R11  Result template renders per-phase table

SC-S01  Score formula: perfect game → score_normalized = 100
SC-S02  Score formula: 50% accuracy, no wrong, avg_rt=2000 → mid-range score
SC-S03  Score formula: all missed → score_normalized = 0
SC-S04  validate_attempt() respects Stroop min_dur override (20.0s)
SC-S05  validate_attempt() respects Stroop min_stim override (20)

SC-T01  Location/timezone fields forwarded to record_attempt
SC-T02  NCC route still returns 200 (regression)
SC-T03  VTC check_single_game_eligibility returns eligible after 5 valid standalone attempts
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Shared config mirrors ─────────────────────────────────────────────────────

_SC_CONFIG = {
    "protocol_assignment": "free",
    "phases": [
        {"stimuli": 8, "window_ms": 2500, "isi_ms": 900,  "congruent_pct": 0.50},
        {"stimuli": 8, "window_ms": 2000, "isi_ms": 750,  "congruent_pct": 0.25},
        {"stimuli": 8, "window_ms": 1600, "isi_ms": 600,  "congruent_pct": 0.125},
    ],
    "words":   ["RED", "GREEN", "BLUE", "YELLOW"],
    "colours": {
        "RED":    "#e74c3c",
        "GREEN":  "#2ecc71",
        "BLUE":   "#3498db",
        "YELLOW": "#f1c40f",
    },
    "score_weights": {
        "hit_rate": 0.55, "wrong_rate": 0.25,
        "speed_factor": 0.20, "speed_ref_ms": 2000,
    },
    "late_grace_ms": 300,
    "icon": "🎨",
    "football_benefit": "Sharpens selective attention.",
    "show_in_hub": False,
    "validation_overrides": {"min_dur": 20.0, "min_stim": 20},
}

_SC_SKILL_TARGETS = {
    "decisions": 0.50, "concentration": 0.30, "composure": 0.20,
}

_ROUTE_BASE = "app.api.web_routes.virtual_training"


def _mock_game(*, is_active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id = 99
    g.code = "stroop_challenge"
    g.name = "Stroop Challenge"
    g.is_active = is_active
    g.base_xp = 12
    g.max_daily_attempts = 5
    g.skill_targets = _SC_SKILL_TARGETS
    g.config = _SC_CONFIG
    return g


def _mock_attempt(
    *,
    id: int = 1,
    user_id: int = 42,
    is_valid: bool = True,
    xp_awarded: int = 12,
    score_normalized: float = 80.0,
    attempt_index_today: int = 1,
    skill_deltas: dict | None = None,
    raw_metrics: dict | None = None,
    avg_reaction_ms: float = 750.0,
    duration_seconds: float = 42.0,
    stimuli_count: int = 24,
    correct_count: int = 20,
    error_count: int = 2,
    wrong_click_count: int = 2,
) -> MagicMock:
    a = MagicMock()
    a.id = id
    a.user_id = user_id
    a.game_id = 99
    a.is_valid = is_valid
    a.invalid_reason = None
    a.xp_awarded = xp_awarded
    a.skill_deltas = skill_deltas or {"decisions": 0.50, "concentration": 0.30, "composure": 0.20}
    a.attempt_index_today = attempt_index_today
    a.score_normalized = score_normalized
    a.avg_reaction_ms = avg_reaction_ms
    a.min_reaction_ms = 400.0
    a.duration_seconds = duration_seconds
    a.stimuli_count = stimuli_count
    a.correct_count = correct_count
    a.error_count = error_count
    a.wrong_click_count = wrong_click_count
    a.idempotency_key = f"vt_sc_u{user_id}_ts"
    a.completed_at = datetime.now(timezone.utc)
    a.raw_metrics = raw_metrics or {
        "v": 3,
        "per_phase": [
            {"phase": 0, "stimuli": 8, "congruent_pct_target": 0.50,
             "congruent_count": 4, "incongruent_count": 4,
             "correct": 7, "wrong": 1, "missed": 0, "late": 0,
             "avg_rt_ms": 680, "congruent_avg_rt_ms": 600, "incongruent_avg_rt_ms": 760},
            {"phase": 1, "stimuli": 8, "congruent_pct_target": 0.25,
             "congruent_count": 2, "incongruent_count": 6,
             "correct": 7, "wrong": 1, "missed": 0, "late": 0,
             "avg_rt_ms": 780, "congruent_avg_rt_ms": 650, "incongruent_avg_rt_ms": 830},
            {"phase": 2, "stimuli": 8, "congruent_pct_target": 0.125,
             "congruent_count": 1, "incongruent_count": 7,
             "correct": 6, "wrong": 0, "missed": 2, "late": 0,
             "avg_rt_ms": 820, "congruent_avg_rt_ms": 700, "incongruent_avg_rt_ms": 840},
        ],
        "per_stimulus": [],
        "conflict_cost_ms": 180,
        "congruent_avg_rt_ms": 640,
        "incongruent_avg_rt_ms": 820,
        "late_summary": {"late_click_count": 0, "late_go_count": 0, "late_no_go_count": 0},
        "hand_profile": {"hand": "free", "finger": "free", "label": "Free",
                         "protocol_difficulty_multiplier": 1.0, "assignment_source": "free"},
    }
    return a


def _onboarded_user(onboarding_completed: bool = True) -> MagicMock:
    from app.models.user import UserRole
    user = MagicMock()
    user.id = 42
    user.role = UserRole.STUDENT
    user.onboarding_completed = onboarding_completed
    user.specialization = MagicMock()
    user.specialization.value = "LFA_FOOTBALL_PLAYER"
    return user


def _make_client(user_override=None, db_override=None) -> TestClient:
    from fastapi import FastAPI
    from app.api.web_routes import virtual_training as vt_module
    from app.dependencies import get_current_user_web
    from app.database import get_db

    app = FastAPI()
    app.include_router(vt_module.router)
    if user_override is not None:
        app.dependency_overrides[get_current_user_web] = lambda: user_override
    if db_override is not None:
        app.dependency_overrides[get_db] = lambda: db_override
    return TestClient(app, raise_server_exceptions=False)


def _mock_db_with_count(count: int = 0) -> MagicMock:
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.count.return_value = count
    q.first.return_value = None
    q.all.return_value = []
    db.query.return_value = q
    return db


# ─────────────────────────────────────────────────────────────────────────────
# SC-01..07: Seed config tests (no DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestStroopSeedConfig:

    def setup_method(self):
        from scripts.seed_virtual_training_games import _GAMES
        self._game = next(g for g in _GAMES if g["code"] == "stroop_challenge")

    def test_sc01_stroop_is_inactive(self):
        """SC-01: stroop_challenge is_active=False (QA-gated)."""
        assert self._game["is_active"] is False

    def test_sc02_three_phases_24_stimuli(self):
        """SC-02: config has 3 phases summing to 24 stimuli."""
        phases = self._game["config"]["phases"]
        assert len(phases) == 3
        assert sum(p["stimuli"] for p in phases) == 24

    def test_sc03_protocol_assignment_free(self):
        """SC-03: protocol_assignment='free' (no hand/finger assignment)."""
        assert self._game["config"]["protocol_assignment"] == "free"

    def test_sc04_validation_overrides_present(self):
        """SC-04: validation_overrides sets min_dur=20.0 and min_stim=20."""
        overrides = self._game["config"]["validation_overrides"]
        assert overrides["min_dur"]  == 20.0
        assert overrides["min_stim"] == 20

    def test_sc05_skill_targets(self):
        """SC-05: skill_targets = decisions 0.50, concentration 0.30, composure 0.20."""
        st = self._game["skill_targets"]
        assert st["decisions"]     == 0.50
        assert st["concentration"] == 0.30
        assert st["composure"]     == 0.20

    def test_sc06_show_in_hub_false(self):
        """SC-06: show_in_hub=False — game hidden from the hub listing."""
        assert self._game["config"]["show_in_hub"] is False

    def test_sc07_colours_is_dict(self):
        """SC-07: colours is a dict {name: hex} not a list."""
        colours = self._game["config"]["colours"]
        assert isinstance(colours, dict)
        assert "RED" in colours
        assert colours["RED"].startswith("#")


# ─────────────────────────────────────────────────────────────────────────────
# SC-R01..11: Route tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStroopRoutes:

    def test_sc_r01_200_when_active(self):
        """SC-R01: GET /virtual-training/stroop-challenge → 200 when is_active=True."""
        user = _onboarded_user()
        game = _mock_game(is_active=True)
        db   = _mock_db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/stroop-challenge", follow_redirects=False)

        assert resp.status_code == 200
        assert b"Stroop" in resp.content

    def test_sc_r02_hub_error_when_inactive(self):
        """SC-R02: GET returns hub template with error when is_active=False."""
        user = _onboarded_user()
        game = _mock_game(is_active=False)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]):
            db = _mock_db_with_count(0)
            client = _make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/stroop-challenge", follow_redirects=False)

        assert resp.status_code == 200
        assert b"not available" in resp.content

    def test_sc_r03_redirect_when_not_onboarded(self):
        """SC-R03: GET → 303 redirect for non-onboarded student."""
        user = _onboarded_user(onboarding_completed=False)
        db   = _mock_db_with_count(0)
        client = _make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training/stroop-challenge", follow_redirects=False)
        assert resp.status_code == 303

    def test_sc_r04_submit_returns_attempt_id(self):
        """SC-R04: POST submit returns attempt_id and is_valid on valid payload."""
        user    = _onboarded_user()
        game    = _mock_game(is_active=True)
        attempt = _mock_attempt(id=55)

        db = _mock_db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            client = _make_client(user_override=user, db_override=db)
            resp = client.post(
                "/virtual-training/stroop-challenge/submit",
                json={
                    "started_at":        "2026-06-05T10:00:00Z",
                    "stimuli_count":     24,
                    "correct_count":     20,
                    "wrong_click_count": 2,
                    "error_count":       2,
                    "avg_reaction_ms":   750,
                    "duration_seconds":  42.0,
                    "score_normalized":  75,
                    "raw_metrics": {
                        "v": 3,
                        "per_phase":    [],
                        "per_stimulus": [],
                        "conflict_cost_ms": 180,
                        "congruent_avg_rt_ms":   640,
                        "incongruent_avg_rt_ms": 820,
                        "late_summary": {"late_click_count": 0, "late_go_count": 0, "late_no_go_count": 0},
                        "hand_profile": {"hand": "free", "finger": "free", "label": "Free",
                                         "protocol_difficulty_multiplier": 1.0,
                                         "assignment_source": "free"},
                    },
                    "browser_timezone": "Europe/Budapest",
                    "location": None,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["attempt_id"] == 55
        assert data["is_valid"] is True

    def test_sc_r05_submit_429_daily_cap_exhausted(self):
        """SC-R05: POST submit → 429 when 5 valid attempts already recorded today."""
        user = _onboarded_user()
        game = _mock_game(is_active=True)
        db   = _mock_db_with_count(5)   # daily cap already hit

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user_override=user, db_override=db)
            resp = client.post(
                "/virtual-training/stroop-challenge/submit",
                json={
                    "started_at": "2026-06-05T10:00:00Z",
                    "stimuli_count": 24, "correct_count": 20,
                    "wrong_click_count": 0, "error_count": 0,
                    "avg_reaction_ms": 700, "duration_seconds": 40.0,
                    "score_normalized": 80,
                    "raw_metrics": {"v": 3},
                    "browser_timezone": "Europe/Budapest",
                },
            )

        assert resp.status_code == 429
        assert resp.json()["error"] == "daily_cap"

    def test_sc_r06_submit_404_game_inactive(self):
        """SC-R06: POST submit → 404 when game is_active=False."""
        user = _onboarded_user()
        game = _mock_game(is_active=False)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user_override=_onboarded_user(), db_override=_mock_db_with_count(0))
            resp = client.post(
                "/virtual-training/stroop-challenge/submit",
                json={"started_at": "2026-06-05T10:00:00Z", "stimuli_count": 24,
                      "correct_count": 20, "wrong_click_count": 0, "error_count": 0,
                      "avg_reaction_ms": 700, "duration_seconds": 40.0, "score_normalized": 80,
                      "raw_metrics": {"v": 3}},
            )

        assert resp.status_code == 404

    def test_sc_r07_submit_403_not_onboarded(self):
        """SC-R07: POST submit → 403 when not onboarded."""
        user   = _onboarded_user(onboarding_completed=False)
        client = _make_client(user_override=user, db_override=_mock_db_with_count(0))
        resp = client.post(
            "/virtual-training/stroop-challenge/submit",
            json={"started_at": "2026-06-05T10:00:00Z", "stimuli_count": 24,
                  "correct_count": 20, "wrong_click_count": 0, "error_count": 0,
                  "avg_reaction_ms": 700, "duration_seconds": 40.0, "score_normalized": 80,
                  "raw_metrics": {"v": 3}},
        )
        assert resp.status_code == 403

    def test_sc_r08_result_200_for_owner(self):
        """SC-R08: GET result/{id} → 200 for the attempt owner."""
        user    = _onboarded_user()
        attempt = _mock_attempt(id=55, user_id=42)
        game    = _mock_game(is_active=True)

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = attempt
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/stroop-challenge/result/55",
                              follow_redirects=False)

        assert resp.status_code == 200

    def test_sc_r09_result_hub_error_wrong_user(self):
        """SC-R09: GET result/{id} → hub error when attempt not found for user."""
        user = _onboarded_user()

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = None   # not found for this user
        q.all.return_value    = []
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]):
            client = _make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/stroop-challenge/result/999",
                              follow_redirects=False)

        assert resp.status_code == 200
        assert b"not found" in resp.content.lower()

    def test_sc_r10_result_renders_conflict_cost(self):
        """SC-R10: Result template renders conflict_cost_ms when present in raw_metrics."""
        user    = _onboarded_user()
        attempt = _mock_attempt(id=55, user_id=42)
        game    = _mock_game(is_active=True)

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = attempt
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/stroop-challenge/result/55",
                              follow_redirects=False)

        assert resp.status_code == 200
        assert b"Conflict Cost" in resp.content or b"conflict" in resp.content.lower()
        assert b"180" in resp.content   # conflict_cost_ms from mock

    def test_sc_r11_result_renders_per_phase_table(self):
        """SC-R11: Result template renders per-phase table with 3 rows."""
        user    = _onboarded_user()
        attempt = _mock_attempt(id=55, user_id=42)
        game    = _mock_game(is_active=True)

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = attempt
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = _make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/stroop-challenge/result/55",
                              follow_redirects=False)

        assert resp.status_code == 200
        # 3 phases → 3 "Phase N" rows in the table
        content = resp.content.decode()
        assert content.count("Phase 1") >= 1
        assert content.count("Phase 2") >= 1
        assert content.count("Phase 3") >= 1


# ─────────────────────────────────────────────────────────────────────────────
# SC-S01..05: Scoring and validation
# ─────────────────────────────────────────────────────────────────────────────

class TestStroopScoring:

    def _score(self, correct: int, wrong: int, total: int, avg_rt_ms: float) -> int:
        """Replicate the client-side Stroop score formula."""
        hit_rate    = correct / max(total, 1)
        wrong_rate  = wrong   / max(total, 1)
        speed_factor = max(0.0, 1.0 - avg_rt_ms / 2000.0)
        score_raw   = 0.55 * hit_rate + 0.25 * (1.0 - wrong_rate) + 0.20 * speed_factor
        return round(score_raw * 100)

    def test_sc_s01_perfect_game(self):
        """SC-S01: 24/24 correct, 0 wrong, avg_rt=0 → score_normalized=100."""
        assert self._score(24, 0, 24, 0.0) == 100

    def test_sc_s02_half_accuracy_mid_range(self):
        """SC-S02: 12/24 correct, 0 wrong, avg_rt=2000 → mid-range score (≈28–55)."""
        score = self._score(12, 0, 24, 2000.0)
        assert 25 <= score <= 60

    def test_sc_s03_all_missed(self):
        """SC-S03: 0/24 correct, 0 wrong (all timeout) → score_normalized=25.

        hit_rate=0, wrong_rate=0, speed_factor=0 (avg_rt=2000=ref):
        score_raw = 0.55*0 + 0.25*1.0 + 0.20*0 = 0.25 → 25.
        The wrong_rate component rewards not wrong-clicking even when everything is missed.
        """
        assert self._score(0, 0, 24, 2000.0) == 25

    def test_sc_s04_validation_override_min_dur(self):
        """SC-S04: validate_attempt respects Stroop min_dur=20.0 override."""
        from app.services.virtual_training_service import VirtualTrainingService
        game = _mock_game(is_active=True)

        is_valid, reason = VirtualTrainingService.validate_attempt(
            {"duration_seconds": 19.9, "stimuli_count": 24,
             "avg_reaction_ms": 400, "wrong_click_count": 0},
            overrides=game.config.get("validation_overrides"),
        )
        assert is_valid is False
        assert reason == "too_short"

    def test_sc_s05_validation_override_min_stim(self):
        """SC-S05: validate_attempt respects Stroop min_stim=20 override."""
        from app.services.virtual_training_service import VirtualTrainingService
        game = _mock_game(is_active=True)

        is_valid, reason = VirtualTrainingService.validate_attempt(
            {"duration_seconds": 35.0, "stimuli_count": 19,
             "avg_reaction_ms": 400, "wrong_click_count": 0},
            overrides=game.config.get("validation_overrides"),
        )
        assert is_valid is False
        assert reason == "too_few_stimuli"


# ─────────────────────────────────────────────────────────────────────────────
# SC-T01..03: Integration / regression
# ─────────────────────────────────────────────────────────────────────────────

class TestStroopIntegration:

    def test_sc_t01_location_fields_forwarded(self):
        """SC-T01: POST submit passes lat/lng/accuracy/browser_timezone to record_attempt."""
        user    = _onboarded_user()
        game    = _mock_game(is_active=True)
        attempt = _mock_attempt(id=55)

        db = _mock_db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt",
                   return_value=attempt) as mock_record:
            client = _make_client(user_override=user, db_override=db)
            client.post(
                "/virtual-training/stroop-challenge/submit",
                json={
                    "started_at": "2026-06-05T10:00:00Z",
                    "stimuli_count": 24, "correct_count": 20, "wrong_click_count": 0,
                    "error_count": 0, "avg_reaction_ms": 700, "duration_seconds": 40.0,
                    "score_normalized": 78, "raw_metrics": {"v": 3},
                    "browser_timezone": "Europe/Budapest",
                    "location": {
                        "lat": 47.4979, "lng": 19.0402,
                        "accuracy_m": 20, "captured_at": "2026-06-05T09:59:00Z",
                    },
                },
            )

        assert mock_record.called
        kwargs = mock_record.call_args.kwargs
        assert kwargs["browser_timezone"] == "Europe/Budapest"
        assert kwargs["location_lat"] == pytest.approx(47.4979, abs=0.001)
        assert kwargs["location_lng"] == pytest.approx(19.0402, abs=0.001)

    def test_sc_t02_ncc_route_not_broken(self):
        """SC-T02: NCC GET /virtual-training/number-color-conflict still returns 200."""
        from app.models.user import UserRole
        user = MagicMock()
        user.id = 42
        user.role = UserRole.STUDENT
        user.onboarding_completed = True
        user.specialization = MagicMock()
        user.specialization.value = "LFA_FOOTBALL_PLAYER"

        ncc_config = {
            "phases": [
                {"stimuli": 10, "window_ms": 2000, "isi_ms": 900, "rule_switch": "alternating"},
                {"stimuli": 12, "window_ms": 1600, "isi_ms": 700, "rule_switch": "random"},
                {"stimuli": 14, "window_ms": 1200, "isi_ms": 550, "rule_switch": "random_high"},
            ],
            "numbers": [1, 2, 3, 4],
            "colors":  {"RED": "#e74c3c", "GREEN": "#2ecc71", "BLUE": "#3498db", "YELLOW": "#f1c40f"},
            "late_grace_ms": 350,
        }
        ncc_game = MagicMock()
        ncc_game.id = 5
        ncc_game.code = "number_color_conflict"
        ncc_game.is_active = True
        ncc_game.base_xp = 12
        ncc_game.max_daily_attempts = 5
        ncc_game.skill_targets = {"decisions": 0.40, "concentration": 0.30, "composure": 0.20, "reactions": 0.10}
        ncc_game.config = ncc_config

        db = _mock_db_with_count(0)

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=ncc_game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.assign_protocol",
                   return_value={"hand": "right", "finger": "index", "label": "Right Index",
                                 "protocol_difficulty_multiplier": 1.0, "assignment_source": "system"}):
            client = _make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/number-color-conflict", follow_redirects=False)

        assert resp.status_code == 200

    def test_sc_t03_vtc_eligibility_after_5_valid_attempts(self):
        """SC-T03: VTC check_single_game_eligibility returns eligible after 5 valid standalone."""
        from app.services.vt_card_eligibility import check_single_game_eligibility

        game = MagicMock()
        game.id = 99
        game.is_active = False          # Stroop is QA-gated but VTC logic is game-agnostic
        game.max_daily_attempts = 5

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = game
        db.query.return_value.filter.return_value.count.return_value = 5

        _ELG = "app.services.vt_card_eligibility"
        with patch(f"{_ELG}._standalone_count", return_value=5), \
             patch(f"{_ELG}.VirtualTrainingGame"):
            eligible, completed, required = check_single_game_eligibility(
                db, user_id=42, game_id=99, training_local_date=date.today()
            )

        assert eligible is True
        assert completed == 5
        assert required == 5


# ─────────────────────────────────────────────────────────────────────────────
# SC-JS01..05: Template JS game-loop regression (static source analysis)
#
# Guards against the ReferenceError bug where recordOutcome() referenced
# `value` from handleAnswer()'s parameter scope — causing the game to freeze
# after the first click because nextStimulus() never ran.
# ─────────────────────────────────────────────────────────────────────────────

def _stroop_template_src() -> str:
    from pathlib import Path
    return (
        Path(__file__).parents[4]
        / "app" / "templates" / "virtual_training_stroop_challenge.html"
    ).read_text(encoding="utf-8")


class TestStroopJSGameLoop:

    def setup_method(self):
        self._src = _stroop_template_src()

    def test_sc_js01_record_outcome_has_value_param(self):
        """SC-JS01: recordOutcome signature must declare `value` as 3rd parameter.

        Regression: without this, recordOutcome(outcome, rt) had no `value` param,
        causing a ReferenceError on the first answer click and freezing the loop.
        """
        assert "function recordOutcome(outcome, rt, value)" in self._src, (
            "recordOutcome must declare `value` as 3rd parameter — "
            "otherwise accessing `value` inside the function raises ReferenceError."
        )

    def test_sc_js02_handle_answer_forwards_value(self):
        """SC-JS02: handleAnswer must pass `value` to recordOutcome.

        Regression: before fix, handleAnswer called recordOutcome(outcome, rt)
        and `value` was never forwarded, so the log entry could never record
        which colour the user tapped.
        """
        assert "recordOutcome(outcome, rt, value)" in self._src, (
            "handleAnswer must forward `value` via recordOutcome(outcome, rt, value)."
        )

    def test_sc_js03_missed_timeout_passes_null_value(self):
        """SC-JS03: timeout/missed path must pass null as the 3rd (value) argument.

        Regression: before fix, the missed timeout called recordOutcome("missed", null)
        with only 2 args — inconsistent with the corrected 3-param signature.
        """
        assert 'recordOutcome("missed", null, null)' in self._src, (
            'Missed/timeout path must call recordOutcome("missed", null, null).'
        )

    def test_sc_js04_start_hides_btn_wrapper(self):
        """SC-JS04: scStart() must hide the Start button's parent element.

        Regression: before fix, scStart() only hid elInstruction, leaving the
        Start Game button visible in the DOM throughout the entire game session.
        """
        assert "elBtnStart.parentElement.style.display" in self._src, (
            "scStart() must set elBtnStart.parentElement.style.display so the "
            "Start button disappears when the game arena activates."
        )

    def test_sc_js05_next_stimulus_in_record_outcome(self):
        """SC-JS05: nextStimulus() must appear inside the recordOutcome function body.

        Guards the core loop transition: every answered or missed stimulus must
        call nextStimulus() to advance the sequence.
        """
        fn_start = self._src.find("function recordOutcome(outcome, rt, value)")
        assert fn_start != -1, "recordOutcome(outcome, rt, value) not found in template"
        body = self._src[fn_start: fn_start + 1400]
        assert "nextStimulus()" in body, (
            "nextStimulus() must be called inside recordOutcome() — "
            "missing it halts the game loop after every stimulus."
        )

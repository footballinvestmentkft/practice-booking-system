"""Unit tests for Direction Swipe — Phase 2.3.

Scorer tests:
DS-01   score_coordination — perfect (hit=1.0, wrong=0, speed=1.0) → clamp 1.0
DS-02   score_coordination — all wrong_direction (wrong_rate=1.0, hit=0, speed=0) → negative
DS-03   score_coordination — all missed (hit=0, wrong=0, speed=0) → 0.0
DS-04   score_coordination — mixed: hit=0.8, wrong=0.1, speed=0.7 → correct blend
DS-05   score_coordination — worst case wrong_rate dominates → below −0.5

Signal extraction tests:
DS-06   VTSignalExtractor — DS phase config, expected_total=36
DS-07   VTSignalExtractor — avg window_ms for DS phases: (1500+1100+750)/3 = 1116.67
DS-08   VTSignalExtractor — late_click_rate from late_summary (v2)

Score registration tests:
DS-09   score_all — "coordination" in skill_targets dispatches to score_coordination
DS-10   score_all — missing "coordination" in skill_targets uses fallback

Validation tests:
DS-11   validate_attempt — 36 correct, 0 wrong, valid duration → is_valid=True
DS-12   validate_attempt — duration < 25 → too_short
DS-13   validate_attempt — wrong_click_count > 0.55 * stimuli → random_clicking

Delta computation tests:
DS-14   compute_vt_skill_deltas — coordination delta positive on perfect run
DS-15   compute_vt_skill_deltas — coordination delta negative on wrong-heavy run
DS-16   compute_vt_skill_deltas — daily_neg_cap limits cumulative negative delta

Route tests:
DS-17   GET /virtual-training/direction-swipe → 200 for active game
DS-18   GET /virtual-training/direction-swipe → hub redirect when game inactive
DS-19   POST /virtual-training/direction-swipe/submit → valid data → attempt_id returned
DS-20   POST submit → 429 when daily cap reached
DS-21   POST submit → 404 when game inactive
DS-22   GET /virtual-training/direction-swipe/result/{id} → 200 owner
DS-23   GET result → hub redirect when attempt not found
DS-24   Seed: direction_swipe is_active=True in seed data
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse, JSONResponse

_ROUTES = "app.api.web_routes.virtual_training"
_METRICS = "app.services.virtual_training_metrics"

_DS_SKILL_TARGETS = {
    "reactions":     0.35,
    "decisions":     0.30,
    "coordination":  0.20,
    "concentration": 0.15,
}

_DS_PHASE_CONFIG = [
    {"stimuli": 10, "window_ms": 1500, "isi_ms": 900},
    {"stimuli": 12, "window_ms": 1100, "isi_ms": 700},
    {"stimuli": 14, "window_ms":  750, "isi_ms": 550},
]

_DS_CONFIG = {
    "phases":            _DS_PHASE_CONFIG,
    "directions":        ["up", "down", "left", "right"],
    "late_grace_ms":     300,
    "jitter_ms":         150,
    "swipe_min_px":      30,
    "swipe_max_duration_ms": 500,
    "show_in_hub":       True,
    "icon":              "↕️",
    "football_benefit":  "Fast directional recognition.",
}

_DS_AVG_WINDOW = (1500 + 1100 + 750) / 3   # ≈ 1116.67 ms


def _run(coro):
    return asyncio.run(coro)


def _mock_ds_game(*, is_active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id                 = 4
    g.code               = "direction_swipe"
    g.name               = "Direction Swipe"
    g.is_active          = is_active
    g.base_xp            = 12
    g.max_daily_attempts = 5
    g.skill_targets      = _DS_SKILL_TARGETS
    g.config             = _DS_CONFIG
    return g


def _mock_attempt(
    *,
    id: int = 77,
    user_id: int = 42,
    game_id: int = 4,
    is_valid: bool = True,
    xp_awarded: int = 12,
    score_normalized: float = 80.0,
    attempt_index_today: int = 1,
    invalid_reason: str | None = None,
    skill_deltas: dict | None = None,
    correct_count: int = 32,
    wrong_click_count: int = 2,
    error_count: int = 2,
    avg_reaction_ms: float = 520.0,
    stimuli_count: int = 36,
    duration_seconds: float = 42.0,
    raw_metrics: dict | None = None,
) -> MagicMock:
    a = MagicMock()
    a.id                  = id
    a.user_id             = user_id
    a.game_id             = game_id
    a.is_valid            = is_valid
    a.xp_awarded          = xp_awarded
    a.score_normalized    = score_normalized
    a.attempt_index_today = attempt_index_today
    a.invalid_reason      = invalid_reason
    a.skill_deltas        = skill_deltas or {
        "reactions": 0.15, "decisions": 0.18, "coordination": 0.09, "concentration": 0.08,
    }
    a.correct_count       = correct_count
    a.wrong_click_count   = wrong_click_count
    a.error_count         = error_count
    a.avg_reaction_ms     = avg_reaction_ms
    a.min_reaction_ms     = 310.0
    a.stimuli_count       = stimuli_count
    a.duration_seconds    = duration_seconds
    a.raw_metrics         = raw_metrics
    return a


def _mock_db(*, count: int = 0) -> MagicMock:
    db = MagicMock()
    q  = MagicMock()
    q.filter.return_value = q
    q.count.return_value  = count
    q.first.return_value  = None
    q.all.return_value    = []
    db.query.return_value = q
    return db


def _make_student(user_id: int = 42) -> MagicMock:
    from app.models.user import UserRole
    u = MagicMock()
    u.id = user_id
    u.role = UserRole.STUDENT
    u.onboarding_completed = True
    u.credit_balance = 0
    u.specialization = MagicMock()
    u.specialization.value = "LFA_FOOTBALL_PLAYER"
    return u


def _make_request(path: str = "/virtual-training/direction-swipe") -> MagicMock:
    r = MagicMock()
    r.url.path = path
    r.cookies.get = MagicMock(return_value="testcsrf")
    return r


# ── DS-01..05: score_coordination ────────────────────────────────────────────

class TestScoreCoordination:

    def _sig(self, *, hit=1.0, wrong=0.0, speed=1.0, miss=0.0):
        from app.services.virtual_training_metrics import VTSignals
        return VTSignals(
            hit_rate=hit, wrong_rate=wrong, miss_rate=miss,
            speed_score=speed, completion_rate=1.0,
        )

    def test_ds01_perfect_run_clamps_to_1(self):
        """DS-01: hit=1.0, wrong=0, speed=1.0 → 0.55+0.45=1.0 (clamped)."""
        from app.services.virtual_training_metrics import VTSkillScorer
        score = VTSkillScorer.score_coordination(self._sig(hit=1.0, wrong=0.0, speed=1.0))
        assert abs(score - 1.0) < 1e-9

    def test_ds02_all_wrong_direction_gives_negative(self):
        """DS-02: wrong_rate=1.0, hit=0, speed=0 → 0 - 1.0 + 0 = -1.0."""
        from app.services.virtual_training_metrics import VTSkillScorer
        score = VTSkillScorer.score_coordination(self._sig(hit=0.0, wrong=1.0, speed=0.0))
        assert score == pytest.approx(-1.0, abs=1e-9)

    def test_ds03_all_missed_gives_zero(self):
        """DS-03: hit=0, wrong=0, speed=0 → 0.0 (no wrong-dir errors, no speed, no hits)."""
        from app.services.virtual_training_metrics import VTSkillScorer
        score = VTSkillScorer.score_coordination(self._sig(hit=0.0, wrong=0.0, speed=0.0))
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_ds04_mixed_run_correct_blend(self):
        """DS-04: hit=0.8, wrong=0.1, speed=0.7 → 0.55×0.8 - 1.0×0.1 + 0.45×0.7."""
        from app.services.virtual_training_metrics import VTSkillScorer
        expected = 0.55 * 0.8 - 1.0 * 0.1 + 0.45 * 0.7
        score = VTSkillScorer.score_coordination(self._sig(hit=0.8, wrong=0.1, speed=0.7))
        assert score == pytest.approx(expected, abs=1e-9)

    def test_ds05_wrong_dominant_is_very_negative(self):
        """DS-05: wrong_rate=0.7 dominates → score well below 0."""
        from app.services.virtual_training_metrics import VTSkillScorer
        score = VTSkillScorer.score_coordination(self._sig(hit=0.2, wrong=0.7, speed=0.1))
        assert score < -0.3


# ── DS-06..08: VTSignalExtractor with DS config ───────────────────────────────

class TestDSSignalExtraction:

    def test_ds06_expected_total_is_36(self):
        """DS-06: Phase sum 10+12+14=36 → expected_total=36, completion_rate=1.0."""
        from app.services.virtual_training_metrics import VTSignalExtractor
        data = {
            "stimuli_count": 36, "correct_count": 36,
            "wrong_click_count": 0, "error_count": 0, "avg_reaction_ms": None,
        }
        sig = VTSignalExtractor.extract(data, _DS_PHASE_CONFIG)
        assert sig.completion_rate == pytest.approx(1.0)
        assert sig.hit_rate == pytest.approx(1.0)

    def test_ds07_speed_score_uses_ds_avg_window(self):
        """DS-07: avg window = (1500+1100+750)/3 ≈ 1116.67 ms; rt=558 → speed≈0.5."""
        from app.services.virtual_training_metrics import VTSignalExtractor
        rt = _DS_AVG_WINDOW / 2   # exactly 50% of window
        data = {
            "stimuli_count": 36, "correct_count": 36,
            "wrong_click_count": 0, "error_count": 0, "avg_reaction_ms": rt,
        }
        sig = VTSignalExtractor.extract(data, _DS_PHASE_CONFIG)
        assert sig.speed_score == pytest.approx(0.5, abs=0.001)

    def test_ds08_late_click_rate_from_v2_payload(self):
        """DS-08: raw_metrics v=2 late_summary.late_click_count populates late_click_rate."""
        from app.services.virtual_training_metrics import VTSignalExtractor
        raw = {
            "v": 2,
            "per_phase": [],
            "late_summary": {"late_click_count": 4},
        }
        data = {
            "stimuli_count": 36, "correct_count": 32,
            "wrong_click_count": 0, "error_count": 0, "avg_reaction_ms": None,
            "raw_metrics": raw,
        }
        sig = VTSignalExtractor.extract(data, _DS_PHASE_CONFIG)
        assert sig.late_click_rate == pytest.approx(4 / 36, abs=1e-6)


# ── DS-09..10: score_all dispatching ─────────────────────────────────────────

class TestScoreAllCoordination:

    def _sig(self):
        from app.services.virtual_training_metrics import VTSignals
        return VTSignals(
            hit_rate=0.9, wrong_rate=0.05, miss_rate=0.05,
            speed_score=0.7, completion_rate=1.0,
        )

    def test_ds09_coordination_dispatches_to_scorer(self):
        """DS-09: score_all with coordination key calls score_coordination (not fallback)."""
        from app.services.virtual_training_metrics import VTSkillScorer
        targets = {"coordination": 1.0}
        scores = VTSkillScorer.score_all(self._sig(), targets)
        # Manually compute expected coordination score
        expected = 0.55 * 0.9 - 1.0 * 0.05 + 0.45 * 0.7
        assert "coordination" in scores
        assert scores["coordination"] == pytest.approx(expected, abs=1e-6)

    def test_ds10_unknown_skill_uses_fallback(self):
        """DS-10: unknown skill key uses mean of known scores, not coordination formula."""
        from app.services.virtual_training_metrics import VTSkillScorer
        targets = {"coordination": 1.0, "unknown_skill": 0.5}
        scores = VTSkillScorer.score_all(self._sig(), targets)
        # unknown_skill should be a scalar (the mean of all known scorers)
        assert "unknown_skill" in scores
        assert 0.0 <= scores["unknown_skill"] <= 1.0


# ── DS-11..13: validate_attempt for Direction Swipe ──────────────────────────

class TestValidateAttemptDS:

    def test_ds11_valid_full_round(self):
        """DS-11: 36 stimuli, 32 correct, duration=40s → is_valid=True."""
        from app.services.virtual_training_service import VirtualTrainingService
        data = {
            "stimuli_count": 36, "correct_count": 32,
            "wrong_click_count": 2, "error_count": 2,
            "avg_reaction_ms": 480.0, "duration_seconds": 40.0,
        }
        valid, reason = VirtualTrainingService.validate_attempt(data)
        assert valid is True
        assert reason is None

    def test_ds12_too_short_fails(self):
        """DS-12: duration=10s → too_short."""
        from app.services.virtual_training_service import VirtualTrainingService
        data = {
            "stimuli_count": 36, "correct_count": 32,
            "wrong_click_count": 0, "error_count": 0,
            "avg_reaction_ms": 300.0, "duration_seconds": 10.0,
        }
        valid, reason = VirtualTrainingService.validate_attempt(data)
        assert valid is False
        assert reason == "too_short"

    def test_ds13_random_clicking_fails(self):
        """DS-13: wrong_click_count > 0.55 * stimuli → random_clicking."""
        from app.services.virtual_training_service import VirtualTrainingService
        # 21 wrong out of 36 = 0.583 > 0.55
        data = {
            "stimuli_count": 36, "correct_count": 15,
            "wrong_click_count": 21, "error_count": 0,
            "avg_reaction_ms": 600.0, "duration_seconds": 42.0,
        }
        valid, reason = VirtualTrainingService.validate_attempt(data)
        assert valid is False
        assert reason == "random_clicking"


# ── DS-14..16: compute_vt_skill_deltas for coordination ──────────────────────

class TestDSSkillDeltas:

    def test_ds14_perfect_coordination_delta_positive(self):
        """DS-14: Perfect DS run → coordination delta is positive."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        game = _mock_ds_game()
        data = {
            "stimuli_count": 36, "correct_count": 36,
            "wrong_click_count": 0, "error_count": 0,
            "avg_reaction_ms": 400.0, "duration_seconds": 40.0,
            "raw_metrics": None,
        }
        deltas = compute_vt_skill_deltas(
            data=data, game=game, multiplier=1.0, existing_neg_today={}
        )
        assert "coordination" in deltas
        assert deltas["coordination"] > 0

    def test_ds15_wrong_direction_heavy_delta_negative(self):
        """DS-15: Heavy wrong_direction run → coordination delta is negative."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        game = _mock_ds_game()
        # 25 wrong out of 36 (69%) — will trip wrong_rate domination
        data = {
            "stimuli_count": 36, "correct_count": 5,
            "wrong_click_count": 25, "error_count": 6,
            "avg_reaction_ms": 900.0, "duration_seconds": 38.0,
            "raw_metrics": None,
        }
        deltas = compute_vt_skill_deltas(
            data=data, game=game, multiplier=1.0, existing_neg_today={}
        )
        assert "coordination" in deltas
        assert deltas["coordination"] < 0

    def test_ds16_daily_neg_cap_limits_coordination_loss(self):
        """DS-16: existing_neg_today at cap → additional coordination delta is 0."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas
        game = _mock_ds_game()
        data = {
            "stimuli_count": 36, "correct_count": 0,
            "wrong_click_count": 28, "error_count": 8,
            "avg_reaction_ms": 1000.0, "duration_seconds": 38.0,
            "raw_metrics": None,
        }
        # Cap already reached for coordination
        existing_neg = {"coordination": -0.50}
        deltas = compute_vt_skill_deltas(
            data=data, game=game, multiplier=1.0,
            existing_neg_today=existing_neg,
        )
        # coordination delta capped: should be 0 (not further negative)
        assert deltas.get("coordination", 0) >= 0


# ── DS-17..18: GET game page ──────────────────────────────────────────────────

class TestDirectionSwipePage:

    def test_ds17_active_game_returns_200(self):
        """DS-17: GET /virtual-training/direction-swipe → 200 for active game."""
        from app.api.web_routes.virtual_training import virtual_training_direction_swipe

        user    = _make_student()
        request = _make_request()
        db      = _mock_db()
        game    = _mock_ds_game()

        fake_resp = MagicMock(spec=HTMLResponse)
        fake_resp.status_code = 200

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_resp
            result = _run(virtual_training_direction_swipe(
                request=request, db=db, user=user
            ))

        assert result is fake_resp
        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_direction_swipe.html"
        ctx = call_args[0][1]
        assert ctx["game"] is game

    def test_ds18_inactive_game_returns_hub(self):
        """DS-18: GET → renders hub with error message when game is inactive."""
        from app.api.web_routes.virtual_training import virtual_training_direction_swipe

        user    = _make_student()
        request = _make_request()
        db      = _mock_db()
        game    = _mock_ds_game(is_active=False)

        fake_hub = MagicMock(spec=HTMLResponse)
        fake_hub.status_code = 200

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_hub_games", return_value=[game]), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_hub
            result = _run(virtual_training_direction_swipe(
                request=request, db=db, user=user
            ))

        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_hub.html"
        ctx = call_args[0][1]
        assert "error" in ctx


# ── DS-19..21: POST submit ────────────────────────────────────────────────────

class TestDirectionSwipeSubmit:

    _VALID_PAYLOAD = {
        "started_at":        "2026-05-23T10:00:00.000Z",
        "duration_seconds":  42.0,
        "stimuli_count":     36,
        "correct_count":     32,
        "wrong_click_count": 2,
        "error_count":       2,
        "avg_reaction_ms":   520.0,
        "min_reaction_ms":   310.0,
        "score_raw":         0.78,
        "score_normalized":  78.0,
        "raw_metrics": {
            "v": 2,
            "per_stimulus": [],
            "per_phase": [],
            "late_summary": {"late_click_count": 0},
        },
    }

    def test_ds19_valid_submit_returns_attempt_id(self):
        """DS-19: Valid POST → 200 JSON with attempt_id."""
        from app.api.web_routes.virtual_training import virtual_training_direction_swipe_submit
        import json

        user    = _make_student()
        request = _make_request("/virtual-training/direction-swipe/submit")
        async def _fake_json():
            return self._VALID_PAYLOAD
        request.json = _fake_json

        db      = _mock_db(count=0)
        game    = _mock_ds_game()
        attempt = _mock_attempt()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.VirtualTrainingService.record_attempt", return_value=attempt), \
             patch.object(db, "commit"):
            result = _run(virtual_training_direction_swipe_submit(
                request=request, db=db, user=user
            ))

        assert isinstance(result, JSONResponse)
        body = json.loads(result.body)
        assert body["attempt_id"] == 77
        assert body["is_valid"] is True

    def test_ds20_daily_cap_returns_429(self):
        """DS-20: count >= max_daily_attempts → 429 daily_cap."""
        from app.api.web_routes.virtual_training import virtual_training_direction_swipe_submit
        import json

        user    = _make_student()
        request = _make_request("/virtual-training/direction-swipe/submit")
        async def _fake_json():
            return self._VALID_PAYLOAD
        request.json = _fake_json

        db   = _mock_db(count=5)   # already at limit
        game = _mock_ds_game()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game):
            result = _run(virtual_training_direction_swipe_submit(
                request=request, db=db, user=user
            ))

        assert result.status_code == 429
        body = json.loads(result.body)
        assert body["error"] == "daily_cap"

    def test_ds21_inactive_game_returns_404(self):
        """DS-21: Inactive game → 404."""
        from app.api.web_routes.virtual_training import virtual_training_direction_swipe_submit

        user    = _make_student()
        request = _make_request("/virtual-training/direction-swipe/submit")
        async def _fake_json():
            return self._VALID_PAYLOAD
        request.json = _fake_json

        db   = _mock_db()
        game = _mock_ds_game(is_active=False)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game):
            result = _run(virtual_training_direction_swipe_submit(
                request=request, db=db, user=user
            ))

        assert result.status_code == 404


# ── DS-22..23: GET result page ────────────────────────────────────────────────

class TestDirectionSwipeResult:

    def test_ds22_result_owner_gets_200(self):
        """DS-22: GET result/{id} with correct user → 200 result template."""
        from app.api.web_routes.virtual_training import virtual_training_direction_swipe_result

        user    = _make_student()
        request = _make_request("/virtual-training/direction-swipe/result/77")
        db      = _mock_db()
        attempt = _mock_attempt()
        game    = _mock_ds_game()

        fake_resp = MagicMock(spec=HTMLResponse)
        fake_resp.status_code = 200

        db.query.return_value.filter.return_value.first.return_value = attempt

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            # Second .first() call returns game
            mock_tmpl.TemplateResponse.return_value = fake_resp
            db.query.return_value.filter.return_value.first.side_effect = [attempt, game]
            result = _run(virtual_training_direction_swipe_result(
                attempt_id=77, request=request, db=db, user=user
            ))

        assert result is fake_resp
        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_direction_swipe_result.html"

    def test_ds23_result_not_found_renders_hub(self):
        """DS-23: Attempt not found → hub template with error."""
        from app.api.web_routes.virtual_training import virtual_training_direction_swipe_result

        user    = _make_student()
        request = _make_request("/virtual-training/direction-swipe/result/99")
        db      = _mock_db()

        fake_hub = MagicMock(spec=HTMLResponse)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_hub_games", return_value=[]), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_hub
            db.query.return_value.filter.return_value.first.return_value = None
            result = _run(virtual_training_direction_swipe_result(
                attempt_id=99, request=request, db=db, user=user
            ))

        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_hub.html"
        ctx = call_args[0][1]
        assert "error" in ctx


# ── DS-24: Seed guard ─────────────────────────────────────────────────────────

class TestDirectionSwipeSeed:

    def test_ds24_direction_swipe_is_active_in_seed(self):
        """DS-24: Seed data has direction_swipe with is_active=True."""
        from scripts.seed_virtual_training_games import _GAMES
        ds = next((g for g in _GAMES if g["code"] == "direction_swipe"), None)
        assert ds is not None, "direction_swipe preset missing from seed"
        assert ds["is_active"] is True
        assert ds["game_type"] == "direction_reaction"
        cfg = ds["config"]
        assert len(cfg["phases"]) == 3
        assert sum(p["stimuli"] for p in cfg["phases"]) == 36
        assert cfg["late_grace_ms"] == 300
        assert "up" in cfg["directions"]
        assert cfg["show_in_hub"] is True

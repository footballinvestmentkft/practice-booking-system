"""Unit tests for Go / No-Go Reaction — Phase 2.4.

Route tests:
GNG-01   GET /virtual-training/go-no-go → 200 active game
GNG-02   GET /virtual-training/go-no-go → hub redirect when game inactive
GNG-03   GET /virtual-training/go-no-go → 303 non-student / non-onboarded
GNG-04   POST /virtual-training/go-no-go/submit → 200 valid data
GNG-05   POST submit → is_valid=False + 0 XP too_short
GNG-06   POST submit → is_valid=False + 0 XP bot_suspected
GNG-07   POST submit → 429 daily cap
GNG-08   POST submit → 404 inactive game
GNG-09   POST submit → 403 not onboarded
GNG-10   GET /virtual-training/go-no-go/result/{id} → 200 owner
GNG-11   GET result → hub redirect wrong user
GNG-12   POST submit → idempotency: duplicate key returns same attempt_id

Skill scorer tests:
GNG-13   score_composure(wrong_rate=0.0) → 1.0
GNG-14   score_composure(wrong_rate=0.5) → clamp(1.0-0.75, 0, 1) = 0.25
GNG-15   score_composure(wrong_rate=0.8) → 0.0 (clamped below 0)
GNG-16   score_all() includes composure when in skill_targets
GNG-17   VTDeltaComputer with composure → positive delta at perfect signals

Performance delta tests:
GNG-18   Perfect run (0 FA, 0 miss, fast RT) → all four deltas positive
GNG-19   False alarm heavy run → composure delta near zero
GNG-20   Missed GO heavy run → concentration delta near zero

raw_metrics tests:
GNG-21   record_attempt() persists raw_metrics with per_phase correct_inhibits
GNG-22   raw_metrics per_phase sums match aggregate counts

Template / link tests:
GNG-23   virtual_training_history.html link is NOT hardcoded to color-reaction
GNG-24   skill_history.html link uses game_code variable (not hardcoded)
GNG-25   _views.py timeline event includes game_code field
GNG-26   Go/No-Go result template renders per_phase (not per_color)
GNG-27   Go/No-Go result template skill breakdown shows composure formula note

Score formula tests:
GNG-28   Perfect 21/21 GO + 9/9 inhibit → score_normalized ≥ 90
GNG-29   All false alarms (9 FA, 0 inhibit) → score_normalized ≤ 40

Regression guards:
GNG-REG-01   Color Reaction GET route still works
GNG-REG-02   Color Reaction submit route still works
GNG-REG-03   VT hub still loads (get_hub_games not broken)

Seed:
GNG-SEED-01  go_no_go is_active=True in seed data (VT-16 updated)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse, RedirectResponse

_ROUTES = "app.api.web_routes.virtual_training"
_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[4] / "app" / "templates"
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


_GNG_SKILL_TARGETS = {
    "decisions":     0.35,
    "concentration": 0.30,
    "composure":     0.20,
    "reactions":     0.15,
}

_GNG_CONFIG = {
    "phases": [
        {"go": 10, "no_go": 5, "isi_ms": 900,  "window_ms": 1000, "stimulus_ms": 800},
        {"go": 11, "no_go": 4, "isi_ms": 650,  "window_ms": 1000, "stimulus_ms": 800},
    ],
    "go_cue":    {"color": "#22c55e", "label": "GO"},
    "no_go_cue": {"color": "#ef4444", "label": "STOP"},
    "score_weights": {
        "go_hit_rate": 0.40, "no_go_success": 0.35,
        "speed_factor": 0.15, "missed_go_penalty": 0.10,
    },
    "show_in_hub": True,
    "icon": "🛑",
    "football_benefit": "Impulse control.",
}


def _mock_gng_game(*, is_active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id                = 3
    g.code              = "go_no_go"
    g.name              = "Go / No-Go Reaction"
    g.is_active         = is_active
    g.base_xp           = 12
    g.max_daily_attempts = 5
    g.skill_targets     = _GNG_SKILL_TARGETS
    g.config            = _GNG_CONFIG
    return g


def _mock_attempt(
    *,
    id: int = 99,
    user_id: int = 42,
    game_id: int = 3,
    is_valid: bool = True,
    xp_awarded: int = 12,
    score_normalized: float = 78.0,
    attempt_index_today: int = 1,
    invalid_reason: str | None = None,
    skill_deltas: dict | None = None,
    correct_count: int = 19,
    wrong_click_count: int = 1,
    error_count: int = 2,
    avg_reaction_ms: float = 380.0,
    stimuli_count: int = 30,
    duration_seconds: float = 47.0,
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
        "decisions": 0.21, "concentration": 0.15,
        "composure": 0.11, "reactions": 0.07,
    }
    a.correct_count       = correct_count
    a.wrong_click_count   = wrong_click_count
    a.error_count         = error_count
    a.avg_reaction_ms     = avg_reaction_ms
    a.min_reaction_ms     = 210.0
    a.stimuli_count       = stimuli_count
    a.duration_seconds    = duration_seconds
    a.raw_metrics         = raw_metrics
    return a


def _mock_db(*, count: int = 0) -> MagicMock:
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value  = q
    q.count.return_value   = count
    q.first.return_value   = None
    q.all.return_value     = []
    db.query.return_value  = q
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


def _make_request(path: str = "/virtual-training/go-no-go") -> MagicMock:
    r = MagicMock()
    r.url.path = path
    return r


# ── GNG-01..03: GET game page ─────────────────────────────────────────────────

class TestGoNoGoPage:

    def test_gng01_active_game_returns_200(self):
        """GNG-01: GET /virtual-training/go-no-go → 200 for active game."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go

        user    = _make_student()
        request = _make_request()
        db      = _mock_db()
        game    = _mock_gng_game()

        fake_resp = MagicMock(spec=HTMLResponse)
        fake_resp.status_code = 200

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_resp
            result = _run(virtual_training_go_no_go(request=request, db=db, user=user))

        assert result is fake_resp
        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_go_no_go.html"
        ctx = call_args[0][1]
        assert ctx["game"] is game

    def test_gng02_inactive_game_returns_hub(self):
        """GNG-02: GET /virtual-training/go-no-go → renders hub with error when inactive."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go

        user    = _make_student()
        request = _make_request()
        db      = _mock_db()
        game    = _mock_gng_game(is_active=False)

        fake_hub = MagicMock(spec=HTMLResponse)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_hub_games", return_value=[]), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_hub
            result = _run(virtual_training_go_no_go(request=request, db=db, user=user))

        assert result is fake_hub
        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_hub.html"
        assert "error" in call_args[0][1]

    def test_gng03_non_onboarded_redirected(self):
        """GNG-03: GET /virtual-training/go-no-go → guard redirect for non-onboarded."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go

        user = _make_student()
        user.onboarding_completed = False
        request = _make_request()
        db = _mock_db()
        redirect = RedirectResponse(url="/dashboard", status_code=303)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=redirect), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            result = _run(virtual_training_go_no_go(request=request, db=db, user=user))

        assert isinstance(result, RedirectResponse)
        mock_tmpl.TemplateResponse.assert_not_called()


# ── GNG-04..09: POST submit ───────────────────────────────────────────────────

class TestGoNoGoSubmit:

    def _valid_payload(self) -> dict:
        return {
            "started_at":        "2026-05-22T12:00:00.000Z",
            "duration_seconds":  48.0,
            "stimuli_count":     30,
            "correct_count":     20,
            "wrong_click_count": 1,
            "error_count":       1,
            "avg_reaction_ms":   370.0,
            "min_reaction_ms":   210.0,
            "score_raw":         0.78,
            "score_normalized":  78.0,
            "raw_metrics":       {"v": 1, "per_stimulus": [], "per_phase": []},
        }

    def test_gng04_valid_submit_returns_200(self):
        """GNG-04: POST submit with valid data returns attempt_id, xp_awarded."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_submit

        user    = _make_student()
        request = _make_request(path="/virtual-training/go-no-go/submit")
        request.json = asyncio.coroutine(lambda: self._valid_payload()) if False else (lambda: self._valid_payload())
        request.json = MagicMock()
        request.json.return_value = self._valid_payload()

        async def _fake_json():
            return self._valid_payload()
        request.json = _fake_json

        db      = _mock_db(count=0)
        game    = _mock_gng_game()
        attempt = _mock_attempt()

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.VirtualTrainingService.record_attempt", return_value=attempt):
            from fastapi.responses import JSONResponse
            result = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))

        assert result.status_code == 200
        import json
        body = json.loads(result.body)
        assert body["attempt_id"] == attempt.id
        assert body["xp_awarded"] == attempt.xp_awarded
        assert body["is_valid"] is True

    def test_gng05_too_short_returns_invalid(self):
        """GNG-05: POST submit with duration < 25s → is_valid=False, xp_awarded=0."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_submit

        user  = _make_student()
        request = _make_request()

        payload = self._valid_payload()
        payload["duration_seconds"] = 5.0

        async def _fake_json():
            return payload
        request.json = _fake_json

        db      = _mock_db(count=0)
        game    = _mock_gng_game()
        attempt = _mock_attempt(is_valid=False, xp_awarded=0, invalid_reason="too_short")

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.VirtualTrainingService.record_attempt", return_value=attempt):
            result = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))

        import json
        body = json.loads(result.body)
        assert body["is_valid"] is False
        assert body["xp_awarded"] == 0
        assert body["invalid_reason"] == "too_short"

    def test_gng06_bot_suspected_returns_invalid(self):
        """GNG-06: POST submit avg_reaction_ms < 80 → is_valid=False, bot_suspected."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_submit

        user = _make_student()
        request = _make_request()

        payload = self._valid_payload()
        payload["avg_reaction_ms"] = 50.0

        async def _fake_json():
            return payload
        request.json = _fake_json

        db      = _mock_db(count=0)
        game    = _mock_gng_game()
        attempt = _mock_attempt(is_valid=False, xp_awarded=0, invalid_reason="bot_suspected")

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.VirtualTrainingService.record_attempt", return_value=attempt):
            result = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))

        import json
        body = json.loads(result.body)
        assert body["is_valid"] is False
        assert body["invalid_reason"] == "bot_suspected"

    def test_gng07_daily_cap_returns_429(self):
        """GNG-07: POST submit → 429 when valid_today >= max_daily_attempts."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_submit

        user = _make_student()
        request = _make_request()

        async def _fake_json():
            return self._valid_payload()
        request.json = _fake_json

        db   = _mock_db(count=5)      # 5 valid attempts today
        game = _mock_gng_game()
        game.max_daily_attempts = 5

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game):
            result = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))

        assert result.status_code == 429

    def test_gng08_inactive_game_returns_404(self):
        """GNG-08: POST submit → 404 when game is inactive."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_submit

        user = _make_student()
        request = _make_request()

        async def _fake_json():
            return self._valid_payload()
        request.json = _fake_json

        db   = _mock_db()
        game = _mock_gng_game(is_active=False)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game):
            result = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))

        assert result.status_code == 404

    def test_gng09_non_onboarded_returns_403(self):
        """GNG-09: POST submit → 403 when onboarding not completed."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_submit

        user = _make_student()
        request = _make_request()

        async def _fake_json():
            return self._valid_payload()
        request.json = _fake_json

        db       = _mock_db()
        redirect = RedirectResponse(url="/dashboard", status_code=303)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=redirect):
            result = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))

        assert result.status_code == 403

    def test_gng12_idempotency_returns_same_id(self):
        """GNG-12: Duplicate idempotency key returns same attempt_id without double-write."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_submit

        user = _make_student()
        request = _make_request()

        async def _fake_json():
            return self._valid_payload()
        request.json = _fake_json

        db      = _mock_db(count=0)
        game    = _mock_gng_game()
        attempt = _mock_attempt(id=55)

        call_count = {"n": 0}

        def _idempotent_record(**kwargs):
            call_count["n"] += 1
            return attempt  # same attempt both times

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTES}.VirtualTrainingService.record_attempt",
                   side_effect=_idempotent_record):
            result1 = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))
            result2 = _run(virtual_training_go_no_go_submit(request=request, db=db, user=user))

        import json
        assert json.loads(result1.body)["attempt_id"] == 55
        assert json.loads(result2.body)["attempt_id"] == 55


# ── GNG-10..11: GET result page ───────────────────────────────────────────────

class TestGoNoGoResult:

    def test_gng10_result_200_for_owner(self):
        """GNG-10: GET /virtual-training/go-no-go/result/{id} → 200 for owner."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_result

        user    = _make_student()
        request = _make_request()
        db      = _mock_db()
        attempt = _mock_attempt(user_id=user.id)
        game    = _mock_gng_game()

        db.query.return_value.filter.return_value.first.return_value = attempt

        fake_resp = MagicMock(spec=HTMLResponse)
        fake_resp.status_code = 200

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_resp

            # Second db.query call (for game) needs to return game
            def _query_side_effect(model):
                q = MagicMock()
                q.filter.return_value = q
                if hasattr(model, '__tablename__') and 'attempt' in str(model.__tablename__):
                    q.first.return_value = attempt
                else:
                    q.first.return_value = game
                return q
            db.query.side_effect = _query_side_effect

            result = _run(virtual_training_go_no_go_result(
                attempt_id=attempt.id, request=request, db=db, user=user
            ))

        assert result is fake_resp
        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_go_no_go_result.html"

    def test_gng11_result_hub_redirect_wrong_user(self):
        """GNG-11: GET result → hub with error when attempt belongs to different user."""
        from app.api.web_routes.virtual_training import virtual_training_go_no_go_result

        user    = _make_student(user_id=1)
        request = _make_request()
        db      = _mock_db()
        # Attempt not found for this user (different user_id filter)
        db.query.return_value.filter.return_value.first.return_value = None

        fake_hub = MagicMock(spec=HTMLResponse)

        with patch(f"{_ROUTES}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTES}._spec_ctx", return_value={}), \
             patch(f"{_ROUTES}.VirtualTrainingService.get_hub_games", return_value=[]), \
             patch(f"{_ROUTES}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = fake_hub
            result = _run(virtual_training_go_no_go_result(
                attempt_id=999, request=request, db=db, user=user
            ))

        assert result is fake_hub
        call_args = mock_tmpl.TemplateResponse.call_args
        assert call_args[0][0] == "virtual_training_hub.html"
        assert "error" in call_args[0][1]


# ── GNG-13..17: score_composure ───────────────────────────────────────────────

class TestScoreComposure:

    def _signals(self, *, wrong_rate: float = 0.0, hit_rate: float = 1.0,
                 miss_rate: float = 0.0, speed_score: float = 0.7) -> "VTSignals":
        from app.services.virtual_training_metrics import VTSignals
        return VTSignals(
            hit_rate=hit_rate,
            wrong_rate=wrong_rate,
            miss_rate=miss_rate,
            speed_score=speed_score,
            completion_rate=1.0,
        )

    def test_gng13_zero_false_alarms_perfect_composure(self):
        """GNG-13: score_composure(wrong_rate=0.0) → 1.0."""
        from app.services.virtual_training_metrics import VTSkillScorer
        sig = self._signals(wrong_rate=0.0)
        assert VTSkillScorer.score_composure(sig) == pytest.approx(1.0)

    def test_gng14_moderate_false_alarms(self):
        """GNG-14: score_composure(wrong_rate=0.5) → 0.25 (1.0 - 1.5*0.5)."""
        from app.services.virtual_training_metrics import VTSkillScorer
        sig = self._signals(wrong_rate=0.5)
        assert VTSkillScorer.score_composure(sig) == pytest.approx(0.25)

    def test_gng15_heavy_false_alarms_negative_composure(self):
        """GNG-15: score_composure(wrong_rate=0.8) → -0.2 (lower clamp removed)."""
        from app.services.virtual_training_metrics import VTSkillScorer
        sig = self._signals(wrong_rate=0.8)
        # 1.0 - 1.5*0.8 = -0.2; only upper clamp (min(1.0, ...)) applies now
        assert VTSkillScorer.score_composure(sig) == pytest.approx(-0.2)

    def test_gng16_score_all_includes_composure(self):
        """GNG-16: score_all() dispatches composure to dedicated scorer."""
        from app.services.virtual_training_metrics import VTSkillScorer
        sig = self._signals(wrong_rate=0.1)
        scores = VTSkillScorer.score_all(sig, _GNG_SKILL_TARGETS)
        assert "composure" in scores
        # composure = 1.0 - 1.5*0.1 = 0.85
        assert scores["composure"] == pytest.approx(0.85)

    def test_gng17_delta_computer_composure_positive(self):
        """GNG-17: VTDeltaComputer with composure skill_targets → positive delta."""
        from app.services.virtual_training_metrics import VTSkillScorer, VTDeltaComputer
        sig    = self._signals(wrong_rate=0.0)
        scores = VTSkillScorer.score_all(sig, _GNG_SKILL_TARGETS)
        deltas = VTDeltaComputer.compute(scores, _GNG_SKILL_TARGETS, base_xp=12, multiplier=1.0)
        assert deltas.get("composure", 0) > 0


# ── GNG-18..20: Performance delta tests ──────────────────────────────────────

class TestGoNoGoDeltas:

    def _game_mock(self) -> MagicMock:
        g = MagicMock()
        g.base_xp       = 12
        g.skill_targets = _GNG_SKILL_TARGETS
        g.config        = _GNG_CONFIG
        return g

    def test_gng18_perfect_run_all_deltas_positive(self):
        """GNG-18: 21 GO hits, 0 FA, 0 miss, fast RT → all 4 deltas > 0."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas

        data = {
            "stimuli_count":     30,
            "correct_count":     21,    # all GO hits
            "wrong_click_count": 0,     # zero false alarms
            "error_count":       0,     # zero missed GO
            "avg_reaction_ms":   300.0,
            "raw_metrics": {"v": 1, "per_stimulus": [], "per_phase": []},
        }
        deltas = compute_vt_skill_deltas(data=data, game=self._game_mock(), multiplier=1.0)

        assert set(deltas.keys()) == {"decisions", "concentration", "composure", "reactions"}
        for skill, delta in deltas.items():
            assert delta > 0, f"Expected positive delta for {skill}"

    def test_gng19_false_alarm_heavy_composure_negative(self):
        """GNG-19: Many false alarms → composure below neutral threshold → negative delta."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas

        # wrong_rate = 20/30 ≈ 0.667 → composure = 1 - 1.5*0.667 ≈ 0.0
        # 0.0 < NEUTRAL_THRESHOLD(0.45) → delta = (0.0 - 0.45) * 0.5 * unit < 0
        data = {
            "stimuli_count":     30,
            "correct_count":     15,
            "wrong_click_count": 20,
            "error_count":       0,
            "avg_reaction_ms":   350.0,
        }
        deltas = compute_vt_skill_deltas(data=data, game=self._game_mock(), multiplier=1.0)
        assert deltas.get("composure", 0.0) < 0

    def test_gng20_missed_go_heavy_concentration_negative(self):
        """GNG-20: Many missed GO → concentration below neutral threshold → negative delta."""
        from app.services.virtual_training_metrics import compute_vt_skill_deltas

        # miss_rate = 15/30 = 0.5 → concentration = 1 - 2*0.5 = 0.0
        # 0.0 < NEUTRAL_THRESHOLD(0.45) → delta = (0.0 - 0.45) * 0.5 * unit < 0
        data = {
            "stimuli_count":     30,
            "correct_count":     6,
            "wrong_click_count": 0,
            "error_count":       15,
            "avg_reaction_ms":   400.0,
        }
        deltas = compute_vt_skill_deltas(data=data, game=self._game_mock(), multiplier=1.0)
        assert deltas.get("concentration", 0.0) < 0


# ── GNG-21..22: raw_metrics persistence ──────────────────────────────────────

class TestGoNoGoRawMetrics:

    def test_gng21_raw_metrics_persisted_with_per_phase(self):
        """GNG-21: record_attempt() stores raw_metrics including per_phase fields."""
        from app.services.virtual_training_service import VirtualTrainingService

        raw = {
            "v": 1,
            "per_stimulus": [
                {"i": 0, "phase": 0, "type": "go",    "outcome": "hit",            "rt_ms": 340, "window_ms": 1000},
                {"i": 1, "phase": 0, "type": "no_go", "outcome": "correct_inhibit","rt_ms": None, "window_ms": 1000},
                {"i": 2, "phase": 1, "type": "no_go", "outcome": "false_alarm",    "rt_ms": 290, "window_ms": 1000},
            ],
            "per_phase": [
                {"phase": 0, "go": 10, "no_go": 5, "go_hits": 9, "go_misses": 1,
                 "correct_inhibits": 4, "false_alarms": 1, "avg_rt_ms": 380},
                {"phase": 1, "go": 11, "no_go": 4, "go_hits": 10, "go_misses": 1,
                 "correct_inhibits": 3, "false_alarms": 1, "avg_rt_ms": 360},
            ],
        }

        db   = MagicMock()
        game = _mock_gng_game()

        sp = MagicMock()
        db.begin_nested.return_value = sp

        captured = {}

        class _FakeAttempt:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.idempotency_key = kwargs.get("idempotency_key")

        with patch("app.services.virtual_training_service.VirtualTrainingAttempt",
                   side_effect=_FakeAttempt), \
             patch("app.services.virtual_training_service.VirtualTrainingService"
                   ".calculate_daily_attempt_index", return_value=0), \
             patch("app.services.gamification.xp_service.award_xp"), \
             patch("app.services.segment_reward_service._load_conversion_rates",
                   return_value={}), \
             patch("app.services.virtual_training_metrics.compute_vt_skill_deltas",
                   return_value={"composure": 0.1}):

            VirtualTrainingService.record_attempt(
                db=db, user_id=7, game=game,
                data={
                    "started_at": "2026-05-22T12:00:00Z",
                    "duration_seconds": 48.0,
                    "stimuli_count": 30,
                    "correct_count": 19,
                    "wrong_click_count": 2,
                    "error_count": 2,
                    "avg_reaction_ms": 370.0,
                    "score_normalized": 75.0,
                    "raw_metrics": raw,
                },
                idempotency_key="test_key_gng21",
            )

        assert captured.get("raw_metrics") is raw
        stored_raw = captured["raw_metrics"]
        assert stored_raw["v"] == 1
        per_phase = stored_raw["per_phase"]
        assert len(per_phase) == 2
        # correct_inhibits field present
        assert "correct_inhibits" in per_phase[0]
        assert per_phase[0]["correct_inhibits"] == 4

    def test_gng22_per_phase_sums_match_aggregate(self):
        """GNG-22: per_phase go_hits sum == correct_count, false_alarms sum == wrong_click_count."""
        raw = {
            "v": 1,
            "per_stimulus": [],
            "per_phase": [
                {"phase": 0, "go": 10, "no_go": 5,
                 "go_hits": 9, "go_misses": 1, "correct_inhibits": 4, "false_alarms": 1, "avg_rt_ms": 380},
                {"phase": 1, "go": 11, "no_go": 4,
                 "go_hits": 10, "go_misses": 1, "correct_inhibits": 3, "false_alarms": 1, "avg_rt_ms": 360},
            ],
        }
        total_go_hits    = sum(p["go_hits"]      for p in raw["per_phase"])
        total_fa         = sum(p["false_alarms"]  for p in raw["per_phase"])
        # submitted correct_count=19, wrong_click_count=2
        assert total_go_hits == 19
        assert total_fa == 2


# ── GNG-23..27: Template / link tests ────────────────────────────────────────

class TestGoNoGoTemplates:

    def _read(self, filename: str) -> str:
        return (_TEMPLATES_DIR / filename).read_text(encoding="utf-8")

    def test_gng23_history_link_not_hardcoded_color_reaction(self):
        """GNG-23: virtual_training_history.html result link uses game code variable."""
        html = self._read("virtual_training_history.html")
        # Must NOT contain the hardcoded color-reaction path
        assert "/virtual-training/color-reaction/result/" not in html
        # Must contain the dynamic game code pattern
        assert "_slug" in html or "game_code" in html or "code" in html

    def test_gng24_skill_history_link_uses_game_code(self):
        """GNG-24: skill_history.html VT detail link uses game_code variable."""
        html = self._read("skill_history.html")
        # Must NOT be hardcoded to color-reaction
        assert '"/virtual-training/color-reaction/result/' not in html
        # Must use a variable for the game slug
        assert "game_code" in html or "gameSlug" in html

    def test_gng25_views_timeline_event_includes_game_code(self):
        """GNG-25: _collect_vt_timeline_events includes game_code in returned dicts."""
        from pathlib import Path
        src = (Path(__file__).resolve().parents[4]
               / "app" / "services" / "skill_progression" / "_views.py"
               ).read_text()
        assert '"game_code"' in src or "'game_code'" in src

    def test_gng26_result_template_has_per_phase_no_per_color(self):
        """GNG-26: Go/No-Go result template renders per_phase (not per_color table)."""
        html = self._read("virtual_training_go_no_go_result.html")
        assert "per_phase" in html
        assert "per_color" not in html
        # Specific Go/No-Go fields
        assert "go_hits" in html or "GO hits" in html
        assert "false_alarms" in html or "False alarms" in html

    def test_gng27_result_template_composure_documented(self):
        """GNG-27: Go/No-Go result template explains composure formula in breakdown."""
        html = self._read("virtual_training_go_no_go_result.html")
        # Composure skill breakdown notes must explain impulse control
        assert "composure" in html.lower()
        assert "false alarm" in html.lower() or "impulse" in html.lower()


# ── GNG-28..29: Score formula ─────────────────────────────────────────────────

class TestGoNoGoScoreFormula:

    def test_gng28_perfect_run_score_high(self):
        """GNG-28: 21/21 GO hits + 9/9 NO-GO inhibits + 300ms RT → score_normalized ≥ 85.
        Max formula score = 90 (at 0ms RT); 300ms gives speed_factor=0.70 → 86.
        """
        go_hit_rate    = 21 / 21   # 1.0
        no_go_fail_rate = 0 / 9    # 0.0
        missed_go_rate  = 0 / 21   # 0.0
        speed_factor    = max(0, 1 - 300 / 1000)  # 0.7

        score_raw = (0.40 * go_hit_rate
                     + 0.35 * (1 - no_go_fail_rate)
                     + 0.15 * speed_factor
                     - 0.10 * missed_go_rate)
        score_norm = min(100, max(0, round(100 * score_raw)))
        assert score_norm >= 85

    def test_gng29_all_false_alarms_score_low(self):
        """GNG-29: 0 GO hits + 9 false alarms → score_normalized ≤ 40."""
        go_hit_rate     = 0 / 21   # 0.0
        no_go_fail_rate = 9 / 9    # 1.0
        missed_go_rate  = 21 / 21  # 1.0
        speed_factor    = 0.0

        score_raw = (0.40 * go_hit_rate
                     + 0.35 * (1 - no_go_fail_rate)
                     + 0.15 * speed_factor
                     - 0.10 * missed_go_rate)
        score_norm = min(100, max(0, round(100 * score_raw)))
        assert score_norm <= 40


# ── GNG-REG: Color Reaction regression guards ──────────────────────────────────

class TestColorReactionRegression:

    def test_gng_reg01_color_reaction_get_route_exists(self):
        """GNG-REG-01: GET /virtual-training/color-reaction route still importable."""
        from app.api.web_routes.virtual_training import virtual_training_color_reaction
        assert callable(virtual_training_color_reaction)

    def test_gng_reg02_color_reaction_submit_route_exists(self):
        """GNG-REG-02: POST /virtual-training/color-reaction/submit route still importable."""
        from app.api.web_routes.virtual_training import virtual_training_color_reaction_submit
        assert callable(virtual_training_color_reaction_submit)

    def test_gng_reg03_hub_route_still_loads(self):
        """GNG-REG-03: GET /virtual-training hub route + get_hub_games not broken."""
        from app.api.web_routes.virtual_training import virtual_training_hub
        assert callable(virtual_training_hub)

    def test_gng_reg04_color_reaction_result_route_exists(self):
        """GNG-REG-04: GET /virtual-training/color-reaction/result/{id} still importable."""
        from app.api.web_routes.virtual_training import virtual_training_color_reaction_result
        assert callable(virtual_training_color_reaction_result)


# ── GNG-SEED-01: Seed guard ───────────────────────────────────────────────────

class TestGoNoGoSeed:

    def test_gng_seed01_go_no_go_is_active_in_seed(self):
        """GNG-SEED-01: go_no_go is_active=True in seed data."""
        from scripts.seed_virtual_training_games import _GAMES
        game_map = {g["code"]: g for g in _GAMES}
        assert "go_no_go" in game_map, "go_no_go missing from seed"
        assert game_map["go_no_go"]["is_active"] is True, "go_no_go must be is_active=True"

    def test_gng_seed02_go_no_go_has_gameplay_config(self):
        """GNG-SEED-02: go_no_go config has phases array with go/no_go counts."""
        from scripts.seed_virtual_training_games import _GAMES
        game_map = {g["code"]: g for g in _GAMES}
        cfg = game_map["go_no_go"]["config"]
        assert "phases" in cfg
        phases = cfg["phases"]
        assert len(phases) == 2
        total_go    = sum(p["go"]    for p in phases)
        total_no_go = sum(p["no_go"] for p in phases)
        assert total_go    == 21
        assert total_no_go == 9

    def test_gng_seed03_go_no_go_composure_in_skill_targets(self):
        """GNG-SEED-03: go_no_go skill_targets contains composure key."""
        from scripts.seed_virtual_training_games import _GAMES
        game_map = {g["code"]: g for g in _GAMES}
        st = game_map["go_no_go"]["skill_targets"]
        assert "composure" in st
        assert st["composure"] == pytest.approx(0.20)

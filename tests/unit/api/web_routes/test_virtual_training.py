"""Unit tests for Virtual Training Phase 2 — Color Reaction MVP + Phase 2.1 Target Selection.

Phase 2 (MVP):
VT2-01   GET /virtual-training → 200 for onboarded student
VT2-02   GET /virtual-training → all_games context key populated via get_hub_games()
VT2-03   GET /virtual-training/color-reaction → 200 when game is_active=True
VT2-04   GET /virtual-training/color-reaction → renders hub with error when game inactive
VT2-05   GET /virtual-training/history → 200 for onboarded student
VT2-06   validate_attempt() too_short fires before too_few_stimuli
VT2-07   validate_attempt() too_few_stimuli fires before bot_suspected
VT2-08   validate_attempt() passes when all fields present and valid
VT2-09   validate_attempt() passes when duration/stimuli not provided (backwards-compat)
VT2-10   validate_attempt() too_short: boundary 4.9 → invalid, 5.0 → valid
VT2-11   validate_attempt() too_few_stimuli: boundary 4 → invalid, 5 → valid
VT2-12   POST /virtual-training/color-reaction/submit → 200 with valid data
VT2-13   POST submit → is_valid=False + 0 XP when too_short
VT2-14   POST submit → is_valid=False + 0 XP when bot_suspected
VT2-15   POST submit → 429 when daily cap exhausted
VT2-16   POST submit → 404 when game is_active=False
VT2-17   POST submit → 403 when not onboarded
VT2-18   record_attempt() returns existing row on duplicate idempotency_key
VT2-19   record_attempt() awards XP for valid attempt
VT2-20   record_attempt() xp_awarded=0 when is_valid=False
VT2-21   record_attempt() xp_awarded=0 when multiplier=0 (4th+ attempt)
VT2-22   GET /virtual-training/color-reaction/result/{id} → 200 for owner
VT2-23   GET result → 404-style redirect for wrong user (shows hub with error)
VT2-24   History page shows last 20 valid attempts only
VT2-25   GET /virtual-training → 303 redirect for non-student
VT2-26   GET /virtual-training → 303 redirect for student without onboarding

Phase 2.1 (Target Selection Reaction):
VT2.1-01  validate_attempt() G4 random_clicking fires when wrong_click_count > 0.55 × stimuli
VT2.1-02  G4 boundary: wrong_click_count == floor(0.55×stimuli) → valid (not >)
VT2.1-03  G4 not triggered when wrong_click_count is absent
VT2.1-04  G4 fires before bot_suspected in priority order
VT2.1-05  validate_attempt() passes full Phase 2.1 data (36 stimuli, 5 wrong clicks)
VT2.1-06  Updated _MIN_STIMULI_COUNT=28: boundary 27 → invalid, 28 → valid
VT2.1-07  Updated _MIN_DURATION_SECONDS=25.0: boundary 24.9 → invalid, 25.0 → valid
VT2.1-08  Updated _BOT_REACTION_THRESHOLD_MS=80: boundary 79.9 → bot_suspected, 80.0 → valid
VT2.1-09  record_attempt() stores wrong_click_count from data payload
VT2.1-10  record_attempt() random_clicking data → is_valid=False, xp_awarded=0
VT2.1-11  Result page renders wrong_click_count and error_count stats
VT2.1-12  Result page renders random_clicking invalid reason message
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient

from app.services.virtual_training_service import VirtualTrainingService


# ── Helpers ────────────────────────────────────────────────────────────────────

_PHASE21_CONFIG = {
    "phases": [
        {"stimuli": 12, "targets": 3, "delay_ms": 2000, "window_ms": 4000, "diameter_px": 70},
        {"stimuli": 12, "targets": 4, "delay_ms": 1200, "window_ms": 3000, "diameter_px": 64},
        {"stimuli": 12, "targets": 5, "delay_ms":  700, "window_ms": 2200, "diameter_px": 58},
    ],
    "colours": {
        "RED": "#e74c3c", "GREEN": "#2ecc71", "BLUE": "#3498db",
        "YELLOW": "#f39c12", "PURPLE": "#9b59b6", "ORANGE": "#e67e22",
    },
    "miss_penalty_ms": 300,
    "wrong_penalty_ms": 200,
}

_PHASE21_SKILL_TARGETS = {
    "reactions": 0.35, "decisions": 0.30, "concentration": 0.20, "anticipation": 0.15,
}


def _mock_game(
    *,
    id: int = 1,
    code: str = "color_reaction",
    name: str = "Color Reaction",
    is_active: bool = True,
    base_xp: int = 20,
    max_daily_attempts: int = 5,
    skill_targets: dict | None = None,
    config: dict | None = None,
) -> MagicMock:
    g = MagicMock()
    g.id = id
    g.code = code
    g.name = name
    g.is_active = is_active
    g.base_xp = base_xp
    g.max_daily_attempts = max_daily_attempts
    g.skill_targets = skill_targets or _PHASE21_SKILL_TARGETS
    g.config = config or _PHASE21_CONFIG
    return g


def _mock_attempt(
    *,
    id: int = 1,
    user_id: int = 42,
    game_id: int = 1,
    is_valid: bool = True,
    invalid_reason: str | None = None,
    xp_awarded: int = 20,
    skill_deltas: dict | None = None,
    attempt_index_today: int = 1,
    score_normalized: float = 80.0,
    avg_reaction_ms: float | None = 320.0,
    min_reaction_ms: float | None = 180.0,
    duration_seconds: float | None = 45.0,
    stimuli_count: int | None = 36,
    correct_count: int | None = 30,
    error_count: int | None = 3,
    wrong_click_count: int | None = 3,
    idempotency_key: str | None = None,
    completed_at: datetime | None = None,
) -> MagicMock:
    a = MagicMock()
    a.id = id
    a.user_id = user_id
    a.game_id = game_id
    a.is_valid = is_valid
    a.invalid_reason = invalid_reason
    a.xp_awarded = xp_awarded
    a.skill_deltas = skill_deltas or {
        "reactions": 0.70, "decisions": 0.60, "concentration": 0.40, "anticipation": 0.30,
    }
    a.attempt_index_today = attempt_index_today
    a.score_normalized = score_normalized
    a.avg_reaction_ms = avg_reaction_ms
    a.min_reaction_ms = min_reaction_ms
    a.duration_seconds = duration_seconds
    a.stimuli_count = stimuli_count
    a.correct_count = correct_count
    a.error_count = error_count
    a.wrong_click_count = wrong_click_count
    a.idempotency_key = idempotency_key or f"vt_cr_u{user_id}_ts"
    a.completed_at = completed_at or datetime.now(timezone.utc)
    return a


def _mock_db() -> MagicMock:
    return MagicMock()


def _build_route_mocks(
    *,
    active_games: list | None = None,
    game: MagicMock | None = None,
    attempts_today_count: int = 0,
    attempt: MagicMock | None = None,
    history_attempts: list | None = None,
    game_query_result: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build (db, user) mocks for route-level tests."""
    from app.models.user import UserRole

    user = MagicMock()
    user.id = 42
    user.role = UserRole.STUDENT
    user.onboarding_completed = True
    user.specialization = MagicMock()
    user.specialization.value = "LFA_FOOTBALL_PLAYER"

    db = _mock_db()
    return db, user


# ── VT2-06..11: validate_attempt guards ───────────────────────────────────────

class TestValidateAttemptPhase2:

    def test_vt2_06_too_short_fires_first(self):
        """VT2-06: duration_seconds < threshold returns too_short before other checks."""
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 3.0,
            "stimuli_count": 3,     # would also fail too_few_stimuli
            "avg_reaction_ms": 42.0,  # would also fail bot_suspected
        })
        assert is_valid is False
        assert reason == "too_short"

    def test_vt2_07_too_few_fires_before_bot(self):
        """VT2-07: stimuli_count below threshold returns too_few_stimuli before bot_suspected."""
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 30.0,
            "stimuli_count": 2,
            "avg_reaction_ms": 42.0,  # would also fail bot_suspected
        })
        assert is_valid is False
        assert reason == "too_few_stimuli"

    def test_vt2_08_valid_full_data(self):
        """VT2-08: All fields present and valid → passes."""
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0,
            "stimuli_count": 36,
            "avg_reaction_ms": 320.0,
        })
        assert is_valid is True
        assert reason is None

    def test_vt2_09_missing_duration_and_stimuli_passes(self):
        """VT2-09: Phase 1 compat — omitting duration/stimuli still passes."""
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "avg_reaction_ms": 250.0,
        })
        assert is_valid is True
        assert reason is None

    def test_vt2_10_too_short_boundary(self):
        """VT2-10: 24.9 → invalid; 25.0 → valid (Phase 2.1 boundary)."""
        is_v1, r1 = VirtualTrainingService.validate_attempt({"duration_seconds": 24.9})
        is_v2, r2 = VirtualTrainingService.validate_attempt({"duration_seconds": 25.0})
        assert is_v1 is False and r1 == "too_short"
        assert is_v2 is True and r2 is None

    def test_vt2_11_too_few_stimuli_boundary(self):
        """VT2-11: 27 stimuli → invalid; 28 → valid (Phase 2.1 boundary)."""
        is_v1, r1 = VirtualTrainingService.validate_attempt({
            "duration_seconds": 30.0, "stimuli_count": 27,
        })
        is_v2, r2 = VirtualTrainingService.validate_attempt({
            "duration_seconds": 30.0, "stimuli_count": 28,
        })
        assert is_v1 is False and r1 == "too_few_stimuli"
        assert is_v2 is True and r2 is None


# ── VT2-18..21: record_attempt ────────────────────────────────────────────────

class TestRecordAttempt:

    def _make_db(self, count: int = 0, existing_attempt: MagicMock | None = None):
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = count
        q.all.return_value = []

        if existing_attempt is not None:
            q.first.return_value = existing_attempt
        else:
            q.first.return_value = None

        db.query.return_value = q

        sp = MagicMock()
        db.begin_nested.return_value = sp
        return db

    @patch("app.services.virtual_training_service.VirtualTrainingService.calculate_daily_attempt_index", return_value=1)
    @patch("app.services.segment_reward_service._load_conversion_rates", return_value={})
    @patch("app.services.gamification.xp_service.award_xp")
    def test_vt2_18_idempotent_on_duplicate_key(self, mock_xp, mock_rates, mock_idx):
        """VT2-18: IntegrityError on dup key → returns existing row, award_xp not called again."""
        from sqlalchemy.exc import IntegrityError

        existing = _mock_attempt(id=99)
        db = self._make_db(existing_attempt=existing)

        sp = MagicMock()
        sp.commit.side_effect = IntegrityError("dup", {}, None)
        db.begin_nested.return_value = sp

        # Make filter+first return the existing attempt
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = existing
        q.count.return_value = 0
        db.query.return_value = q

        game = _mock_game()
        result = VirtualTrainingService.record_attempt(
            db=db,
            user_id=42,
            game=game,
            data={"duration_seconds": 60.0, "stimuli_count": 36, "avg_reaction_ms": 300.0,
                  "started_at": "2026-05-21T10:00:00+00:00"},
            idempotency_key="vt_cr_u42_ts",
        )
        assert result is existing
        mock_xp.assert_not_called()

    @patch("app.services.virtual_training_service.VirtualTrainingService.calculate_daily_attempt_index", return_value=1)
    @patch("app.services.segment_reward_service._load_conversion_rates", return_value={})
    @patch("app.services.gamification.xp_service.award_xp")
    def test_vt2_19_awards_xp_for_valid_attempt(self, mock_xp, mock_rates, mock_idx):
        """VT2-19: Valid attempt with xp_awarded > 0 calls award_xp once."""
        db = self._make_db()
        sp = MagicMock()
        db.begin_nested.return_value = sp

        game = _mock_game(base_xp=20)
        result = VirtualTrainingService.record_attempt(
            db=db,
            user_id=42,
            game=game,
            data={"duration_seconds": 60.0, "stimuli_count": 36, "avg_reaction_ms": 300.0,
                  "started_at": "2026-05-21T10:00:00+00:00"},
            idempotency_key="vt_cr_u42_ts",
        )
        mock_xp.assert_called_once()
        call_kwargs = mock_xp.call_args
        assert call_kwargs.kwargs["transaction_type"] == "VIRTUAL_TRAINING_XP"

    @patch("app.services.virtual_training_service.VirtualTrainingService.calculate_daily_attempt_index", return_value=1)
    @patch("app.services.segment_reward_service._load_conversion_rates", return_value={})
    @patch("app.services.gamification.xp_service.award_xp")
    def test_vt2_20_no_xp_for_invalid_attempt(self, mock_xp, mock_rates, mock_idx):
        """VT2-20: too_short attempt → is_valid=False, xp_awarded=0, award_xp not called."""
        db = self._make_db()
        sp = MagicMock()
        db.begin_nested.return_value = sp

        game = _mock_game(base_xp=20)
        result = VirtualTrainingService.record_attempt(
            db=db,
            user_id=42,
            game=game,
            data={"duration_seconds": 1.0, "stimuli_count": 36,
                  "started_at": "2026-05-21T10:00:00+00:00"},
            idempotency_key="vt_cr_u42_short",
        )
        assert result.xp_awarded == 0
        mock_xp.assert_not_called()

    @patch("app.services.virtual_training_service.VirtualTrainingService.calculate_daily_attempt_index", return_value=4)
    @patch("app.services.segment_reward_service._load_conversion_rates", return_value={})
    @patch("app.services.gamification.xp_service.award_xp")
    def test_vt2_21_no_xp_for_4th_attempt(self, mock_xp, mock_rates, mock_idx):
        """VT2-21: 4th attempt → multiplier=0.0 → xp_awarded=0, award_xp not called."""
        db = self._make_db()
        sp = MagicMock()
        db.begin_nested.return_value = sp

        game = _mock_game(base_xp=20)
        result = VirtualTrainingService.record_attempt(
            db=db,
            user_id=42,
            game=game,
            data={"duration_seconds": 60.0, "stimuli_count": 36, "avg_reaction_ms": 300.0,
                  "started_at": "2026-05-21T10:00:00+00:00"},
            idempotency_key="vt_cr_u42_4th",
        )
        assert result.xp_awarded == 0
        mock_xp.assert_not_called()


# ── VT2-01..05 + VT2-22..26: Route-level tests ────────────────────────────────

# These use FastAPI TestClient with mocked DB and user injections.

_ROUTE_BASE = "app.api.web_routes.virtual_training"


def _make_test_app():
    """Build a minimal FastAPI app that includes the VT router for testing."""
    from fastapi import FastAPI
    from app.api.web_routes import virtual_training as vt_module
    app = FastAPI()
    app.include_router(vt_module.router)
    return app


class TestVTRoutes:
    """Route-level tests using overridden dependencies."""

    def _make_client(self, user_override=None, db_override=None):
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

    def _onboarded_user(self, onboarding_completed: bool = True):
        from app.models.user import UserRole
        user = MagicMock()
        user.id = 42
        user.role = UserRole.STUDENT
        user.onboarding_completed = onboarding_completed
        user.specialization = MagicMock()
        user.specialization.value = "LFA_FOOTBALL_PLAYER"
        return user

    def _non_student_user(self):
        from app.models.user import UserRole
        user = MagicMock()
        user.id = 99
        user.role = UserRole.INSTRUCTOR
        user.onboarding_completed = True
        return user

    def _db_with_games(self, games: list) -> MagicMock:
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = games
        q.first.return_value = games[0] if games else None
        q.count.return_value = 0
        q.limit.return_value = q
        db.query.return_value = q
        return db

    # VT2-01: Hub → 200
    def test_vt2_01_hub_200_for_onboarded_student(self):
        """VT2-01: /virtual-training returns 200 for onboarded student."""
        user = self._onboarded_user()
        db = self._db_with_games([])
        client = self._make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training", follow_redirects=False)
        assert resp.status_code == 200

    # VT2-02: Hub uses get_hub_games (all_games context key)
    def test_vt2_02_hub_all_games_via_get_hub_games(self):
        """VT2-02: Hub page calls get_hub_games() and passes all_games to template."""
        user = self._onboarded_user()
        game = _mock_game(is_active=True)
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[game]) as mock_hub:
            db = _mock_db()
            db.query.return_value = MagicMock()
            client = self._make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training", follow_redirects=False)
        assert resp.status_code == 200
        mock_hub.assert_called_once()

    # VT2-03: Color Reaction → 200 when active
    def test_vt2_03_color_reaction_200_when_active(self):
        """VT2-03: /virtual-training/color-reaction returns 200 when game is_active=True."""
        user = self._onboarded_user()
        game = _mock_game(is_active=True)
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            db = _mock_db()
            q = MagicMock()
            q.filter.return_value = q
            q.count.return_value = 0
            db.query.return_value = q
            client = self._make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/color-reaction", follow_redirects=False)
        assert resp.status_code == 200

    # VT2-04: Color Reaction → hub with error when inactive
    def test_vt2_04_color_reaction_hub_error_when_inactive(self):
        """VT2-04: Inactive game redirects to hub template with error message."""
        user = self._onboarded_user()
        game = _mock_game(is_active=False)
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_games", return_value=[]):
            db = _mock_db()
            client = self._make_client(user_override=user, db_override=db)
            resp = client.get("/virtual-training/color-reaction", follow_redirects=False)
        assert resp.status_code == 200
        assert b"not available" in resp.content

    # VT2-05: History → 200
    def test_vt2_05_history_200_for_onboarded_student(self):
        """VT2-05: /virtual-training/history returns 200 for onboarded student."""
        user = self._onboarded_user()
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = []
        q.in_.return_value = q
        db.query.return_value = q
        client = self._make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training/history", follow_redirects=False)
        assert resp.status_code == 200

    # VT2-12: Submit → 200 with valid data
    def test_vt2_12_submit_valid_data(self):
        """VT2-12: POST submit returns 200 with attempt_id and xp_awarded."""
        user = self._onboarded_user()
        game = _mock_game(is_active=True)
        attempt = _mock_attempt(id=7, xp_awarded=20, is_valid=True)

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 0
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            client = self._make_client(user_override=user, db_override=db)
            resp = client.post(
                "/virtual-training/color-reaction/submit",
                json={
                    "started_at": "2026-05-21T10:00:00+00:00",
                    "duration_seconds": 60.0,
                    "stimuli_count": 36,
                    "correct_count": 30,
                    "wrong_click_count": 3,
                    "error_count": 3,
                    "avg_reaction_ms": 320.0,
                    "min_reaction_ms": 180.0,
                    "score_raw": 0.72,
                    "score_normalized": 72.0,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["attempt_id"] == 7
        assert data["xp_awarded"] == 20
        assert data["is_valid"] is True

    # VT2-13: Submit → is_valid=False when too_short
    def test_vt2_13_submit_too_short_returns_invalid(self):
        """VT2-13: too_short attempt saved as is_valid=False, xp_awarded=0."""
        user = self._onboarded_user()
        game = _mock_game(is_active=True)
        attempt = _mock_attempt(id=8, xp_awarded=0, is_valid=False, invalid_reason="too_short")

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 0
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            client = self._make_client(user_override=user, db_override=db)
            resp = client.post(
                "/virtual-training/color-reaction/submit",
                json={"started_at": "2026-05-21T10:00:00+00:00", "duration_seconds": 1.0},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is False
        assert data["invalid_reason"] == "too_short"
        assert data["xp_awarded"] == 0

    # VT2-14: Submit → bot_suspected
    def test_vt2_14_submit_bot_suspected(self):
        """VT2-14: bot_suspected attempt is saved as is_valid=False."""
        user = self._onboarded_user()
        game = _mock_game(is_active=True)
        attempt = _mock_attempt(id=9, xp_awarded=0, is_valid=False, invalid_reason="bot_suspected")

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 0
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.record_attempt", return_value=attempt):
            client = self._make_client(user_override=user, db_override=db)
            resp = client.post(
                "/virtual-training/color-reaction/submit",
                json={"started_at": "2026-05-21T10:00:00+00:00",
                      "duration_seconds": 60.0, "stimuli_count": 36, "avg_reaction_ms": 42.0},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is False
        assert data["invalid_reason"] == "bot_suspected"

    # VT2-15: Submit → 429 when daily cap exhausted
    def test_vt2_15_submit_429_when_daily_cap_exhausted(self):
        """VT2-15: POST submit returns 429 when valid_today >= max_daily_attempts."""
        user = self._onboarded_user()
        game = _mock_game(is_active=True, max_daily_attempts=5)

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 5  # already at cap
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = self._make_client(user_override=user, db_override=db)
            resp = client.post(
                "/virtual-training/color-reaction/submit",
                json={"started_at": "2026-05-21T10:00:00+00:00",
                      "duration_seconds": 25.0, "stimuli_count": 10},
            )
        assert resp.status_code == 429
        assert resp.json()["error"] == "daily_cap"

    # VT2-16: Submit → 404 when game inactive
    def test_vt2_16_submit_404_when_game_inactive(self):
        """VT2-16: POST submit returns 404 when game is_active=False."""
        user = self._onboarded_user()
        game = _mock_game(is_active=False)

        db = _mock_db()
        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_game", return_value=game):
            client = self._make_client(user_override=user, db_override=db)
            resp = client.post(
                "/virtual-training/color-reaction/submit",
                json={"started_at": "2026-05-21T10:00:00+00:00"},
            )
        assert resp.status_code == 404

    # VT2-17: Submit → 403 when not onboarded
    def test_vt2_17_submit_403_without_onboarding(self):
        """VT2-17: POST submit returns 403 for student without completed onboarding."""
        user = self._onboarded_user(onboarding_completed=False)
        db = _mock_db()
        client = self._make_client(user_override=user, db_override=db)
        resp = client.post(
            "/virtual-training/color-reaction/submit",
            json={"started_at": "2026-05-21T10:00:00+00:00"},
        )
        assert resp.status_code == 403

    # VT2-22: Result → 200 for owner
    def test_vt2_22_result_200_for_owner(self):
        """VT2-22: /result/{id} returns 200 when attempt belongs to current user."""
        user = self._onboarded_user()
        attempt = _mock_attempt(id=5, user_id=42)
        game = _mock_game()

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = attempt
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingGame", MagicMock()):
            client = self._make_client(user_override=user, db_override=db)
            resp = client.get(f"/virtual-training/color-reaction/result/5",
                              follow_redirects=False)
        assert resp.status_code == 200

    # VT2-23: Result → shows hub with error for wrong user (no attempt found)
    def test_vt2_23_result_shows_hub_error_for_wrong_user(self):
        """VT2-23: /result/{id} returns hub with error when attempt not found for this user."""
        user = self._onboarded_user()

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = None
        q.order_by.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        with patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_games", return_value=[]):
            client = self._make_client(user_override=user, db_override=db)
            resp = client.get(f"/virtual-training/color-reaction/result/999",
                              follow_redirects=False)
        assert resp.status_code == 200
        assert b"not found" in resp.content.lower()

    # VT2-24: History shows only valid attempts
    def test_vt2_24_history_shows_valid_attempts_only(self):
        """VT2-24: History route returns 200 and applies filter (is_valid=True)."""
        user = self._onboarded_user()
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = []
        q.in_.return_value = q
        db.query.return_value = q

        client = self._make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training/history", follow_redirects=False)
        assert resp.status_code == 200
        # The route calls .filter(...) with at least one condition — verify it was called
        assert q.filter.called, "filter() should have been called by the history query"
        # The filter call should include two BinaryExpression args (user_id + is_valid)
        call_args = q.filter.call_args_list[0][0]
        assert len(call_args) == 2, "Expected filter(user_id==..., is_valid==True)"

    # VT2-25: Non-student → 303 redirect
    def test_vt2_25_non_student_redirected(self):
        """VT2-25: Instructor accessing /virtual-training → 303."""
        user = self._non_student_user()
        db = _mock_db()
        client = self._make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training", follow_redirects=False)
        assert resp.status_code == 303

    # VT2-26: Student without onboarding → 303 redirect
    def test_vt2_26_student_without_onboarding_redirected(self):
        """VT2-26: Student with onboarding_completed=False → 303."""
        user = self._onboarded_user(onboarding_completed=False)
        db = _mock_db()
        client = self._make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training", follow_redirects=False)
        assert resp.status_code == 303


# ── Phase 2.1: Target Selection Reaction ──────────────────────────────────────

class TestVT21ValidateAttempt:
    """VT2.1-01..08: validate_attempt() guards for Phase 2.1 Target Selection."""

    def test_vt21_01_random_clicking_guard_fires(self):
        """VT2.1-01: wrong_click_count > 0.55 × stimuli → random_clicking."""
        # 20/36 = 55.6% > 55% threshold
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0,
            "stimuli_count": 36,
            "wrong_click_count": 20,
            "avg_reaction_ms": 350.0,
        })
        assert is_valid is False
        assert reason == "random_clicking"

    def test_vt21_02_random_clicking_boundary_at_threshold_is_valid(self):
        """VT2.1-02: wrong_click_count not > 0.55 × stimuli → valid (uses = not >)."""
        # 0.55 * 36 = 19.8; wrong=19 is NOT > 19.8 → passes
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0,
            "stimuli_count": 36,
            "wrong_click_count": 19,
            "avg_reaction_ms": 350.0,
        })
        assert is_valid is True
        assert reason is None

    def test_vt21_02b_random_clicking_one_above_threshold_fires(self):
        """VT2.1-02b: wrong_click_count just above 0.55 × stimuli → random_clicking."""
        # 0.55 * 36 = 19.8; wrong=20 > 19.8 → fires
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0,
            "stimuli_count": 36,
            "wrong_click_count": 20,
            "avg_reaction_ms": 350.0,
        })
        assert is_valid is False
        assert reason == "random_clicking"

    def test_vt21_03_g4_not_triggered_when_field_absent(self):
        """VT2.1-03: Missing wrong_click_count → G4 not checked, attempt may pass."""
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0,
            "stimuli_count": 36,
            "avg_reaction_ms": 350.0,
            # no wrong_click_count key
        })
        assert is_valid is True
        assert reason is None

    def test_vt21_04_g4_fires_before_bot_suspected(self):
        """VT2.1-04: random_clicking check precedes bot_suspected in priority order."""
        # Both G4 and G5 would fail; G4 must win
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0,
            "stimuli_count": 36,
            "wrong_click_count": 25,   # > 0.55*36=19.8 → random_clicking
            "avg_reaction_ms": 42.0,   # < 80 → would also trigger bot_suspected
        })
        assert is_valid is False
        assert reason == "random_clicking"

    def test_vt21_05_valid_phase21_full_payload(self):
        """VT2.1-05: Full valid Phase 2.1 payload (36 stimuli, 5 wrong) → passes."""
        is_valid, reason = VirtualTrainingService.validate_attempt({
            "duration_seconds": 65.0,
            "stimuli_count": 36,
            "wrong_click_count": 5,
            "avg_reaction_ms": 380.0,
            "correct_count": 28,
            "error_count": 3,
        })
        assert is_valid is True
        assert reason is None

    def test_vt21_06_updated_min_stimuli_boundary(self):
        """VT2.1-06: Phase 2.1 boundary — 27 stimuli → invalid, 28 → valid."""
        is_v1, r1 = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0, "stimuli_count": 27,
        })
        is_v2, r2 = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0, "stimuli_count": 28,
        })
        assert is_v1 is False and r1 == "too_few_stimuli"
        assert is_v2 is True  and r2 is None

    def test_vt21_07_updated_min_duration_boundary(self):
        """VT2.1-07: Phase 2.1 boundary — 24.9 s → invalid, 25.0 s → valid."""
        is_v1, r1 = VirtualTrainingService.validate_attempt({"duration_seconds": 24.9})
        is_v2, r2 = VirtualTrainingService.validate_attempt({"duration_seconds": 25.0})
        assert is_v1 is False and r1 == "too_short"
        assert is_v2 is True  and r2 is None

    def test_vt21_08_updated_bot_threshold_boundary(self):
        """VT2.1-08: Phase 2.1 boundary — avg_ms 79.9 → bot_suspected, 80.0 → valid."""
        is_v1, r1 = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0, "stimuli_count": 36, "avg_reaction_ms": 79.9,
        })
        is_v2, r2 = VirtualTrainingService.validate_attempt({
            "duration_seconds": 60.0, "stimuli_count": 36, "avg_reaction_ms": 80.0,
        })
        assert is_v1 is False and r1 == "bot_suspected"
        assert is_v2 is True  and r2 is None


class TestVT21RecordAttempt:
    """VT2.1-09..10: record_attempt() Phase 2.1 behaviour."""

    def _make_db(self, count: int = 0):
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = count
        q.first.return_value = None
        q.all.return_value = []
        db.query.return_value = q
        sp = MagicMock()
        db.begin_nested.return_value = sp
        return db

    @patch("app.services.virtual_training_service.VirtualTrainingService.calculate_daily_attempt_index", return_value=1)
    @patch("app.services.segment_reward_service._load_conversion_rates", return_value={})
    @patch("app.services.gamification.xp_service.award_xp")
    def test_vt21_09_wrong_click_count_persisted(self, mock_xp, mock_rates, mock_idx):
        """VT2.1-09: wrong_click_count from payload is stored in the ORM object."""
        db = self._make_db()
        game = _mock_game(base_xp=20)
        result = VirtualTrainingService.record_attempt(
            db=db,
            user_id=42,
            game=game,
            data={
                "duration_seconds": 60.0,
                "stimuli_count": 36,
                "wrong_click_count": 5,
                "avg_reaction_ms": 350.0,
                "started_at": "2026-05-22T10:00:00+00:00",
            },
            idempotency_key="vt_cr_u42_p21",
        )
        assert result.wrong_click_count == 5

    @patch("app.services.virtual_training_service.VirtualTrainingService.calculate_daily_attempt_index", return_value=1)
    @patch("app.services.segment_reward_service._load_conversion_rates", return_value={})
    @patch("app.services.gamification.xp_service.award_xp")
    def test_vt21_10_random_clicking_gives_invalid_no_xp(self, mock_xp, mock_rates, mock_idx):
        """VT2.1-10: wrong_click_count > 0.55 × stimuli → is_valid=False, xp=0, award_xp not called."""
        db = self._make_db()
        game = _mock_game(base_xp=20)
        result = VirtualTrainingService.record_attempt(
            db=db,
            user_id=42,
            game=game,
            data={
                "duration_seconds": 60.0,
                "stimuli_count": 36,
                "wrong_click_count": 25,   # 25/36 = 69% > 55%
                "avg_reaction_ms": 350.0,
                "started_at": "2026-05-22T10:00:00+00:00",
            },
            idempotency_key="vt_cr_u42_random",
        )
        assert result.is_valid is False
        assert result.invalid_reason == "random_clicking"
        assert result.xp_awarded == 0
        mock_xp.assert_not_called()


class TestVT21ResultPage:
    """VT2.1-11..12: Result page rendering for Phase 2.1 attempts."""

    def _make_client(self, user_override=None, db_override=None):
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

    def _onboarded_user(self):
        from app.models.user import UserRole
        user = MagicMock()
        user.id = 42
        user.role = UserRole.STUDENT
        user.onboarding_completed = True
        user.specialization = MagicMock()
        user.specialization.value = "LFA_FOOTBALL_PLAYER"
        return user

    def test_vt21_11_result_shows_wrong_click_count(self):
        """VT2.1-11: Result page renders wrong_click_count stat when present."""
        user = self._onboarded_user()
        attempt = _mock_attempt(id=10, user_id=42, wrong_click_count=4, is_valid=True)
        game = _mock_game()

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = attempt
        db.query.return_value = q

        client = self._make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training/color-reaction/result/10",
                          follow_redirects=False)
        assert resp.status_code == 200
        # The stat grid includes Wrong Clicks label
        assert b"Wrong Clicks" in resp.content or b"wrong" in resp.content.lower()

    def test_vt21_12_result_shows_random_clicking_message(self):
        """VT2.1-12: Invalid attempt with random_clicking reason shows the correct message."""
        user = self._onboarded_user()
        attempt = _mock_attempt(
            id=11, user_id=42, is_valid=False, invalid_reason="random_clicking",
            xp_awarded=0, wrong_click_count=25,
        )
        game = _mock_game()

        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = attempt
        db.query.return_value = q

        client = self._make_client(user_override=user, db_override=db)
        resp = client.get("/virtual-training/color-reaction/result/11",
                          follow_redirects=False)
        assert resp.status_code == 200
        assert b"random_clicking" not in resp.content  # must be translated to human message
        assert b"wrong" in resp.content.lower()        # user-facing message mentions "wrong"

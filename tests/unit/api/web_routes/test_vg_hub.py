"""Unit tests for Virtual Games Hub (VGH-01..14).

VGH-01   GET /virtual-training returns 200 for onboarded student
VGH-02   Hub route uses get_hub_games() — includes active + planned games
VGH-03   get_hub_games() excludes games with config.show_in_hub=False
VGH-04   get_hub_games() includes games with config.show_in_hub=True
VGH-05   get_hub_games() includes games with no show_in_hub key (default True)
VGH-06   virtual_training_hub.html page title says "Virtual Games"
VGH-07   virtual_training_hub.html contains "Coming soon" text for planned cards
VGH-08   virtual_training_hub.html contains Play link structure for active cards
VGH-09   virtual_training_hub.html contains disabled CTA for planned cards
VGH-10   virtual_training_hub.html contains vg-skill-badge CSS class
VGH-11   virtual_training_hub.html contains vg-football-benefit CSS class
VGH-12   training_hub.html says "Virtual Games" (not "Virtual Training") as link text
VGH-13   GET /virtual-training/color-reaction regression — route still 200
VGH-14   /adaptive-learning route regression — handler still callable
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROUTE_BASE = "app.api.web_routes.virtual_training"
_SVC_BASE = "app.services.virtual_training_service"
_TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_game(
    *,
    id: int = 1,
    code: str = "color_reaction",
    name: str = "Color Reaction",
    is_active: bool = True,
    skill_targets: dict | None = None,
    config: dict | None = None,
) -> MagicMock:
    g = MagicMock()
    g.id = id
    g.code = code
    g.name = name
    g.is_active = is_active
    g.description = f"Description for {name}"
    g.skill_targets = skill_targets or {"reactions": 0.5, "decisions": 0.5}
    g.config = config if config is not None else {
        "show_in_hub": True,
        "icon": "⚡",
        "football_benefit": "Test benefit text.",
    }
    return g


def _make_planned_game(**kwargs) -> MagicMock:
    defaults = dict(
        id=2,
        code="go_no_go",
        name="Go / No-Go Reaction",
        is_active=False,
        config={
            "show_in_hub": True,
            "icon": "🛑",
            "football_benefit": "Impulse control benefit.",
        },
    )
    defaults.update(kwargs)
    return _make_game(**defaults)


def _make_hidden_game(**kwargs) -> MagicMock:
    defaults = dict(
        id=99,
        code="stroop_challenge",
        name="Stroop Challenge",
        is_active=False,
        config={"show_in_hub": False, "trial_count": 12},
    )
    defaults.update(kwargs)
    return _make_game(**defaults)


def _make_student() -> MagicMock:
    from app.models.user import UserRole
    user = MagicMock()
    user.id = 42
    user.role = UserRole.STUDENT
    user.onboarding_completed = True
    user.specialization = MagicMock()
    user.specialization.value = "LFA_FOOTBALL_PLAYER"
    return user


def _make_db() -> MagicMock:
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.all.return_value = []
    q.first.return_value = None
    q.count.return_value = 0
    q.limit.return_value = q
    db.query.return_value = q
    return db


# ── VGH-01: Hub route 200 ─────────────────────────────────────────────────────

class TestVGHubRoute:

    def _client(self, user=None, db=None):
        from fastapi import FastAPI
        from app.api.web_routes import virtual_training as vt_module
        from app.dependencies import get_current_user_web
        from app.database import get_db

        app = FastAPI()
        app.include_router(vt_module.router)
        if user:
            app.dependency_overrides[get_current_user_web] = lambda: user
        if db:
            app.dependency_overrides[get_db] = lambda: db

        from fastapi.testclient import TestClient
        return TestClient(app, raise_server_exceptions=False)

    def test_vgh01_hub_200_for_onboarded_student(self):
        """VGH-01: GET /virtual-training returns 200 for onboarded student."""
        user = _make_student()
        with patch(f"{_ROUTE_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games", return_value=[]):
            client = self._client(user=user, db=_make_db())
            resp = client.get("/virtual-training", follow_redirects=False)
        assert resp.status_code == 200

    def test_vgh02_hub_calls_get_hub_games(self):
        """VGH-02: Hub route calls get_hub_games() and receives all_games (active + planned)."""
        user = _make_student()
        active = _make_game(id=1, is_active=True)
        planned = _make_planned_game(id=2, is_active=False)
        with patch(f"{_ROUTE_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_ROUTE_BASE}._spec_ctx", return_value={}), \
             patch(f"{_ROUTE_BASE}.VirtualTrainingService.get_hub_games",
                   return_value=[active, planned]) as mock_hub:
            client = self._client(user=user, db=_make_db())
            resp = client.get("/virtual-training", follow_redirects=False)
        assert resp.status_code == 200
        mock_hub.assert_called_once()


# ── VGH-03..05: get_hub_games filtering ──────────────────────────────────────

class TestGetHubGames:

    def _svc(self):
        from app.services.virtual_training_service import VirtualTrainingService
        return VirtualTrainingService

    def test_vgh03_excludes_show_in_hub_false(self):
        """VGH-03: get_hub_games() excludes games with config.show_in_hub=False."""
        hidden = _make_hidden_game()     # show_in_hub=False
        active = _make_game(id=1)       # show_in_hub=True

        db = _make_db()
        db.query.return_value.order_by.return_value.all.return_value = [active, hidden]

        result = self._svc().get_hub_games(db)
        codes = [g.code for g in result]
        assert "stroop_challenge" not in codes
        assert "color_reaction" in codes

    def test_vgh04_includes_show_in_hub_true(self):
        """VGH-04: get_hub_games() includes games explicitly marked show_in_hub=True."""
        planned = _make_planned_game()   # show_in_hub=True, is_active=False

        db = _make_db()
        db.query.return_value.order_by.return_value.all.return_value = [planned]

        result = self._svc().get_hub_games(db)
        assert len(result) == 1
        assert result[0].code == "go_no_go"

    def test_vgh05_includes_games_with_no_show_in_hub_key(self):
        """VGH-05: get_hub_games() includes games where config has no show_in_hub key (default=True)."""
        game_no_key = _make_game(config={"icon": "⚡"})   # no show_in_hub key

        db = _make_db()
        db.query.return_value.order_by.return_value.all.return_value = [game_no_key]

        result = self._svc().get_hub_games(db)
        assert len(result) == 1

    def test_vgh05b_handles_none_config(self):
        """VGH-05b: get_hub_games() treats config=None as show_in_hub=True."""
        game_null_cfg = _make_game(config=None)

        db = _make_db()
        db.query.return_value.order_by.return_value.all.return_value = [game_null_cfg]

        result = self._svc().get_hub_games(db)
        assert len(result) == 1


# ── VGH-06..11: Template static analysis ─────────────────────────────────────

class TestVGHubTemplate:

    def _read(self, filename: str) -> str:
        return (_TEMPLATES_DIR / filename).read_text(encoding="utf-8")

    def test_vgh06_page_title_says_virtual_games(self):
        """VGH-06: virtual_training_hub.html block title says 'Virtual Games'."""
        html = self._read("virtual_training_hub.html")
        assert "Virtual Games" in html

    def test_vgh06b_page_name_says_virtual_games(self):
        """VGH-06b: virtual_training_hub.html _page_name is 'Virtual Games'."""
        html = self._read("virtual_training_hub.html")
        assert "'Virtual Games'" in html

    def test_vgh07_contains_coming_soon_for_planned(self):
        """VGH-07: virtual_training_hub.html contains 'Coming soon' text for planned cards."""
        html = self._read("virtual_training_hub.html")
        assert "Coming soon" in html

    def test_vgh08_contains_play_cta_structure(self):
        """VGH-08: virtual_training_hub.html contains Play CTA link class for active game."""
        html = self._read("virtual_training_hub.html")
        assert "vg-btn-play" in html

    def test_vgh09_contains_disabled_cta_for_planned(self):
        """VGH-09: virtual_training_hub.html contains disabled Coming Soon button."""
        html = self._read("virtual_training_hub.html")
        assert "vg-btn-coming-soon" in html
        assert "disabled" in html

    def test_vgh10_skill_badge_class_present(self):
        """VGH-10: virtual_training_hub.html contains vg-skill-badge CSS class."""
        html = self._read("virtual_training_hub.html")
        assert "vg-skill-badge" in html

    def test_vgh11_football_benefit_class_present(self):
        """VGH-11: virtual_training_hub.html contains vg-football-benefit CSS class."""
        html = self._read("virtual_training_hub.html")
        assert "vg-football-benefit" in html

    def test_vgh11b_all_games_context_key_used(self):
        """VGH-11b: virtual_training_hub.html iterates over all_games (not active_games)."""
        html = self._read("virtual_training_hub.html")
        assert "all_games" in html
        assert "active_games" not in html


# ── VGH-12: Training hub label ────────────────────────────────────────────────

class TestTrainingHubVirtualGamesLabel:

    def _read(self, filename: str) -> str:
        return (_TEMPLATES_DIR / filename).read_text(encoding="utf-8")

    def test_vgh12_training_hub_shows_virtual_games_label(self):
        """VGH-12: training_hub.html Virtual sub-link text says 'Virtual Games'."""
        html = self._read("training_hub.html")
        assert "Virtual Games" in html

    def test_vgh12b_virtual_training_url_unchanged(self):
        """VGH-12b: training_hub.html Virtual sub-link still points to /virtual-training (URL unchanged)."""
        html = self._read("training_hub.html")
        assert 'href="/virtual-training"' in html


# ── VGH-13: Color Reaction regression ────────────────────────────────────────

class TestColorReactionRegression:

    def test_vgh13_color_reaction_route_still_registered(self):
        """VGH-13: /virtual-training/color-reaction route is still registered."""
        from app.api.web_routes import router
        paths = [r.path for r in router.routes]
        assert "/virtual-training/color-reaction" in paths

    def test_vgh13b_color_reaction_submit_route_registered(self):
        """VGH-13b: POST /virtual-training/color-reaction/submit still registered."""
        from app.api.web_routes import router
        found = any(
            "/virtual-training/color-reaction/submit" in getattr(r, "path", "")
            for r in router.routes
        )
        assert found

    def test_vgh13c_history_route_registered(self):
        """VGH-13c: /virtual-training/history still registered."""
        from app.api.web_routes import router
        paths = [r.path for r in router.routes]
        assert "/virtual-training/history" in paths


# ── VGH-14: Adaptive Learning regression ─────────────────────────────────────

class TestAdaptiveLearningRegression:

    def test_vgh14_adaptive_learning_handler_callable(self):
        """VGH-14: /adaptive-learning handler still importable and callable."""
        from app.api.web_routes.adaptive_learning import adaptive_learning_page
        assert callable(adaptive_learning_page)

    def test_vgh14b_adaptive_learning_route_registered(self):
        """VGH-14b: /adaptive-learning is still registered in the app router."""
        from app.api.web_routes import router
        paths = [r.path for r in router.routes]
        assert "/adaptive-learning" in paths

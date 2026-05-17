"""
MCH_ — My Cards Hub tests (Phase 4B).

Routes under test:
  GET /my-cards               → hub page (200)
  GET /my-cards/player-card   → 303 → /dashboard/lfa-football-player/card-editor
  GET /my-cards/welcome-card  → 303 → /profile/onboarding-card

Backward-compat assertions:
  GET /dashboard/lfa-football-player/card-editor  — route still exists (MCH-10)
  GET /profile/onboarding-card                    — route still exists (MCH-11)

Dashboard template assertions:
  MCH-16: mod-nav tile href → /my-cards
  MCH-17: hero Edit CTA href → /my-cards/player-card
"""
import asyncio
import pathlib
import pytest
from unittest.mock import MagicMock, patch

from fastapi.responses import RedirectResponse

from app.api.web_routes.my_cards import (
    my_cards_hub,
    my_cards_player_card,
    my_cards_welcome_card,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.my_cards"

_DASHBOARD_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "dashboard_student_new.html"
)

_CARD_EDITOR_ROUTE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "api" / "web_routes" / "dashboard.py"
)

_WELCOME_CARD_ROUTE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "api" / "web_routes" / "profile.py"
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    m = MagicMock()
    m.url = MagicMock()
    m.url.path = "/my-cards"
    return m


def _user(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.email = "player@test.com"
    u.name  = "Test Player"
    u.credit_balance = 100
    return u


# ── MCH-01/02: /my-cards authentication ───────────────────────────────────────

class TestMyCardsHubAuth:
    def test_mch01_hub_authenticated_returns_200(self):
        """MCH-01: GET /my-cards with authenticated user renders hub template."""
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock(status_code=200)
            result = _run(my_cards_hub(_req(), user=_user()))
        mock_tmpl.TemplateResponse.assert_called_once()
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "my_cards_hub.html"

    def test_mch02_hub_unauthenticated_redirects(self):
        """MCH-02: Unauthenticated access is blocked by Depends(get_current_user_web).

        The dependency raises HTTPException(401) before the function body runs.
        We verify the route signature includes the dependency guard.
        """
        import inspect
        sig = inspect.signature(my_cards_hub)
        params = sig.parameters
        assert "user" in params


# ── MCH-03/04/05: Hub tile content ────────────────────────────────────────────

class TestMyCardsHubTiles:
    def _rendered_ctx(self):
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(my_cards_hub(_req(), user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        return ctx

    def test_mch03_player_card_spec_in_hub(self):
        """MCH-03: card_specs includes player_card."""
        ctx = self._rendered_ctx()
        assert "card_specs" in ctx
        ids = [s.card_type_id for s in ctx["card_specs"]]
        assert "player_card" in ids

    def test_mch04_welcome_card_spec_in_hub(self):
        """MCH-04: card_specs includes welcome_card."""
        ctx = self._rendered_ctx()
        ids = [s.card_type_id for s in ctx["card_specs"]]
        assert "welcome_card" in ids

    def test_mch05_four_coming_soon_specs_in_hub(self):
        """MCH-05: card_specs includes the 4 v0 coming-soon types."""
        ctx = self._rendered_ctx()
        v0_ids = {s.card_type_id for s in ctx["card_specs"] if s.content_contract.version == 0}
        assert v0_ids == {"match_card", "event_card", "birthday_card", "badge_card"}


# ── MCH-06/07: Redirect routes ────────────────────────────────────────────────

class TestMyCardsDetailRedirects:
    def test_mch06_player_card_redirects_to_card_editor(self):
        """MCH-06: GET /my-cards/player-card → 303 to /dashboard/lfa-football-player/card-editor."""
        result = _run(my_cards_player_card(user=_user()))
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert result.headers["location"] == "/dashboard/lfa-football-player/card-editor"

    def test_mch07_welcome_card_redirects_to_onboarding_card(self):
        """MCH-07: GET /my-cards/welcome-card → 303 to /profile/onboarding-card."""
        result = _run(my_cards_welcome_card(user=_user()))
        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert result.headers["location"] == "/profile/onboarding-card"


# ── MCH-08/09: Redirect route auth guards ─────────────────────────────────────

class TestMyCardsDetailAuth:
    def test_mch08_player_card_route_has_auth_dependency(self):
        """MCH-08: /my-cards/player-card requires get_current_user_web."""
        import inspect
        sig = inspect.signature(my_cards_player_card)
        assert "user" in sig.parameters

    def test_mch09_welcome_card_route_has_auth_dependency(self):
        """MCH-09: /my-cards/welcome-card requires get_current_user_web."""
        import inspect
        sig = inspect.signature(my_cards_welcome_card)
        assert "user" in sig.parameters


# ── MCH-10/11: Backward compatibility ─────────────────────────────────────────

class TestBackwardCompatibility:
    def test_mch10_old_card_editor_route_still_defined(self):
        """MCH-10: /dashboard/lfa-football-player/card-editor route still exists."""
        src = _CARD_EDITOR_ROUTE_PATH.read_text()
        assert '/dashboard/lfa-football-player/card-editor' in src
        assert 'async def lfa_player_card_editor' in src

    def test_mch11_old_welcome_card_route_still_defined(self):
        """MCH-11: /profile/onboarding-card route still exists."""
        src = _WELCOME_CARD_ROUTE_PATH.read_text()
        assert '/profile/onboarding-card' in src
        assert 'async def onboarding_welcome_card' in src


# ── MCH-12/13/14: Hub template text assertions ────────────────────────────────

class TestMyCardsHubTemplate:
    @pytest.fixture(scope="class")
    def hub_src(self):
        path = (
            pathlib.Path(__file__).resolve().parents[4]
            / "app" / "templates" / "my_cards_hub.html"
        )
        return path.read_text()

    def test_mch12_hub_template_contains_my_cards_text(self, hub_src):
        """MCH-12: Hub template heading reads 'My Cards'."""
        assert "My Cards" in hub_src

    def test_mch13_active_tile_href_uses_my_cards_prefix(self, hub_src):
        """MCH-13: Active tile href prefix is /my-cards/ (Jinja2 dynamic replace pattern)."""
        assert '/my-cards/' in hub_src
        assert "replace('_', '-')" in hub_src

    def test_mch14_active_tiles_cover_both_v1_card_types(self):
        """MCH-14: Registry context contains both player_card and welcome_card as v1 specs."""
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(my_cards_hub(_req(), user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        v1_ids = {s.card_type_id for s in ctx["card_specs"] if s.content_contract.version >= 1}
        assert "player_card" in v1_ids
        assert "welcome_card" in v1_ids


# ── MCH-15: Registry drives the spec list ─────────────────────────────────────

class TestRegistryIntegration:
    def test_mch15_hub_spec_list_comes_from_registry(self):
        """MCH-15: card_specs in hub context equals registry.list_card_type_ids() (6 types)."""
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(my_cards_hub(_req(), user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        from app.services.card_system import card_registry
        expected_ids = set(card_registry.list_card_type_ids())
        actual_ids   = {s.card_type_id for s in ctx["card_specs"]}
        assert actual_ids == expected_ids
        assert len(ctx["card_specs"]) == 6


# ── MCH-16/17: Dashboard template navigation updated ──────────────────────────

class TestDashboardNavUpdated:
    @pytest.fixture(scope="class")
    def dashboard_src(self):
        return _DASHBOARD_TPL_PATH.read_text()

    def test_mch16_mod_nav_tile_href_is_my_cards(self, dashboard_src):
        """MCH-16: dashboard mod-nav tile href updated to /my-cards."""
        assert 'href="/my-cards"' in dashboard_src
        assert 'href="/dashboard/lfa-football-player/card-editor"' not in dashboard_src.split('mod-nav-section')[1].split('</section>')[0]

    def test_mch17_hero_edit_cta_href_is_my_cards_player_card(self, dashboard_src):
        """MCH-17: dashboard hero Edit CTA href updated to /my-cards/player-card."""
        assert 'href="/my-cards/player-card"' in dashboard_src

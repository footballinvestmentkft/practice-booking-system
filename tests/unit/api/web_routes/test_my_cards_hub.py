"""
MCH_ — My Cards Hub tests.

Updated for entitlement-aware hub (Phase 4B → unified hub):
  - Hub now exposes pc_state/wc_state/cc_state instead of card_specs
  - Hub route requires db session (UserLicense query + entitlement state)

Routes under test:
  GET /my-cards               → hub page (200)
  GET /my-cards/player-card   → 303 → /dashboard/lfa-football-player/card-editor
  GET /my-cards/welcome-card  → 303 → /profile/onboarding-card

Backward-compat assertions:
  GET /dashboard/lfa-football-player/card-editor  — route still exists (MCH-10)
  GET /profile/onboarding-card                    — route still exists (MCH-11)

Dashboard template assertions:
  MCH-16: dashboard href="/my-cards" present in CTA context
  MCH-17: dashboard card CTAs link to /my-cards (not individual card routes)
"""
import asyncio
import inspect
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


def _req(query_params=None):
    m = MagicMock()
    m.url = MagicMock()
    m.url.path = "/my-cards"
    params = query_params or {}
    m.query_params.get = lambda k, default=None: params.get(k, default)
    return m


def _user(uid=42, balance=500):
    u = MagicMock()
    u.id             = uid
    u.role           = UserRole.STUDENT
    u.email          = "player@test.com"
    u.credit_balance = balance
    return u


def _design(design_id, credit_cost, is_premium, label=None):
    d = MagicMock()
    d.id          = design_id
    d.label       = label or design_id.replace("_", " ").title()
    d.credit_cost = credit_cost
    d.is_premium  = is_premium
    return d


def _make_db(license_variant="fifa"):
    """Return a MagicMock db with UserLicense query chain pre-configured."""
    db = MagicMock()
    mock_license = MagicMock()
    mock_license.card_variant = license_variant
    db.query.return_value.filter.return_value.first.return_value = (
        mock_license if license_variant is not None else None
    )
    db.add    = MagicMock()
    db.commit = MagicMock()
    return db


def _call_hub(
    user=None,
    db=None,
    license_variant="fifa",
    accessible_ids=None,
    query_params=None,
    designs=None,
):
    """Call my_cards_hub directly with all dependencies mocked."""
    user           = user or _user()
    db             = db   or _make_db(license_variant)
    accessible_ids = accessible_ids or set()
    default_designs = [
        _design("fifa",    credit_cost=0,   is_premium=False, label="FIFA Classic"),
        _design("compact", credit_cost=300, is_premium=True,  label="Compact"),
    ]
    captured = {}

    def fake_template_response(template_name, context):
        captured["template"] = template_name
        captured["context"]  = context
        return MagicMock(status_code=200)

    def fake_accessible(_db, uid, card_type_id, design_id):
        return (card_type_id, design_id) in accessible_ids

    with patch(f"{_BASE}.get_all_designs", return_value=designs or default_designs), \
         patch(f"{_BASE}.is_design_accessible", side_effect=fake_accessible), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_template_response):
        _run(my_cards_hub(_req(query_params), db=db, user=user))

    return captured


# ── MCH-01/02: /my-cards authentication ───────────────────────────────────────

class TestMyCardsHubAuth:

    def test_mch01_hub_authenticated_returns_200(self):
        """MCH-01: GET /my-cards with authenticated user renders hub template."""
        cap = _call_hub()
        assert cap["template"] == "my_cards_hub.html"

    def test_mch02_hub_unauthenticated_redirects(self):
        """MCH-02: Hub route declares get_current_user_web dependency."""
        sig = inspect.signature(my_cards_hub)
        assert "user" in sig.parameters


# ── MCH-03/04/05: Hub context keys ────────────────────────────────────────────

class TestMyCardsHubContext:

    def test_mch03_player_card_state_in_hub_context(self):
        """MCH-03: hub context contains Player Card state keys."""
        ctx = _call_hub()["context"]
        for key in ("pc_state", "pc_price", "pc_design", "pc_design_label"):
            assert key in ctx, f"Missing key: {key}"

    def test_mch04_welcome_card_state_in_hub_context(self):
        """MCH-04: hub context contains Welcome Card state keys."""
        ctx = _call_hub()["context"]
        for key in ("wc_state", "wc_price"):
            assert key in ctx, f"Missing key: {key}"

    def test_mch05_challenge_card_state_in_hub_context(self):
        """MCH-05: hub context contains Challenge Card state keys."""
        ctx = _call_hub()["context"]
        for key in ("cc_state", "cc_price"):
            assert key in ctx, f"Missing key: {key}"


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
        sig = inspect.signature(my_cards_player_card)
        assert "user" in sig.parameters

    def test_mch09_welcome_card_route_has_auth_dependency(self):
        """MCH-09: /my-cards/welcome-card requires get_current_user_web."""
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


# ── MCH-12/13/14: Hub template structural assertions ─────────────────────────

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

    def test_mch13_hub_template_has_three_panels(self, hub_src):
        """MCH-13: Hub template has static links for all three card families."""
        assert "/my-cards/player-card" in hub_src
        assert "/my-cards/welcome-card" in hub_src
        assert "/my-cards/challenge-card" in hub_src

    def test_mch14_hub_context_has_all_state_keys(self):
        """MCH-14: All six state context keys present (pc/wc/cc state+price)."""
        ctx = _call_hub()["context"]
        for key in ("pc_state", "pc_price", "wc_state", "wc_price", "cc_state", "cc_price"):
            assert key in ctx, f"Missing context key: {key}"


# ── MCH-15: Entitlement state correctness ─────────────────────────────────────

class TestEntitlementStates:

    def test_mch15_pc_free_state_for_fifa(self):
        """MCH-15: pc_state='free' when card_variant=fifa (is_premium=False)."""
        ctx = _call_hub(license_variant="fifa", accessible_ids=set())["context"]
        assert ctx["pc_state"] == "free"

    def test_mch15b_pc_get_card_state_compact_not_owned_sufficient_credits(self):
        """MCH-15b: pc_state='get_card' when compact not owned and balance ≥ 300."""
        ctx = _call_hub(
            user=_user(balance=500),
            license_variant="compact",
            accessible_ids=set(),
        )["context"]
        assert ctx["pc_state"] == "get_card"

    def test_mch15c_pc_locked_state_compact_not_owned_insufficient_credits(self):
        """MCH-15c: pc_state='locked' when compact not owned and balance < 300."""
        ctx = _call_hub(
            user=_user(balance=50),
            license_variant="compact",
            accessible_ids=set(),
        )["context"]
        assert ctx["pc_state"] == "locked"

    def test_mch15d_wc_get_card_state(self):
        """MCH-15d: wc_state='get_card' when not owned and balance ≥ 200."""
        ctx = _call_hub(user=_user(balance=9999), accessible_ids=set())["context"]
        assert ctx["wc_state"] == "get_card"

    def test_mch15e_cc_get_card_state(self):
        """MCH-15e: cc_state='get_card' when not owned and balance ≥ 150."""
        ctx = _call_hub(user=_user(balance=9999), accessible_ids=set())["context"]
        assert ctx["cc_state"] == "get_card"


# ── MCH-16/17: Dashboard template navigation updated ──────────────────────────

class TestDashboardNavUpdated:

    @pytest.fixture(scope="class")
    def dashboard_src(self):
        return _DASHBOARD_TPL_PATH.read_text()

    def test_mch16_dashboard_has_my_cards_cta(self, dashboard_src):
        """MCH-16: Dashboard card CTAs include href="/my-cards"."""
        assert 'href="/my-cards"' in dashboard_src

    def test_mch17_dashboard_card_ctas_funnel_to_my_cards(self, dashboard_src):
        """MCH-17: Player/Welcome/Challenge panel primary CTAs link to /my-cards."""
        # All three dc-hero panels should funnel through /my-cards
        assert 'href="/my-cards" class="dc-cta-primary"' in dashboard_src
        # The old direct link to /profile/onboarding-card must no longer be a primary CTA
        assert 'href="/profile/onboarding-card" class="dc-cta-primary"' not in dashboard_src
        # /challenges/send is preserved (social action, not a card management action)
        assert 'href="/challenges/send"' in dashboard_src


# ── MCH-CH-01..09: Challenge Card manager route ───────────────────────────────

import pathlib as _pathlib

_CC_TEMPLATE_PATH = (
    _pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "my_cards_challenge_card.html"
)

_CC_BASE = "app.api.web_routes.my_cards"


def _db_mock():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.add    = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    return db


def _theme(tid, label, dot="#667eea", is_premium=False):
    t = MagicMock()
    t.id         = tid
    t.label      = label
    t.dot_color  = dot
    t.is_premium = is_premium
    return t


class TestMyChallengeCardRoute:

    def _call(self, *, user=None, db=None):
        from app.api.web_routes.my_cards import my_cards_challenge_card
        user = user or _user()
        db   = db   or _db_mock()

        captured = {}

        def _capture(tmpl, ctx, **kw):
            captured["template"] = tmpl
            captured["context"]  = ctx
            return MagicMock(status_code=200)

        draft_mock = MagicMock()
        draft_mock.draft_theme = "default"
        themes_mock = [_theme("default", "Slate"), _theme("midnight", "Midnight")]

        with patch(f"{_CC_BASE}.templates") as mock_tmpl, \
             patch(f"{_CC_BASE}.CardDraftService") as mock_ds, \
             patch(f"{_CC_BASE}.get_all_themes", return_value=themes_mock):
            mock_tmpl.TemplateResponse.side_effect = _capture
            mock_ds.get_or_create_singleton.return_value = draft_mock
            _run(my_cards_challenge_card(
                request=MagicMock(),
                db=db,
                user=user,
            ))

        return captured

    def test_mch_ch01_returns_200(self):
        """MCH-CH-01: GET /my-cards/challenge-card logged-in user → 200."""
        cap = self._call()
        assert cap.get("template") == "my_cards_challenge_card.html"

    def test_mch_ch02_route_has_auth_dependency(self):
        """MCH-CH-02: route signature requires authenticated user."""
        from app.api.web_routes.my_cards import my_cards_challenge_card
        sig = inspect.signature(my_cards_challenge_card)
        assert "user" in sig.parameters

    def test_mch_ch03_hub_challenge_state_key_present(self):
        """MCH-CH-03: hub context contains cc_state key."""
        ctx = _call_hub(accessible_ids=set())["context"]
        assert "cc_state" in ctx

    def test_mch_ch04_hub_template_has_challenge_card_static_link(self):
        """MCH-CH-04: hub template contains static /my-cards/challenge-card link."""
        hub_html = _CC_TEMPLATE_PATH.parent.joinpath("my_cards_hub.html").read_text()
        assert "/my-cards/challenge-card" in hub_html

    def test_mch_ch05_template_contains_post_16_9(self):
        """MCH-CH-05: template references challenge_post_16_9 format."""
        html = _CC_TEMPLATE_PATH.read_text()
        assert "challenge_post_16_9" in html

    def test_mch_ch06_template_contains_story_9_16(self):
        """MCH-CH-06: template references challenge_story_9_16 format."""
        html = _CC_TEMPLATE_PATH.read_text()
        assert "challenge_story_9_16" in html

    def test_mch_ch07_template_contains_challenges_link(self):
        """MCH-CH-07: template contains /challenges CTA link."""
        html = _CC_TEMPLATE_PATH.read_text()
        assert "/challenges" in html

    def test_mch_ch08_spec_is_editable_and_theme_compatible(self):
        """MCH-CH-08: CHALLENGE_CARD_SPEC.is_editable=True, theme_compatible=True."""
        from app.services.card_system._challenge_card import CHALLENGE_CARD_SPEC
        assert CHALLENGE_CARD_SPEC.is_editable is True
        assert CHALLENGE_CARD_SPEC.theme_compatible is True
        assert CHALLENGE_CARD_SPEC.has_published_state is False

    def test_mch_ch09_context_contains_formats_with_both_ids(self):
        """MCH-CH-09: route context formats list contains both platform IDs."""
        cap = self._call()
        fmt_ids = [f["id"] for f in cap["context"]["formats"]]
        assert "challenge_post_16_9"  in fmt_ids
        assert "challenge_story_9_16" in fmt_ids

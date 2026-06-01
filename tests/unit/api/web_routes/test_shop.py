"""Card shop route tests — SH-01..SH-12.

SH-01  GET /shop → 200, shop_landing.html
SH-02  GET /shop/cards → 200, shop_cards.html
SH-03  GET /shop/cards/player → 200, shop_player_card.html, design_rows present
SH-04  Player Card not owned, credits ≥ cost → state='get_card'
SH-05  Player Card not owned, credits < cost → state='locked'
SH-06  Player Card owned → state='owned'
SH-07  GET /shop/cards/welcome → 200, shop_welcome_card.html, format_rows present
SH-08  GET /shop/cards/challenge → 200, shop_challenge_card.html, format_rows present
SH-09  POST /shop/cards/{type}/buy/{id} success → 303 to shop family page
SH-10  POST purchase error → redirects with error param (free/owned/credits/invalid)
SH-11  All shop routes declare auth dependency
SH-12  Shop templates: breadcrumb links to /shop, include spec_subpage_hdr
"""
import asyncio
import inspect
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_BASE = "app.api.web_routes.shop"

_TEMPLATE_BASE = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates"
)


def _run(coro):
    return asyncio.run(coro)


def _user(balance=500):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = 42
    u.credit_balance = balance
    u.role = UserRole.STUDENT
    return u


def _req(path="/shop", query_params=None):
    r = MagicMock()
    r.url.path = path
    params = query_params or {}
    r.query_params.get = lambda k, default=None: params.get(k, default)
    return r


def _db():
    return MagicMock()


def _design(design_id, credit_cost, is_premium=True, label=None):
    d = MagicMock()
    d.id          = design_id
    d.label       = label or design_id.title()
    d.description = ""
    d.credit_cost = credit_cost
    d.is_premium  = is_premium
    return d


# ── SH-01: /shop landing ──────────────────────────────────────────────────────

class TestShopLanding:

    def test_sh01_landing_renders_shop_unified_html(self):
        """SH-01 (SHOP-1): GET /shop → shop_unified.html (unified listing)."""
        from app.api.web_routes.shop import shop_landing

        captured = {}

        def fake_tmpl(tmpl, ctx, **kw):
            captured["template"] = tmpl
            captured["context"]  = ctx
            return MagicMock(status_code=200)

        with patch(f"{_BASE}._build_catalog", return_value=[]), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
            _run(shop_landing(request=_req("/shop"), db=MagicMock(), user=_user()))

        assert captured["template"] == "shop_unified.html", \
            "SHOP-1: /shop must render shop_unified.html (not shop_landing.html)"

    def test_sh01b_landing_context_has_user(self):
        """SH-01b: /shop context includes user."""
        from app.api.web_routes.shop import shop_landing

        captured = {}

        def fake_tmpl(tmpl, ctx, **kw):
            captured["context"] = ctx
            return MagicMock(status_code=200)

        u = _user(balance=999)
        with patch(f"{_BASE}._build_catalog", return_value=[]), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
            _run(shop_landing(request=_req(), db=MagicMock(), user=u))

        assert captured["context"]["user"] is u


# ── SH-02: /shop/cards overview ───────────────────────────────────────────────

class TestShopCards:

    def test_sh02_cards_redirects_to_unified(self):
        """SH-02 (SHOP-2): GET /shop/cards -> 302 /shop."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.shop import shop_cards
        resp = _run(shop_cards(request=_req("/shop/cards"), user=_user()))
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/shop"



# ── SH-03..06: /shop/cards/player ─────────────────────────────────────────────
# SHOP-2: handler is now a 302 redirect

class TestShopPlayerCard:

    def test_sh03_player_redirects_302(self):
        """SH-03 (SHOP-2): GET /shop/cards/player → 302 /shop?type=player_card."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.shop import shop_player_card
        resp = _run(shop_player_card(request=_req("/shop/cards/player"), user=_user()))
        assert isinstance(resp, RedirectResponse) and resp.status_code == 302
        assert "/shop?type=player_card" in resp.headers["location"]

    def test_sh04_purchased_param_passthrough(self):
        """SH-04 (SHOP-2): purchased param passes through redirect URL."""
        from app.api.web_routes.shop import shop_player_card
        resp = _run(shop_player_card(
            request=_req("/shop/cards/player", {"purchased": "compact"}), user=_user()
        ))
        assert "purchased=compact" in resp.headers["location"]

    def test_sh05_error_param_passthrough(self):
        """SH-05 (SHOP-2): error param passes through redirect URL."""
        from app.api.web_routes.shop import shop_player_card
        resp = _run(shop_player_card(
            request=_req("/shop/cards/player", {"error": "credits"}), user=_user()
        ))
        assert "error=credits" in resp.headers["location"]

    def test_sh06_state_logic_in_catalog_service(self):
        """SH-06 (SHOP-2): owned/locked/get_card state logic in shop_catalog_service._state()."""
        from app.services.shop_catalog_service import _state
        assert _state(300, True, owned=True,  credits=0)   == "owned"
        assert _state(300, True, owned=False, credits=500) == "get_card"
        assert _state(300, True, owned=False, credits=50)  == "locked"
        assert _state(0,   False, owned=False, credits=999) == "not_available"


# ── SH-07: /shop/cards/welcome ────────────────────────────────────────────────
# SHOP-2: handler is now a 302 redirect

class TestShopWelcomeCard:

    def test_sh07_welcome_redirects_302(self):
        """SH-07 (SHOP-2): GET /shop/cards/welcome → 302 /shop?type=welcome_card."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.shop import shop_welcome_card
        resp = _run(shop_welcome_card(request=_req("/shop/cards/welcome"), user=_user()))
        assert isinstance(resp, RedirectResponse) and resp.status_code == 302
        assert "/shop?type=welcome_card" in resp.headers["location"]

    def test_sh07b_error_passthrough(self):
        """SH-07b (SHOP-2): error=not_owned passes through redirect URL."""
        from app.api.web_routes.shop import shop_welcome_card
        resp = _run(shop_welcome_card(
            request=_req("/shop/cards/welcome", {"error": "not_owned"}), user=_user()
        ))
        assert "error=not_owned" in resp.headers["location"]


# ── SH-08: /shop/cards/challenge ──────────────────────────────────────────────
# SHOP-2: handler is now a 302 redirect

class TestShopChallengeCard:

    def test_sh08_challenge_redirects_302(self):
        """SH-08 (SHOP-2): GET /shop/cards/challenge → 302 /shop?type=challenge_card."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.shop import shop_challenge_card
        resp = _run(shop_challenge_card(request=_req("/shop/cards/challenge"), user=_user()))
        assert isinstance(resp, RedirectResponse) and resp.status_code == 302
        assert "/shop?type=challenge_card" in resp.headers["location"]


# ── SH-09..10: Purchase POST ──────────────────────────────────────────────────

class TestShopBuy:

    def _call_buy(self, card_type_id, design_id, user=None, side_effect=None):
        from app.api.web_routes.shop import shop_buy

        with patch(f"{_BASE}.purchase_design", side_effect=side_effect or (lambda *a, **kw: None)):
            return _run(shop_buy(
                card_type_id=card_type_id,
                design_id=design_id,
                db=_db(),
                user=user or _user(),
            ))

    def test_sh09_success_redirects_to_shop_family(self):
        """SH-09: successful purchase → 303 to /shop/cards/player?purchased=compact."""
        resp = self._call_buy("player_card", "compact")
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert loc.startswith("/shop/cards/player?")
        assert "purchased=compact" in loc

    def test_sh09b_welcome_card_success_redirects_to_shop_welcome(self):
        """SH-09b: WC purchase → /shop/cards/welcome?purchased=..."""
        resp = self._call_buy("welcome_card", "instagram_portrait")
        loc  = resp.headers["location"]
        assert "/shop/cards/welcome" in loc
        assert "purchased=instagram_portrait" in loc

    def test_sh10a_free_error_redirect(self):
        """SH-10a: FreeDesignError → /shop/cards/player?error=free."""
        from app.services.card_design_service import FreeDesignError
        resp = self._call_buy("player_card", "fclassic", side_effect=FreeDesignError())
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=free" in loc
        assert "/shop/cards/player" in loc

    def test_sh10b_already_owned_redirect(self):
        """SH-10b: AlreadyOwnedError → /shop/cards/player?error=owned."""
        from app.services.card_design_service import AlreadyOwnedError
        resp = self._call_buy("player_card", "compact", side_effect=AlreadyOwnedError())
        assert "error=owned" in resp.headers["location"]
        assert "/shop/cards/player" in resp.headers["location"]

    def test_sh10c_insufficient_credits_redirect(self):
        """SH-10c: InsufficientCreditsError → /shop/cards/player?error=credits."""
        from app.services.credit_service import InsufficientCreditsError
        resp = self._call_buy("player_card", "compact", side_effect=InsufficientCreditsError(300, 0))
        assert "error=credits" in resp.headers["location"]

    def test_sh10d_unknown_type_fallback(self):
        """SH-10d: unknown card_type_id → /shop/cards?error=invalid (fallback)."""
        resp = self._call_buy("bogus_type", "bogus", side_effect=ValueError("x"))
        loc  = resp.headers["location"]
        assert "error=invalid" in loc

    def test_sh10e_challenge_card_error_to_shop_challenge(self):
        """SH-10e: CC AlreadyOwnedError → /shop/cards/challenge?error=owned."""
        from app.services.card_design_service import AlreadyOwnedError
        resp = self._call_buy("challenge_card", "challenge_post_16_9", side_effect=AlreadyOwnedError())
        assert "/shop/cards/challenge" in resp.headers["location"]
        assert "error=owned" in resp.headers["location"]


# ── SH-11: Auth dependencies ──────────────────────────────────────────────────

class TestShopAuthDependencies:

    def test_sh11_all_shop_routes_have_user_dependency(self):
        """SH-11: all shop routes declare get_current_user_web dependency."""
        from app.api.web_routes.shop import (
            shop_landing,
            shop_cards,
            shop_player_card,
            shop_welcome_card,
            shop_challenge_card,
            shop_buy,
        )
        for fn in (shop_landing, shop_cards, shop_player_card, shop_welcome_card, shop_challenge_card, shop_buy):
            sig = inspect.signature(fn)
            assert "user" in sig.parameters, f"{fn.__name__} missing 'user' dependency"


# ── SH-12: Template structure ─────────────────────────────────────────────────

class TestShopTemplates:

    @pytest.fixture(scope="class")
    def player_src(self):
        return (_TEMPLATE_BASE / "shop_player_card.html").read_text()

    @pytest.fixture(scope="class")
    def welcome_src(self):
        return (_TEMPLATE_BASE / "shop_welcome_card.html").read_text()

    @pytest.fixture(scope="class")
    def challenge_src(self):
        return (_TEMPLATE_BASE / "shop_challenge_card.html").read_text()

    @pytest.fixture(scope="class")
    def landing_src(self):
        return (_TEMPLATE_BASE / "shop_landing.html").read_text()

    def test_sh12a_player_template_extends_student_base(self, player_src):
        """SH-12a: shop_player_card.html extends student_base."""
        assert "student_base.html" in player_src

    def test_sh12b_player_template_includes_spec_hdr(self, player_src):
        """SH-12b: shop_player_card.html includes spec_subpage_hdr."""
        assert "spec_subpage_hdr.html" in player_src

    def test_sh12c_player_template_has_breadcrumb_to_shop(self, player_src):
        """SH-12c: shop_player_card.html breadcrumb links to /shop."""
        assert 'href="/shop"' in player_src

    def test_sh12d_player_template_has_purchase_form(self, player_src):
        """SH-12d: shop_player_card.html has POST form to /shop/cards/player_card/buy/."""
        assert "/shop/cards/player_card/buy/" in player_src
        assert 'method="POST"' in player_src

    def test_sh12e_welcome_template_has_my_collection_link(self, welcome_src):
        """SH-12e: shop_welcome_card.html has back link to /my-cards/welcome-card."""
        assert "/my-cards/welcome" in welcome_src

    def test_sh12f_challenge_template_has_results_cta(self, challenge_src):
        """SH-12f: shop_challenge_card.html has link to /challenges/results."""
        assert "/challenges/results" in challenge_src

    def test_sh12g_landing_has_three_card_family_links(self, landing_src):
        """SH-12g: shop_landing.html links to all three shop family pages."""
        assert "/shop/cards/player" in landing_src
        assert "/shop/cards/welcome" in landing_src
        assert "/shop/cards/challenge" in landing_src

    def test_sh12h_welcome_template_purchase_form(self, welcome_src):
        """SH-12h: shop_welcome_card.html has POST form to /shop/cards/welcome_card/buy/."""
        assert "/shop/cards/welcome_card/buy/" in welcome_src
        assert 'method="POST"' in welcome_src

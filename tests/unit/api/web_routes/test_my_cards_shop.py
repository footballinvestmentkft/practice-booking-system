"""My Cards Shop tests.

MCS-01  GET /my-cards/shop renders without error (200 + template context)
MCS-02  Player Card free design → state="free"
MCS-03  Player Card premium, not owned, enough credits → state="purchasable"
MCS-04  Player Card premium, not owned, insufficient credits → state="locked"
MCS-05  Player Card premium, owned → state="owned"
MCS-06  Welcome Card not owned, enough credits → wc_state="purchasable"
MCS-07  Welcome Card not owned, insufficient credits → wc_state="locked"
MCS-08  Welcome Card owned → wc_state="owned"
MCS-09  Welcome/Challenge prices > 0 (never free)
MCS-10  POST /my-cards/designs/{type}/{id}/get → 303 redirect on success;
         ?error=free / ?error=owned / ?error=credits / ?error=invalid on failure
MCS-11  my_cards_shop.html uses student_content block + spec_subpage_hdr include
MCS-12  my_cards_shop.html breadcrumb links back to /my-cards
MCS-13  my_cards_shop.html URLSearchParams JS present for tab activation
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request
from fastapi.responses import RedirectResponse

_MY_CARDS = "app.api.web_routes.my_cards"
_SVC      = "app.services.card_design_service"


def _run(coro):
    return asyncio.run(coro)


def _make_user(balance: int = 500):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = 1
    u.credit_balance = balance
    u.role = UserRole.STUDENT
    return u


def _make_db():
    db = MagicMock()
    return db


def _make_request(path="/my-cards/shop", query_params=None):
    r = MagicMock(spec=Request)
    r.url.path = path
    params = query_params or {}
    r.query_params.get = lambda k, default=None: params.get(k, default)
    return r


def _make_design(design_id: str, credit_cost: int, is_premium: bool):
    d = MagicMock()
    d.id = design_id
    d.label = design_id.capitalize()
    d.description = ""
    d.credit_cost = credit_cost
    d.is_premium = is_premium
    return d


# ── MCS-01: Shop renders ──────────────────────────────────────────────────────

class TestShopRender:

    def _call_shop(self, user, db, accessible_ids=None, query_params=None):
        from app.api.web_routes.my_cards import my_cards_shop

        accessible_ids = accessible_ids or set()
        request = _make_request(query_params=query_params or {})

        free_design    = _make_design("fifa",    credit_cost=0,   is_premium=False)
        premium_design = _make_design("compact", credit_cost=300, is_premium=True)

        captured = {}

        def fake_template_response(template_name, context):
            captured["template"] = template_name
            captured["context"]  = context
            resp = MagicMock()
            resp.status_code = 200
            return resp

        def fake_accessible(db, uid, card_type_id, design_id):
            return (card_type_id, design_id) in accessible_ids

        with patch(f"{_MY_CARDS}.get_all_designs", return_value=[free_design, premium_design]), \
             patch(f"{_MY_CARDS}.is_design_accessible", side_effect=fake_accessible), \
             patch(f"{_MY_CARDS}.templates.TemplateResponse", side_effect=fake_template_response):
            _run(my_cards_shop(request=request, db=db, user=user))

        return captured

    def test_mcs01_renders_shop(self):
        """MCS-01: GET shop returns my_cards_shop.html with expected context keys."""
        user = _make_user(balance=500)
        db   = _make_db()
        ctx  = self._call_shop(user, db)
        assert ctx["template"] == "my_cards_shop.html"
        for key in ("player_design_rows", "wc_price", "cc_price", "wc_state", "cc_state"):
            assert key in ctx["context"], f"Missing context key: {key}"

    def test_mcs02_free_design_state(self):
        """MCS-02: Player Card non-premium design → state='free'."""
        user = _make_user(balance=0)
        db   = _make_db()
        ctx  = self._call_shop(user, db)
        rows = ctx["context"]["player_design_rows"]
        free_row = next(r for r in rows if r["id"] == "fifa")
        assert free_row["state"] == "free"

    def test_mcs03_premium_purchasable(self):
        """MCS-03: premium design, not owned, credits ≥ cost → state='purchasable'."""
        user = _make_user(balance=500)
        db   = _make_db()
        ctx  = self._call_shop(user, db, accessible_ids=set())
        rows = ctx["context"]["player_design_rows"]
        row  = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "purchasable"

    def test_mcs04_premium_locked_insufficient_credits(self):
        """MCS-04: premium design, not owned, credits < cost → state='locked'."""
        user = _make_user(balance=50)
        db   = _make_db()
        ctx  = self._call_shop(user, db, accessible_ids=set())
        rows = ctx["context"]["player_design_rows"]
        row  = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "locked"

    def test_mcs05_premium_owned(self):
        """MCS-05: premium design, owned → state='owned'."""
        user = _make_user(balance=500)
        db   = _make_db()
        ctx  = self._call_shop(user, db, accessible_ids={("player_card", "compact")})
        rows = ctx["context"]["player_design_rows"]
        row  = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "owned"

    def test_mcs06_welcome_card_purchasable(self):
        """MCS-06: Welcome Card not owned, credits ≥ price → wc_state='purchasable'."""
        user = _make_user(balance=9999)
        db   = _make_db()
        ctx  = self._call_shop(user, db, accessible_ids=set())
        assert ctx["context"]["wc_state"] == "purchasable"

    def test_mcs07_welcome_card_locked(self):
        """MCS-07: Welcome Card not owned, credits < price → wc_state='locked'."""
        user = _make_user(balance=0)
        db   = _make_db()
        ctx  = self._call_shop(user, db, accessible_ids=set())
        assert ctx["context"]["wc_state"] == "locked"

    def test_mcs08_welcome_card_owned(self):
        """MCS-08: Welcome Card owned → wc_state='owned'."""
        user = _make_user(balance=9999)
        db   = _make_db()
        ctx  = self._call_shop(user, db, accessible_ids={("welcome_card", "default")})
        assert ctx["context"]["wc_state"] == "owned"

    def test_mcs09_wc_cc_prices_never_free(self):
        """MCS-09: Welcome Card and Challenge Card prices are > 0 (never free)."""
        from app.services.card_design_service import _NON_PLAYER_CARD_PRICES
        wc = _NON_PLAYER_CARD_PRICES[("welcome_card",   "default")]
        cc = _NON_PLAYER_CARD_PRICES[("challenge_card", "challenge")]
        assert wc > 0, "Welcome Card price must be > 0"
        assert cc > 0, "Challenge Card price must be > 0"


# ── MCS-10: POST purchase redirect behaviour ──────────────────────────────────

class TestPurchaseRedirects:

    def _call_get_card(self, card_type_id, design_id, user, db, side_effect=None):
        from app.api.web_routes.my_cards import get_card

        with patch(f"{_MY_CARDS}.purchase_design", side_effect=side_effect or (lambda *a, **kw: None)):
            return _run(get_card(card_type_id=card_type_id, design_id=design_id, db=db, user=user))

    def test_mcs10a_success_redirect(self):
        """MCS-10a: successful purchase → 303 to /my-cards?purchased=... (hub, not shop)."""
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "compact", user, db)
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert loc.startswith("/my-cards?"), f"Expected hub redirect, got: {loc}"
        assert "purchased=player_card:compact" in loc
        assert "/my-cards/shop" not in loc

    def test_mcs10b_free_error_redirect(self):
        """MCS-10b: FreeDesignError → /my-cards/shop?error=free&tab=player."""
        from app.services.card_design_service import FreeDesignError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "fifa", user, db, side_effect=FreeDesignError())
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=free" in loc
        assert "tab=player" in loc

    def test_mcs10c_already_owned_redirect(self):
        """MCS-10c: AlreadyOwnedError → /my-cards/shop?error=owned&tab=player."""
        from app.services.card_design_service import AlreadyOwnedError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "compact", user, db, side_effect=AlreadyOwnedError())
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=owned" in loc
        assert "tab=player" in loc

    def test_mcs10d_insufficient_credits_redirect(self):
        """MCS-10d: InsufficientCreditsError → /my-cards/shop?error=credits&tab=player."""
        from app.services.credit_service import InsufficientCreditsError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "compact", user, db, side_effect=InsufficientCreditsError(300, 0))
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=credits" in loc
        assert "tab=player" in loc

    def test_mcs10e_invalid_card_type_redirect(self):
        """MCS-10e: ValueError (unknown card_type_id) → ?error=invalid."""
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("bogus_type", "bogus_design", user, db, side_effect=ValueError("unknown"))
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=invalid" in loc

    def test_mcs10f_welcome_card_error_tab(self):
        """MCS-10f: AlreadyOwnedError on welcome_card → tab=welcome in redirect."""
        from app.services.card_design_service import AlreadyOwnedError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("welcome_card", "default", user, db, side_effect=AlreadyOwnedError())
        loc  = resp.headers["location"]
        assert "tab=welcome" in loc

    def test_mcs10g_challenge_card_error_tab(self):
        """MCS-10g: InsufficientCreditsError on challenge_card → tab=challenge in redirect."""
        from app.services.credit_service import InsufficientCreditsError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("challenge_card", "challenge", user, db, side_effect=InsufficientCreditsError(150, 0))
        loc  = resp.headers["location"]
        assert "tab=challenge" in loc


# ── MCS-11..13: Template structural tests ────────────────────────────────────

class TestShopTemplate:

    _TEMPLATE_PATH = (
        "app/templates/my_cards_shop.html"
    )

    def _read_template(self):
        from pathlib import Path
        base = Path(__file__).resolve().parents[4]
        return (base / self._TEMPLATE_PATH).read_text()

    def test_mcs11_uses_student_content_block(self):
        """MCS-11: shop template uses student_content block (not bare 'content')."""
        src = self._read_template()
        assert "block student_content" in src, "Must extend student_base via student_content block"
        assert "spec_subpage_hdr.html" in src, "Must include spec_subpage_hdr for nav context"

    def test_mcs12_breadcrumb_links_to_hub(self):
        """MCS-12: breadcrumb contains link back to /my-cards hub."""
        src = self._read_template()
        assert 'href="/my-cards"' in src, "Breadcrumb must link back to /my-cards hub"

    def test_mcs13_tab_url_param_js_present(self):
        """MCS-13: URLSearchParams JS present for ?tab= auto-activation on page load."""
        src = self._read_template()
        assert "URLSearchParams" in src, "Must use URLSearchParams to auto-activate tab from URL"

"""My Cards collection + compat wrapper tests — Phase 2 owned-only refactor.

MCS-01  GET /my-cards/player-card renders without error (owned-only context)
MCS-02  Player Card non-accessible design → not in design_rows (owned-only)
MCS-03  Player Card accessible design → state="owned" in design_rows
MCS-04  (reserved — shop state tests now in test_shop.py)
MCS-05  (reserved — shop state tests now in test_shop.py)
MCS-06  Welcome Card owned format → state="owned" in format_rows
MCS-07  Welcome Card non-accessible format → not in format_rows
MCS-08  Welcome Card format owned → state="owned" in format_rows
MCS-09  All WC and CC format prices > 0 (never free)
MCS-10  POST /my-cards/designs/{type}/{id}/get → 303 redirect on success to family page;
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


def _make_request(path="/my-cards/player-card", query_params=None):
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


# ── MCS-01..05: Player Card owned-only collection ─────────────────────────────

class TestPlayerCardShop:

    def _call_player_shop(self, user, db, accessible_ids=None, query_params=None):
        from app.api.web_routes.my_cards import my_cards_player_card

        accessible_ids = accessible_ids or set()
        request = _make_request(query_params=query_params or {})

        free_design    = _make_design("fclassic",    credit_cost=0,   is_premium=False)
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
            _run(my_cards_player_card(request=request, db=db, user=user))

        return captured

    def test_mcs01_renders_player_card_collection(self):
        """MCS-01: GET /my-cards/player-card returns my_cards_player_card.html with owned context."""
        user = _make_user(balance=500)
        db   = _make_db()
        ctx  = self._call_player_shop(user, db, accessible_ids={("player_card", "compact")})
        assert ctx["template"] == "my_cards_player_card.html"
        for key in ("design_rows", "owned_count", "total_count"):
            assert key in ctx["context"], f"Missing context key: {key}"

    def test_mcs02_non_accessible_design_not_in_rows(self):
        """MCS-02: non-accessible design → not in design_rows (owned-only)."""
        user = _make_user(balance=500)
        db   = _make_db()
        ctx  = self._call_player_shop(user, db, accessible_ids=set())
        rows = ctx["context"]["design_rows"]
        assert len(rows) == 0  # no accessible designs → empty

    def test_mcs03_accessible_design_in_rows_with_owned_state(self):
        """MCS-03: accessible design → in design_rows with state='owned'."""
        user = _make_user(balance=500)
        db   = _make_db()
        ctx  = self._call_player_shop(user, db, accessible_ids={("player_card", "compact")})
        rows = ctx["context"]["design_rows"]
        row  = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "owned"

    def test_mcs04_reserved(self):
        """MCS-04: reserved — shop state tests moved to test_shop.py (SH-04..06)."""
        pass

    def test_mcs05_reserved(self):
        """MCS-05: reserved — shop state tests moved to test_shop.py (SH-04..06)."""
        pass


# ── MCS-06..08: Welcome Card owned-only collection ───────────────────────────

class TestWelcomeCardShop:

    def _call_welcome_shop(self, user, db, accessible_ids=None):
        from app.api.web_routes.my_cards import my_cards_welcome_card

        accessible_ids = accessible_ids or set()
        request = _make_request(path="/my-cards/welcome-card")

        captured = {}

        def fake_template_response(template_name, context):
            captured["template"] = template_name
            captured["context"]  = context
            resp = MagicMock()
            resp.status_code = 200
            return resp

        def fake_accessible(db, uid, card_type_id, design_id):
            return (card_type_id, design_id) in accessible_ids

        with patch(f"{_MY_CARDS}.is_design_accessible", side_effect=fake_accessible), \
             patch(f"{_MY_CARDS}.templates.TemplateResponse", side_effect=fake_template_response):
            _run(my_cards_welcome_card(request=request, db=db, user=user))

        return captured

    def test_mcs06_welcome_card_owned_format_in_rows(self):
        """MCS-06: owned WC format → in format_rows with state='owned'."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        first_fmt = WELCOME_CARD_FORMATS[0]
        user = _make_user(balance=9999)
        db   = _make_db()
        ctx  = self._call_welcome_shop(
            user, db,
            accessible_ids={("welcome_card", first_fmt.design_id)},
        )
        rows = ctx["context"]["format_rows"]
        row  = next(r for r in rows if r["design_id"] == first_fmt.design_id)
        assert row["state"] == "owned"

    def test_mcs07_welcome_card_non_accessible_not_in_rows(self):
        """MCS-07: non-accessible WC format → not in format_rows."""
        user = _make_user(balance=9999)
        db   = _make_db()
        ctx  = self._call_welcome_shop(user, db, accessible_ids=set())
        rows = ctx["context"]["format_rows"]
        assert len(rows) == 0

    def test_mcs08_welcome_card_format_owned(self):
        """MCS-08: WC format owned → state='owned' in format_rows."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        first_fmt = WELCOME_CARD_FORMATS[0]
        user = _make_user(balance=9999)
        db   = _make_db()
        ctx  = self._call_welcome_shop(
            user, db,
            accessible_ids={("welcome_card", first_fmt.design_id)},
        )
        rows = ctx["context"]["format_rows"]
        row  = next(r for r in rows if r["design_id"] == first_fmt.design_id)
        assert row["state"] == "owned"


# ── MCS-09: Format prices > 0 ────────────────────────────────────────────────

class TestFormatPrices:

    def test_mcs09_all_wc_cc_format_prices_never_free(self):
        """MCS-09: All WC and CC format prices are > 0 (no free formats)."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS, CHALLENGE_CARD_FORMATS
        for fmt in WELCOME_CARD_FORMATS:
            assert fmt.credit_cost > 0, f"WC format {fmt.design_id} must cost > 0"
        for fmt in CHALLENGE_CARD_FORMATS:
            assert fmt.credit_cost > 0, f"CC format {fmt.design_id} must cost > 0"


# ── MCS-10: POST purchase redirect behaviour ──────────────────────────────────

class TestPurchaseRedirects:

    def _call_get_card(self, card_type_id, design_id, user, db, side_effect=None):
        from app.api.web_routes.my_cards import get_card

        with patch(f"{_MY_CARDS}.purchase_design", side_effect=side_effect or (lambda *a, **kw: None)):
            return _run(get_card(card_type_id=card_type_id, design_id=design_id, db=db, user=user))

    def test_mcs10a_success_redirect_to_family_page(self):
        """MCS-10a: successful purchase → 303 to /my-cards/player?purchased=compact."""
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "compact", user, db)
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert loc.startswith("/my-cards/player?"), f"Expected player redirect, got: {loc}"
        assert "purchased=compact" in loc
        assert "/my-cards/shop" not in loc
        assert "/my-cards?" not in loc

    def test_mcs10b_free_error_redirect(self):
        """MCS-10b: FreeDesignError → /my-cards/player?error=free."""
        from app.services.card_design_service import FreeDesignError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "fclassic", user, db, side_effect=FreeDesignError())
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=free" in loc
        assert "/my-cards/player" in loc
        assert "/my-cards/shop" not in loc

    def test_mcs10c_already_owned_redirect(self):
        """MCS-10c: AlreadyOwnedError → /my-cards/player?error=owned."""
        from app.services.card_design_service import AlreadyOwnedError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "compact", user, db, side_effect=AlreadyOwnedError())
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=owned" in loc
        assert "/my-cards/player" in loc

    def test_mcs10d_insufficient_credits_redirect(self):
        """MCS-10d: InsufficientCreditsError → /my-cards/player?error=credits."""
        from app.services.credit_service import InsufficientCreditsError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("player_card", "compact", user, db, side_effect=InsufficientCreditsError(300, 0))
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=credits" in loc
        assert "/my-cards/player" in loc

    def test_mcs10e_invalid_card_type_redirect(self):
        """MCS-10e: ValueError (unknown card_type_id) → /my-cards?error=invalid."""
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("bogus_type", "bogus_design", user, db, side_effect=ValueError("unknown"))
        loc  = resp.headers["location"]
        assert resp.status_code == 303
        assert "error=invalid" in loc

    def test_mcs10f_welcome_card_error_goes_to_welcome_family(self):
        """MCS-10f: AlreadyOwnedError on welcome_card → /my-cards/welcome?error=owned."""
        from app.services.card_design_service import AlreadyOwnedError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("welcome_card", "instagram_portrait", user, db, side_effect=AlreadyOwnedError())
        loc  = resp.headers["location"]
        assert "/my-cards/welcome" in loc
        assert "error=owned" in loc

    def test_mcs10g_challenge_card_error_goes_to_challenge_family(self):
        """MCS-10g: InsufficientCreditsError on challenge_card → /my-cards/challenge?error=credits."""
        from app.services.credit_service import InsufficientCreditsError
        user = _make_user()
        db   = _make_db()
        resp = self._call_get_card("challenge_card", "challenge_post_16_9", user, db, side_effect=InsufficientCreditsError(100, 0))
        loc  = resp.headers["location"]
        assert "/my-cards/challenge" in loc
        assert "error=credits" in loc


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

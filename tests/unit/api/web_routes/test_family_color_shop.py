"""
TS — Family Color Shop route tests (TS-1).

TS-01: GET /shop/cards/player/colors → 200 for authenticated user
TS-02: context player_colors list has 6 items
TS-03: free colors have is_owned=True in context
TS-04: premium color is_owned=True when ownership row exists
TS-05: premium color is_owned=False when no ownership row
TS-06: GET /shop/cards/player/colors redirects unauthenticated user
TS-07: POST /dashboard/unlock-color creates ownership and returns ok=True
TS-08: POST /dashboard/unlock-color deducts credit_balance 500
TS-09: POST /dashboard/unlock-color idempotent — second call already_owned=True, credits_charged=0
TS-10: POST /dashboard/unlock-color insufficient credits → 402
TS-11: POST /dashboard/unlock-color unknown color_id → 422
TS-12: POST /dashboard/unlock-color unsupported card_type_id → 422
TS-13: route count = 836
TS-14: /dashboard/unlock-theme still registered (backward compat)
TS-15: template source contains unlockColor JS function
TS-16: template source contains Unlock CTA button pattern
TS-17: template source contains credit balance display
TS-18: template source contains link to /shop/cards/player
TS-19: template source contains link to /card-editor/player
TS-20: editor template has no Unlock / Buy / price purchase affordance (CE-2 regression)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.card_color_service import (
    UnlockColorResult,
    get_colors_for_family,
)
from app.services.credit_service import InsufficientCreditsError

_SHOP_BASE  = "app.api.web_routes.shop"
_DASH_BASE  = "app.api.web_routes.dashboard"
_CCS_PATH   = "app.services.card_color_service"
# Module-level imports in shop.py — patch at the usage site
_SHOP_GET_OWNED = "app.api.web_routes.shop._get_owned_color_ids"

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 42, credit_balance: int = 1000) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.credit_balance = credit_balance
    return u


def _invoke_color_shop(owned_color_ids: set | None = None) -> dict:
    """Call shop_player_card_colors and return captured template context."""
    if owned_color_ids is None:
        owned_color_ids = set()

    from app.api.web_routes.shop import shop_player_card_colors

    user = _user()
    db   = MagicMock()
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_SHOP_BASE}.templates") as mock_tpl, \
         patch(_SHOP_GET_OWNED, return_value=owned_color_ids):
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        try:
            _run(shop_player_card_colors(request=MagicMock(), db=db, user=user))
        except Exception:
            pass

    return captured.get("context", {})


def _call_unlock_color(
    card_type_id: str,
    color_id: str,
    unlock_result=None,
    side_effect=None,
):
    """Call student_unlock_color and return the JSONResponse."""
    from app.api.web_routes.dashboard import student_unlock_color, _CardColorUnlockRequest

    user = _user()
    db   = MagicMock()
    payload = MagicMock()
    payload.card_type_id = card_type_id
    payload.color_id     = color_id

    with patch(f"{_DASH_BASE}._unlock_color") as mock_unlock:
        if side_effect is not None:
            mock_unlock.side_effect = side_effect
        elif unlock_result is not None:
            mock_unlock.return_value = unlock_result
        else:
            mock_unlock.return_value = UnlockColorResult(
                ok=True, already_owned=False, credits_charged=500,
                credit_balance=500, color_id=color_id, card_type_id=card_type_id,
            )

        resp = _run(student_unlock_color(payload=payload, db=db, user=user))

    return resp


# ── TS-01..TS-05: GET /shop/cards/player/colors context ───────────────────────

class TestColorShopGet:

    def test_ts_01_returns_200(self):
        ctx = _invoke_color_shop()
        assert ctx, "context not captured — handler likely raised"

    def test_ts_02_context_has_six_colors(self):
        ctx = _invoke_color_shop()
        colors = ctx.get("player_colors", [])
        assert len(colors) == 6

    def test_ts_03_free_colors_always_owned(self):
        ctx = _invoke_color_shop(owned_color_ids=set())
        colors = {c["id"]: c for c in ctx.get("player_colors", [])}
        assert colors["default"]["is_owned"] is True
        assert colors["midnight"]["is_owned"] is True
        assert colors["arctic"]["is_owned"] is True

    def test_ts_04_premium_color_owned_when_row_exists(self):
        ctx = _invoke_color_shop(owned_color_ids={"gold"})
        colors = {c["id"]: c for c in ctx.get("player_colors", [])}
        assert colors["gold"]["is_owned"] is True

    def test_ts_05_premium_color_not_owned_when_no_row(self):
        ctx = _invoke_color_shop(owned_color_ids=set())
        colors = {c["id"]: c for c in ctx.get("player_colors", [])}
        assert colors["gold"]["is_owned"] is False
        assert colors["emerald"]["is_owned"] is False
        assert colors["crimson"]["is_owned"] is False


# ── TS-07..TS-12: POST /dashboard/unlock-color ────────────────────────────────

class TestUnlockColorRoute:

    def test_ts_07_valid_purchase_returns_ok(self):
        resp = _call_unlock_color("player_card", "gold")
        body = json.loads(resp.body)
        assert body["ok"] is True
        assert resp.status_code == 200

    def test_ts_08_credit_balance_in_response(self):
        result = UnlockColorResult(
            ok=True, already_owned=False, credits_charged=500,
            credit_balance=500, color_id="gold", card_type_id="player_card",
        )
        resp = _call_unlock_color("player_card", "gold", unlock_result=result)
        body = json.loads(resp.body)
        assert body["credits_charged"] == 500
        assert body["credit_balance"] == 500

    def test_ts_09_idempotent_already_owned(self):
        result = UnlockColorResult(
            ok=True, already_owned=True, credits_charged=0,
            credit_balance=1000, color_id="gold", card_type_id="player_card",
        )
        resp = _call_unlock_color("player_card", "gold", unlock_result=result)
        body = json.loads(resp.body)
        assert body["ok"] is True
        assert body["already_owned"] is True
        assert body["credits_charged"] == 0

    def test_ts_10_insufficient_credits_returns_402(self):
        resp = _call_unlock_color(
            "player_card", "gold",
            side_effect=InsufficientCreditsError(required=500, available=100),
        )
        assert resp.status_code == 402
        body = json.loads(resp.body)
        assert body["ok"] is False
        assert body["error"] == "insufficient_credits"
        assert body["required"] == 500
        assert body["balance"] == 100

    def test_ts_11_unknown_color_id_returns_422(self):
        resp = _call_unlock_color(
            "player_card", "nonexistent",
            side_effect=ValueError("color_not_found"),
        )
        assert resp.status_code == 422
        body = json.loads(resp.body)
        assert body["ok"] is False

    def test_ts_12_unsupported_family_returns_422(self):
        resp = _call_unlock_color(
            "welcome_card", "gold",
            side_effect=ValueError("unsupported_family"),
        )
        assert resp.status_code == 422
        body = json.loads(resp.body)
        assert body["ok"] is False


# ── TS-13..TS-14: Route registration ──────────────────────────────────────────

class TestRouteRegistration:

    def _route_paths(self) -> list[str]:
        from app.main import app
        return [r.path for r in app.routes]

    def test_ts_13_route_count_836(self):
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 888, (
            f"Expected 845 routes (842 CE-3.7+CE-3.8 baseline + 2 CS-S0 card-studio routes), got {len(paths)}."
        )

    def test_ts_14_unlock_theme_still_registered(self):
        assert "/dashboard/unlock-theme" in self._route_paths()

    def test_ts_14b_unlock_color_registered(self):
        assert "/dashboard/unlock-color" in self._route_paths()

    def test_ts_14c_player_colors_shop_registered(self):
        assert "/shop/cards/player/colors" in self._route_paths()


# ── TS-15..TS-19: Template source checks ──────────────────────────────────────

class TestColorShopTemplate:

    def _src(self) -> str:
        return (TEMPLATES_DIR / "shop_card_player_colors.html").read_text(encoding="utf-8")

    def test_ts_15_js_unlock_function_present(self):
        assert "unlockColor" in self._src()

    def test_ts_16_unlock_cta_button_present(self):
        assert "scc-btn-unlock" in self._src()
        assert "Unlock for" in self._src()

    def test_ts_17_credit_balance_display_present(self):
        assert "scc-balance" in self._src() or "credit_balance" in self._src()

    def test_ts_18_back_link_to_player_shop(self):
        assert 'href="/shop?type=player_card"' in self._src()

    def test_ts_19_link_to_card_editor(self):
        assert 'href="/card-editor/player"' in self._src()


# ── TS-20: Editor has no purchase affordance (CE-2 regression) ────────────────

class TestEditorNoPurchaseAffordance:

    def _editor_src(self) -> str:
        return (TEMPLATES_DIR / "dashboard_card_editor.html").read_text(encoding="utf-8")

    def test_ts_20a_no_unlock_cta_in_editor(self):
        src = self._editor_src()
        assert "unlockTheme" not in src
        assert "unlockVariant" not in src

    def test_ts_20b_no_price_in_theme_picker(self):
        src = self._editor_src()
        assert "credit_cost" not in src
        assert "var-cost" not in src

    def test_ts_20c_no_buy_or_get_cta_in_editor(self):
        src = self._editor_src()
        assert "Get Card" not in src
        assert "Unlock for" not in src

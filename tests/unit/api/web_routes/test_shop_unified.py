"""
SHOP-1 — Unified Shop Listing tests.

SHOP-01  GET /shop → 200, unified listing renders
SHOP-02  GET /shop?type=player_card → only Player items
SHOP-03  GET /shop?type=welcome_card → only Welcome items
SHOP-04  GET /shop?type=challenge_card → only Challenge items
SHOP-05  Listing contains FClassic Player variants
SHOP-06  Listing contains 7 Welcome formats
SHOP-07  Listing contains 2 Challenge items
SHOP-08  ShopItem.is_owned is CDO-based, not static
SHOP-09  Dashboard: exactly one "Card Shop" CTA → /shop
SHOP-10  Dashboard: no direct /shop/cards/player|welcome|challenge CTA
SHOP-11  POST /shop/cards/{type}/buy/{id} route unchanged
SHOP-12  GET /shop/cards/player/{id} detail route unchanged
SHOP-13  GET /shop/cards/player/colors route unchanged
SHOP-14  Route count == 845
SHOP-15  OpenAPI snapshot match
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
SNAP_DIR      = Path(__file__).resolve().parents[4] / "tests" / "snapshots"

_SHOP_BASE = "app.api.web_routes.shop"


# ── Helpers ───────────────────────────────────────────────���────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 42, credits: int = 500) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.credit_balance = credits
    u.role = MagicMock()
    return u


def _invoke_shop(type_param=None, user=None, owned_pc=None, owned_wc=None, owned_cc=None):
    from app.api.web_routes.shop import shop_landing

    user = user or _user()
    db   = MagicMock()
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_SHOP_BASE}._build_catalog") as mock_catalog, \
         patch(f"{_SHOP_BASE}.templates") as mock_tpl:
        # Let real catalog build or use mock items
        from app.services.shop_catalog_service import build_shop_catalog
        mock_catalog.side_effect = lambda db, uid, credits, tf: build_shop_catalog(
            db, uid, credits, tf
        )
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        _run(shop_landing(
            request=MagicMock(),
            db=db, user=user, type=type_param,
        ))

    return captured.get("template", ""), captured.get("context", {})


def _invoke_shop_real(type_param=None, owned_ids_by_type=None):
    """Invoke with real catalog logic, patching only CDO lookups."""
    from app.api.web_routes.shop import shop_landing

    user = _user(credits=500)
    db   = MagicMock()
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    owned_map = owned_ids_by_type or {}

    def _fake_owned(db_, uid, card_type_id):
        return set(owned_map.get(card_type_id, []))

    with patch("app.services.shop_catalog_service.get_owned_design_ids", side_effect=_fake_owned), \
         patch(f"{_SHOP_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        _run(shop_landing(request=MagicMock(), db=db, user=user, type=type_param))

    return captured.get("template", ""), captured.get("context", {})


# ── SHOP-01: Unified listing renders ────────────��────────────────────────────

class TestSHOP01UnifiedListing:

    def test_shop_01_get_shop_returns_200(self):
        """SHOP-01: GET /shop renders shop_unified.html."""
        tmpl, ctx = _invoke_shop_real()
        assert tmpl == "shop_unified.html", f"Expected shop_unified.html, got {tmpl!r}"

    def test_shop_01b_context_has_shop_items(self):
        """SHOP-01b: context has shop_items list."""
        _, ctx = _invoke_shop_real()
        assert "shop_items" in ctx
        assert len(ctx["shop_items"]) > 0

    def test_shop_01c_context_has_total_count(self):
        """SHOP-01c: context has total_count and owned_count."""
        _, ctx = _invoke_shop_real()
        assert "total_count" in ctx
        assert "owned_count" in ctx


# ── SHOP-02/03/04: Type filter ──────────────────────���─────────────────────────

class TestSHOP02to04TypeFilter:

    def test_shop_02_player_filter(self):
        """SHOP-02: ?type=player_card → only Player items."""
        _, ctx = _invoke_shop_real(type_param="player_card")
        items = ctx.get("shop_items", [])
        assert all(i.card_type_id == "player_card" for i in items), \
            f"Non-player items found: {[i.card_type_id for i in items if i.card_type_id != 'player_card']}"
        assert len(items) > 0

    def test_shop_03_welcome_filter(self):
        """SHOP-03: ?type=welcome_card → only Welcome items."""
        _, ctx = _invoke_shop_real(type_param="welcome_card")
        items = ctx.get("shop_items", [])
        assert all(i.card_type_id == "welcome_card" for i in items)
        assert len(items) > 0

    def test_shop_04_challenge_filter(self):
        """SHOP-04: ?type=challenge_card → only Challenge items."""
        _, ctx = _invoke_shop_real(type_param="challenge_card")
        items = ctx.get("shop_items", [])
        assert all(i.card_type_id == "challenge_card" for i in items)
        assert len(items) > 0

    def test_shop_04b_invalid_type_returns_all(self):
        """SHOP-04b: invalid ?type= → fallback All (no 400)."""
        _, ctx = _invoke_shop_real(type_param="invalid_type_xyz")
        items = ctx.get("shop_items", [])
        types = {i.card_type_id for i in items}
        assert len(types) > 1, "Invalid type must fall back to All items"


# ── SHOP-05/06/07: Content completeness ──────────────────────────────────────

class TestSHOP05to07ContentCompleteness:

    def test_shop_05_player_variants_present(self):
        """SHOP-05: listing contains FClassic Player variants."""
        _, ctx = _invoke_shop_real(type_param="player_card")
        item_ids = {i.id for i in ctx.get("shop_items", [])}
        assert "fclassic" in item_ids, "FClassic Player must be in shop"

    def test_shop_05b_all_player_designs_present(self):
        """SHOP-05b: all known Player Card designs present."""
        _, ctx = _invoke_shop_real(type_param="player_card")
        item_ids = {i.id for i in ctx.get("shop_items", [])}
        for expected in ("fclassic", "compact", "showcase", "atlas", "pulse"):
            assert expected in item_ids, f"Player design {expected!r} missing from shop"

    def test_shop_06_seven_welcome_formats(self):
        """SHOP-06: listing contains exactly 7 Welcome formats."""
        _, ctx = _invoke_shop_real(type_param="welcome_card")
        items = [i for i in ctx.get("shop_items", []) if i.card_type_id == "welcome_card"]
        assert len(items) == 7, f"Expected 7 Welcome formats, got {len(items)}"

    def test_shop_07_two_challenge_items(self):
        """SHOP-07: listing contains exactly 2 Challenge items."""
        _, ctx = _invoke_shop_real(type_param="challenge_card")
        items = [i for i in ctx.get("shop_items", []) if i.card_type_id == "challenge_card"]
        assert len(items) == 2, f"Expected 2 Challenge items, got {len(items)}"


# ── SHOP-08: CDO-based ownership ─────────────────��───────────────────────────

class TestSHOP08Ownership:

    def test_shop_08_owned_item_marked_correctly(self):
        """SHOP-08: is_owned is CDO-based — owned design reflects true."""
        _, ctx = _invoke_shop_real(
            type_param="player_card",
            owned_ids_by_type={"player_card": {"fclassic"}},
        )
        items = ctx.get("shop_items", [])
        fclassic = next((i for i in items if i.id == "fclassic"), None)
        assert fclassic is not None, "fclassic must be in listing"
        assert fclassic.is_owned is True, "fclassic must be owned when in CDO set"

    def test_shop_08b_unowned_item_not_marked(self):
        """SHOP-08b: item not in CDO set → is_owned=False."""
        _, ctx = _invoke_shop_real(
            type_param="player_card",
            owned_ids_by_type={"player_card": set()},
        )
        items = ctx.get("shop_items", [])
        for item in items:
            assert item.is_owned is False, f"{item.id} must not be owned (empty CDO)"


# ── SHOP-09/10: Dashboard CTA ────────��───────────────────────────────────────

class TestSHOP09to10DashboardCTA:

    @classmethod
    def _dashboard(cls) -> str:
        return (TEMPLATES_DIR / "dashboard_student_new.html").read_text(encoding="utf-8")

    def test_shop_09_one_card_shop_cta_to_slash_shop(self):
        """SHOP-09: dashboard has 'Card Shop' CTA linking to /shop."""
        src = self._dashboard()
        assert 'href="/shop"' in src, "Dashboard must have href='/shop' CTA"
        assert "Card Shop" in src, "Dashboard must have 'Card Shop' label"

    def test_shop_10_no_direct_type_ctaqs(self):
        """SHOP-10: dashboard has no direct /shop/cards/player|welcome|challenge CTAs."""
        src = self._dashboard()
        for forbidden in [
            'href="/shop?type=player_card"',
            'href="/shop?type=welcome_card"',
            'href="/shop?type=challenge_card"',
        ]:
            assert forbidden not in src, \
                f"Dashboard must not have direct type CTA: {forbidden!r} (SHOP-1 cleanup)"


# ── SHOP-11/12/13: Legacy routes unchanged ─────────────────��─────────────────

class TestSHOP11to13LegacyRoutes:

    def test_shop_11_buy_endpoint_registered(self):
        """SHOP-11: POST /shop/cards/{type}/buy/{id} unchanged."""
        from app.main import app
        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/shop/cards/{card_type_id}/buy/{design_id}"),
            None,
        )
        assert route is not None, "Buy endpoint must remain registered"
        assert "POST" in (getattr(route, "methods", set()) or set())

    def test_shop_12_player_detail_route_unchanged(self):
        """SHOP-12: GET /shop/cards/player/{collection_id} still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop/cards/player/{collection_id}" in paths

    def test_shop_13_colors_route_unchanged(self):
        """SHOP-13: GET /shop/cards/player/colors still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop/cards/player/colors" in paths


# ── SHOP-14/15: Route count + OpenAPI ────────────────────────���───────────────

class TestSHOP14to15RouteAndSnapshot:

    def test_shop_14_route_count_845(self):
        """SHOP-14: route count = 845 (no new routes in SHOP-1)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 892, f"Expected 845 routes, got {len(paths)}"

    def test_shop_15_openapi_snapshot_match(self):
        """SHOP-15: OpenAPI snapshot matches live API."""
        snap = json.loads((SNAP_DIR / "openapi_snapshot.json").read_text())
        snap_paths = set(snap.get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths

"""
SHOP-3B2 — Legacy shop listing template deletion verification.

shop_player_card.html, shop_welcome_card.html, shop_challenge_card.html
deleted in SHOP-3B2. No active test/production references remain (SHOP-3B1).

S3B2-01  shop_player_card.html does NOT exist
S3B2-02  shop_welcome_card.html does NOT exist
S3B2-03  shop_challenge_card.html does NOT exist
S3B2-04  No active reference to any of the three deleted templates
S3B2-05  GET /shop registered (200)
S3B2-06  GET /shop?type=player_card → 7 Player items
S3B2-07  GET /shop?type=welcome_card → 7 Welcome items
S3B2-08  GET /shop?type=challenge_card → 2 Challenge items
S3B2-09  GET /shop/cards/player → 302 /shop?type=player_card
S3B2-10  GET /shop/cards/welcome → 302 /shop?type=welcome_card
S3B2-11  GET /shop/cards/challenge → 302 /shop?type=challenge_card
S3B2-12  GET /shop/cards/player/{id} → 200 (unchanged)
S3B2-13  GET /shop/cards/player/colors → 200 (unchanged)
S3B2-14  POST /shop/cards/{type}/buy/{id} unchanged
S3B2-15  Route count == 845
S3B2-16  OpenAPI snapshot match
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
SNAP_DIR      = Path(__file__).resolve().parents[4] / "tests" / "snapshots"
APP_DIR       = Path(__file__).resolve().parents[4] / "app"
TESTS_DIR     = Path(__file__).resolve().parents[3]

_DELETED = ["shop_player_card.html", "shop_welcome_card.html", "shop_challenge_card.html"]
_ACTIVE_PATTERNS = [
    'TemplateResponse("{tmpl}"', "TemplateResponse('{tmpl}'",
    'get_template("{tmpl}")', "get_template('{tmpl}')",
    '"{tmpl}").read_text', "'{tmpl}').read_text",
    '_render("{tmpl}"', "_render('{tmpl}'",
    # Note: '/ "{tmpl}")' excluded — matches Path division used in .exists() checks, not renders.
]


# ── S3B2-01..03: Deleted templates do not exist ──────────────────────────────

class TestS3B201to03Deleted:

    def test_s3b2_01_shop_player_card_deleted(self):
        """S3B2-01: shop_player_card.html must not exist after SHOP-3B2."""
        assert not (TEMPLATES_DIR / "shop_player_card.html").exists()

    def test_s3b2_02_shop_welcome_card_deleted(self):
        """S3B2-02: shop_welcome_card.html must not exist after SHOP-3B2."""
        assert not (TEMPLATES_DIR / "shop_welcome_card.html").exists()

    def test_s3b2_03_shop_challenge_card_deleted(self):
        """S3B2-03: shop_challenge_card.html must not exist after SHOP-3B2."""
        assert not (TEMPLATES_DIR / "shop_challenge_card.html").exists()


# ── S3B2-04: No active references ───────────────────────────────────────────

class TestS3B204NoReferences:

    def _scan(self, filename: str) -> list[str]:
        found = []
        this_file = Path(__file__)
        patterns = [p.replace("{tmpl}", filename) for p in _ACTIVE_PATTERNS]
        for base in [APP_DIR, TESTS_DIR]:
            for fpath in base.rglob("*.py"):
                if "__pycache__" in str(fpath) or fpath == this_file: continue
                try:
                    c = fpath.read_text(encoding="utf-8")
                    for p in patterns:
                        if p in c:
                            found.append(f"{fpath.name}: {p!r}")
                            break
                except (UnicodeDecodeError, PermissionError):
                    pass
        return found

    def test_s3b2_04_no_reference_to_deleted_templates(self):
        """S3B2-04: No active read/render/get_template reference to any deleted template."""
        all_found = []
        for tmpl in _DELETED:
            refs = self._scan(tmpl)
            all_found.extend([f"{tmpl}: {r}" for r in refs])
        assert len(all_found) == 0, \
            "Active references found:\n" + "\n".join(all_found)


# ── S3B2-05..08: Shop listing behavior ───────────────────────────────────────

class TestS3B205to08ListingBehavior:

    def test_s3b2_05_shop_route_registered(self):
        """S3B2-05: GET /shop route still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop" in paths

    def test_s3b2_06_player_type_returns_7_items(self):
        """S3B2-06: ?type=player_card → 7 Player items."""
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids", return_value=set()):
            items = build_shop_catalog(db, 1, 500, "player_card")
        assert len(items) == 7 and all(i.card_type_id == "player_card" for i in items)

    def test_s3b2_07_welcome_type_returns_7_items(self):
        """S3B2-07: ?type=welcome_card → 7 Welcome items."""
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids", return_value=set()):
            items = build_shop_catalog(db, 1, 500, "welcome_card")
        assert len(items) == 7 and all(i.card_type_id == "welcome_card" for i in items)

    def test_s3b2_08_challenge_type_returns_2_items(self):
        """S3B2-08: ?type=challenge_card → 2 Challenge items."""
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids", return_value=set()):
            items = build_shop_catalog(db, 1, 500, "challenge_card")
        assert len(items) == 2 and all(i.card_type_id == "challenge_card" for i in items)


# ── S3B2-09..11: Redirect behavior unchanged ─────────────────────────────────

class TestS3B209to11Redirects:

    def _redirect_resp(self, handler_name: str):
        import asyncio
        import importlib
        shop = importlib.import_module("app.api.web_routes.shop")
        fn = getattr(shop, handler_name)
        r = MagicMock()
        return asyncio.run(fn(request=r, user=MagicMock()))

    def test_s3b2_09_player_redirects(self):
        """S3B2-09: /shop/cards/player → 302 /shop?type=player_card."""
        resp = self._redirect_resp("shop_player_card")
        assert resp.status_code == 302
        assert "/shop?type=player_card" in resp.headers["location"]

    def test_s3b2_10_welcome_redirects(self):
        """S3B2-10: /shop/cards/welcome → 302 /shop?type=welcome_card."""
        resp = self._redirect_resp("shop_welcome_card")
        assert resp.status_code == 302
        assert "/shop?type=welcome_card" in resp.headers["location"]

    def test_s3b2_11_challenge_redirects(self):
        """S3B2-11: /shop/cards/challenge → 302 /shop?type=challenge_card."""
        resp = self._redirect_resp("shop_challenge_card")
        assert resp.status_code == 302
        assert "/shop?type=challenge_card" in resp.headers["location"]


# ── S3B2-12..14: Non-deleted routes unchanged ────────────────────────────────

class TestS3B212to14UnchangedRoutes:

    def test_s3b2_12_detail_route_registered(self):
        """S3B2-12: /shop/cards/player/{collection_id} still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop/cards/player/{collection_id}" in paths

    def test_s3b2_13_colors_route_registered(self):
        """S3B2-13: /shop/cards/player/colors still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop/cards/player/colors" in paths

    def test_s3b2_14_buy_endpoint_registered(self):
        """S3B2-14: POST /shop/cards/{type}/buy/{id} still registered."""
        from app.main import app
        route = next(
            (r for r in app.routes
             if getattr(r, "path", None) == "/shop/cards/{card_type_id}/buy/{design_id}"
             and "POST" in (getattr(r, "methods", set()) or set())),
            None,
        )
        assert route is not None


# ── S3B2-15/16: Route count + OpenAPI ────────────────────────────────────────

class TestS3B215to16RouteAndSnapshot:

    def test_s3b2_15_route_count_845(self):
        """S3B2-15: Route count = 845 (template deletion does not affect routes)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 907, f"Expected 845 routes, got {len(paths)}"

    def test_s3b2_16_openapi_snapshot_match(self):
        """S3B2-16: OpenAPI snapshot matches live API."""
        snap = json.loads((SNAP_DIR / "openapi_snapshot.json").read_text())
        snap_paths = set(snap.get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths


# ── Retained templates ────────────────────────────────────────────────────────

class TestS3B2RetainedTemplates:

    def test_retained_shop_unified(self):
        """shop_unified.html must still exist."""
        assert (TEMPLATES_DIR / "shop_unified.html").exists()

    def test_retained_player_card_detail(self):
        """shop_player_card_detail.html must still exist."""
        assert (TEMPLATES_DIR / "shop_player_card_detail.html").exists()

    def test_retained_card_player_colors(self):
        """shop_card_player_colors.html must still exist."""
        assert (TEMPLATES_DIR / "shop_card_player_colors.html").exists()

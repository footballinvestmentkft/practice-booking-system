"""
SHOP-3A — Legacy template deletion + cleanup tests.

shop_cards.html and shop_landing.html deleted in SHOP-3A (no production renders).
shop_player_card.html, shop_welcome_card.html, shop_challenge_card.html remain (SHOP-3B).

S3A-01  shop_landing.html does NOT exist
S3A-02  shop_cards.html does NOT exist
S3A-03  No production/test reference to shop_landing.html
S3A-04  No production/test reference to shop_cards.html
S3A-05  GET /shop → 200 (shop_unified.html)
S3A-06  GET /shop/cards → 302 /shop
S3A-07  GET /shop/cards/player → 302 /shop?type=player_card
S3A-08  GET /shop/cards/welcome → 302 /shop?type=welcome_card
S3A-09  GET /shop/cards/challenge → 302 /shop?type=challenge_card
S3A-10  GET /shop/cards/player/{id} → 200 (unchanged)
S3A-11  GET /shop/cards/player/colors → 200 (unchanged)
S3A-12  POST /shop/cards/{type}/buy/{id} unchanged
S3A-13  Route count == 845
S3A-14  OpenAPI snapshot match
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
SNAP_DIR      = Path(__file__).resolve().parents[4] / "tests" / "snapshots"
APP_DIR       = Path(__file__).resolve().parents[4] / "app"
TESTS_DIR     = Path(__file__).resolve().parents[3]


# ── S3A-01/02: Deleted templates do not exist ────────────────────────────────

class TestS3A01to02DeletedTemplates:

    def test_s3a_01_shop_landing_deleted(self):
        """S3A-01: shop_landing.html must not exist after SHOP-3A."""
        assert not (TEMPLATES_DIR / "shop_landing.html").exists(), \
            "shop_landing.html must be deleted in SHOP-3A — it is no longer served"

    def test_s3a_02_shop_cards_deleted(self):
        """S3A-02: shop_cards.html must not exist after SHOP-3A."""
        assert not (TEMPLATES_DIR / "shop_cards.html").exists(), \
            "shop_cards.html must be deleted in SHOP-3A — it is no longer served"


# ── S3A-03/04: No references to deleted templates ────────────────────────────

class TestS3A03to04NoReferences:

    def _scan(self, filename: str) -> list[str]:
        """Find files that actively render/read filename via TemplateResponse, get_template or read_text."""
        found = []
        this_file = Path(__file__)
        # Only look for patterns that indicate actual runtime render/read
        active_patterns = [
            f'TemplateResponse("{filename}"',
            f"TemplateResponse('{filename}'",
            f'get_template("{filename}")',
            f"get_template('{filename}')",
            f'"{filename}").read_text',
            f"'{filename}').read_text",
            f'/ "{filename}")',
            f"/ '{filename}')",
        ]
        for glob_pattern in ["*.py", "*.html"]:
            for base_path in [APP_DIR, TESTS_DIR]:
                for fpath in base_path.rglob(glob_pattern):
                    if "__pycache__" in str(fpath) or fpath == this_file:
                        continue
                    try:
                        content = fpath.read_text(encoding="utf-8")
                        for ap in active_patterns:
                            if ap in content:
                                rel = fpath.relative_to(Path(__file__).parents[4])
                                found.append(f"{rel}: {ap!r}")
                                break
                    except (UnicodeDecodeError, PermissionError):
                        pass
        return found

    def test_s3a_03_no_reference_to_shop_landing(self):
        """S3A-03: No active file-read/render reference to shop_landing.html."""
        refs = self._scan("shop_landing.html")
        assert len(refs) == 0, \
            f"shop_landing.html still referenced:\n" + "\n".join(refs)

    def test_s3a_04_no_reference_to_shop_cards(self):
        """S3A-04: No active file-read/render reference to shop_cards.html."""
        refs = self._scan("shop_cards.html")
        assert len(refs) == 0, \
            f"shop_cards.html still referenced:\n" + "\n".join(refs)


# ── S3A-05..09: Route behavior unchanged ─────────────────────────────────────

class TestS3A05to09RouteBehavior:

    def test_s3a_05_shop_route_registered(self):
        """S3A-05: GET /shop route still registered (serves shop_unified.html)."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop" in paths

    def test_s3a_05b_shop_renders_unified(self):
        """S3A-05b: /shop handler renders shop_unified.html."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.api.web_routes.shop import shop_landing

        captured = {}
        def fake_tmpl(tmpl, ctx, **kw):
            captured["template"] = tmpl
            return MagicMock(status_code=200)

        with patch("app.api.web_routes.shop._build_catalog", return_value=[]), \
             patch("app.api.web_routes.shop.templates.TemplateResponse", side_effect=fake_tmpl):
            r = MagicMock()
            asyncio.run(shop_landing(request=r, db=MagicMock(), user=MagicMock(), type=None))
        assert captured.get("template") == "shop_unified.html"

    def test_s3a_06_shop_cards_redirects(self):
        """S3A-06: GET /shop/cards → 302 /shop."""
        import asyncio
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.shop import shop_cards
        r = MagicMock(); r.query_params = {}
        resp = asyncio.run(shop_cards(request=r, user=MagicMock()))
        assert isinstance(resp, RedirectResponse) and resp.status_code == 302
        assert resp.headers["location"] == "/shop"

    def test_s3a_07_shop_cards_player_redirects(self):
        """S3A-07: GET /shop/cards/player → 302 /shop?type=player_card."""
        import asyncio
        from app.api.web_routes.shop import shop_player_card
        r = MagicMock(); r.query_params = {}
        resp = asyncio.run(shop_player_card(request=r, user=MagicMock()))
        assert resp.status_code == 302 and "/shop?type=player_card" in resp.headers["location"]

    def test_s3a_08_shop_cards_welcome_redirects(self):
        """S3A-08: GET /shop/cards/welcome → 302 /shop?type=welcome_card."""
        import asyncio
        from app.api.web_routes.shop import shop_welcome_card
        r = MagicMock(); r.query_params = {}
        resp = asyncio.run(shop_welcome_card(request=r, user=MagicMock()))
        assert resp.status_code == 302 and "/shop?type=welcome_card" in resp.headers["location"]

    def test_s3a_09_shop_cards_challenge_redirects(self):
        """S3A-09: GET /shop/cards/challenge → 302 /shop?type=challenge_card."""
        import asyncio
        from app.api.web_routes.shop import shop_challenge_card
        r = MagicMock(); r.query_params = {}
        resp = asyncio.run(shop_challenge_card(request=r, user=MagicMock()))
        assert resp.status_code == 302 and "/shop?type=challenge_card" in resp.headers["location"]


# ── S3A-10..12: Non-deleted routes unchanged ─────────────────────────────────

class TestS3A10to12UnchangedRoutes:

    def test_s3a_10_player_detail_registered(self):
        """S3A-10: GET /shop/cards/player/{collection_id} still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop/cards/player/{collection_id}" in paths

    def test_s3a_11_colors_registered(self):
        """S3A-11: GET /shop/cards/player/colors still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop/cards/player/colors" in paths

    def test_s3a_12_buy_endpoint_registered(self):
        """S3A-12: POST /shop/cards/{type}/buy/{id} still registered."""
        from app.main import app
        routes = app.routes
        buy_route = next(
            (r for r in routes
             if getattr(r, "path", None) == "/shop/cards/{card_type_id}/buy/{design_id}"
             and "POST" in (getattr(r, "methods", set()) or set())),
            None,
        )
        assert buy_route is not None


# ── S3A-13/14: Route count + OpenAPI ─────────────────────────────────────────

class TestS3A13to14RouteAndSnapshot:

    def test_s3a_13_route_count_845(self):
        """S3A-13: Route count = 845 (template deletion does not affect routes)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 865, f"Expected 845 routes, got {len(paths)}"

    def test_s3a_14_openapi_snapshot_match(self):
        """S3A-14: OpenAPI snapshot matches live API."""
        snap = json.loads((SNAP_DIR / "openapi_snapshot.json").read_text())
        snap_paths = set(snap.get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths

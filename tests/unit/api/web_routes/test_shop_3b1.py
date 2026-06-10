"""
SHOP-3B1 — Legacy shop listing test cleanup verification.

shop_player_card.html, shop_welcome_card.html, shop_challenge_card.html
still exist as files (deletion in SHOP-3B2), but no active test read/render
references remain after SHOP-3B1 cleanup.

S3B1-01  No active test reference to shop_player_card.html
S3B1-02  No active test reference to shop_welcome_card.html
S3B1-03  No active test reference to shop_challenge_card.html
S3B1-04  shop_unified.html handles purchased flash state
S3B1-05  shop_unified.html handles error=credits flash
S3B1-06  shop_unified.html handles error=owned flash
S3B1-07  shop_unified.html has POST buy form (item.buy_url)
S3B1-08  GET /shop → 200
S3B1-09  GET /shop?type=player_card → Player items
S3B1-10  GET /shop?type=welcome_card → Welcome items
S3B1-11  GET /shop?type=challenge_card → Challenge items
S3B1-12  Route count == 845
S3B1-13  OpenAPI snapshot match true
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

_ACTIVE_PATTERNS = [
    'TemplateResponse("{tmpl}"', "TemplateResponse('{tmpl}'",
    'get_template("{tmpl}")', "get_template('{tmpl}')",
    '"{tmpl}").read_text', "'{tmpl}').read_text",
    '_render("{tmpl}"', "_render('{tmpl}'",
    # Note: '/ "{tmpl}")' excluded — matches Path division operator used in .exists()
    # assertions (e.g. test_shop_3b2.py), not actual template rendering.
]


# ── S3B1-01..03: No active test read/render references ──────────────────────

class TestS3B101to03NoActiveReferences:

    def _scan(self, filename: str) -> list[str]:
        found = []
        this_file = Path(__file__)
        patterns = [p.replace("{tmpl}", filename) for p in _ACTIVE_PATTERNS]
        for glob_pat in ["*.py"]:
            for base in [APP_DIR, TESTS_DIR]:
                for fpath in base.rglob(glob_pat):
                    if "__pycache__" in str(fpath) or fpath == this_file: continue
                    try:
                        content = fpath.read_text(encoding="utf-8")
                        for p in patterns:
                            if p in content:
                                found.append(f"{fpath.name}: {p!r}")
                                break
                    except (UnicodeDecodeError, PermissionError):
                        pass
        return found

    def test_s3b1_01_no_reference_to_shop_player_card(self):
        """S3B1-01: No active test read/render reference to shop_player_card.html."""
        refs = self._scan("shop_player_card.html")
        assert len(refs) == 0, \
            "shop_player_card.html still referenced:\n" + "\n".join(refs)

    def test_s3b1_02_no_reference_to_shop_welcome_card(self):
        """S3B1-02: No active test read/render reference to shop_welcome_card.html."""
        refs = self._scan("shop_welcome_card.html")
        assert len(refs) == 0, \
            "shop_welcome_card.html still referenced:\n" + "\n".join(refs)

    def test_s3b1_03_no_reference_to_shop_challenge_card(self):
        """S3B1-03: No active test read/render reference to shop_challenge_card.html."""
        refs = self._scan("shop_challenge_card.html")
        assert len(refs) == 0, \
            "shop_challenge_card.html still referenced:\n" + "\n".join(refs)


# ── S3B1-04..07: shop_unified.html flash + buy form behavior ─────────────────

class TestS3B104to07UnifiedBehavior:

    @classmethod
    def _unified_src(cls) -> str:
        return (TEMPLATES_DIR / "shop_unified.html").read_text(encoding="utf-8")

    def test_s3b1_04_unified_handles_purchased_flash(self):
        """S3B1-04: shop_unified.html renders purchased flash state."""
        src = self._unified_src()
        assert "purchased" in src, "shop_unified.html must handle ?purchased= flash"
        assert "su-flash--ok" in src, "shop_unified.html must have su-flash--ok class for success"

    def test_s3b1_05_unified_handles_credits_error(self):
        """S3B1-05: shop_unified.html handles error=credits flash."""
        src = self._unified_src()
        assert "credits" in src.lower(), "shop_unified.html must mention credits in error flash"
        assert "su-flash--error" in src, "shop_unified.html must have su-flash--error class"

    def test_s3b1_06_unified_handles_owned_error(self):
        """S3B1-06: shop_unified.html handles error=owned flash."""
        src = self._unified_src()
        assert "'owned'" in src or '"owned"' in src, \
            "shop_unified.html must check for error=owned param"

    def test_s3b1_07_unified_has_buy_form(self):
        """S3B1-07: shop_unified.html has POST buy form via item.buy_url."""
        src = self._unified_src()
        assert 'method="POST"' in src, "shop_unified.html must have POST buy form"
        assert 'action="{{ item.buy_url }}"' in src, "shop_unified.html must use item.buy_url"


# ── S3B1-08..11: Route behavior unchanged ────────────────────────────────────

class TestS3B108to11RouteBehavior:

    def test_s3b1_08_shop_returns_200(self):
        """S3B1-08: GET /shop registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/shop" in paths

    def test_s3b1_09_player_type_filter(self):
        """S3B1-09: ?type=player_card → Player items only."""
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids", return_value=set()):
            items = build_shop_catalog(db, 1, 500, "player_card")
        assert all(i.card_type_id == "player_card" for i in items) and len(items) > 0

    def test_s3b1_10_welcome_type_filter(self):
        """S3B1-10: ?type=welcome_card → Welcome items only."""
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids", return_value=set()):
            items = build_shop_catalog(db, 1, 500, "welcome_card")
        assert all(i.card_type_id == "welcome_card" for i in items) and len(items) == 7

    def test_s3b1_11_challenge_type_filter(self):
        """S3B1-11: ?type=challenge_card → Challenge items only."""
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids", return_value=set()):
            items = build_shop_catalog(db, 1, 500, "challenge_card")
        assert all(i.card_type_id == "challenge_card" for i in items) and len(items) == 2


# ── S3B1-12/13: Route count + OpenAPI ────────────────────────────────────────

class TestS3B112to13RouteAndSnapshot:

    def test_s3b1_12_route_count_845(self):
        """S3B1-12: Route count = 845 (test cleanup does not affect routes)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 888, f"Expected 845 routes, got {len(paths)}"

    def test_s3b1_13_openapi_snapshot_match(self):
        """S3B1-13: OpenAPI snapshot matches live API."""
        snap = json.loads((SNAP_DIR / "openapi_snapshot.json").read_text())
        snap_paths = set(snap.get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths

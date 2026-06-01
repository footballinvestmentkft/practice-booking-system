"""Shop Feedback UX tests — SCF-12..14, SCF-18..19.

SHOP-3B1: Legacy template-specific tests (SCF-01..11, SCF-15..17, SCF-20..24) removed.
These tested shop_player_card.html, shop_welcome_card.html, shop_challenge_card.html
which are now unused listing templates (SHOP-1/2 redirects).

Route context tests (shop_catalog_service):
  SCF-12..14   owned_count via catalog service

Format grid CSS:
  SCF-18..19   mc_format_grid.html CSS classes
"""
import asyncio
import pathlib
from unittest.mock import MagicMock, patch

from jinja2 import Environment, FileSystemLoader, Undefined

_TMPL_DIR = str(pathlib.Path(__file__).resolve().parents[4] / "app" / "templates")
_TMPL = pathlib.Path(_TMPL_DIR)

_GRID = (_TMPL / "includes" / "mc_format_grid.html").read_text()

_BASE = "app.api.web_routes.shop"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(balance=500):
    u = MagicMock()
    u.id = 1
    u.credit_balance = balance
    return u


def _req(path="/", query_params=None):
    r = MagicMock()
    r.url.path = path
    params = query_params or {}
    r.query_params.get = lambda k, default=None: params.get(k, default)
    return r


def _db():
    return MagicMock()


def _design(did, credit_cost, label=None):
    d = MagicMock()
    d.id          = did
    d.label       = label or did.replace("_", " ").title()
    d.description = None
    d.credit_cost = credit_cost
    d.is_premium  = credit_cost > 0
    return d


def _call_pc(query_params=None, accessible_ids=None, designs=None, balance=500):
    from app.api.web_routes.shop import shop_player_card
    accessible_ids = accessible_ids or set()
    default_designs = [
        _design("compact",    300, "Compact"),
        _design("fclassic",       0,   "FClassic Player"),
    ]
    captured = {}

    def fake_tmpl(tmpl, ctx):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.get_all_designs", return_value=designs or default_designs), \
         patch(f"{_BASE}.is_design_accessible",
               side_effect=lambda db, uid, ct, did: did in accessible_ids), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
        _run(shop_player_card(
            request=_req("/shop?type=player_card", query_params),
            db=_db(),
            user=_user(balance),
        ))
    return captured


def _call_wc(query_params=None, accessible_ids=None, balance=500):
    from app.api.web_routes.shop import shop_welcome_card
    accessible_ids = accessible_ids or set()
    captured = {}

    def fake_tmpl(tmpl, ctx):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.is_design_accessible",
               side_effect=lambda db, uid, ct, did: did in accessible_ids), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
        _run(shop_welcome_card(
            request=_req("/shop?type=welcome_card", query_params),
            db=_db(),
            user=_user(balance),
        ))
    return captured


def _call_cc(query_params=None, accessible_ids=None, balance=500):
    from app.api.web_routes.shop import shop_challenge_card
    accessible_ids = accessible_ids or set()
    captured = {}

    def fake_tmpl(tmpl, ctx):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.is_design_accessible",
               side_effect=lambda db, uid, ct, did: did in accessible_ids), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
        _run(shop_challenge_card(
            request=_req("/shop?type=challenge_card", query_params),
            db=_db(),
            user=_user(balance),
        ))
    return captured


def _render(template_name: str, ctx: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(_TMPL_DIR),
        undefined=Undefined,
        autoescape=False,
    )
    tmpl = env.get_template(template_name)
    return tmpl.render(**ctx)


def _pc_ctx(purchased=None, error=None, owned_ids=None, balance=500):
    owned_ids = owned_ids or []
    rows = [
        {"id": "compact",    "label": "Compact",      "credit_cost": 300, "state": "owned" if "compact" in owned_ids else "get_card", "description": None},
        {"id": "fclassic",       "label": "FClassic Player",  "credit_cost": 0,   "state": "owned" if "fclassic" in owned_ids else "not_available", "description": None},
    ]
    return {
        "request": MagicMock(query_params={}),
        "user": _user(balance),
        "design_rows": rows,
        "owned_count": sum(1 for r in rows if r["state"] == "owned"),
        "total_count": len(rows),
        "flash_purchased": purchased,
        "flash_error": error,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }


def _wc_ctx(purchased=None, error=None, owned_ids=None):
    owned_ids = owned_ids or []
    rows = [
        {"design_id": "instagram_portrait", "label": "Instagram Portrait", "style_tag": "IDENTITY CARD", "dims": "1080 × 1350", "credit_cost": 75, "state": "owned" if "instagram_portrait" in owned_ids else "get_card", "preview_url": "/x", "export_url": "/x"},
        {"design_id": "instagram_story",    "label": "Instagram Story",    "style_tag": "IDENTITY CARD", "dims": "1080 × 1920", "credit_cost": 75, "state": "get_card", "preview_url": "/x", "export_url": "/x"},
    ]
    return {
        "request": MagicMock(query_params={}),
        "user": _user(),
        "format_rows": rows,
        "owned_count": sum(1 for r in rows if r["state"] == "owned"),
        "total_count": len(rows),
        "flash_purchased": purchased,
        "flash_error": error,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }


def _cc_ctx(purchased=None, error=None, owned_ids=None):
    owned_ids = owned_ids or []
    rows = [
        {"design_id": "challenge_post_16_9",  "label": "Post (16:9)",  "style_tag": "POST",  "dims": "1280 × 720",  "credit_cost": 100, "state": "owned" if "challenge_post_16_9" in owned_ids else "get_card"},
        {"design_id": "challenge_story_9_16", "label": "Story (9:16)", "style_tag": "STORY", "dims": "1080 × 1920", "credit_cost": 100, "state": "owned" if "challenge_story_9_16" in owned_ids else "get_card"},
    ]
    return {
        "request": MagicMock(query_params={}),
        "user": _user(),
        "format_rows": rows,
        "owned_count": sum(1 for r in rows if r["state"] == "owned"),
        "total_count": len(rows),
        "flash_purchased": purchased,
        "flash_error": error,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }


# ── SCF-01..04: purchased flash label resolution — template structure ─────────

class TestRouteContextCounts:

    def test_scf12_pc_owned_count_in_catalog(self):
        """SCF-12 (SHOP-2): PC owned_count correct via catalog service."""
        from unittest.mock import MagicMock, patch
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids",
                   side_effect=lambda db, uid, ct: {"compact"} if ct == "player_card" else set()):
            items = build_shop_catalog(db, 1, 500, "player_card")
        owned = sum(1 for i in items if i.is_owned)
        total = len(items)
        assert owned == 1, f"Expected 1 owned, got {owned}"
        assert total == 7

    def test_scf12b_pc_owned_zero_when_none_owned(self):
        """SCF-12b: PC owned_count=0 when nothing owned."""
        from unittest.mock import MagicMock, patch
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids", return_value=set()):
            items = build_shop_catalog(db, 1, 500, "player_card")
        assert sum(1 for i in items if i.is_owned) == 0

    def test_scf13_wc_owned_count_in_catalog(self):
        """SCF-13 (SHOP-2): WC owned_count correct via catalog service."""
        from unittest.mock import MagicMock, patch
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids",
                   side_effect=lambda db, uid, ct: {"instagram_portrait"} if ct == "welcome_card" else set()):
            items = build_shop_catalog(db, 1, 500, "welcome_card")
        owned = sum(1 for i in items if i.is_owned)
        total = len(items)
        assert owned == 1 and total == 7

    def test_scf14_cc_owned_count_in_catalog(self):
        """SCF-14 (SHOP-2): CC owned_count correct via catalog service."""
        from unittest.mock import MagicMock, patch
        from app.services.shop_catalog_service import build_shop_catalog
        db = MagicMock()
        with patch("app.services.shop_catalog_service.get_owned_design_ids",
                   side_effect=lambda db, uid, ct: {"challenge_post_16_9"} if ct == "challenge_card" else set()):
            items = build_shop_catalog(db, 1, 500, "challenge_card")
        owned = sum(1 for i in items if i.is_owned)
        total = len(items)
        assert owned == 1 and total == 2


# ── SCF-15..17: count badge in section header ─────────────────────────────────

class TestFormatGridCSSAdditions:

    def test_scf18_mfg_card_just_purchased_css_defined(self):
        """SCF-18: mc_format_grid.html defines .mfg-card-just-purchased."""
        assert "mfg-card-just-purchased" in _GRID

    def test_scf19_mfg_flash_cta_css_defined(self):
        """SCF-19: mc_format_grid.html defines .mfg-flash-cta."""
        assert "mfg-flash-cta" in _GRID


# ── SCF-20..23: purchased highlight class wiring ─────────────────────────────


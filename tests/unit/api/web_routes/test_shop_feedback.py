"""Shop Flash / Feedback UX tests — SCF-01..23.

Structural tests (template text assertions):
  SCF-01..04   purchased flash uses label resolution (selectattr + _pl or fallback)
  SCF-05..11   error messages contain correct CTA links per template
  SCF-15..19   count badge + CSS classes present in templates

Route context tests (direct handler calls):
  SCF-12..14   shop.py routes expose owned_count and total_count in context

Rendering tests (Jinja2 direct render):
  SCF-20..23   mfg-card-just-purchased applied to correct item; badge conditional
"""
import asyncio
import pathlib
from unittest.mock import MagicMock, patch

from jinja2 import Environment, FileSystemLoader, Undefined

_TMPL_DIR = str(pathlib.Path(__file__).resolve().parents[4] / "app" / "templates")
_TMPL = pathlib.Path(_TMPL_DIR)

_SPC  = (_TMPL / "shop_player_card.html").read_text()
_SWC  = (_TMPL / "shop_welcome_card.html").read_text()
_SCC  = (_TMPL / "shop_challenge_card.html").read_text()
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
            request=_req("/shop/cards/player", query_params),
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
            request=_req("/shop/cards/welcome", query_params),
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
            request=_req("/shop/cards/challenge", query_params),
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

class TestPurchasedFlashLabelStructure:

    def test_scf01_pc_template_has_label_resolution(self):
        """SCF-01: PC template resolves flash label via selectattr + label attribute."""
        assert "selectattr('id', 'equalto', flash_purchased)" in _SPC
        assert "map(attribute='label')" in _SPC
        assert "_pl or flash_purchased" in _SPC

    def test_scf02_wc_template_has_label_resolution(self):
        """SCF-02: WC template resolves flash label via selectattr on design_id."""
        assert "selectattr('design_id', 'equalto', flash_purchased)" in _SWC
        assert "map(attribute='label')" in _SWC
        assert "_pl or flash_purchased" in _SWC

    def test_scf03_cc_template_has_label_resolution(self):
        """SCF-03: CC template resolves flash label via selectattr on design_id."""
        assert "selectattr('design_id', 'equalto', flash_purchased)" in _SCC
        assert "map(attribute='label')" in _SCC
        assert "_pl or flash_purchased" in _SCC

    def test_scf04_pc_flash_renders_label_not_raw_id(self):
        """SCF-04: PC flash renders 'Compact', not raw 'compact', when purchased."""
        html = _render("shop_player_card.html", _pc_ctx(purchased="compact", owned_ids=["compact"]))
        assert "Compact" in html
        assert "Design unlocked" in html
        # The raw lower-case ID should not appear in the flash banner sentence
        flash_section = html.split("Design unlocked")[1].split("</div>")[0] if "Design unlocked" in html else ""
        assert "compact" not in flash_section.lower() or "Compact" in flash_section


# ── SCF-05..11: error feedback + CTA links — template structure ───────────────

class TestErrorFeedbackCTA:

    def test_scf05_pc_credits_error_has_credits_link(self):
        """SCF-05: PC template credits error block contains /credits."""
        assert "/credits" in _SPC
        assert "Get more credits" in _SPC

    def test_scf06_pc_owned_error_has_my_cards_player(self):
        """SCF-06: PC template owned error links to /my-cards/player."""
        assert "/my-cards/player" in _SPC
        assert "View in My Cards" in _SPC

    def test_scf07_wc_owned_error_has_my_cards_welcome(self):
        """SCF-07: WC template owned error links to /my-cards/welcome."""
        assert "/my-cards/welcome" in _SWC
        assert "View in My Cards" in _SWC

    def test_scf08_cc_owned_error_has_my_cards_challenge(self):
        """SCF-08: CC template owned error links to /my-cards/challenge."""
        assert "/my-cards/challenge" in _SCC
        assert "View in My Cards" in _SCC

    def test_scf09_pc_invalid_error_has_back_to_shop(self):
        """SCF-09: PC template invalid error links back to /shop/cards."""
        assert "Back to shop" in _SPC

    def test_scf10_wc_invalid_error_has_back_to_shop(self):
        """SCF-10: WC template invalid error links back to /shop/cards."""
        assert "Back to shop" in _SWC

    def test_scf11_cc_invalid_error_has_back_to_shop(self):
        """SCF-11: CC template invalid error links back to /shop/cards."""
        assert "Back to shop" in _SCC

    def test_scf05b_pc_credits_renders_cta_link(self):
        """SCF-05b: PC flash credits error renders /credits link in HTML output."""
        html = _render("shop_player_card.html", _pc_ctx(error="credits"))
        assert "/credits" in html
        assert "Get more credits" in html

    def test_scf06b_pc_owned_renders_my_cards_link(self):
        """SCF-06b: PC owned error renders /my-cards/player link."""
        html = _render("shop_player_card.html", _pc_ctx(error="owned"))
        assert "/my-cards/player" in html

    def test_scf08b_cc_owned_renders_my_cards_challenge_link(self):
        """SCF-08b: CC owned error renders /my-cards/challenge link."""
        html = _render("shop_challenge_card.html", _cc_ctx(error="owned"))
        assert "/my-cards/challenge" in html


# ── SCF-12..14: route context exposes owned_count / total_count ───────────────

class TestRouteContextCounts:

    def test_scf12_pc_context_has_owned_count_and_total(self):
        """SCF-12: PC route context includes owned_count and total_count."""
        ctx = _call_pc(accessible_ids={"compact"})["context"]
        assert "owned_count" in ctx
        assert "total_count" in ctx
        assert ctx["owned_count"] == 1
        assert ctx["total_count"] == 2

    def test_scf12b_pc_context_zero_when_none_owned(self):
        """SCF-12b: PC owned_count=0 when nothing owned."""
        ctx = _call_pc()["context"]
        assert ctx["owned_count"] == 0

    def test_scf13_wc_context_has_owned_count_and_total(self):
        """SCF-13: WC route context includes owned_count and total_count."""
        ctx = _call_wc(accessible_ids={"instagram_portrait"})["context"]
        assert "owned_count" in ctx
        assert "total_count" in ctx
        assert ctx["owned_count"] == 1
        assert ctx["total_count"] == 7

    def test_scf14_cc_context_has_owned_count_and_total(self):
        """SCF-14: CC route context includes owned_count and total_count."""
        ctx = _call_cc(accessible_ids={"challenge_post_16_9"})["context"]
        assert "owned_count" in ctx
        assert "total_count" in ctx
        assert ctx["owned_count"] == 1
        assert ctx["total_count"] == 2


# ── SCF-15..17: count badge in section header ─────────────────────────────────

class TestCountBadgeTemplate:

    def test_scf15_pc_template_has_count_badge(self):
        """SCF-15: PC template section header contains mfg-count-badge."""
        assert "mfg-count-badge" in _SPC

    def test_scf16_wc_template_has_count_badge(self):
        """SCF-16: WC template section header contains mfg-count-badge."""
        assert "mfg-count-badge" in _SWC

    def test_scf17_cc_template_has_count_badge(self):
        """SCF-17: CC template section header contains mfg-count-badge."""
        assert "mfg-count-badge" in _SCC

    def test_scf15b_pc_badge_renders_when_owned(self):
        """SCF-15b: PC renders 'X / Y owned' badge when owned_count > 0."""
        html = _render("shop_player_card.html", _pc_ctx(owned_ids=["compact"]))
        assert "1 / 2 owned" in html

    def test_scf15c_pc_badge_absent_when_zero_owned(self):
        """SCF-15c: PC badge does not render when owned_count == 0."""
        html = _render("shop_player_card.html", _pc_ctx(owned_ids=[]))
        assert "0 / 2 owned" not in html


# ── SCF-18..19: CSS additions in mc_format_grid.html ─────────────────────────

class TestFormatGridCSSAdditions:

    def test_scf18_mfg_card_just_purchased_css_defined(self):
        """SCF-18: mc_format_grid.html defines .mfg-card-just-purchased."""
        assert "mfg-card-just-purchased" in _GRID

    def test_scf19_mfg_flash_cta_css_defined(self):
        """SCF-19: mc_format_grid.html defines .mfg-flash-cta."""
        assert "mfg-flash-cta" in _GRID


# ── SCF-20..23: purchased highlight class wiring ─────────────────────────────

class TestPurchasedHighlight:

    def test_scf20_pc_template_wires_highlight_on_d_id(self):
        """SCF-20: PC template wires mfg-card-just-purchased on d.id == flash_purchased."""
        assert "mfg-card-just-purchased" in _SPC
        assert "d.id == flash_purchased" in _SPC

    def test_scf21_wc_template_wires_highlight_on_design_id(self):
        """SCF-21: WC template wires mfg-card-just-purchased on r.design_id == flash_purchased."""
        assert "mfg-card-just-purchased" in _SWC
        assert "r.design_id == flash_purchased" in _SWC

    def test_scf22_cc_template_wires_highlight_on_design_id(self):
        """SCF-22: CC template wires mfg-card-just-purchased on r.design_id == flash_purchased."""
        assert "mfg-card-just-purchased" in _SCC
        assert "r.design_id == flash_purchased" in _SCC

    def test_scf23_pc_highlight_renders_on_purchased_item(self):
        """SCF-23: PC renders mfg-card-just-purchased on the compact card when purchased=compact."""
        html = _render("shop_player_card.html", _pc_ctx(purchased="compact", owned_ids=["compact"]))
        assert "mfg-card-just-purchased" in html

    def test_scf24_pc_highlight_absent_without_param(self):
        """SCF-24: PC does not apply mfg-card-just-purchased to any card when no ?purchased param."""
        html = _render("shop_player_card.html", _pc_ctx())
        # The CSS definition is always present in the included <style> block.
        # Check that no <div class="mfg-card mfg-card-just-purchased"> is emitted.
        assert 'mfg-card mfg-card-just-purchased' not in html

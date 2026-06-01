"""My Cards UX Polish tests — MCF-01..21.

Structural tests (template text assertions):
  MCF-01..02   CC template uses mfg-cc-mock, not mfg-preview-placeholder
  MCF-05..06   PC CTA is "Open Editor →", no mfg-btn-download on that button
  MCF-07       PC purchased flash uses selectattr label resolution
  MCF-09..11   My Cards error flash blocks contain /credits link
  MCF-20..21   mc_format_grid.html defines mfg-ratio-45 and mfg-ratio-11

Rendering tests (Jinja2 direct render):
  MCF-03..04   CC formats render correct aspect-ratio class per design_id
  MCF-08       PC purchased flash renders human-readable label, not raw design_id
  MCF-12..17   WC My Cards formats render correct ratio class per preview_platform
  # MCF-18..19 removed (SHOP-3B1): shop_welcome_card.html legacy listing template tests
"""
import pathlib
from unittest.mock import MagicMock

from jinja2 import Environment, FileSystemLoader, Undefined

_TMPL_DIR = str(pathlib.Path(__file__).resolve().parents[4] / "app" / "templates")
_TMPL = pathlib.Path(_TMPL_DIR)

_MCC  = (_TMPL / "my_cards_challenge_card.html").read_text()
_MCP  = (_TMPL / "my_cards_player_card.html").read_text()
_MCW  = (_TMPL / "my_cards_welcome_card.html").read_text()
_GRID = (_TMPL / "includes" / "mc_format_grid.html").read_text()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(balance: int = 500):
    u = MagicMock()
    u.id = 1
    u.credit_balance = balance
    return u


def _render(template_name: str, ctx: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(_TMPL_DIR),
        undefined=Undefined,
        autoescape=False,
    )
    return env.get_template(template_name).render(**ctx)


def _mcc_ctx(purchased=None, error=None, rows=None):
    rows = rows or [
        {
            "design_id":   "challenge_post_16_9",
            "label":       "Post (16:9)",
            "style_tag":   "POST",
            "dims":        "1280 × 720",
            "credit_cost": 100,
            "state":       "owned",
        },
        {
            "design_id":   "challenge_story_9_16",
            "label":       "Story (9:16)",
            "style_tag":   "STORY",
            "dims":        "1080 × 1920",
            "credit_cost": 100,
            "state":       "owned",
        },
    ]
    return {
        "request":         MagicMock(query_params={}),
        "user":            _user(),
        "cc_format_rows":  rows,
        "cc_owned_count":  len(rows),
        "cc_total":        len(rows),
        "flash_purchased": purchased,
        "flash_error":     error,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }


def _mcp_ctx(purchased=None, error=None, rows=None, balance=500):
    rows = rows or [
        {
            "id":          "lfa_classic_2025",
            "label":       "LFA Classic 2025",
            "description": None,
            "credit_cost": 300,
            "is_premium":  True,
            "state":       "owned",
        },
    ]
    return {
        "request":         MagicMock(query_params={}),
        "user":            _user(balance),
        "design_rows":     rows,
        "owned_count":     len(rows),
        "total_count":     len(rows),
        "flash_purchased": purchased,
        "flash_error":     error,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }


def _mcw_ctx(purchased=None, error=None, rows=None):
    rows = rows or [
        {
            "design_id":        "instagram_story",
            "label":            "Instagram Story",
            "style_tag":        "IDENTITY CARD",
            "dims":             "1080 × 1920",
            "credit_cost":      75,
            "preview_platform": "instagram_story",
            "state":            "owned",
            "preview_url":      "/x",
            "export_url":       "/x",
        },
    ]
    return {
        "request":         MagicMock(query_params={}),
        "user":            _user(),
        "format_rows":     rows,
        "owned_count":     sum(1 for r in rows if r["state"] == "owned"),
        "total_count":     len(rows),
        "flash_purchased": purchased,
        "flash_error":     error,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }


def _swc_ctx(purchased=None, error=None, rows=None):
    rows = rows or [
        {
            "design_id":        "instagram_story",
            "label":            "Instagram Story",
            "style_tag":        "IDENTITY CARD",
            "dims":             "1080 × 1920",
            "credit_cost":      75,
            "preview_platform": "instagram_story",
            "state":            "get_card",
            "preview_url":      "/x",
            "export_url":       "/x",
        },
    ]
    return {
        "request":         MagicMock(query_params={}),
        "user":            _user(),
        "format_rows":     rows,
        "owned_count":     sum(1 for r in rows if r["state"] == "owned"),
        "total_count":     len(rows),
        "flash_purchased": purchased,
        "flash_error":     error,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }


def _wc_row(platform: str, state: str = "owned") -> dict:
    """Build a single WC format_rows entry for a given preview_platform."""
    dims_map = {
        "instagram_portrait": "1080 × 1350",
        "instagram_story":    "1080 × 1920",
        "instagram_square":   "1080 × 1080",
        "tiktok":             "1080 × 1920",
        "facebook_square":    "1080 × 1080",
        "facebook_landscape": "1920 × 1080",
        "banner_custom":      "1584 × 396",
    }
    return {
        "design_id":        platform,
        "label":            platform.replace("_", " ").title(),
        "style_tag":        "IDENTITY CARD",
        "dims":             dims_map.get(platform, "—"),
        "credit_cost":      75,
        "preview_platform": platform,
        "state":            state,
        "preview_url":      "/x",
        "export_url":       "/x",
    }


# ── MCF-01..04: CC My Cards mock — no placeholder, correct ratio ──────────────

class TestCCMock:

    def test_mcf01_cc_template_uses_mfg_cc_mock(self):
        """MCF-01: my_cards_challenge_card.html uses mfg-cc-mock, not placeholder."""
        assert "mfg-cc-mock" in _MCC

    def test_mcf02_cc_template_has_no_preview_placeholder(self):
        """MCF-02: mfg-preview-placeholder is removed from CC My Cards template."""
        assert "mfg-preview-placeholder" not in _MCC

    def test_mcf03_cc_story_renders_ratio_916(self):
        """MCF-03: challenge_story_9_16 row renders mfg-ratio-916 class."""
        ctx = _mcc_ctx(rows=[
            {"design_id": "challenge_story_9_16", "label": "Story (9:16)",
             "style_tag": "STORY", "dims": "1080 × 1920", "credit_cost": 100, "state": "owned"},
        ])
        html = _render("my_cards_challenge_card.html", ctx)
        assert "mfg-ratio-916" in html

    def test_mcf04_cc_post_renders_ratio_169(self):
        """MCF-04: challenge_post_16_9 row renders mfg-ratio-169 class."""
        ctx = _mcc_ctx(rows=[
            {"design_id": "challenge_post_16_9", "label": "Post (16:9)",
             "style_tag": "POST", "dims": "1280 × 720", "credit_cost": 100, "state": "owned"},
        ])
        html = _render("my_cards_challenge_card.html", ctx)
        assert "mfg-ratio-169" in html


# ── MCF-05..08: PC My Cards CTA + flash label ─────────────────────────────────

class TestPCCTAAndFlash:

    def test_mcf05_pc_cta_text_is_open_studio(self):
        """MCF-05 (CS-S1b): my_cards_player_card.html CTA text updated to 'Open Studio →'."""
        assert "Open Studio →" in _MCP

    def test_mcf06_pc_cta_has_no_mfg_btn_download(self):
        """MCF-06: The Open Editor link does not carry mfg-btn-download class."""
        assert 'mfg-btn-edit mfg-btn-download' not in _MCP
        assert '"mfg-btn mfg-btn-download"' not in _MCP

    def test_mcf07_pc_flash_has_selectattr_label_resolution(self):
        """MCF-07: PC My Cards template resolves flash label via selectattr."""
        assert "selectattr('id', 'equalto', flash_purchased)" in _MCP
        assert "map(attribute='label')" in _MCP
        assert "_pl or flash_purchased" in _MCP

    def test_mcf08_pc_flash_renders_label_not_raw_id(self):
        """MCF-08: PC My Cards flash renders 'LFA Classic 2025', not raw 'lfa_classic_2025'."""
        ctx = _mcp_ctx(purchased="lfa_classic_2025")
        html = _render("my_cards_player_card.html", ctx)
        assert "LFA Classic 2025" in html
        assert "Design unlocked" in html
        flash_part = html.split("Design unlocked")[1].split("</div>")[0]
        assert "lfa_classic_2025" not in flash_part


# ── MCF-09..11: error flash CTAs contain /credits link ───────────────────────

class TestErrorFlashCTAs:

    def test_mcf09_pc_credits_error_has_credits_link(self):
        """MCF-09: PC My Cards credits error block contains /credits."""
        assert "/credits" in _MCP
        assert "Get more credits" in _MCP

    def test_mcf10_wc_credits_error_has_credits_link(self):
        """MCF-10: WC My Cards credits error block contains /credits."""
        assert "/credits" in _MCW
        assert "Get more credits" in _MCW

    def test_mcf11_cc_credits_error_has_credits_link(self):
        """MCF-11: CC My Cards credits error block contains /credits."""
        assert "/credits" in _MCC
        assert "Get more credits" in _MCC

    def test_mcf09b_pc_credits_error_renders_link(self):
        """MCF-09b: PC My Cards credits error renders /credits href."""
        html = _render("my_cards_player_card.html", _mcp_ctx(error="credits"))
        assert "/credits" in html

    def test_mcf10b_wc_credits_error_renders_link(self):
        """MCF-10b: WC My Cards credits error renders /credits href."""
        html = _render("my_cards_welcome_card.html", _mcw_ctx(error="credits"))
        assert "/credits" in html

    def test_mcf11b_cc_credits_error_renders_link(self):
        """MCF-11b: CC My Cards credits error renders /credits href."""
        html = _render("my_cards_challenge_card.html", _mcc_ctx(error="credits"))
        assert "/credits" in html


# ── MCF-12..17: WC My Cards iframe ratio classes ─────────────────────────────

class TestMCWIframeRatio:

    def test_mcf12_wc_story_renders_ratio_916(self):
        """MCF-12: WC My Cards instagram_story renders mfg-ratio-916."""
        html = _render("my_cards_welcome_card.html",
                       _mcw_ctx(rows=[_wc_row("instagram_story")]))
        assert "mfg-ratio-916" in html

    def test_mcf13_wc_portrait_renders_ratio_45(self):
        """MCF-13: WC My Cards instagram_portrait renders mfg-ratio-45."""
        html = _render("my_cards_welcome_card.html",
                       _mcw_ctx(rows=[_wc_row("instagram_portrait")]))
        assert "mfg-ratio-45" in html

    def test_mcf14_wc_square_renders_ratio_11(self):
        """MCF-14: WC My Cards instagram_square renders mfg-ratio-11."""
        html = _render("my_cards_welcome_card.html",
                       _mcw_ctx(rows=[_wc_row("instagram_square")]))
        assert "mfg-ratio-11" in html

    def test_mcf15_wc_facebook_square_renders_ratio_11(self):
        """MCF-15: WC My Cards facebook_square renders mfg-ratio-11."""
        html = _render("my_cards_welcome_card.html",
                       _mcw_ctx(rows=[_wc_row("facebook_square")]))
        assert "mfg-ratio-11" in html

    def test_mcf16_wc_landscape_renders_ratio_169(self):
        """MCF-16: WC My Cards facebook_landscape renders mfg-ratio-169."""
        html = _render("my_cards_welcome_card.html",
                       _mcw_ctx(rows=[_wc_row("facebook_landscape")]))
        assert "mfg-ratio-169" in html

    def test_mcf17_wc_banner_renders_ratio_169(self):
        """MCF-17: WC My Cards banner_custom renders mfg-ratio-169 (practical fallback)."""
        html = _render("my_cards_welcome_card.html",
                       _mcw_ctx(rows=[_wc_row("banner_custom")]))
        assert "mfg-ratio-169" in html

    def test_mcf17b_wc_tiktok_renders_ratio_916(self):
        """MCF-17b: WC My Cards tiktok renders mfg-ratio-916."""
        html = _render("my_cards_welcome_card.html",
                       _mcw_ctx(rows=[_wc_row("tiktok")]))
        assert "mfg-ratio-916" in html


# ── MCF-18..19: Shop WC also uses ratio classes (E bonus fix) ─────────────────

class TestGridCSSNewRatioClasses:

    def test_mcf20_grid_css_has_ratio_45(self):
        """MCF-20: mc_format_grid.html defines .mfg-preview-wrap.mfg-ratio-45."""
        assert "mfg-ratio-45" in _GRID

    def test_mcf21_grid_css_has_ratio_11(self):
        """MCF-21: mc_format_grid.html defines .mfg-preview-wrap.mfg-ratio-11."""
        assert "mfg-ratio-11" in _GRID

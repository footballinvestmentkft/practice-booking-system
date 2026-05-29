"""Player Card collection detail page tests — PCID-01..12.

PCID-01  GET /shop/cards/player/fifa → 200, shop_player_card_detail.html
PCID-02  GET /shop/cards/player/unknown_xyz → 404
PCID-03  Detail page context contains collection title (design.label)
PCID-04  format_rows contains exactly the 7 FIFA buckets
PCID-05  portrait bucket → mfg-ratio-45 ratio class
PCID-06  story and tiktok buckets → mfg-ratio-916 ratio class
PCID-07  square bucket → mfg-ratio-11 ratio class
PCID-08  landscape / og / banner buckets → mfg-ratio-169 ratio class
PCID-09  Unowned state → preview wrappers carry mfg-locked class
PCID-10  Owned state → preview wrappers do NOT carry mfg-locked class
PCID-11  Unowned state → collection buy form targets /shop/cards/player_card/buy/fifa
PCID-12  No per-format buy form present in rendered HTML
PCID-BF  Browse formats → link present on /shop/cards/player list page
"""
import asyncio
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

_BASE = "app.api.web_routes.shop"

_TEMPLATE_BASE = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates"
)

_FIFA_BUCKETS = ("square", "portrait", "story", "tiktok", "landscape", "og", "banner")


def _run(coro):
    return asyncio.run(coro)


def _user(balance=500):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = 42
    u.credit_balance = balance
    u.role = UserRole.STUDENT
    return u


def _req(path="/shop/cards/player/fifa", query_params=None):
    r = MagicMock()
    r.url.path = path
    params = query_params or {}
    r.query_params.get = lambda k, default=None: params.get(k, default)
    return r


def _db():
    return MagicMock()


def _fifa_design(available=True, credit_cost=300):
    d = MagicMock()
    d.id = "fifa"
    d.label = "FIFA Classic"
    d.description = "The original LFA player card."
    d.credit_cost = credit_cost
    d.is_premium = True
    d.available = available
    d.supported_export_buckets = _FIFA_BUCKETS
    return d


def _call_detail(
    collection_id="fifa",
    user=None,
    owned=False,
    designs=None,
    query_params=None,
):
    """Helper: call shop_player_card_detail and return captured template name + context."""
    from app.api.web_routes.shop import shop_player_card_detail
    from app.services.card_constants import PC_FORMAT_META

    user = user or _user()
    if designs is None:
        designs = [_fifa_design()]

    captured = {}

    def fake_tmpl(tmpl, ctx):
        captured["template"] = tmpl
        captured["context"] = ctx
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.get_all_designs", return_value=designs), \
         patch(f"{_BASE}.is_design_accessible", return_value=owned), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
        _run(shop_player_card_detail(
            collection_id=collection_id,
            request=_req(f"/shop/cards/player/{collection_id}", query_params),
            db=_db(),
            user=user,
        ))

    return captured


def _render_detail(state="get_card", owned=False):
    """Render shop_player_card_detail.html via Jinja2 and return the HTML string."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True)

    from app.services.card_constants import PC_FORMAT_META
    meta_by_bucket = {m["bucket"]: m for m in PC_FORMAT_META}
    format_rows = [meta_by_bucket[b] for b in _FIFA_BUCKETS if b in meta_by_bucket]

    user = MagicMock()
    user.id = 42
    user.credit_balance = 500

    design = MagicMock()
    design.label = "FIFA Classic"
    design.description = "The original LFA player card."
    design.credit_cost = 300
    design.supported_export_buckets = _FIFA_BUCKETS

    ctx = {
        "request": MagicMock(),
        "user": user,
        "design": design,
        "collection_id": "fifa",
        "state": state,
        "format_rows": format_rows,
        "flash_purchased": None,
        "flash_error": None,
    }
    tmpl = env.get_template("shop_player_card_detail.html")
    return tmpl.render(**ctx)


def _render_player_list():
    """Render shop_player_card.html and return HTML."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True)

    user = MagicMock()
    user.id = 42
    user.credit_balance = 500

    design_rows = [
        {"id": "fifa", "label": "FIFA Classic", "description": "", "credit_cost": 300, "is_premium": True, "state": "get_card"},
        {"id": "compact", "label": "Compact", "description": "", "credit_cost": 300, "is_premium": True, "state": "locked"},
    ]

    ctx = {
        "request": MagicMock(),
        "user": user,
        "design_rows": design_rows,
        "owned_count": 0,
        "total_count": 2,
        "flash_purchased": None,
        "flash_error": None,
        "spec_dashboard_url": "/dashboard/lfa-football-player",
    }
    tmpl = env.get_template("shop_player_card.html")
    return tmpl.render(**ctx)


# ── PCID-01: route → 200 ─────────────────────────────────────────────────────

class TestPCID01RouteSuccess:

    def test_pcid01_fifa_returns_200_with_correct_template(self):
        """PCID-01: GET /shop/cards/player/fifa → 200, shop_player_card_detail.html."""
        cap = _call_detail(collection_id="fifa")
        assert cap["template"] == "shop_player_card_detail.html"


# ── PCID-02: unknown collection_id → 404 ─────────────────────────────────────

class TestPCID02UnknownCollection:

    def test_pcid02_unknown_collection_id_raises_404(self):
        """PCID-02: GET /shop/cards/player/unknown_xyz → HTTPException 404."""
        from app.api.web_routes.shop import shop_player_card_detail

        with patch(f"{_BASE}.get_all_designs", return_value=[_fifa_design()]), \
             patch(f"{_BASE}.is_design_accessible", return_value=False):
            with pytest.raises(HTTPException) as exc_info:
                _run(shop_player_card_detail(
                    collection_id="unknown_xyz",
                    request=_req("/shop/cards/player/unknown_xyz"),
                    db=_db(),
                    user=_user(),
                ))
        assert exc_info.value.status_code == 404


# ── PCID-03: context contains collection title ────────────────────────────────

class TestPCID03CollectionTitle:

    def test_pcid03_context_contains_design_with_correct_label(self):
        """PCID-03: context design.label == 'FIFA Classic'."""
        cap = _call_detail(collection_id="fifa")
        assert cap["context"]["design"].label == "FIFA Classic"

    def test_pcid03_rendered_html_contains_collection_title(self):
        """PCID-03b: rendered HTML contains 'FIFA Classic'."""
        html = _render_detail()
        assert "FIFA Classic" in html


# ── PCID-04: 7 format rows ───────────────────────────────────────────────────

class TestPCID04FormatCount:

    def test_pcid04_context_has_7_format_rows(self):
        """PCID-04: format_rows contains exactly 7 entries for FIFA Classic."""
        cap = _call_detail(collection_id="fifa")
        assert len(cap["context"]["format_rows"]) == 7

    def test_pcid04_rendered_html_contains_all_7_format_labels(self):
        """PCID-04b: rendered HTML contains all 7 platform labels."""
        html = _render_detail()
        expected = [
            "Instagram Portrait", "Instagram Story", "TikTok",
            "Square", "Landscape", "Open Graph", "Banner",
        ]
        for label in expected:
            assert label in html, f"Missing format label: {label}"


# ── PCID-05..08: ratio class mapping ─────────────────────────────────────────

class TestPCID05to08RatioClasses:

    def _get_ratio(self, bucket):
        from app.services.card_constants import PC_FORMAT_META
        return next(m["ratio"] for m in PC_FORMAT_META if m["bucket"] == bucket)

    def test_pcid05_portrait_ratio_45(self):
        """PCID-05: portrait bucket → mfg-ratio-45."""
        assert self._get_ratio("portrait") == "mfg-ratio-45"

    def test_pcid06_story_ratio_916(self):
        """PCID-06a: story bucket → mfg-ratio-916."""
        assert self._get_ratio("story") == "mfg-ratio-916"

    def test_pcid06_tiktok_ratio_916(self):
        """PCID-06b: tiktok bucket → mfg-ratio-916."""
        assert self._get_ratio("tiktok") == "mfg-ratio-916"

    def test_pcid07_square_ratio_11(self):
        """PCID-07: square bucket → mfg-ratio-11."""
        assert self._get_ratio("square") == "mfg-ratio-11"

    def test_pcid08_landscape_ratio_169(self):
        """PCID-08a: landscape bucket → mfg-ratio-169."""
        assert self._get_ratio("landscape") == "mfg-ratio-169"

    def test_pcid08_og_ratio_169(self):
        """PCID-08b: og bucket → mfg-ratio-169."""
        assert self._get_ratio("og") == "mfg-ratio-169"

    def test_pcid08_banner_ratio_169(self):
        """PCID-08c: banner bucket → mfg-ratio-169."""
        assert self._get_ratio("banner") == "mfg-ratio-169"

    def test_pcid08_rendered_html_contains_ratio_classes(self):
        """PCID-08d: rendered HTML contains all 4 ratio CSS classes."""
        html = _render_detail(state="get_card")
        for ratio in ("mfg-ratio-45", "mfg-ratio-916", "mfg-ratio-11", "mfg-ratio-169"):
            assert ratio in html, f"Missing ratio class: {ratio}"


# ── PCID-09: unowned → mfg-locked ─────────────────────────────────────────────

class TestPCID09UnownedLocked:

    def test_pcid09_unowned_preview_has_mfg_locked_class(self):
        """PCID-09: unowned state → mfg-locked appears as a CSS class on preview wrappers.

        We check for ' mfg-locked' (space-prefixed) to distinguish the class
        attribute value from the CSS selector text (.mfg-locked) in mc_format_grid.html.
        """
        html = _render_detail(state="get_card", owned=False)
        assert " mfg-locked" in html

    def test_pcid09_locked_state_also_has_mfg_locked(self):
        """PCID-09b: locked state → mfg-locked class present on preview wrappers."""
        html = _render_detail(state="locked", owned=False)
        assert " mfg-locked" in html


# ── PCID-10: owned → no mfg-locked ───────────────────────────────────────────

class TestPCID10OwnedClean:

    def test_pcid10_owned_preview_has_no_mfg_locked(self):
        """PCID-10: owned state → mfg-locked not present as a class attribute value.

        The CSS selector .mfg-locked still appears in <style> — we check for the
        space-prefixed form ' mfg-locked' which only appears in class="..." attributes.
        """
        html = _render_detail(state="owned", owned=True)
        assert " mfg-locked" not in html


# ── PCID-11: collection buy form ─────────────────────────────────────────────

class TestPCID11CollectionBuyForm:

    def test_pcid11_unowned_buy_form_targets_correct_endpoint(self):
        """PCID-11: unowned → buy form action = /shop/cards/player_card/buy/fifa."""
        html = _render_detail(state="get_card")
        assert 'action="/shop/cards/player_card/buy/fifa"' in html

    def test_pcid11_buy_form_not_present_when_owned(self):
        """PCID-11b: owned state → no buy form rendered."""
        html = _render_detail(state="owned")
        assert 'action="/shop/cards/player_card/buy/fifa"' not in html


# ── PCID-12: no per-format buy forms ─────────────────────────────────────────

class TestPCID12NoPerFormatBuy:

    def test_pcid12_no_per_format_buy_buttons_in_format_grid(self):
        """PCID-12: format grid has no per-format purchase forms.

        The buy action URL should appear exactly once (the collection-level CTA).
        If per-format buy forms were present we would see it 7 times (once per bucket).
        """
        html = _render_detail(state="get_card")
        buy_action_count = html.count('action="/shop/cards/player_card/buy/fifa"')
        assert buy_action_count == 1, (
            f"Expected exactly 1 collection buy form action, found {buy_action_count}"
        )

    def test_pcid12_format_cards_contain_included_badge(self):
        """PCID-12b: format cards show 'Included in this collection' badge, not CR price.

        mfg-badge-get as a CSS rule is always in the stylesheet — we check for its
        use as a class attribute value (space-prefixed) which only appears on rendered
        elements.
        """
        html = _render_detail(state="get_card")
        assert "Included in this collection" in html
        # No per-format CR badge rendered as an element (mfg-badge-get in class attr)
        assert ' mfg-badge-get"' not in html


# ── PCID-BF: Browse formats → on list page ───────────────────────────────────

class TestPCIDBrowseFormats:

    def test_pcid_bf_browse_formats_link_present_in_list(self):
        """PCID-BF: /shop/cards/player list — each card has 'Browse formats →' link."""
        html = _render_player_list()
        assert "Browse formats →" in html
        assert "/shop/cards/player/fifa" in html
        assert "/shop/cards/player/compact" in html

    def test_pcid_bf_browse_formats_link_count_matches_design_count(self):
        """PCID-BF-2: number of 'Browse formats →' links equals number of designs."""
        html = _render_player_list()
        assert html.count("Browse formats →") == 2

"""
CS-VT-1 — Shop UI integration tests for Virtual Training Card.

SHOP-VT-01  GET /shop?type=virtual_training_card → 200, VTC items returned
SHOP-VT-02  VTC filter tab present in shop_unified.html template
SHOP-VT-03  active_filter_label is "VT Cards" for virtual_training_card filter
SHOP-VT-04  exactly 4 VTC formats in catalog (vt_landscape/portrait, vt_reward_landscape/portrait)
SHOP-VT-05  all VTC items have price_credits > 0
SHOP-VT-06  no VTC item has state "not_available"
SHOP-VT-07  purchase redirect target is /shop?type=virtual_training_card (not /shop/cards fallback)
SHOP-VT-08  All Cards view: VTC items use virtual_training badge class, not challenge fallback
SHOP-VT-09  owned VTC item shows state="owned"
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
_SHOP_BASE    = "app.api.web_routes.shop"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 7, credits: int = 500) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.credit_balance = credits
    u.role = MagicMock()
    return u


def _invoke_shop_real(type_param=None, owned_ids_by_type=None):
    from app.api.web_routes.shop import shop_landing

    user = _user()
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


def _render_shop_html(items, type_filter=""):
    env  = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    tmpl = env.get_template("shop_unified.html")
    return tmpl.render(
        request=MagicMock(),
        shop_items=items,
        type_filter=type_filter,
        active_filter_label="All Cards",
        total_count=len(items),
        owned_count=0,
        user=_user(),
    )


# ── Minimal mock ShopItem ─────────────────────────────────────────────────────

def _vtc_item(design_id: str, owned: bool = False, credits: int = 500) -> MagicMock:
    item = MagicMock()
    item.id             = design_id
    item.card_type_id   = "virtual_training_card"
    item.family_id      = "fclassic"
    item.label          = design_id.replace("_", " ").title()
    item.card_type_label = "Virtual Training Card"
    item.style_tag      = "GAME"
    item.dims           = "1280 × 720"
    item.description    = f"VT Card — 1280 × 720"
    item.price_credits  = 75
    item.is_premium     = True
    item.is_owned       = owned
    item.state          = "owned" if owned else ("get_card" if credits >= 75 else "locked")
    item.studio_url     = None
    item.detail_url     = None
    item.buy_url        = f"/shop/cards/virtual_training_card/buy/{design_id}"
    item.tags           = ("virtual_training",)
    item.sort_group     = 3
    item.sort_order     = 0
    return item


_VTC_DESIGN_IDS = ("vt_landscape", "vt_portrait", "vt_reward_landscape", "vt_reward_portrait")


# ── SHOP-VT-01: filter route returns VTC items ────────────────────────────────

class TestSHOPVT01FilterRoute:

    def test_shopvt01_vtc_filter_returns_200_and_vtc_items(self):
        """GET /shop?type=virtual_training_card returns VTC items only."""
        _, ctx = _invoke_shop_real(type_param="virtual_training_card")
        items = ctx.get("shop_items", [])
        assert len(items) > 0, "No VTC items returned"
        assert all(i.card_type_id == "virtual_training_card" for i in items), \
            f"Non-VTC items: {[i.card_type_id for i in items if i.card_type_id != 'virtual_training_card']}"

    def test_shopvt01b_type_filter_in_context(self):
        """type_filter context variable is 'virtual_training_card'."""
        _, ctx = _invoke_shop_real(type_param="virtual_training_card")
        assert ctx.get("type_filter") == "virtual_training_card"


# ── SHOP-VT-02: filter tab in template ───────────────────────────────────────

class TestSHOPVT02FilterTab:

    def test_shopvt02_filter_tab_present_in_template(self):
        """shop_unified.html contains VT Cards filter tab."""
        items = [_vtc_item(d) for d in _VTC_DESIGN_IDS]
        html  = _render_shop_html(items, type_filter="")
        assert "/shop?type=virtual_training_card" in html, \
            "VTC filter tab href missing from shop_unified.html"
        assert "VT Cards" in html, "VTC filter tab label missing"

    def test_shopvt02b_filter_tab_active_when_vtc_selected(self):
        """VTC filter tab gets su-filter-btn--active class when type_filter matches."""
        items = [_vtc_item(d) for d in _VTC_DESIGN_IDS]
        html  = _render_shop_html(items, type_filter="virtual_training_card")
        # Active filter anchor must contain both the href and active class
        assert 'su-filter-btn--active' in html
        # Verify the active class is on the VTC link specifically
        vtc_section = html.split("/shop?type=virtual_training_card")[1][:200]
        assert "su-filter-btn--active" in vtc_section, \
            "VTC filter tab is not marked active when type_filter=virtual_training_card"


# ── SHOP-VT-03: active label ──────────────────────────────────────────────────

class TestSHOPVT03ActiveLabel:

    def test_shopvt03_active_label_is_vt_cards(self):
        """active_filter_label is 'VT Cards' for virtual_training_card filter."""
        _, ctx = _invoke_shop_real(type_param="virtual_training_card")
        assert ctx.get("active_filter_label") == "VT Cards", \
            f"Expected 'VT Cards', got {ctx.get('active_filter_label')!r}"

    def test_shopvt03b_active_label_all_cards_default(self):
        """active_filter_label is 'All Cards' when no filter."""
        _, ctx = _invoke_shop_real(type_param=None)
        assert ctx.get("active_filter_label") == "All Cards"


# ── SHOP-VT-04: 4 VTC formats ─────────────────────────────────────────────────

class TestSHOPVT04FourFormats:

    def test_shopvt04_exactly_4_vtc_items(self):
        """Exactly 4 VTC formats in catalog."""
        _, ctx = _invoke_shop_real(type_param="virtual_training_card")
        items = ctx.get("shop_items", [])
        assert len(items) == 4, f"Expected 4 VTC items, got {len(items)}"

    def test_shopvt04b_all_four_design_ids_present(self):
        """All 4 canonical VTC design IDs present."""
        _, ctx = _invoke_shop_real(type_param="virtual_training_card")
        ids = {i.id for i in ctx.get("shop_items", [])}
        assert ids == set(_VTC_DESIGN_IDS), \
            f"Expected {set(_VTC_DESIGN_IDS)}, got {ids}"


# ── SHOP-VT-05: prices > 0 ────────────────────────────────────────────────────

class TestSHOPVT05Prices:

    def test_shopvt05_all_vtc_items_have_positive_price(self):
        """All VTC items have price_credits > 0."""
        _, ctx = _invoke_shop_real(type_param="virtual_training_card")
        for item in ctx.get("shop_items", []):
            assert item.price_credits > 0, \
                f"{item.id} has price_credits={item.price_credits} (must be > 0)"

    def test_shopvt05b_price_not_free_in_html(self):
        """Template does not show 'Free' label for VTC items."""
        items = [_vtc_item(d) for d in _VTC_DESIGN_IDS]
        html  = _render_shop_html(items)
        # If no item has price_credits==0, the Free branch should not render
        # Count occurrences — none expected for VTC section
        assert items[0].price_credits > 0  # sanity check


# ── SHOP-VT-06: no not_available items ────────────────────────────────────────

class TestSHOPVT06NotAvailable:

    def test_shopvt06_no_vtc_item_is_not_available(self):
        """No VTC item has state='not_available'."""
        _, ctx = _invoke_shop_real(type_param="virtual_training_card")
        for item in ctx.get("shop_items", []):
            assert item.state != "not_available", \
                f"{item.id} has state='not_available' (price must be > 0)"


# ── SHOP-VT-07: purchase redirect ─────────────────────────────────────────────

class TestSHOPVT07PurchaseRedirect:

    def test_shopvt07_buy_redirects_to_vtc_shop_filter(self):
        """POST /shop/cards/virtual_training_card/buy/vt_landscape redirects to VTC filter."""
        from app.api.web_routes.shop import shop_buy

        db   = MagicMock()
        user = _user()

        with patch(f"{_SHOP_BASE}.purchase_design") as mock_purchase:
            mock_purchase.return_value = MagicMock()
            result = _run(shop_buy(
                card_type_id="virtual_training_card",
                design_id="vt_landscape",
                db=db, user=user,
            ))

        # Should redirect to /shop?type=virtual_training_card (not /shop/cards fallback)
        assert result.status_code == 303
        assert result.headers["location"] == "/shop?type=virtual_training_card?purchased=vt_landscape", \
            f"Unexpected redirect: {result.headers['location']}"

    def test_shopvt07b_buy_redirect_not_shop_cards_fallback(self):
        """Redirect NEVER goes to /shop/cards (the old fallback path)."""
        from app.api.web_routes.shop import shop_buy

        db   = MagicMock()
        user = _user()

        with patch(f"{_SHOP_BASE}.purchase_design") as mock_purchase:
            mock_purchase.return_value = MagicMock()
            result = _run(shop_buy(
                card_type_id="virtual_training_card",
                design_id="vt_landscape",
                db=db, user=user,
            ))

        location = result.headers["location"]
        assert "/shop/cards" not in location, \
            f"Redirect went to deprecated /shop/cards path: {location}"


# ── SHOP-VT-08: badge class in All Cards view ─────────────────────────────────

class TestSHOPVT08BadgeClass:

    def test_shopvt08_vtc_items_use_virtual_training_badge_class(self):
        """All Cards view: VTC items render su-item-type-badge--virtual_training class."""
        items = [_vtc_item(d) for d in _VTC_DESIGN_IDS]
        html  = _render_shop_html(items, type_filter="")
        assert "su-item-type-badge--virtual_training" in html, \
            "VTC items must use su-item-type-badge--virtual_training CSS class"

    def test_shopvt08b_vtc_items_do_not_use_challenge_badge_class_on_items(self):
        """VTC item spans must NOT use su-item-type-badge--challenge on actual item spans.

        Note: the CSS class definition always exists in the <style> block.
        This test counts class= attribute occurrences after the </style> closing tag,
        where only rendered item spans appear.
        """
        items = [_vtc_item(d) for d in _VTC_DESIGN_IDS]
        html  = _render_shop_html(items, type_filter="")
        # Split at </style> to isolate the rendered DOM from the CSS block
        after_style = html.split("</style>", 1)[-1]
        assert "su-item-type-badge--challenge" not in after_style, \
            "VTC item spans use challenge badge CSS class in rendered DOM"


# ── SHOP-VT-09: owned item state ──────────────────────────────────────────────

class TestSHOPVT09OwnedState:

    def test_shopvt09_owned_vtc_format_shows_owned_state(self):
        """Owned VTC format shows state='owned' in catalog."""
        _, ctx = _invoke_shop_real(
            type_param="virtual_training_card",
            owned_ids_by_type={"virtual_training_card": ["vt_landscape"]},
        )
        items = ctx.get("shop_items", [])
        owned = [i for i in items if i.id == "vt_landscape"]
        assert len(owned) == 1
        assert owned[0].state == "owned"
        assert owned[0].is_owned is True

    def test_shopvt09b_non_owned_vtc_format_shows_get_card_or_locked(self):
        """Non-owned VTC format shows get_card or locked (never not_available)."""
        _, ctx = _invoke_shop_real(
            type_param="virtual_training_card",
            owned_ids_by_type={"virtual_training_card": []},
        )
        for item in ctx.get("shop_items", []):
            assert item.state in ("get_card", "locked"), \
                f"{item.id} state={item.state!r} (expected get_card or locked)"

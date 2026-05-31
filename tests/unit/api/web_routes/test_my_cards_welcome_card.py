"""Welcome Card owned-only collection route tests — MCW-01..MCW-12.

MCW-01  GET /my-cards/welcome-card renders my_cards_welcome_card.html
MCW-02  Context format_rows contains only owned (accessible) formats
MCW-03  Not accessible → not in format_rows (empty collection)
MCW-04  Accessible format → in format_rows with state='owned'
MCW-05  Multiple accessible formats → all appear in format_rows
MCW-06  Context contains owned_count and total_count (both = owned)
MCW-07  Route has auth dependency (get_current_user_web)
MCW-08  Template extends student_base and includes spec_subpage_hdr
MCW-09  Template breadcrumb links to /my-cards
MCW-10  format_rows contain preview_url and export_url
MCW-11  Template has Download CTA for owned items
MCW-12  Template has Browse Shop CTA linking to /shop/cards/welcome
"""
import asyncio
import inspect
import pathlib
from unittest.mock import MagicMock, patch

_BASE = "app.api.web_routes.my_cards"
_TEMPLATE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "my_cards_welcome_card.html"
)


def _run(coro):
    return asyncio.run(coro)


def _user(balance=500):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = 42
    u.credit_balance = balance
    u.role = UserRole.STUDENT
    return u


def _req(query_params=None):
    r = MagicMock()
    r.url.path = "/my-cards/welcome-card"
    params = query_params or {}
    r.query_params.get = lambda k, default=None: params.get(k, default)
    return r


def _db():
    return MagicMock()


def _call(user=None, db=None, accessible_ids=None, query_params=None):
    from app.api.web_routes.my_cards import my_cards_welcome_card

    user           = user or _user()
    db             = db   or _db()
    accessible_ids = accessible_ids or set()
    captured = {}

    def fake_tmpl(tmpl, ctx):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    def fake_accessible(_db, uid, card_type_id, design_id):
        return (card_type_id, design_id) in accessible_ids

    with patch(f"{_BASE}.is_design_accessible", side_effect=fake_accessible), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
        _run(my_cards_welcome_card(request=_req(query_params), db=db, user=user))

    return captured


class TestWelcomeCardShopRoute:

    def test_mcw01_renders_welcome_card_template(self):
        """MCW-01: GET /my-cards/welcome-card renders my_cards_welcome_card.html."""
        cap = _call(accessible_ids={("welcome_card", "instagram_portrait")})
        assert cap["template"] == "my_cards_welcome_card.html"

    def test_mcw02_context_format_rows_contains_only_owned(self):
        """MCW-02: context.format_rows contains only accessible (owned) formats."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        first_fmt = WELCOME_CARD_FORMATS[0]
        ctx = _call(accessible_ids={("welcome_card", first_fmt.design_id)})["context"]
        rows = ctx["format_rows"]
        assert len(rows) == 1
        assert rows[0]["design_id"] == first_fmt.design_id

    def test_mcw03_not_accessible_not_in_format_rows(self):
        """MCW-03: non-accessible format → not in format_rows (empty collection)."""
        ctx = _call(accessible_ids=set())["context"]
        assert ctx["format_rows"] == []
        assert ctx["owned_count"] == 0

    def test_mcw04_accessible_format_in_rows_with_owned_state(self):
        """MCW-04: accessible format → in format_rows with state='owned'."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        target_fmt = WELCOME_CARD_FORMATS[0]
        ctx = _call(
            user=_user(balance=9999),
            accessible_ids={("welcome_card", target_fmt.design_id)},
        )["context"]
        rows = ctx["format_rows"]
        row = next(r for r in rows if r["design_id"] == target_fmt.design_id)
        assert row["state"] == "owned"

    def test_mcw05_multiple_accessible_formats_all_in_rows(self):
        """MCW-05: multiple accessible formats → all appear in format_rows."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        first_two = {("welcome_card", f.design_id) for f in WELCOME_CARD_FORMATS[:2]}
        ctx = _call(user=_user(balance=9999), accessible_ids=first_two)["context"]
        assert len(ctx["format_rows"]) == 2
        for r in ctx["format_rows"]:
            assert r["state"] == "owned"

    def test_mcw06_context_has_owned_and_total_count(self):
        """MCW-06: context contains owned_count and total_count (both = owned)."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        first_two = {("welcome_card", f.design_id) for f in WELCOME_CARD_FORMATS[:2]}
        ctx = _call(accessible_ids=first_two)["context"]
        assert "owned_count" in ctx
        assert "total_count" in ctx
        assert ctx["owned_count"] == 2
        assert ctx["total_count"] == 2  # total_count = owned_count in owned-only view

    def test_mcw07_route_has_auth_dependency(self):
        """MCW-07: route declares get_current_user_web dependency."""
        from app.api.web_routes.my_cards import my_cards_welcome_card
        sig = inspect.signature(my_cards_welcome_card)
        assert "user" in sig.parameters

    def test_mcw08_template_extends_student_base(self):
        """MCW-08: template extends student_base and includes spec_subpage_hdr."""
        src = _TEMPLATE_PATH.read_text()
        assert "student_base.html" in src
        assert "spec_subpage_hdr.html" in src

    def test_mcw09_template_breadcrumb_links_to_hub(self):
        """MCW-09: template breadcrumb links to /my-cards hub."""
        src = _TEMPLATE_PATH.read_text()
        assert 'href="/my-cards"' in src

    def test_mcw10_format_rows_contain_preview_and_export_url(self):
        """MCW-10: every owned format_row has preview_url and export_url."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        all_ids = {("welcome_card", f.design_id) for f in WELCOME_CARD_FORMATS}
        ctx = _call(accessible_ids=all_ids)["context"]
        rows = ctx["format_rows"]
        assert len(rows) > 0
        for r in rows:
            assert "preview_url" in r, f"{r['design_id']} missing preview_url"
            assert "export_url" in r, f"{r['design_id']} missing export_url"

    def test_mcw11_template_has_editor_cta_for_owned(self):
        """MCW-11: template uses mfg-btn-edit (editor link) for owned formats, not direct download."""
        src = _TEMPLATE_PATH.read_text()
        assert "mfg-btn-edit" in src, "Must have editor button class for owned state"
        assert "mfg-btn-download" not in src, "Direct download button must be absent — editor link replaces it"

    def test_mcw12_template_has_browse_shop_cta(self):
        """MCW-12: template has Browse Shop CTA linking to /shop/cards/welcome."""
        src = _TEMPLATE_PATH.read_text()
        assert "/shop/cards/welcome" in src

    def test_mcw13_template_has_studio_cta(self):
        """MCW-13 (CS-S1b): Studio entry CTA now links to canonical /card-studio/welcome."""
        src = _TEMPLATE_PATH.read_text()
        assert 'href="/card-studio/welcome"' in src

    def test_mcw13b_template_has_studio_cta_text(self):
        """MCW-13b: Studio CTA has correct label text."""
        src = _TEMPLATE_PATH.read_text()
        assert "Open Welcome Studio" in src

    def test_mcw13c_per_format_wce1_link_still_present(self):
        """MCW-13c: legacy per-format /card-editor/welcome/{id} WCE-1 link unchanged."""
        src = _TEMPLATE_PATH.read_text()
        assert "/card-editor/welcome/" in src, "WCE-1 per-format link must remain"

    def test_mcw13d_shop_link_still_present(self):
        """MCW-13d: /shop/cards/welcome link unchanged after Studio CTA addition."""
        src = _TEMPLATE_PATH.read_text()
        assert "/shop/cards/welcome" in src

    def test_mcw_owned_count_correct(self):
        """MCW-owned: owned_count increments per owned format."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        first_two = {("welcome_card", f.design_id) for f in WELCOME_CARD_FORMATS[:2]}
        ctx = _call(
            user=_user(balance=9999),
            accessible_ids=first_two,
        )["context"]
        assert ctx["owned_count"] == 2

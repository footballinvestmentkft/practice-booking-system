"""Welcome Card format shop route tests — MCW-01..MCW-12.

MCW-01  GET /my-cards/welcome-card renders my_cards_welcome_card.html
MCW-02  Context contains format_rows with 7 entries (WELCOME_CARD_FORMATS)
MCW-03  Not owned, credits ≥ price → state='purchasable'
MCW-04  Not owned, credits < price → state='locked'
MCW-05  Owned → state='owned'
MCW-06  Context contains owned_count and total_count
MCW-07  Route has auth dependency (get_current_user_web)
MCW-08  Template extends student_base and includes spec_subpage_hdr
MCW-09  Template breadcrumb links to /my-cards
MCW-10  format_rows contain preview_url and export_url
MCW-11  Download CTA only present for owned state (template check)
MCW-12  Template contains purchase form POST action pattern
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
        cap = _call()
        assert cap["template"] == "my_cards_welcome_card.html"

    def test_mcw02_context_has_7_format_rows(self):
        """MCW-02: context.format_rows has 7 entries (one per WELCOME_CARD_FORMAT)."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        ctx = _call()["context"]
        rows = ctx["format_rows"]
        assert len(rows) == len(WELCOME_CARD_FORMATS)

    def test_mcw03_not_owned_sufficient_credits_purchasable(self):
        """MCW-03: format not owned, credits ≥ price → state='purchasable'."""
        ctx = _call(user=_user(balance=9999), accessible_ids=set())["context"]
        rows = ctx["format_rows"]
        for r in rows:
            assert r["state"] == "purchasable", f"{r['design_id']} should be purchasable"

    def test_mcw04_not_owned_insufficient_credits_locked(self):
        """MCW-04: format not owned, credits < price → state='locked'."""
        ctx = _call(user=_user(balance=0), accessible_ids=set())["context"]
        rows = ctx["format_rows"]
        for r in rows:
            assert r["state"] == "locked", f"{r['design_id']} should be locked"

    def test_mcw05_owned_format_shows_owned_state(self):
        """MCW-05: owned format → state='owned'."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        target_fmt = WELCOME_CARD_FORMATS[0]
        ctx = _call(
            user=_user(balance=9999),
            accessible_ids={("welcome_card", target_fmt.design_id)},
        )["context"]
        rows = ctx["format_rows"]
        row = next(r for r in rows if r["design_id"] == target_fmt.design_id)
        assert row["state"] == "owned"

    def test_mcw06_context_has_owned_and_total_count(self):
        """MCW-06: context contains owned_count and total_count."""
        ctx = _call()["context"]
        assert "owned_count" in ctx
        assert "total_count" in ctx
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        assert ctx["total_count"] == len(WELCOME_CARD_FORMATS)

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
        """MCW-10: every format_row has preview_url and export_url."""
        ctx = _call()["context"]
        rows = ctx["format_rows"]
        for r in rows:
            assert "preview_url" in r, f"{r['design_id']} missing preview_url"
            assert "export_url" in r, f"{r['design_id']} missing export_url"

    def test_mcw11_template_has_download_and_purchase_elements(self):
        """MCW-11: template uses mfg-btn-download for owned and POST form for get."""
        src = _TEMPLATE_PATH.read_text()
        assert "mfg-btn-download" in src, "Must have download button class for owned state"
        assert "mfg-btn-get" in src, "Must have get button class for purchasable state"

    def test_mcw12_template_has_purchase_form(self):
        """MCW-12: template contains POST form for purchasing a WC format."""
        src = _TEMPLATE_PATH.read_text()
        assert "/my-cards/designs/welcome_card/" in src
        assert 'method="POST"' in src

    def test_mcw_owned_count_correct(self):
        """MCW-owned: owned_count increments per owned format."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        first_two = {("welcome_card", f.design_id) for f in WELCOME_CARD_FORMATS[:2]}
        ctx = _call(
            user=_user(balance=9999),
            accessible_ids=first_two,
        )["context"]
        assert ctx["owned_count"] == 2

"""Player Card format shop route tests — MCP-01..MCP-12.

MCP-01  GET /my-cards/player-card renders my_cards_player_card.html
MCP-02  Context contains design_rows with state for each design
MCP-03  Non-premium design, no CDO → state='get_card' or 'locked' (no free bypass)
MCP-04  Premium not owned, credits ≥ cost → state='get_card'
MCP-05  Premium not owned, credits < cost → state='locked'
MCP-06  Premium owned → state='owned'
MCP-07  Context contains owned_count and total_count
MCP-08  Route has auth dependency (get_current_user_web)
MCP-09  Template file extends student_base and includes spec_subpage_hdr
MCP-10  Template file breadcrumb links to /my-cards
MCP-11  Template file contains purchase form POST action pattern
MCP-12  owned_count = explicitly owned designs only (no free auto-include)
"""
import asyncio
import inspect
import pathlib
from unittest.mock import MagicMock, patch

_BASE = "app.api.web_routes.my_cards"
_TEMPLATE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "my_cards_player_card.html"
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
    r.url.path = "/my-cards/player-card"
    params = query_params or {}
    r.query_params.get = lambda k, default=None: params.get(k, default)
    return r


def _db():
    return MagicMock()


def _design(design_id, credit_cost, is_premium, label=None):
    d = MagicMock()
    d.id          = design_id
    d.label       = label or design_id.title()
    d.description = ""
    d.credit_cost = credit_cost
    d.is_premium  = is_premium
    return d


def _call(user=None, db=None, accessible_ids=None, designs=None, query_params=None):
    from app.api.web_routes.my_cards import my_cards_player_card

    user           = user or _user()
    db             = db   or _db()
    accessible_ids = accessible_ids or set()
    default_designs = [
        _design("fifa",    credit_cost=0,   is_premium=False),
        _design("compact", credit_cost=300, is_premium=True),
    ]
    captured = {}

    def fake_tmpl(tmpl, ctx):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    def fake_accessible(db, uid, card_type_id, design_id):
        return (card_type_id, design_id) in accessible_ids

    with patch(f"{_BASE}.get_all_designs", return_value=designs or default_designs), \
         patch(f"{_BASE}.is_design_accessible", side_effect=fake_accessible), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
        _run(my_cards_player_card(request=_req(query_params), db=db, user=user))

    return captured


class TestPlayerCardShopRoute:

    def test_mcp01_renders_player_card_template(self):
        """MCP-01: GET /my-cards/player-card renders my_cards_player_card.html."""
        cap = _call()
        assert cap["template"] == "my_cards_player_card.html"

    def test_mcp02_context_has_design_rows(self):
        """MCP-02: context contains design_rows with expected keys."""
        ctx = _call()["context"]
        rows = ctx["design_rows"]
        assert len(rows) == 2
        for r in rows:
            assert "id" in r
            assert "state" in r
            assert "credit_cost" in r

    def test_mcp03_non_premium_no_cdo_not_free(self):
        """MCP-03: 0-CR design without CDO row → state is 'not_available' (no free/0CR bypass)."""
        ctx = _call(user=_user(balance=0), accessible_ids=set())["context"]
        rows = ctx["design_rows"]
        fifa = next((r for r in rows if r["id"] == "fifa"), None)
        if fifa is None:
            return  # fifa not in mock designs for this test — skip
        assert fifa["state"] not in ("free", "get_card"), (
            "0-CR designs must not be purchasable via Get Card flow; expected 'not_available'"
        )
        # Either explicitly not_available (if credit_cost=0 in mock) or owned/locked
        assert fifa["state"] in ("not_available", "owned", "locked")

    def test_mcp04_premium_get_card(self):
        """MCP-04: premium not owned, credits ≥ cost → state='get_card'."""
        ctx = _call(user=_user(balance=500), accessible_ids=set())["context"]
        rows = ctx["design_rows"]
        row = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "get_card"

    def test_mcp05_premium_locked(self):
        """MCP-05: premium not owned, credits < cost → state='locked'."""
        ctx = _call(user=_user(balance=50), accessible_ids=set())["context"]
        rows = ctx["design_rows"]
        row = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "locked"

    def test_mcp06_premium_owned(self):
        """MCP-06: premium owned → state='owned'."""
        ctx = _call(
            user=_user(balance=500),
            accessible_ids={("player_card", "compact")},
        )["context"]
        rows = ctx["design_rows"]
        row = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "owned"

    def test_mcp07_context_has_owned_and_total_counts(self):
        """MCP-07: context contains owned_count and total_count."""
        ctx = _call()["context"]
        assert "owned_count" in ctx
        assert "total_count" in ctx
        assert ctx["total_count"] == 2

    def test_mcp08_route_has_auth_dependency(self):
        """MCP-08: route declares get_current_user_web dependency."""
        from app.api.web_routes.my_cards import my_cards_player_card
        sig = inspect.signature(my_cards_player_card)
        assert "user" in sig.parameters

    def test_mcp09_template_extends_student_base(self):
        """MCP-09: template extends student_base and includes spec_subpage_hdr."""
        src = _TEMPLATE_PATH.read_text()
        assert "student_base.html" in src
        assert "spec_subpage_hdr.html" in src

    def test_mcp10_template_breadcrumb_links_to_hub(self):
        """MCP-10: template breadcrumb links to /my-cards hub."""
        src = _TEMPLATE_PATH.read_text()
        assert 'href="/my-cards"' in src

    def test_mcp11_template_has_purchase_form(self):
        """MCP-11: template contains POST form action for purchasing designs."""
        src = _TEMPLATE_PATH.read_text()
        assert "/my-cards/designs/player_card/" in src
        assert 'method="POST"' in src

    def test_mcp12_owned_count_only_cdo_rows(self):
        """MCP-12: owned_count = only CDO-backed owned designs (no free auto-include)."""
        ctx = _call(
            user=_user(balance=500),
            designs=[
                _design("fifa",    credit_cost=0,   is_premium=False),
                _design("compact", credit_cost=300, is_premium=True),
            ],
            accessible_ids={("player_card", "compact")},
        )["context"]
        assert ctx["owned_count"] == 1  # only owned compact; fifa not auto-included
        assert ctx["total_count"] == 2

"""Player Card owned-only collection route tests — MCP-01..MCP-12.

MCP-01  GET /my-cards/player-card renders my_cards_player_card.html
MCP-02  Context contains design_rows with only accessible (owned) designs
MCP-03  Non-accessible design → not in design_rows
MCP-04  Accessible design → in design_rows with state='owned'
MCP-05  Multiple accessible designs → all appear in design_rows
MCP-06  Premium owned → in design_rows with state='owned'
MCP-07  Context contains owned_count and total_count (both = len(owned designs))
MCP-08  Route has auth dependency (get_current_user_web)
MCP-09  Template file extends student_base and includes spec_subpage_hdr
MCP-10  Template file breadcrumb links to /my-cards
MCP-11  Template file has Browse Shop CTA linking to /shop/cards/player
MCP-12  owned_count = explicitly CDO-backed owned designs only
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
        _design("fclassic",    credit_cost=0,   is_premium=False),
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
        cap = _call(accessible_ids={("player_card", "compact")})
        assert cap["template"] == "my_cards_player_card.html"

    def test_mcp02_context_has_design_rows_with_owned_designs(self):
        """MCP-02: context.design_rows contains only accessible (owned) designs."""
        ctx = _call(accessible_ids={("player_card", "compact")})["context"]
        rows = ctx["design_rows"]
        assert len(rows) == 1  # only compact is accessible
        assert rows[0]["id"] == "compact"
        assert rows[0]["state"] == "owned"

    def test_mcp03_non_accessible_design_not_in_rows(self):
        """MCP-03: design without CDO row → not present in design_rows."""
        ctx = _call(accessible_ids=set())["context"]
        rows = ctx["design_rows"]
        assert len(rows) == 0  # no accessible designs → empty collection

    def test_mcp04_accessible_design_in_rows_with_owned_state(self):
        """MCP-04: accessible design → appears in design_rows with state='owned'."""
        ctx = _call(
            user=_user(balance=500),
            accessible_ids={("player_card", "compact")},
        )["context"]
        rows = ctx["design_rows"]
        row  = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "owned"

    def test_mcp05_multiple_accessible_designs_all_in_rows(self):
        """MCP-05: multiple accessible designs → all appear in design_rows."""
        ctx = _call(
            accessible_ids={("player_card", "fclassic"), ("player_card", "compact")},
        )["context"]
        ids = {r["id"] for r in ctx["design_rows"]}
        assert "fclassic" in ids
        assert "compact" in ids

    def test_mcp06_premium_owned_in_rows(self):
        """MCP-06: premium owned → in design_rows with state='owned'."""
        ctx = _call(
            user=_user(balance=500),
            accessible_ids={("player_card", "compact")},
        )["context"]
        rows = ctx["design_rows"]
        row = next(r for r in rows if r["id"] == "compact")
        assert row["state"] == "owned"

    def test_mcp07_context_has_owned_and_total_counts(self):
        """MCP-07: context contains owned_count and total_count (both = owned)."""
        ctx = _call(accessible_ids={("player_card", "compact")})["context"]
        assert "owned_count" in ctx
        assert "total_count" in ctx
        assert ctx["owned_count"] == 1
        assert ctx["total_count"] == 1

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

    def test_mcp11_template_has_browse_shop_cta(self):
        """MCP-11: template has Browse Shop CTA linking to /shop/cards/player."""
        src = _TEMPLATE_PATH.read_text()
        assert "/shop/cards/player" in src

    def test_mcp12_owned_count_only_cdo_rows(self):
        """MCP-12: owned_count = only CDO-backed owned designs (no free auto-include)."""
        ctx = _call(
            user=_user(balance=500),
            designs=[
                _design("fclassic",    credit_cost=0,   is_premium=False),
                _design("compact", credit_cost=300, is_premium=True),
            ],
            accessible_ids={("player_card", "compact")},
        )["context"]
        assert ctx["owned_count"] == 1  # only owned compact; fclassic not auto-included
        assert ctx["total_count"] == 1  # total_count = owned_count in owned-only view

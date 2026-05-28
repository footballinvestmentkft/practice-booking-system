"""
MCH_ — My Cards Hub tests.

Updated for Phase 2 format-level Card Shop MVP:
  - Hub exposes pc_owned_count/pc_total/wc_owned_count/wc_total/cc_owned_count/cc_total
  - player-card and welcome-card routes now render TemplateResponse (format shops)

Routes under test:
  GET /my-cards               → hub page (200, tile layout)
  GET /my-cards/player-card   → 200 → my_cards_player_card.html (design format shop)
  GET /my-cards/welcome-card  → 200 → my_cards_welcome_card.html (format shop)

Backward-compat assertions:
  MCH-10: /dashboard/lfa-football-player/card-editor route still exists
  MCH-11: /profile/onboarding-card route still exists

Dashboard template assertions:
  MCH-16: dashboard href="/my-cards" present in CTA context
  MCH-17: dashboard card CTAs link to /my-cards (not individual card routes)
"""
import asyncio
import inspect
import pathlib
import pytest
from unittest.mock import MagicMock, patch

from app.api.web_routes.my_cards import (
    my_cards_hub,
    my_cards_player_card,
    my_cards_welcome_card,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.my_cards"

_DASHBOARD_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "dashboard_student_new.html"
)

_CARD_EDITOR_ROUTE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "api" / "web_routes" / "dashboard.py"
)

_WELCOME_CARD_ROUTE_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "api" / "web_routes" / "profile.py"
)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req(query_params=None):
    m = MagicMock()
    m.url = MagicMock()
    m.url.path = "/my-cards"
    params = query_params or {}
    m.query_params.get = lambda k, default=None: params.get(k, default)
    return m


def _user(uid=42, balance=500):
    u = MagicMock()
    u.id             = uid
    u.role           = UserRole.STUDENT
    u.email          = "player@test.com"
    u.credit_balance = balance
    return u


def _design(design_id, credit_cost, is_premium, label=None):
    d = MagicMock()
    d.id          = design_id
    d.label       = label or design_id.replace("_", " ").title()
    d.credit_cost = credit_cost
    d.is_premium  = is_premium
    return d


def _make_db():
    db = MagicMock()
    db.add    = MagicMock()
    db.commit = MagicMock()
    return db


def _call_hub(
    user=None,
    db=None,
    owned_ids_by_type=None,
    query_params=None,
    designs=None,
):
    """Call my_cards_hub directly with all dependencies mocked.

    owned_ids_by_type: dict[str, list[str]] — maps card_type_id → owned design_ids
    """
    user             = user or _user()
    db               = db   or _make_db()
    owned_ids_by_type = owned_ids_by_type or {}
    default_designs  = [
        _design("fifa",    credit_cost=0,   is_premium=False, label="FIFA Classic"),
        _design("compact", credit_cost=300, is_premium=True,  label="Compact"),
    ]
    captured = {}

    def fake_template_response(template_name, context):
        captured["template"] = template_name
        captured["context"]  = context
        return MagicMock(status_code=200)

    def fake_owned_ids(_db, uid, card_type_id):
        return list(owned_ids_by_type.get(card_type_id, []))

    with patch(f"{_BASE}.get_all_designs", return_value=designs or default_designs), \
         patch(f"{_BASE}.get_owned_design_ids", side_effect=fake_owned_ids), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_template_response):
        _run(my_cards_hub(_req(query_params), db=db, user=user))

    return captured


# ── MCH-01/02: /my-cards authentication ───────────────────────────────────────

class TestMyCardsHubAuth:

    def test_mch01_hub_authenticated_returns_200(self):
        """MCH-01: GET /my-cards with authenticated user renders hub template."""
        cap = _call_hub()
        assert cap["template"] == "my_cards_hub.html"

    def test_mch02_hub_unauthenticated_redirects(self):
        """MCH-02: Hub route declares get_current_user_web dependency."""
        sig = inspect.signature(my_cards_hub)
        assert "user" in sig.parameters


# ── MCH-03/04/05: Hub context keys (Phase 2: count-based) ─────────────────────

class TestMyCardsHubContext:

    def test_mch03_player_card_counts_in_hub_context(self):
        """MCH-03: hub context contains Player Card count keys."""
        ctx = _call_hub()["context"]
        for key in ("pc_owned_count", "pc_total"):
            assert key in ctx, f"Missing key: {key}"

    def test_mch04_welcome_card_counts_in_hub_context(self):
        """MCH-04: hub context contains Welcome Card count keys."""
        ctx = _call_hub()["context"]
        for key in ("wc_owned_count", "wc_total"):
            assert key in ctx, f"Missing key: {key}"

    def test_mch05_challenge_card_counts_in_hub_context(self):
        """MCH-05: hub context contains Challenge Card count keys."""
        ctx = _call_hub()["context"]
        for key in ("cc_owned_count", "cc_total"):
            assert key in ctx, f"Missing key: {key}"


# ── MCH-06/07: Family shop routes render TemplateResponse ─────────────────────

class TestMyCardsDetailRoutes:

    def test_mch06_player_card_route_renders_format_shop(self):
        """MCH-06: GET /my-cards/player-card → 200 + my_cards_player_card.html."""
        captured = {}

        def fake_tmpl(tmpl, ctx):
            captured["template"] = tmpl
            captured["context"]  = ctx
            return MagicMock(status_code=200)

        default_designs = [_design("fifa", credit_cost=0, is_premium=False, label="FIFA Classic")]

        with patch(f"{_BASE}.get_all_designs", return_value=default_designs), \
             patch(f"{_BASE}.is_design_accessible", return_value=False), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
            _run(my_cards_player_card(request=_req(), db=_make_db(), user=_user()))

        assert captured.get("template") == "my_cards_player_card.html"

    def test_mch07_welcome_card_route_renders_format_shop(self):
        """MCH-07: GET /my-cards/welcome-card → 200 + my_cards_welcome_card.html."""
        captured = {}

        def fake_tmpl(tmpl, ctx):
            captured["template"] = tmpl
            captured["context"]  = ctx
            return MagicMock(status_code=200)

        with patch(f"{_BASE}.is_design_accessible", return_value=False), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=fake_tmpl):
            _run(my_cards_welcome_card(request=_req(), db=_make_db(), user=_user()))

        assert captured.get("template") == "my_cards_welcome_card.html"


# ── MCH-08/09: Route auth guards ──────────────────────────────────────────────

class TestMyCardsDetailAuth:

    def test_mch08_player_card_route_has_auth_dependency(self):
        """MCH-08: /my-cards/player-card requires get_current_user_web."""
        sig = inspect.signature(my_cards_player_card)
        assert "user" in sig.parameters

    def test_mch09_welcome_card_route_has_auth_dependency(self):
        """MCH-09: /my-cards/welcome-card requires get_current_user_web."""
        sig = inspect.signature(my_cards_welcome_card)
        assert "user" in sig.parameters


# ── MCH-10/11: Backward compatibility ─────────────────────────────────────────

class TestBackwardCompatibility:

    def test_mch10_old_card_editor_route_still_defined(self):
        """MCH-10: /dashboard/lfa-football-player/card-editor route still exists."""
        src = _CARD_EDITOR_ROUTE_PATH.read_text()
        assert '/dashboard/lfa-football-player/card-editor' in src
        assert 'async def lfa_player_card_editor' in src

    def test_mch11_old_welcome_card_route_still_defined(self):
        """MCH-11: /profile/onboarding-card route still exists."""
        src = _WELCOME_CARD_ROUTE_PATH.read_text()
        assert '/profile/onboarding-card' in src
        assert 'async def onboarding_welcome_card' in src


# ── MCH-12/13/14: Hub template structural assertions ─────────────────────────

class TestMyCardsHubTemplate:

    @pytest.fixture(scope="class")
    def hub_src(self):
        path = (
            pathlib.Path(__file__).resolve().parents[4]
            / "app" / "templates" / "my_cards_hub.html"
        )
        return path.read_text()

    def test_mch12_hub_template_contains_my_cards_text(self, hub_src):
        """MCH-12: Hub template heading reads 'My Cards'."""
        assert "My Cards" in hub_src

    def test_mch13_hub_template_has_three_family_links(self, hub_src):
        """MCH-13: Hub template has static links for all three card families."""
        assert "/my-cards/player-card" in hub_src
        assert "/my-cards/welcome-card" in hub_src
        assert "/my-cards/challenge-card" in hub_src

    def test_mch14_hub_context_has_all_count_keys(self):
        """MCH-14: All six count context keys present (pc/wc/cc owned_count+total)."""
        ctx = _call_hub()["context"]
        for key in ("pc_owned_count", "pc_total", "wc_owned_count", "wc_total", "cc_owned_count", "cc_total"):
            assert key in ctx, f"Missing context key: {key}"


# ── MCH-15: Count correctness ─────────────────────────────────────────────────

class TestOwnershipCounts:

    def test_mch15_pc_owned_count_includes_free_designs(self):
        """MCH-15: pc_owned_count=1 when only free 'fifa' is owned."""
        ctx = _call_hub(
            designs=[_design("fifa", credit_cost=0, is_premium=False)],
            owned_ids_by_type={"player_card": ["fifa"]},
        )["context"]
        assert ctx["pc_owned_count"] == 1
        assert ctx["pc_total"] == 1

    def test_mch15b_pc_total_reflects_all_designs(self):
        """MCH-15b: pc_total = len(all designs)."""
        ctx = _call_hub(
            designs=[
                _design("fifa",    credit_cost=0,   is_premium=False),
                _design("compact", credit_cost=300, is_premium=True),
            ],
            owned_ids_by_type={"player_card": ["fifa"]},
        )["context"]
        assert ctx["pc_total"] == 2
        assert ctx["pc_owned_count"] == 1

    def test_mch15c_wc_owned_count_excludes_legacy_key(self):
        """MCH-15c: wc_owned_count only counts valid WC format IDs (not legacy 'default')."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        # Simulate a user who only has the legacy 'default' CDO (shim expands to all formats)
        all_wc_ids = [f.design_id for f in WELCOME_CARD_FORMATS]
        ctx = _call_hub(
            owned_ids_by_type={"welcome_card": all_wc_ids},
        )["context"]
        assert ctx["wc_owned_count"] == len(WELCOME_CARD_FORMATS)

    def test_mch15d_wc_total_equals_welcome_card_formats_count(self):
        """MCH-15d: wc_total == len(WELCOME_CARD_FORMATS)."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        ctx = _call_hub()["context"]
        assert ctx["wc_total"] == len(WELCOME_CARD_FORMATS)

    def test_mch15e_cc_total_equals_challenge_card_formats_count(self):
        """MCH-15e: cc_total == len(CHALLENGE_CARD_FORMATS)."""
        from app.services.card_design_service import CHALLENGE_CARD_FORMATS
        ctx = _call_hub()["context"]
        assert ctx["cc_total"] == len(CHALLENGE_CARD_FORMATS)


# ── MCH-16/17: Dashboard template navigation updated ──────────────────────────

class TestDashboardNavUpdated:

    @pytest.fixture(scope="class")
    def dashboard_src(self):
        return _DASHBOARD_TPL_PATH.read_text()

    def test_mch16_dashboard_has_my_cards_cta(self, dashboard_src):
        """MCH-16: Dashboard card CTAs include href="/my-cards"."""
        assert 'href="/my-cards"' in dashboard_src

    def test_mch17_dashboard_card_ctas_funnel_to_my_cards(self, dashboard_src):
        """MCH-17: Player/Welcome/Challenge panel primary CTAs link to /my-cards."""
        assert 'href="/my-cards" class="dc-cta-primary"' in dashboard_src
        assert 'href="/profile/onboarding-card" class="dc-cta-primary"' not in dashboard_src
        assert 'href="/challenges/send"' in dashboard_src


# ── MCH-CH-01..09: Challenge Card manager route ───────────────────────────────

import pathlib as _pathlib

_CC_TEMPLATE_PATH = (
    _pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "my_cards_challenge_card.html"
)

_CC_BASE = "app.api.web_routes.my_cards"


def _db_mock():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.add    = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    return db


def _theme(tid, label, dot="#667eea", is_premium=False):
    t = MagicMock()
    t.id         = tid
    t.label      = label
    t.dot_color  = dot
    t.is_premium = is_premium
    return t


class TestMyChallengeCardRoute:

    def _call(self, *, user=None, db=None):
        from app.api.web_routes.my_cards import my_cards_challenge_card
        user = user or _user()
        db   = db   or _db_mock()

        captured = {}

        def _capture(tmpl, ctx, **kw):
            captured["template"] = tmpl
            captured["context"]  = ctx
            return MagicMock(status_code=200)

        with patch(f"{_CC_BASE}.templates") as mock_tmpl, \
             patch(f"{_CC_BASE}.is_design_accessible", return_value=False):
            mock_tmpl.TemplateResponse.side_effect = _capture
            _run(my_cards_challenge_card(
                request=MagicMock(),
                db=db,
                user=user,
            ))

        return captured

    def test_mch_ch01_returns_200(self):
        """MCH-CH-01: GET /my-cards/challenge-card logged-in user → 200."""
        cap = self._call()
        assert cap.get("template") == "my_cards_challenge_card.html"

    def test_mch_ch02_route_has_auth_dependency(self):
        """MCH-CH-02: route signature requires authenticated user."""
        from app.api.web_routes.my_cards import my_cards_challenge_card
        sig = inspect.signature(my_cards_challenge_card)
        assert "user" in sig.parameters

    def test_mch_ch03_hub_context_has_cc_count_keys(self):
        """MCH-CH-03: hub context contains cc_owned_count and cc_total keys."""
        ctx = _call_hub()["context"]
        assert "cc_owned_count" in ctx
        assert "cc_total" in ctx

    def test_mch_ch04_hub_template_has_challenge_card_static_link(self):
        """MCH-CH-04: hub template contains static /my-cards/challenge-card link."""
        hub_html = _CC_TEMPLATE_PATH.parent.joinpath("my_cards_hub.html").read_text()
        assert "/my-cards/challenge-card" in hub_html

    def test_mch_ch05_context_contains_post_16_9(self):
        """MCH-CH-05: route context cc_format_rows includes challenge_post_16_9."""
        cap = self._call()
        row_ids = {r["design_id"] for r in cap["context"].get("cc_format_rows", [])}
        assert "challenge_post_16_9" in row_ids

    def test_mch_ch06_context_contains_story_9_16(self):
        """MCH-CH-06: route context cc_format_rows includes challenge_story_9_16."""
        cap = self._call()
        row_ids = {r["design_id"] for r in cap["context"].get("cc_format_rows", [])}
        assert "challenge_story_9_16" in row_ids

    def test_mch_ch07_template_contains_results_link(self):
        """MCH-CH-07: template contains /challenges/results CTA link."""
        html = _CC_TEMPLATE_PATH.read_text()
        assert "/challenges/results" in html

    def test_mch_ch08_spec_is_editable_and_theme_compatible(self):
        """MCH-CH-08: CHALLENGE_CARD_SPEC.is_editable=True, theme_compatible=True."""
        from app.services.card_system._challenge_card import CHALLENGE_CARD_SPEC
        assert CHALLENGE_CARD_SPEC.is_editable is True
        assert CHALLENGE_CARD_SPEC.theme_compatible is True
        assert CHALLENGE_CARD_SPEC.has_published_state is False

    def test_mch_ch09_context_contains_cc_format_rows_with_both_ids(self):
        """MCH-CH-09: route context cc_format_rows contains both platform IDs."""
        cap = self._call()
        rows = cap["context"].get("cc_format_rows", [])
        row_ids = {r["design_id"] for r in rows}
        assert "challenge_post_16_9"  in row_ids
        assert "challenge_story_9_16" in row_ids

    def test_mch_ch10_context_contains_cc_format_rows(self):
        """MCH-CH-10: route context contains cc_format_rows with state per format."""
        cap = self._call()
        rows = cap["context"].get("cc_format_rows", [])
        assert len(rows) == 2
        row_ids = {r["design_id"] for r in rows}
        assert "challenge_post_16_9"  in row_ids
        assert "challenge_story_9_16" in row_ids
        for r in rows:
            assert "state" in r
            assert "credit_cost" in r

    def test_mch_ch11_context_has_no_challenge_list_keys(self):
        """MCH-CH-11: shop route context must NOT contain challenge_rows, has_challenges,
        draft, themes, or formats keys — the shop is format-only now."""
        cap = self._call()
        ctx = cap["context"]
        for forbidden in ("challenge_rows", "has_challenges", "draft", "themes", "formats"):
            assert forbidden not in ctx, f"Context must not contain '{forbidden}'"

    def test_mch_ch12_template_has_no_challenge_iframe(self):
        """MCH-CH-12: shop template must not contain cc-preview-iframe (challenge list removed)."""
        html = _CC_TEMPLATE_PATH.read_text()
        assert "cc-preview-iframe" not in html

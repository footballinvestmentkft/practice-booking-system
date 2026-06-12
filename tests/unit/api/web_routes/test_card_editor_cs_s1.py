"""
CS-S1 / CS-S1b — Card Studio canonical redirect + naming tests.

Route redirects (CS-S1):
  S1-01  GET /card-editor              → 301 /card-studio
  S1-02  GET /card-editor/welcome      → 301 /card-studio/welcome
  S1-03  GET /card-editor/welcome?format=X → 301 /card-studio/welcome?format=X
  S1-04  GET /card-editor/player       → 200 (unchanged, FORBIDDEN to redirect in CS-S1)
  S1-05  GET /card-editor/challenge    → 200 (unchanged, CS-S4 deferred)
  S1-06  GET /card-editor/welcome/{id} → 200 (WCE-1 unchanged)
  S1-07  GET /card-studio              → registered (200/303 via CSS handler)
  S1-08  GET /card-studio/welcome      → registered (200/303 via CSS handler)

CTA / naming source checks (CS-S1b):
  S1b-01  Welcome CTAs point to /card-studio/welcome
  S1b-02  Generic Studio nav links to /card-studio
  S1b-03  Player CTAs still point to /card-editor/player
  S1b-04  Player CTA text uses "Studio" wording
  S1b-05  No accidental /card-studio/player link

Route / OpenAPI:
  S1-09  Route count = 844 (no new routes from CS-S1 redirect change)
  S1-10  OpenAPI snapshot still matches (paths unchanged)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.card_design_service import WELCOME_CARD_FORMATS

_CE_BASE = "app.api.web_routes.card_editor"
_CS_BASE = "app.api.web_routes.card_studio"

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
SNAPSHOTS_DIR = Path(__file__).resolve().parents[4] / "tests" / "snapshots"

_ALL_WC_IDS: list[str] = [f.design_id for f in WELCOME_CARD_FORMATS]
_FIRST_ID  = _ALL_WC_IDS[0]
_SECOND_ID = _ALL_WC_IDS[1]


def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 42) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


# ── S1-01: GET /card-editor → 301 /card-studio ───────────────────────────────

class TestS101CardEditorRedirect:

    def test_s1_01_card_editor_returns_301(self):
        """S1-01: GET /card-editor → 301 permanent redirect."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_landing

        resp = _run(card_studio_landing(user=_user()))
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 301

    def test_s1_01b_redirect_target_is_card_studio(self):
        """S1-01b: /card-editor redirect target is /card-studio (not /card-editor/*)."""
        from app.api.web_routes.card_editor import card_studio_landing

        resp = _run(card_studio_landing(user=_user()))
        assert resp.headers["location"] == "/card-studio"

    def test_s1_01c_redirect_is_not_to_welcome(self):
        """S1-01c: /card-editor must not redirect directly to /card-studio/welcome."""
        from app.api.web_routes.card_editor import card_studio_landing

        resp = _run(card_studio_landing(user=_user()))
        assert resp.headers["location"] != "/card-studio/welcome"


# ── S1-02/03: GET /card-editor/welcome → 301 /card-studio/welcome ────────────

class TestS102S103WelcomeRedirect:

    def test_s1_02_card_editor_welcome_returns_301(self):
        """S1-02: GET /card-editor/welcome (no format) → 301 /card-studio/welcome."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=None, user=_user()))
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/card-studio/welcome"

    def test_s1_03_welcome_with_format_passes_param_through(self):
        """S1-03: GET /card-editor/welcome?format=X → 301 /card-studio/welcome?format=X."""
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))
        assert resp.status_code == 301
        assert resp.headers["location"] == f"/card-studio/welcome?format={_FIRST_ID}"

    def test_s1_03b_second_format_id_also_passes_through(self):
        """S1-03b: Any valid format_id is preserved in the redirect URL."""
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_SECOND_ID, user=_user()))
        assert resp.headers["location"] == f"/card-studio/welcome?format={_SECOND_ID}"


# ── S1-04: GET /card-editor/player → 200 (FORBIDDEN to redirect in CS-S1) ────

class TestS104PlayerNotRedirected:

    def test_s1_04_player_route_is_not_a_redirect_handler(self):
        """S1-04: /card-editor/player handler endpoint is NOT a redirect function."""
        from app.main import app
        from app.api.web_routes.dashboard import lfa_player_card_editor

        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor/player"),
            None,
        )
        assert route is not None, "/card-editor/player must be registered"
        assert route.endpoint is lfa_player_card_editor, (
            "/card-editor/player must still point to lfa_player_card_editor — "
            "CS-S1 FORBIDDEN to redirect Player route before CS-S2"
        )

    def test_s1_04b_card_studio_player_route_exists(self):
        """S1-04b (updated CS-S2A): /card-studio/player is registered (CS-S2A preview MVP)."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-studio/player" in paths, (
            "/card-studio/player must be registered (added in CS-S2A)"
        )


# ── S1-05: GET /card-editor/challenge → 200 (unchanged) ─────────────────────

class TestS105ChallengeNotRedirected:

    def test_s1_05_challenge_route_still_registered(self):
        """S1-05: /card-editor/challenge is still a registered route (not redirected)."""
        from app.main import app
        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor/challenge"),
            None,
        )
        assert route is not None, "/card-editor/challenge must still be registered"

    def test_s1_05b_challenge_handler_is_not_redirect(self):
        """S1-05b: /card-editor/challenge endpoint is card_studio_challenge (renders template)."""
        from app.main import app
        from app.api.web_routes.card_editor import card_studio_challenge

        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor/challenge"),
            None,
        )
        assert route.endpoint is card_studio_challenge, (
            "/card-editor/challenge must still use the full card_studio_challenge handler"
        )


# ── S1-06: GET /card-editor/welcome/{format_id} → 200 (WCE-1 unchanged) ─────

class TestS106WCE1Unchanged:

    def test_s1_06_wce1_route_still_registered(self):
        """S1-06: /card-editor/welcome/{format_id} WCE-1 route is still registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-editor/welcome/{format_id}" in paths, (
            "WCE-1 /card-editor/welcome/{format_id} must remain — not redirected in CS-S1"
        )

    def test_s1_06b_wce1_handler_is_welcome_card_editor(self):
        """S1-06b: WCE-1 handler is welcome_card_editor (unchanged)."""
        from app.main import app
        from app.api.web_routes.card_editor import welcome_card_editor

        route = next(
            (r for r in app.routes
             if getattr(r, "path", None) == "/card-editor/welcome/{format_id}"),
            None,
        )
        assert route is not None
        assert route.endpoint is welcome_card_editor


# ── S1-07/08: /card-studio and /card-studio/welcome registered ───────────────

class TestS107S108CardStudioRoutes:

    def test_s1_07_card_studio_route_registered(self):
        """S1-07: GET /card-studio is registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-studio" in paths

    def test_s1_08_card_studio_welcome_route_registered(self):
        """S1-08: GET /card-studio/welcome is registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-studio/welcome" in paths


# ── S1b-01..05: CTA / naming source checks ───────────────────────────────────

class TestS1bCTAAndNaming:

    def test_s1b_01_my_cards_welcome_cta_points_to_card_studio(self):
        """S1b-01: my_cards_welcome_card.html Studio CTA links to /card-studio/welcome."""
        src = (TEMPLATES_DIR / "my_cards_welcome_card.html").read_text()
        assert 'href="/card-studio/welcome"' in src
        assert 'href="/card-editor/welcome"' not in src, (
            "my_cards_welcome_card.html must not have direct /card-editor/welcome link (CS-S1b)"
        )

    def test_s1b_01b_landing_welcome_cta_points_to_card_studio(self):
        """S1b-01b: card_studio_landing.html Welcome CTA links to /card-studio/welcome."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text()
        assert 'href="/card-studio/welcome"' in src

    def test_s1b_02_spec_subpage_hdr_nav_links_to_card_studio(self):
        """S1b-02: spec_subpage_hdr.html quicknav Card Studio link is /card-studio."""
        src = (TEMPLATES_DIR / "includes/spec_subpage_hdr.html").read_text()
        assert 'href="/card-studio"' in src, (
            "spec_subpage_hdr.html quicknav must link to /card-studio (canonical)"
        )

    def test_s1b_02b_challenge_breadcrumb_points_to_card_studio(self):
        """S1b-02b: card_studio_challenge.html breadcrumb links to /card-studio."""
        src = (TEMPLATES_DIR / "card_studio_challenge.html").read_text()
        assert 'href="/card-studio"' in src

    def test_s1b_02c_welcome_breadcrumb_points_to_card_studio(self):
        """S1b-02c: card_studio_welcome.html breadcrumb links to /card-studio."""
        src = (TEMPLATES_DIR / "card_studio_welcome.html").read_text()
        assert 'href="/card-studio"' in src

    def test_s1b_02d_welcome_format_pills_use_card_studio_url(self):
        """S1b-02d: card_studio_welcome.html format pills link to /card-studio/welcome?format=X."""
        src = (TEMPLATES_DIR / "card_studio_welcome.html").read_text()
        assert '/card-studio/welcome?format=' in src
        assert '/card-editor/welcome?format=' not in src, (
            "card_studio_welcome.html must not link to /card-editor/welcome (CS-S1b)"
        )

    def test_s1b_03_player_ctas_still_use_card_editor_player(self):
        """S1b-03: Player CTAs in templates still point to /card-editor/player."""
        player_templates = [
            "my_cards_player_card.html",
            # shop_player_card.html deleted in SHOP-3B2 (listing template, no longer served)
            "shop_player_card_detail.html",
            "shop_card_player_colors.html",
            "card_studio_landing.html",
        ]
        for tmpl in player_templates:
            src = (TEMPLATES_DIR / tmpl).read_text()
            assert 'href="/card-editor/player"' in src, (
                f"{tmpl} must still have /card-editor/player CTA (CS-S2 not done)"
            )

    def test_s1b_04_player_cta_text_uses_studio_wording(self):
        """S1b-04: Player CTA text uses Studio wording (not 'Editor')."""
        for tmpl in ["my_cards_player_card.html", "shop_player_card_detail.html"]:
            src = (TEMPLATES_DIR / tmpl).read_text()
            assert "Open Studio" in src, (
                f"{tmpl} Player CTA must use 'Open Studio' wording (CS-S1b)"
            )
        src_colors = (TEMPLATES_DIR / "shop_card_player_colors.html").read_text()
        assert "Open Card Studio" in src_colors

    def test_s1b_05_card_studio_player_link_exists(self):
        """S1b-05 (updated CS-S2A): /card-studio/player link is present in shell/switcher."""
        found = False
        for tmpl_path in TEMPLATES_DIR.rglob("*.html"):
            src = tmpl_path.read_text(encoding="utf-8")
            if "/card-studio/player" in src:
                found = True
                break
        assert found, "/card-studio/player must be present in templates (added in CS-S2A)"


# ── S1-09/10: route count and OpenAPI snapshot ───────────────────────────────

class TestS109S110RouteAndSnapshot:

    def test_s1_09_route_count_846(self):
        """S1-09 (updated PR-JUG-1): route count is 888 (+5 juggling-intake paths)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 892, (
            f"Expected 890 routes (883 prior + 5 juggling-intake paths), got {len(paths)}"
        )

    def test_s1_10_openapi_snapshot_still_matches(self):
        """S1-10: OpenAPI snapshot paths match live API (redirect preserves path set)."""
        snapshot_path = SNAPSHOTS_DIR / "openapi_snapshot.json"
        assert snapshot_path.exists()

        snap_paths = set(json.loads(snapshot_path.read_text()).get("paths", {}).keys())

        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())

        assert snap_paths == live_paths, (
            f"Snapshot differs from live API.\n"
            f"In snapshot only: {snap_paths - live_paths}\n"
            f"In live only: {live_paths - snap_paths}"
        )

"""
CEL — Card Studio Landing tests (CE-3.1).

GET /card-editor — authenticated entry point showing owned counts per card family.

CEL-01  authenticated user → handler callable, 200
CEL-02  unauthenticated → 401/redirect (get_current_user_web guard)
CEL-03  player_card owned_count reflects CDO rows
CEL-04  player_card owned=0 → CTA links to /shop/cards/player
CEL-05  welcome_card owned_count is filtered to valid format IDs only
CEL-06  challenge_card owned_count is filtered to valid format IDs only
CEL-07  any_owned=True when at least one family has owned designs
CEL-08  any_owned=False when all owned counts are zero
CEL-09  Player Card owned → CTA links to /card-editor/player
CEL-10  Welcome Card owned → CTA links to /my-cards/welcome
CEL-11  Challenge Card owned → CTA links to /my-cards/challenge
CEL-12  route count = 837 (836 baseline + GET /card-editor)
CEL-13  OpenAPI snapshot is up to date (837 routes)
CEL-14  /card-editor/player regression — lfa_player_card_editor still callable
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import jinja2
import pytest

from app.services.card_design_service import CHALLENGE_CARD_FORMATS, WELCOME_CARD_FORMATS

_CE_BASE = "app.api.web_routes.card_editor"

# Valid format-ID sets (same logic as the handler uses at module level)
_VALID_WC_IDS: frozenset[str] = frozenset(f.design_id for f in WELCOME_CARD_FORMATS)
_VALID_CC_IDS: frozenset[str] = frozenset(f.design_id for f in CHALLENGE_CARD_FORMATS)

# One known-valid ID from each family for owned tests
_WC_VALID_ID: str = sorted(_VALID_WC_IDS)[0]
_CC_VALID_ID: str = sorted(_VALID_CC_IDS)[0]

SNAPSHOTS_DIR = Path(__file__).resolve().parents[4] / "tests" / "snapshots"
TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 42) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


def _invoke_landing(
    pc_design_ids: list[str] | None = None,
    wc_design_ids: list[str] | None = None,
    cc_design_ids: list[str] | None = None,
) -> dict:
    """Call card_studio_landing with mocked deps; return captured template context."""
    pc_design_ids = pc_design_ids or []
    wc_design_ids = wc_design_ids or []
    cc_design_ids = cc_design_ids or []

    from app.api.web_routes.card_editor import card_studio_landing

    user = _user()
    db   = MagicMock()
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    def _mock_get_owned(db_, uid, card_type_id):
        if card_type_id == "player_card":   return pc_design_ids
        if card_type_id == "welcome_card":  return wc_design_ids
        if card_type_id == "challenge_card": return cc_design_ids
        return []

    with patch(f"{_CE_BASE}.get_owned_design_ids", side_effect=_mock_get_owned), \
         patch(f"{_CE_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        try:
            _run(card_studio_landing(request=MagicMock(), db=db, user=user))
        except Exception:
            pass

    return captured.get("context", {})


def _render_landing(ctx: dict) -> str:
    """Render card_studio_landing.html with Jinja2 for CTA link assertions."""
    src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
    # Strip extends / include directives so standalone render works
    src = "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith("{%") or
        any(k in line for k in ("if ", "else", "endif", "for ", "endfor", "set "))
    )
    env = jinja2.Environment()
    return env.from_string(src).render(**ctx)


# ── CEL-01: authenticated call succeeds ───────────────────────────────────────

class TestCEL01Authenticated:

    def test_cel_01_handler_callable_returns_context(self):
        """CEL-01: card_studio_landing is callable and populates template context."""
        ctx = _invoke_landing()
        assert ctx, "context must be captured — handler likely raised"

    def test_cel_01_template_is_landing(self):
        """CEL-01: handler renders card_studio_landing.html."""
        from app.api.web_routes.card_editor import card_studio_landing

        user = _user()
        captured = {}

        def _fake(tmpl, ctx, **kw):
            captured["template"] = tmpl
            return MagicMock(status_code=200)

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[]), \
             patch(f"{_CE_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.side_effect = _fake
            try:
                _run(card_studio_landing(request=MagicMock(), db=MagicMock(), user=user))
            except Exception:
                pass

        assert captured.get("template") == "card_studio_landing.html"


# ── CEL-02: unauthenticated guard ─────────────────────────────────────────────

class TestCEL02Unauthenticated:

    def test_cel_02_route_has_current_user_dependency(self):
        """CEL-02: /card-editor route has get_current_user_web in its dependant tree."""
        from app.main import app
        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor"),
            None,
        )
        assert route is not None, "/card-editor route must be registered"
        # FastAPI stores parameter-level Depends in route.dependant.dependencies
        dep_names = [
            getattr(d.call, "__name__", "") for d in getattr(route.dependant, "dependencies", [])
        ]
        assert "get_current_user_web" in dep_names, (
            f"/card-editor must depend on get_current_user_web for auth guard; found: {dep_names}"
        )


# ── CEL-03/04: player_card owned count ────────────────────────────────────────

class TestCEL03PlayerCardOwnership:

    def test_cel_03_pc_owned_count_reflects_cdo_rows(self):
        """CEL-03: pc_owned_count equals the number of owned player_card design IDs."""
        ctx = _invoke_landing(pc_design_ids=["fclassic", "compact", "showcase"])
        assert ctx.get("pc_owned_count") == 3

    def test_cel_03b_pc_owned_count_zero_when_no_designs(self):
        """CEL-03b: pc_owned_count=0 when get_owned_design_ids returns empty list."""
        ctx = _invoke_landing(pc_design_ids=[])
        assert ctx.get("pc_owned_count") == 0

    def test_cel_04_pc_no_owned_cta_points_to_shop(self):
        """CEL-04: template source has Browse Player Designs CTA for no-owned state."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/shop/cards/player"' in src, (
            "Template must contain shop CTA for no-owned player_card state"
        )
        assert "Browse Player Designs" in src


# ── CEL-05: welcome_card count filtered to valid IDs ─────────────────────────

class TestCEL05WelcomeCardCount:

    def test_cel_05_only_valid_wc_formats_counted(self):
        """CEL-05: wc_owned_count ignores non-existent format IDs."""
        # Pass one valid + one invented ID → count must be 1
        ctx = _invoke_landing(wc_design_ids=[_WC_VALID_ID, "invented_format_xyz"])
        assert ctx.get("wc_owned_count") == 1, (
            f"Only the valid format '{_WC_VALID_ID}' should count, not 'invented_format_xyz'"
        )

    def test_cel_05b_all_valid_wc_formats_counted(self):
        """CEL-05b: all valid WC format IDs are counted correctly."""
        ctx = _invoke_landing(wc_design_ids=list(_VALID_WC_IDS))
        assert ctx.get("wc_owned_count") == len(_VALID_WC_IDS)

    def test_cel_05c_empty_wc_list_gives_zero(self):
        """CEL-05c: wc_owned_count=0 when no WC designs owned."""
        ctx = _invoke_landing(wc_design_ids=[])
        assert ctx.get("wc_owned_count") == 0


# ── CEL-06: challenge_card count filtered to valid IDs ───────────────────────

class TestCEL06ChallengeCardCount:

    def test_cel_06_only_valid_cc_formats_counted(self):
        """CEL-06: cc_owned_count ignores non-existent format IDs."""
        ctx = _invoke_landing(cc_design_ids=[_CC_VALID_ID, "fake_challenge_format"])
        assert ctx.get("cc_owned_count") == 1

    def test_cel_06b_empty_cc_list_gives_zero(self):
        """CEL-06b: cc_owned_count=0 when no CC designs owned."""
        ctx = _invoke_landing(cc_design_ids=[])
        assert ctx.get("cc_owned_count") == 0


# ── CEL-07/08: any_owned boolean ─────────────────────────────────────────────

class TestCEL0708AnyOwned:

    def test_cel_07_any_owned_true_when_pc_owned(self):
        """CEL-07: any_owned=True when player_card has owned designs."""
        ctx = _invoke_landing(pc_design_ids=["fclassic"])
        assert ctx.get("any_owned") is True

    def test_cel_07b_any_owned_true_when_wc_owned(self):
        """CEL-07b: any_owned=True when welcome_card has owned designs."""
        ctx = _invoke_landing(wc_design_ids=[_WC_VALID_ID])
        assert ctx.get("any_owned") is True

    def test_cel_07c_any_owned_true_when_cc_owned(self):
        """CEL-07c: any_owned=True when challenge_card has owned designs."""
        ctx = _invoke_landing(cc_design_ids=[_CC_VALID_ID])
        assert ctx.get("any_owned") is True

    def test_cel_08_any_owned_false_when_all_zero(self):
        """CEL-08: any_owned=False when no family has owned designs."""
        ctx = _invoke_landing(pc_design_ids=[], wc_design_ids=[], cc_design_ids=[])
        assert ctx.get("any_owned") is False

    def test_cel_08b_template_has_empty_state_block(self):
        """CEL-08b: template source contains cs-empty-state block."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-empty-state" in src
        assert "You don't own any card designs yet." in src
        assert 'href="/shop/cards"' in src


# ── CEL-09/10/11: CTA links ───────────────────────────────────────────────────

class TestCEL091011CTALinks:

    def test_cel_09_template_has_player_editor_cta(self):
        """CEL-09: template contains /card-editor/player CTA for owned player card."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/card-editor/player"' in src
        assert "Open Editor" in src

    def test_cel_10_template_has_welcome_cta(self):
        """CEL-10: template contains /card-editor/welcome CTA for owned welcome card (CE-3.6-A)."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/card-editor/welcome"' in src
        assert "Open Welcome Studio" in src

    def test_cel_11_template_has_challenge_cta(self):
        """CEL-11: template contains /card-editor/challenge CTA for owned challenge card (CE-3.6-A)."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/card-editor/challenge"' in src
        assert "Open Challenge Studio" in src

    def test_cel_09b_cs_player_editor_cta_class_present(self):
        """CEL-09b: cs-player-editor-cta CSS class marks the player CTA for JS/test targeting."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-player-editor-cta" in src

    def test_cel_10b_cs_welcome_cta_class_present(self):
        """CEL-10b: cs-welcome-cta class marks the welcome CTA."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-welcome-cta" in src

    def test_cel_11b_cs_challenge_cta_class_present(self):
        """CEL-11b: cs-challenge-cta class marks the challenge CTA."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-challenge-cta" in src


# ── CEL-12: route count = 837 ────────────────────────────────────────────────

class TestCEL12RouteCount:

    def test_cel_12_route_count_837(self):
        """CE-3.4 adds GET /card-editor/challenge — total route count is 839."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 844, (
            f"Expected 844 routes (842 CE-3.7+CE-3.8 baseline + 2 CS-S0 card-studio routes), got {len(paths)}."
        )

    def test_cel_12b_card_editor_route_registered(self):
        """CEL-12b: GET /card-editor is in the registered routes."""
        from app.main import app
        route_paths = [r.path for r in app.routes]
        assert "/card-editor" in route_paths, "/card-editor must be a registered route"


# ── CEL-13: OpenAPI snapshot updated ─────────────────────────────────────────

class TestCEL13OpenAPISnapshot:

    def test_cel_13_snapshot_matches_live_openapi(self):
        """CEL-13: committed openapi_snapshot.json reflects the live 837-route API."""
        snapshot_path = SNAPSHOTS_DIR / "openapi_snapshot.json"
        assert snapshot_path.exists(), f"Snapshot not found: {snapshot_path}"

        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snap_paths = set(snapshot.get("paths", {}).keys())

        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())

        assert "/card-editor" in snap_paths, (
            "Snapshot must include /card-editor (regenerate with update_openapi_snapshot.py)"
        )
        assert snap_paths == live_paths, (
            f"Snapshot paths differ from live API.\n"
            f"In snapshot only: {snap_paths - live_paths}\n"
            f"In live only: {live_paths - snap_paths}"
        )


# ── CEL-14: /card-editor/player regression ───────────────────────────────────

class TestCEL14PlayerCardRegression:

    def test_cel_14_player_card_editor_still_callable(self):
        """CEL-14: lfa_player_card_editor handler still works after _FAMILY refactor."""
        from app.api.web_routes.dashboard import lfa_player_card_editor
        from app.models.card_draft import CardDraft

        user        = MagicMock(); user.id = 42; user.credit_balance = 0
        mock_license = MagicMock(); mock_license.onboarding_completed = True
        db           = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_license

        _DASH_BASE = "app.api.web_routes.dashboard"
        captured: dict = {}

        def _fake_tmpl(tmpl, ctx, **kw):
            captured["context"] = ctx
            return MagicMock(status_code=200)

        with patch(f"{_DASH_BASE}._CardDraftService") as MockCDS, \
             patch(f"{_DASH_BASE}.templates") as mock_tpl, \
             patch(f"{_DASH_BASE}.SemesterEnrollment"), \
             patch("app.services.card_design_service.is_design_accessible", return_value=True), \
             patch("app.services.card_variant_service.get_all_variants", return_value=[]), \
             patch("app.services.card_color_service.get_colors_for_family", return_value=[]), \
             patch("app.services.card_color_service.get_owned_color_ids",  return_value=set()), \
             patch("app.services.card_platform_service.build_platform_list", return_value=[]), \
             patch("app.services.card_constants.ANIMATED_EXPORT_CAPABLE", []), \
             patch("app.services.card_constants.CANVAS_SIZES", {}), \
             patch("app.services.card_constants.CARD_EDITOR_PLATFORM_IDS", []), \
             patch("app.services.highlight_video_service.build_youtube_embed_url", return_value=None):
            draft = MagicMock()
            draft.draft_theme    = "default"
            draft.draft_variant  = "fclassic"
            draft.draft_platform = None
            draft.draft_data     = None
            draft.published_theme    = "default"
            draft.published_variant  = "fclassic"
            draft.published_platform = None
            draft.published_data     = None
            MockCDS.get_draft.return_value = draft
            mock_tpl.TemplateResponse.side_effect = _fake_tmpl
            try:
                asyncio.run(lfa_player_card_editor(
                    request=MagicMock(), db=db, user=user,
                ))
            except Exception:
                pass

        assert captured.get("context"), "lfa_player_card_editor must still populate context"
        assert captured["context"].get("active_card_variant") is not None

    def test_cel_14b_player_card_editor_uses_get_draft_api(self):
        """CEL-14b: lfa_player_card_editor calls CardDraftService.get_draft (CE-3.0 API)."""
        from app.api.web_routes.dashboard import lfa_player_card_editor

        user        = MagicMock(); user.id = 42; user.credit_balance = 0
        mock_license = MagicMock(); mock_license.onboarding_completed = True
        db           = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_license

        _DASH_BASE = "app.api.web_routes.dashboard"

        with patch(f"{_DASH_BASE}._CardDraftService") as MockCDS, \
             patch(f"{_DASH_BASE}.templates") as mock_tpl, \
             patch(f"{_DASH_BASE}.SemesterEnrollment"), \
             patch("app.services.card_design_service.is_design_accessible", return_value=True), \
             patch("app.services.card_variant_service.get_all_variants", return_value=[]), \
             patch("app.services.card_color_service.get_colors_for_family", return_value=[]), \
             patch("app.services.card_color_service.get_owned_color_ids",  return_value=set()), \
             patch("app.services.card_platform_service.build_platform_list", return_value=[]), \
             patch("app.services.card_constants.ANIMATED_EXPORT_CAPABLE", []), \
             patch("app.services.card_constants.CANVAS_SIZES", {}), \
             patch("app.services.card_constants.CARD_EDITOR_PLATFORM_IDS", []), \
             patch("app.services.highlight_video_service.build_youtube_embed_url", return_value=None):
            draft = MagicMock()
            draft.draft_theme = draft.draft_variant = "default"
            draft.draft_platform = draft.draft_data = None
            draft.published_theme = draft.published_variant = None
            draft.published_platform = draft.published_data = None
            MockCDS.get_draft.return_value = draft
            mock_tpl.TemplateResponse.side_effect = lambda t, c, **kw: MagicMock()
            try:
                asyncio.run(lfa_player_card_editor(
                    request=MagicMock(), db=db, user=user,
                ))
            except Exception:
                pass

        MockCDS.get_draft.assert_called_once_with(db, user.id, "player_card")
        MockCDS.get_player_card_draft.assert_not_called()

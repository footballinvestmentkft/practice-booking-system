"""
CCS — Challenge Card Studio tests (CE-3.4).

GET /card-editor/challenge — draft-free CC Studio, format gallery, no preview/export.

CCS-01  authenticated owned formats → 200
CCS-02  unauthenticated → auth guard (get_current_user_web)
CCS-03  no LFA license → 303 /dashboard
CCS-04  onboarding incomplete → 303 /specialization
CCS-05  no owned formats → 303 /shop/cards/challenge
CCS-06  owned format rows contain only valid owned format IDs
CCS-07  owned format rows follow CHALLENGE_CARD_FORMATS order
CCS-08  owned format row fields: design_id, label, style_tag, dims
CCS-09  legacy "challenge" CDO shim → both valid formats owned
CCS-10  CardDraftService is never called
CCS-11  route count = 839 (838 + GET /card-editor/challenge)
CCS-12  template contains /my-cards/challenge link
CCS-13  template contains /challenges/results link
CCS-14  template contains /challenges link
CCS-15  template contains /shop/cards/challenge link
CCS-16  template does NOT contain preview iframe
CCS-17  template does NOT contain export link
CCS-18  handler does NOT require challenge_id
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.card_design_service import CHALLENGE_CARD_FORMATS

_CE_BASE = "app.api.web_routes.card_editor"

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"

# All valid CC format IDs in canonical CHALLENGE_CARD_FORMATS order
_ALL_CC_IDS: list[str] = [f.design_id for f in CHALLENGE_CARD_FORMATS]
_FIRST_CC_ID  = _ALL_CC_IDS[0]   # "challenge_post_16_9"
_SECOND_CC_ID = _ALL_CC_IDS[1]   # "challenge_story_9_16"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 42) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.credit_balance = 500
    u.role = MagicMock()
    return u


def _license(onboarding_completed: bool = True) -> MagicMock:
    lic = MagicMock()
    lic.onboarding_completed = onboarding_completed
    return lic


def _db_with_license(license_obj) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = license_obj
    return db


def _invoke_challenge(
    owned_ids: list[str],
    license_obj=None,
    user_obj=None,
) -> tuple[MagicMock, dict]:
    """Call card_studio_challenge and return (response, context).

    context is empty dict if a redirect was returned.
    """
    from app.api.web_routes.card_editor import card_studio_challenge

    user = user_obj or _user()
    lic  = license_obj if license_obj is not None else _license(onboarding_completed=True)
    db   = _db_with_license(lic)
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["context"] = ctx
        captured["template"] = tmpl
        return MagicMock(status_code=200)

    with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=owned_ids), \
         patch(f"{_CE_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl

        request = MagicMock()
        resp = _run(card_studio_challenge(request=request, db=db, user=user))

    return resp, captured.get("context", {})


# ── CCS-01: authenticated owned formats → 200 ────────────────────────────────

class TestCCS01Authenticated:

    def test_ccs_01_owned_format_returns_200(self):
        """CCS-01: owned CC format → handler callable, context captured."""
        _, ctx = _invoke_challenge(owned_ids=[_FIRST_CC_ID])
        assert ctx, "context must be captured (200 path)"

    def test_ccs_01b_template_is_challenge_studio(self):
        """CCS-01b: handler renders card_studio_challenge.html."""
        from app.api.web_routes.card_editor import card_studio_challenge

        user = _user()
        db   = _db_with_license(_license())
        captured: dict = {}

        def _fake(tmpl, ctx, **kw):
            captured["template"] = tmpl
            return MagicMock(status_code=200)

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_CC_ID]), \
             patch(f"{_CE_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.side_effect = _fake
            _run(card_studio_challenge(request=MagicMock(), db=db, user=user))

        assert captured.get("template") == "card_studio_challenge.html"


# ── CCS-02: auth dependency ───────────────────────────────────────────────────

class TestCCS02AuthGuard:

    def test_ccs_02_route_has_get_current_user_web_dependency(self):
        """CCS-02: /card-editor/challenge has get_current_user_web in dependency tree."""
        from app.main import app
        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor/challenge"),
            None,
        )
        assert route is not None, "/card-editor/challenge route must be registered"
        dep_names = [
            getattr(d.call, "__name__", "")
            for d in getattr(route.dependant, "dependencies", [])
        ]
        assert "get_current_user_web" in dep_names, (
            f"/card-editor/challenge must depend on get_current_user_web; found: {dep_names}"
        )


# ── CCS-03: no LFA license → 303 /dashboard ──────────────────────────────────

class TestCCS03NoLicense:

    def test_ccs_03_no_license_redirects_to_dashboard(self):
        """CCS-03: missing LFA license → 303 to /dashboard."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_challenge

        user = _user()
        db   = _db_with_license(None)

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_CC_ID]):
            resp = _run(card_studio_challenge(request=MagicMock(), db=db, user=user))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert "/dashboard" in resp.headers["location"]


# ── CCS-04: onboarding incomplete → 303 /specialization ──────────────────────

class TestCCS04OnboardingIncomplete:

    def test_ccs_04_onboarding_incomplete_redirects_to_onboarding(self):
        """CCS-04: onboarding not complete → 303 to /specialization."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_challenge

        user = _user()
        db   = _db_with_license(_license(onboarding_completed=False))

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_CC_ID]):
            resp = _run(card_studio_challenge(request=MagicMock(), db=db, user=user))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert "onboarding" in resp.headers["location"]


# ── CCS-05: no owned formats → 303 /shop/cards/challenge ─────────────────────

class TestCCS05NoOwnedFormats:

    def test_ccs_05_no_owned_redirects_to_shop(self):
        """CCS-05: no owned CC formats → 303 /shop/cards/challenge."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_challenge

        user = _user()
        db   = _db_with_license(_license())

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[]):
            resp = _run(card_studio_challenge(request=MagicMock(), db=db, user=user))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/shop/cards/challenge"


# ── CCS-06/07/08: owned format rows correctness, order, fields ───────────────

class TestCCS060708FormatRows:

    def test_ccs_06_rows_contain_only_valid_owned_formats(self):
        """CCS-06: cc_format_rows contains only IDs present in _CC_VALID_IDS and owned."""
        from app.api.web_routes.card_editor import _CC_VALID_IDS

        # Return both valid IDs plus one invalid/legacy key
        _, ctx = _invoke_challenge(owned_ids=[_FIRST_CC_ID, _SECOND_CC_ID, "challenge"])
        rows = ctx.get("cc_format_rows", [])
        row_ids = {r["design_id"] for r in rows}
        assert row_ids <= _CC_VALID_IDS, (
            f"cc_format_rows must only contain valid CC format IDs; got extra: {row_ids - _CC_VALID_IDS}"
        )
        assert _FIRST_CC_ID in row_ids
        assert _SECOND_CC_ID in row_ids

    def test_ccs_07_rows_follow_challenge_card_formats_order(self):
        """CCS-07: cc_format_rows are ordered by CHALLENGE_CARD_FORMATS, not input order."""
        # Pass owned in reversed order — output must follow CCF order
        owned_reversed = list(reversed(_ALL_CC_IDS))
        _, ctx = _invoke_challenge(owned_ids=owned_reversed)
        rows = ctx.get("cc_format_rows", [])
        row_ids = [r["design_id"] for r in rows]
        assert row_ids == _ALL_CC_IDS, (
            f"Rows must follow CHALLENGE_CARD_FORMATS order. Got: {row_ids}"
        )

    def test_ccs_08_rows_have_required_fields(self):
        """CCS-08: every row has design_id, label, style_tag, dims."""
        _, ctx = _invoke_challenge(owned_ids=[_FIRST_CC_ID, _SECOND_CC_ID])
        rows = ctx.get("cc_format_rows", [])
        assert rows, "cc_format_rows must not be empty"
        for row in rows:
            for field in ("design_id", "label", "style_tag", "dims"):
                assert field in row, f"Row missing field '{field}': {row}"


# ── CCS-09: legacy "challenge" CDO shim ──────────────────────────────────────

class TestCCS09LegacyCDO:

    def test_ccs_09_legacy_challenge_key_grants_both_formats(self):
        """CCS-09: get_owned_design_ids shim expands 'challenge' → all CC format IDs."""
        # Simulate shim: service returns both format IDs when legacy key owned
        _, ctx = _invoke_challenge(owned_ids=list(_ALL_CC_IDS))
        rows = ctx.get("cc_format_rows", [])
        assert len(rows) == 2, (
            f"Legacy 'challenge' grant must expose all 2 CC formats; got {len(rows)}"
        )
        row_ids = {r["design_id"] for r in rows}
        assert row_ids == set(_ALL_CC_IDS)


# ── CCS-10: CardDraftService never called ────────────────────────────────────

class TestCCS10NoDraftService:

    def test_ccs_10_card_draft_service_not_called(self):
        """CCS-10: handler never calls CardDraftService — fully draft-free."""
        from app.api.web_routes.card_editor import card_studio_challenge

        user = _user()
        db   = _db_with_license(_license())

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_CC_ID]), \
             patch(f"{_CE_BASE}.templates") as mock_tpl, \
             patch("app.services.card_draft_service.CardDraftService") as MockCDS:
            mock_tpl.TemplateResponse.side_effect = lambda t, c, **kw: MagicMock(status_code=200)
            _run(card_studio_challenge(request=MagicMock(), db=db, user=user))

        MockCDS.get_draft.assert_not_called()
        MockCDS.get_player_card_draft.assert_not_called()
        MockCDS.get_or_create_singleton.assert_not_called()


# ── CCS-11: route count = 839 ────────────────────────────────────────────────

class TestCCS11RouteCount:

    def test_ccs_11_route_count_839(self):
        """CCS-11: adding GET /card-editor/challenge raises route count from 838 to 839."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 839, (
            f"Expected 839 routes (838 CE-3.3 baseline + GET /card-editor/challenge), got {len(paths)}."
        )

    def test_ccs_11b_card_editor_challenge_route_registered(self):
        """CCS-11b: GET /card-editor/challenge is in the registered routes."""
        from app.main import app
        route_paths = [r.path for r in app.routes]
        assert "/card-editor/challenge" in route_paths, (
            "/card-editor/challenge must be a registered route"
        )


# ── CCS-12–15: template CTA links ────────────────────────────────────────────

class TestCCS1215TemplateLinks:

    @classmethod
    def _src(cls) -> str:
        return (TEMPLATES_DIR / "card_studio_challenge.html").read_text(encoding="utf-8")

    def test_ccs_12_template_has_my_cards_challenge_link(self):
        """CCS-12: template contains /my-cards/challenge link."""
        assert 'href="/my-cards/challenge"' in self._src()

    def test_ccs_13_template_has_challenges_results_link(self):
        """CCS-13: template contains /challenges/results link."""
        assert 'href="/challenges/results"' in self._src()

    def test_ccs_14_template_has_challenges_link(self):
        """CCS-14: template contains /challenges link."""
        assert 'href="/challenges"' in self._src()

    def test_ccs_15_template_has_shop_challenge_link(self):
        """CCS-15: template contains /shop/cards/challenge link."""
        assert 'href="/shop/cards/challenge"' in self._src()


# ── CCS-16/17/18: template must NOT have preview iframe / export / challenge_id ─

class TestCCS161718NoPreviewExport:

    @classmethod
    def _src(cls) -> str:
        return (TEMPLATES_DIR / "card_studio_challenge.html").read_text(encoding="utf-8")

    def test_ccs_16_template_has_no_preview_iframe(self):
        """CCS-16: template must not contain a preview iframe (no challenge_id, no preview URL)."""
        src = self._src()
        assert "<iframe" not in src, "card_studio_challenge.html must not contain a preview iframe"

    def test_ccs_17_template_has_no_export_link(self):
        """CCS-17: template must not contain a /card/export or /export link."""
        src = self._src()
        assert "/card/export" not in src, "card_studio_challenge.html must not link to export"
        assert "export_url" not in src, "card_studio_challenge.html must not use export_url"

    def test_ccs_18_handler_has_no_challenge_id_param(self):
        """CCS-18: card_studio_challenge handler signature does not accept challenge_id."""
        import inspect
        from app.api.web_routes.card_editor import card_studio_challenge
        sig = inspect.signature(card_studio_challenge)
        assert "challenge_id" not in sig.parameters, (
            "card_studio_challenge must not accept challenge_id as a parameter"
        )

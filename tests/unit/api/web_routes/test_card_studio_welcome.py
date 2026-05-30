"""
CEW — Welcome Card Studio tests (CE-3.3).

GET /card-editor/welcome — draft-free WC Studio with ?format= query param.

CEW-01  authenticated valid owned ?format → 200
CEW-02  unauthenticated → auth guard (get_current_user_web)
CEW-03  no LFA license → 303 /dashboard
CEW-04  onboarding incomplete → 303 /specialization
CEW-05  no owned formats → 303 /shop/cards/welcome
CEW-06  no ?format param → 303 canonical first owned URL
CEW-07  invalid ?format param (unknown format) → 303 canonical first owned URL
CEW-08  unowned ?format param → 303 canonical first owned URL
CEW-09  valid owned ?format → active_format in context matches param
CEW-10  owned_format_rows contains only owned format IDs
CEW-11  owned_format_rows respects WELCOME_CARD_FORMATS order
CEW-12  preview_url = /profile/onboarding-card?platform={active_format}
CEW-13  export_url = /profile/onboarding-card/export?platform={active_format}
CEW-14  template contains format selector (owned_format_rows present)
CEW-15  legacy "default" CDO bulk-grant: all 7 valid formats visible
CEW-16  CardDraftService is never called
CEW-17  /card-editor/welcome/{format_id} WCE-1 route unchanged
CEW-18  route count = 838 (837 + GET /card-editor/welcome)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.card_design_service import WELCOME_CARD_FORMATS

_CE_BASE = "app.api.web_routes.card_editor"

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"

# All valid WC format IDs in canonical WELCOME_CARD_FORMATS order
_ALL_WC_IDS: list[str] = [f.design_id for f in WELCOME_CARD_FORMATS]
# One known-valid owned ID used for most tests
_FIRST_ID = _ALL_WC_IDS[0]   # "instagram_portrait"
_SECOND_ID = _ALL_WC_IDS[1]  # "instagram_story"


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _invoke_welcome(
    format_param: str | None,
    owned_ids: list[str],
    license_obj=None,
    user_obj=None,
) -> tuple[MagicMock, dict, MagicMock]:
    """
    Call card_studio_welcome and return (response, context, mock_cds).
    context is empty dict if a redirect was returned.
    """
    from app.api.web_routes.card_editor import card_studio_welcome

    user = user_obj or _user()
    lic  = license_obj if license_obj is not None else _license(onboarding_completed=True)
    db   = _db_with_license(lic)
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["context"] = ctx
        return MagicMock(status_code=200)

    with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=owned_ids), \
         patch(f"{_CE_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl

        request = MagicMock()
        resp = _run(card_studio_welcome(
            request=request,
            format_id=format_param,
            db=db,
            user=user,
        ))

    return resp, captured.get("context", {}), None


# ── CEW-01: authenticated valid owned format → 200 ───────────────────────────

class TestCEW01Authenticated:

    def test_cew_01_valid_owned_format_returns_200(self):
        """CEW-01: valid owned ?format → handler callable, context captured."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert ctx, "context must be captured (200 path)"

    def test_cew_01_template_is_welcome_studio(self):
        """CEW-01: handler renders card_studio_welcome.html."""
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        lic  = _license(onboarding_completed=True)
        db   = _db_with_license(lic)
        captured: dict = {}

        def _fake(tmpl, ctx, **kw):
            captured["template"] = tmpl
            return MagicMock(status_code=200)

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]), \
             patch(f"{_CE_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.side_effect = _fake
            _run(card_studio_welcome(
                request=MagicMock(), format_id=_FIRST_ID, db=db, user=user,
            ))

        assert captured.get("template") == "card_studio_welcome.html"


# ── CEW-02: auth dependency on route ─────────────────────────────────────────

class TestCEW02AuthGuard:

    def test_cew_02_route_has_get_current_user_web_dependency(self):
        """CEW-02: /card-editor/welcome has get_current_user_web in dependant tree."""
        from app.main import app
        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor/welcome"),
            None,
        )
        assert route is not None, "/card-editor/welcome route must be registered"
        dep_names = [
            getattr(d.call, "__name__", "")
            for d in getattr(route.dependant, "dependencies", [])
        ]
        assert "get_current_user_web" in dep_names, (
            f"/card-editor/welcome must depend on get_current_user_web; found: {dep_names}"
        )


# ── CEW-03: no LFA license → 303 /dashboard ──────────────────────────────────

class TestCEW03NoLicense:

    def test_cew_03_no_license_redirects_to_dashboard(self):
        """CEW-03: missing LFA license → 303 to /dashboard."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        db   = _db_with_license(None)   # no license row

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=_FIRST_ID, db=db, user=user,
            ))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert "/dashboard" in resp.headers["location"]


# ── CEW-04: onboarding incomplete → 303 /specialization ──────────────────────

class TestCEW04OnboardingIncomplete:

    def test_cew_04_onboarding_incomplete_redirects_to_onboarding(self):
        """CEW-04: onboarding not complete → 303 to /specialization."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license(onboarding_completed=False))

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=_FIRST_ID, db=db, user=user,
            ))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert "onboarding" in resp.headers["location"]


# ── CEW-05: no owned formats → 303 /shop/cards/welcome ───────────────────────

class TestCEW05NoOwnedFormats:

    def test_cew_05_no_owned_redirects_to_shop(self):
        """CEW-05: no owned WC formats → 303 /shop/cards/welcome."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license())

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=None, db=db, user=user,
            ))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/shop/cards/welcome"


# ── CEW-06: no ?format → 303 canonical first owned ───────────────────────────

class TestCEW06NoFormatParam:

    def test_cew_06_no_format_param_redirects_canonical(self):
        """CEW-06: absent ?format → 303 /card-editor/welcome?format={first_owned}."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license())

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_ID, _SECOND_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=None, db=db, user=user,
            ))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/card-editor/welcome?format={_FIRST_ID}"

    def test_cew_06b_canonical_uses_first_in_welcome_card_formats_order(self):
        """CEW-06b: first_owned = first in WELCOME_CARD_FORMATS order, not alphabetical."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        # Own only the second and third format — first_owned should be second by WCF order
        owned = [_ALL_WC_IDS[1], _ALL_WC_IDS[2]]

        user = _user()
        db   = _db_with_license(_license())

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=owned):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=None, db=db, user=user,
            ))

        assert isinstance(resp, RedirectResponse)
        assert f"format={_ALL_WC_IDS[1]}" in resp.headers["location"]


# ── CEW-07: invalid ?format → 303 canonical ──────────────────────────────────

class TestCEW07InvalidFormat:

    def test_cew_07_unknown_format_id_redirects_canonical(self):
        """CEW-07: unknown ?format value → 303 canonical first owned."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license())

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id="totally_fake_format", db=db, user=user,
            ))

        assert isinstance(resp, RedirectResponse)
        assert f"format={_FIRST_ID}" in resp.headers["location"]


# ── CEW-08: unowned ?format → 303 canonical ──────────────────────────────────

class TestCEW08UnownedFormat:

    def test_cew_08_unowned_valid_format_id_redirects_canonical(self):
        """CEW-08: valid format_id but not owned → 303 canonical first owned."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license())

        # User only owns _FIRST_ID, requests _SECOND_ID
        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=_SECOND_ID, db=db, user=user,
            ))

        assert isinstance(resp, RedirectResponse)
        assert f"format={_FIRST_ID}" in resp.headers["location"]


# ── CEW-09: active_format context ────────────────────────────────────────────

class TestCEW09ActiveFormatContext:

    def test_cew_09_active_format_matches_param(self):
        """CEW-09: context active_format equals the ?format param."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID, _SECOND_ID])
        assert ctx.get("active_format") == _FIRST_ID

    def test_cew_09b_fmt_object_matches_active_format(self):
        """CEW-09b: context fmt.design_id matches active_format."""
        _, ctx, _ = _invoke_welcome(_SECOND_ID, owned_ids=[_FIRST_ID, _SECOND_ID])
        assert ctx.get("active_format") == _SECOND_ID
        fmt = ctx.get("fmt")
        assert fmt is not None
        assert fmt.design_id == _SECOND_ID


# ── CEW-10/11: owned_format_rows correctness and order ───────────────────────

class TestCEW1011OwnedFormatRows:

    def test_cew_10_owned_format_rows_contains_only_owned(self):
        """CEW-10: owned_format_rows contains only IDs from the owned set."""
        # User owns first and third, not second
        owned = [_ALL_WC_IDS[0], _ALL_WC_IDS[2]]
        _, ctx, _ = _invoke_welcome(_ALL_WC_IDS[0], owned_ids=owned)

        rows = ctx.get("owned_format_rows", [])
        row_ids = [r["design_id"] for r in rows]
        assert _ALL_WC_IDS[0] in row_ids
        assert _ALL_WC_IDS[2] in row_ids
        assert _ALL_WC_IDS[1] not in row_ids, "Unowned format must not appear in rows"

    def test_cew_11_owned_format_rows_follow_welcome_card_formats_order(self):
        """CEW-11: owned_format_rows are ordered by WELCOME_CARD_FORMATS, not by input list."""
        # Pass owned in reversed order — output must still be WCF order
        owned_reversed = list(reversed(_ALL_WC_IDS[:3]))
        _, ctx, _ = _invoke_welcome(_ALL_WC_IDS[0], owned_ids=owned_reversed)

        rows = ctx.get("owned_format_rows", [])
        row_ids = [r["design_id"] for r in rows]
        # Expected: [_ALL_WC_IDS[0], _ALL_WC_IDS[1], _ALL_WC_IDS[2]] in WCF order
        assert row_ids == _ALL_WC_IDS[:3], (
            f"Rows must follow WELCOME_CARD_FORMATS order. Got: {row_ids}"
        )

    def test_cew_11b_active_row_is_marked(self):
        """CEW-11b: owned_format_rows marks exactly the active format as active=True."""
        _, ctx, _ = _invoke_welcome(_SECOND_ID, owned_ids=[_FIRST_ID, _SECOND_ID])
        rows = ctx.get("owned_format_rows", [])
        active_ids = [r["design_id"] for r in rows if r.get("active")]
        assert active_ids == [_SECOND_ID], (
            f"Exactly one row must be active={_SECOND_ID!r}; active rows: {active_ids}"
        )


# ── CEW-12/13: preview_url and export_url ────────────────────────────────────

class TestCEW1213URLs:

    def test_cew_12_preview_url_uses_active_format(self):
        """CEW-12: preview_url = /profile/onboarding-card?platform={active_format}."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert ctx.get("preview_url") == f"/profile/onboarding-card?platform={_FIRST_ID}"

    def test_cew_13_export_url_uses_active_format(self):
        """CEW-13: export_url = /profile/onboarding-card/export?platform={active_format}."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert ctx.get("export_url") == f"/profile/onboarding-card/export?platform={_FIRST_ID}"

    def test_cew_12b_preview_and_export_change_with_format(self):
        """CEW-12b: preview_url and export_url reflect the selected format."""
        _, ctx, _ = _invoke_welcome(_SECOND_ID, owned_ids=[_FIRST_ID, _SECOND_ID])
        assert ctx.get("preview_url") == f"/profile/onboarding-card?platform={_SECOND_ID}"
        assert ctx.get("export_url") == f"/profile/onboarding-card/export?platform={_SECOND_ID}"


# ── CEW-14: template has format selector ─────────────────────────────────────

class TestCEW14Template:

    def test_cew_14_template_has_format_selector(self):
        """CEW-14: card_studio_welcome.html contains format selector block."""
        src = (TEMPLATES_DIR / "card_studio_welcome.html").read_text(encoding="utf-8")
        assert "wcs-format-selector" in src
        assert "owned_format_rows" in src

    def test_cew_14b_template_has_preview_and_export(self):
        """CEW-14b: template has preview iframe and export link."""
        src = (TEMPLATES_DIR / "card_studio_welcome.html").read_text(encoding="utf-8")
        assert "preview_url" in src
        assert "export_url" in src
        assert "mfg-preview-iframe" in src
        assert "wcs-btn-download" in src

    def test_cew_14c_template_has_nav_links(self):
        """CEW-14c: template links back to /my-cards/welcome and /shop/cards/welcome."""
        src = (TEMPLATES_DIR / "card_studio_welcome.html").read_text(encoding="utf-8")
        assert 'href="/my-cards/welcome"' in src
        assert 'href="/shop/cards/welcome"' in src


# ── CEW-15: legacy "default" CDO bulk-grant ──────────────────────────────────

class TestCEW15LegacyDefaultGrant:

    def test_cew_15_default_cdo_grants_all_7_formats(self):
        """CEW-15: if 'default' is owned, all 7 WC formats appear in owned_format_rows."""
        # get_owned_design_ids already handles the shim; simulate by returning all IDs
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=list(_ALL_WC_IDS))
        rows = ctx.get("owned_format_rows", [])
        assert len(rows) == 7, (
            f"Legacy 'default' grant must expose all 7 formats; got {len(rows)}"
        )


# ── CEW-16: CardDraftService never called ────────────────────────────────────

class TestCEW16NoDraftService:

    def test_cew_16_card_draft_service_not_called(self):
        """CEW-16: handler never calls CardDraftService — fully draft-free."""
        from app.api.web_routes.card_editor import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license())

        with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]), \
             patch(f"{_CE_BASE}.templates") as mock_tpl, \
             patch("app.services.card_draft_service.CardDraftService") as MockCDS:
            mock_tpl.TemplateResponse.side_effect = lambda t, c, **kw: MagicMock(status_code=200)
            _run(card_studio_welcome(
                request=MagicMock(), format_id=_FIRST_ID, db=db, user=user,
            ))

        MockCDS.get_draft.assert_not_called()
        MockCDS.get_player_card_draft.assert_not_called()
        MockCDS.get_or_create_singleton.assert_not_called()


# ── CEW-17: WCE-1 route unchanged ────────────────────────────────────────────

class TestCEW17WCE1Unchanged:

    def test_cew_17_wce1_route_still_registered(self):
        """CEW-17: /card-editor/welcome/{format_id} WCE-1 route is still registered."""
        from app.main import app
        paths = [r.path for r in app.routes]
        assert "/card-editor/welcome/{format_id}" in paths, (
            "WCE-1 route /card-editor/welcome/{format_id} must remain registered"
        )

    def test_cew_17b_wce1_handler_is_welcome_card_editor(self):
        """CEW-17b: /card-editor/welcome/{format_id} maps to welcome_card_editor function."""
        from app.main import app
        from app.api.web_routes.card_editor import welcome_card_editor
        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor/welcome/{format_id}"),
            None,
        )
        assert route is not None
        assert route.endpoint is welcome_card_editor


# ── CEW-18: route count = 838 ────────────────────────────────────────────────

class TestCEW18RouteCount:

    def test_cew_18_route_count_838(self):
        """CEW-18: adding GET /card-editor/welcome raises route count from 837 to 838."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 839, (
            f"Expected 839 routes (837 CE-3.2 baseline + GET /card-editor/welcome + GET /card-editor/challenge), got {len(paths)}."
        )

    def test_cew_18b_card_editor_welcome_route_registered(self):
        """CEW-18b: GET /card-editor/welcome is in the registered routes."""
        from app.main import app
        route_paths = [r.path for r in app.routes]
        assert "/card-editor/welcome" in route_paths, (
            "/card-editor/welcome must be a registered route"
        )

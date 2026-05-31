"""
CEW — Welcome Card Studio tests (CE-3.3, CE-3.7).

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
CEW-18  route count = 839
CEW-19  context contains wc_photo_url key (CE-3.7)
CEW-20  context contains wc_photo_portrait_url key (CE-3.7)
CEW-21  context contains wc_photo_landscape_url key (CE-3.7)
CEW-22  context photo URLs reflect license object values (CE-3.7)
CEW-23  template contains /dashboard/wc-photo upload route (CE-3.7)
CEW-24  template contains /dashboard/wc-photo/delete route (CE-3.7)
CEW-25  template contains /dashboard/wc-photo-portrait upload route (CE-3.7)
CEW-26  template contains /dashboard/wc-photo-portrait/delete route (CE-3.7)
CEW-27  template contains /dashboard/wc-photo-landscape upload route (CE-3.7)
CEW-28  template contains /dashboard/wc-photo-landscape/delete route (CE-3.7)
CEW-29  template contains preview iframe reload JS (CE-3.7)
CEW-30  template does NOT contain BG removal reference (CE-3.7)
CEW-31  template does NOT contain mood photo reference (CE-3.7)
CEW-32  template contains X-CSRF-Token header reference (CE-3.7 CSRF fix)
CEW-33  template reads csrf_token cookie to obtain CSRF token (CE-3.7 CSRF fix)
CEW-34  upload fetch() block carries X-CSRF-Token header (CE-3.7 CSRF fix)
CEW-35  delete fetch() block carries X-CSRF-Token header (CE-3.7 CSRF fix)
CEW-36  upload FormData fetch has no explicit Content-Type header (CE-3.7 CSRF fix)
CEW-37  card_studio_welcome context contains mood_photos key (CE-3.8)
CEW-38  mood_photos dict contains all 6 MOOD_PHOTO_SLOTS keys (CE-3.8)
CEW-39  GET /dashboard/wc-photo/from-mood is not a registered GET route (CE-3.8)
CEW-40  POST /dashboard/wc-photo/from-mood valid mood_slot → ok + photo_url (CE-3.8)
CEW-41  POST /dashboard/wc-photo/from-mood invalid mood_slot → 422 (CE-3.8)
CEW-42  POST /dashboard/wc-photo/from-mood mood photo not found → 404 (CE-3.8)
CEW-43  portrait and landscape from-mood endpoints assign correct license fields (CE-3.8)
CEW-44  template contains Mood Photo picker UI elements — wcs-mood-chip (CE-3.8 corrected)
CEW-44b template contains wcs-mood-chip--empty class (CE-3.8 corrected)
CEW-44c template contains wcs-mood-chip--selected class (CE-3.8 corrected)
CEW-44d template contains disabled + aria-disabled logic for empty slots (CE-3.8 corrected)
CEW-44e template contains emoji from mood_slot_meta (CE-3.8 corrected)
CEW-38c card_studio_welcome context contains mood_slot_meta key (CE-3.8 corrected)
CEW-38d mood_slot_meta has 6 entries with slot/emoji/label (CE-3.8 corrected)
CEW-45  template references all three /from-mood routes (CE-3.8)
CEW-46  template contains link to /profile/my-mood-photos (CE-3.8)
CEW-47  route count = 842 (CE-3.8 +3 routes)
CEW-48  assign JS fetch carries X-CSRF-Token header (CE-3.8)
CEW-49  assign JS missing CSRF guard present (CE-3.8)
CEW-50  template does NOT contain BG removal reference (CE-3.8 scope guard)
CEW-51  template does NOT use processed_png_url (CE-3.8 scope guard)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.card_design_service import WELCOME_CARD_FORMATS
from app.models.user_mood_photos import MOOD_PHOTO_SLOTS

_CE_BASE = "app.api.web_routes.card_editor"

# Default mood_photos stub: all 6 slots empty (no photos uploaded).
_EMPTY_MOOD_PHOTOS: dict = {slot: None for slot in MOOD_PHOTO_SLOTS}

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
    mood_photos: dict | None = None,
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

    _mp = mood_photos if mood_photos is not None else _EMPTY_MOOD_PHOTOS
    with patch(f"{_CE_BASE}.get_owned_design_ids", return_value=owned_ids), \
         patch(f"{_CE_BASE}.get_mood_photos_for_user", return_value=_mp), \
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
        """CEW-18: route count baseline check (updated by CE-3.8 to 842)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 844, (
            f"Expected 842 routes (839 CE-3.7 baseline + 3 from-mood endpoints), got {len(paths)}."
        )

    def test_cew_18b_card_editor_welcome_route_registered(self):
        """CEW-18b: GET /card-editor/welcome is in the registered routes."""
        from app.main import app
        route_paths = [r.path for r in app.routes]
        assert "/card-editor/welcome" in route_paths, (
            "/card-editor/welcome must be a registered route"
        )


# ── CEW-19/20/21/22: WC photo context keys (CE-3.7) ─────────────────────────

class TestCEW19to22PhotoContext:

    def test_cew_19_context_has_wc_photo_url(self):
        """CEW-19: handler context contains wc_photo_url key."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "wc_photo_url" in ctx, "context must contain wc_photo_url"

    def test_cew_20_context_has_wc_photo_portrait_url(self):
        """CEW-20: handler context contains wc_photo_portrait_url key."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "wc_photo_portrait_url" in ctx, "context must contain wc_photo_portrait_url"

    def test_cew_21_context_has_wc_photo_landscape_url(self):
        """CEW-21: handler context contains wc_photo_landscape_url key."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "wc_photo_landscape_url" in ctx, "context must contain wc_photo_landscape_url"

    def test_cew_22_photo_urls_reflect_license_values(self):
        """CEW-22: context photo URL values are read from the license object."""
        lic = _license()
        lic.wc_photo_url          = "https://example.com/wc.jpg"
        lic.wc_photo_portrait_url = "https://example.com/portrait.jpg"
        lic.wc_photo_landscape_url = "https://example.com/landscape.jpg"

        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID], license_obj=lic)

        assert ctx.get("wc_photo_url")           == "https://example.com/wc.jpg"
        assert ctx.get("wc_photo_portrait_url")  == "https://example.com/portrait.jpg"
        assert ctx.get("wc_photo_landscape_url") == "https://example.com/landscape.jpg"


# ── CEW-23–28: template upload / delete route references (CE-3.7) ────────────

class TestCEW23to28TemplateUploadRoutes:

    @classmethod
    def _src(cls) -> str:
        return (TEMPLATES_DIR / "card_studio_welcome.html").read_text(encoding="utf-8")

    def test_cew_23_template_has_wc_photo_upload(self):
        """CEW-23: template references /dashboard/wc-photo upload route."""
        assert "/dashboard/wc-photo'" in self._src() or "/dashboard/wc-photo\"" in self._src() \
               or "'/dashboard/wc-photo'" in self._src() or '"/dashboard/wc-photo"' in self._src() \
               or "wc-photo'" in self._src()

    def test_cew_24_template_has_wc_photo_delete(self):
        """CEW-24: template references /dashboard/wc-photo/delete route."""
        assert "/dashboard/wc-photo/delete" in self._src()

    def test_cew_25_template_has_wc_photo_portrait_upload(self):
        """CEW-25: template references /dashboard/wc-photo-portrait upload route."""
        assert "/dashboard/wc-photo-portrait'" in self._src() or \
               "/dashboard/wc-photo-portrait\"" in self._src() or \
               "wc-photo-portrait'" in self._src()

    def test_cew_26_template_has_wc_photo_portrait_delete(self):
        """CEW-26: template references /dashboard/wc-photo-portrait/delete route."""
        assert "/dashboard/wc-photo-portrait/delete" in self._src()

    def test_cew_27_template_has_wc_photo_landscape_upload(self):
        """CEW-27: template references /dashboard/wc-photo-landscape upload route."""
        assert "/dashboard/wc-photo-landscape'" in self._src() or \
               "/dashboard/wc-photo-landscape\"" in self._src() or \
               "wc-photo-landscape'" in self._src()

    def test_cew_28_template_has_wc_photo_landscape_delete(self):
        """CEW-28: template references /dashboard/wc-photo-landscape/delete route."""
        assert "/dashboard/wc-photo-landscape/delete" in self._src()


# ── CEW-29–31: template JS reload + no BG removal + no mood photo (CE-3.7) ──

class TestCEW29to31TemplateGuards:

    @classmethod
    def _src(cls) -> str:
        return (TEMPLATES_DIR / "card_studio_welcome.html").read_text(encoding="utf-8")

    def test_cew_29_template_has_preview_iframe_reload(self):
        """CEW-29: template JS reloads the preview iframe after upload/delete."""
        src = self._src()
        assert "wcs-preview-iframe" in src, "preview iframe must have wcs-preview-iframe id"
        assert "iframe.src" in src, "JS must reload iframe via iframe.src reassignment"

    def test_cew_30_template_has_no_bg_removal(self):
        """CEW-30: template must not reference BG removal."""
        src = self._src()
        assert "bg_removal" not in src.lower()
        assert "rembg" not in src.lower()
        assert "background removal" not in src.lower()

    def test_cew_31_template_has_no_mood_photo_upload_in_studio(self):
        """CEW-31 (updated CE-3.8): Studio shows mood picker but no in-Studio upload routes."""
        src = self._src()
        # CE-3.8 intentionally adds wcs-mood-picker — mood_photos IS in the template.
        # What must NOT be present: Studio-side mood photo upload/delete management.
        assert "my-mood-photos/upload" not in src
        assert "my-mood-photos/delete" not in src


# ── CEW-32–36: CSRF fix coverage (CE-3.7 CSRF fix) ──────────────────────────

class TestCEW32to36CsrfFix:

    @classmethod
    def _src(cls) -> str:
        return (TEMPLATES_DIR / "card_studio_welcome.html").read_text(encoding="utf-8")

    @classmethod
    def _upload_section(cls) -> str:
        src = cls._src()
        start = src.find("input.addEventListener('change'")
        end   = src.find("deleteBtn.addEventListener('click'")
        return src[start:end] if start != -1 and end != -1 else ""

    @classmethod
    def _delete_section(cls) -> str:
        src = cls._src()
        start = src.find("deleteBtn.addEventListener('click'")
        return src[start:] if start != -1 else ""

    def test_cew_32_template_has_x_csrf_token_header(self):
        """CEW-32: template references X-CSRF-Token header."""
        assert 'X-CSRF-Token' in self._src(), (
            "Template must include 'X-CSRF-Token' header in fetch() calls"
        )

    def test_cew_33_template_reads_csrf_token_cookie(self):
        """CEW-33: template reads the csrf_token cookie to obtain the CSRF value."""
        assert 'csrf_token=' in self._src(), (
            "Template must read csrf_token= from document.cookie"
        )

    def test_cew_34_upload_fetch_has_csrf_header(self):
        """CEW-34: the upload fetch() block carries X-CSRF-Token header."""
        section = self._upload_section()
        assert section, "Could not extract upload handler section from template"
        assert 'X-CSRF-Token' in section, (
            "Upload fetch() must include 'X-CSRF-Token' header"
        )

    def test_cew_35_delete_fetch_has_csrf_header(self):
        """CEW-35: the delete fetch() block carries X-CSRF-Token header."""
        section = self._delete_section()
        assert section, "Could not extract delete handler section from template"
        assert 'X-CSRF-Token' in section, (
            "Delete fetch() must include 'X-CSRF-Token' header"
        )

    def test_cew_36_upload_formdata_fetch_has_no_explicit_content_type(self):
        """CEW-36: FormData upload fetch must NOT set explicit Content-Type header."""
        section = self._upload_section()
        assert section, "Could not extract upload handler section from template"
        assert 'Content-Type' not in section, (
            "Upload fetch() must not set explicit Content-Type — "
            "browser sets multipart/form-data with boundary automatically"
        )


# ── CEW-37–51: Mood Photo Picker / Media Library (CE-3.8) ────────────────────

_MOCK_MOOD_PHOTO_URL = "/static/uploads/mood_photos/42_mood_happy_smile_orig_1234.png"


def _make_mood_photo(slot: str, url: str = _MOCK_MOOD_PHOTO_URL):
    mp = MagicMock()
    mp.slot         = slot
    mp.original_url = url
    mp.status       = "uploaded"
    return mp


class TestCEW37to38MoodPhotosContext:

    def test_cew_37_context_has_mood_photos_key(self):
        """CEW-37: card_studio_welcome context contains mood_photos key."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "mood_photos" in ctx, "context must contain mood_photos key"

    def test_cew_38_mood_photos_has_all_6_slots(self):
        """CEW-38: mood_photos dict contains all 6 MOOD_PHOTO_SLOTS keys."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        mp = ctx.get("mood_photos", {})
        assert set(mp.keys()) == MOOD_PHOTO_SLOTS, (
            f"mood_photos must contain all 6 MOOD_PHOTO_SLOTS. Got: {set(mp.keys())}"
        )

    def test_cew_38b_mood_photos_values_reflect_passed_data(self):
        """CEW-38b: mood_photos values come from get_mood_photos_for_user."""
        mock_mp = _make_mood_photo("mood_happy_smile")
        mood = {slot: None for slot in MOOD_PHOTO_SLOTS}
        mood["mood_happy_smile"] = mock_mp
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID], mood_photos=mood)
        assert ctx["mood_photos"]["mood_happy_smile"] is mock_mp

    def test_cew_38c_context_has_mood_slot_meta_key(self):
        """CEW-38c: card_studio_welcome context contains mood_slot_meta key."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "mood_slot_meta" in ctx, "context must contain mood_slot_meta key"

    def test_cew_38d_mood_slot_meta_has_6_entries_with_required_fields(self):
        """CEW-38d: mood_slot_meta has 6 entries each with slot, emoji, label."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        meta = ctx.get("mood_slot_meta", [])
        assert len(meta) == 6, f"mood_slot_meta must have 6 entries, got {len(meta)}"
        for entry in meta:
            assert "slot"  in entry, f"entry missing 'slot':  {entry}"
            assert "emoji" in entry, f"entry missing 'emoji': {entry}"
            assert "label" in entry, f"entry missing 'label': {entry}"
        slots = {e["slot"] for e in meta}
        assert slots == MOOD_PHOTO_SLOTS, (
            f"mood_slot_meta slots must match MOOD_PHOTO_SLOTS. Got: {slots}"
        )


class TestCEW39to43FromMoodEndpoints:

    def test_cew_39_get_from_mood_not_registered(self):
        """CEW-39: GET /dashboard/wc-photo/from-mood is not a registered GET route."""
        from app.main import app
        get_routes = [
            r.path for r in app.routes
            if getattr(r, "path", None) == "/dashboard/wc-photo/from-mood"
            and "GET" in getattr(r, "methods", set())
        ]
        assert not get_routes, "GET /dashboard/wc-photo/from-mood must not be registered"

    def test_cew_39b_post_from_mood_is_registered(self):
        """CEW-39b: POST /dashboard/wc-photo/from-mood IS registered."""
        from app.main import app
        post_routes = [
            r.path for r in app.routes
            if getattr(r, "path", None) == "/dashboard/wc-photo/from-mood"
            and "POST" in getattr(r, "methods", set())
        ]
        assert post_routes, "POST /dashboard/wc-photo/from-mood must be registered"

    def test_cew_40_valid_mood_slot_assigns_wc_photo_url(self):
        """CEW-40: POST /dashboard/wc-photo/from-mood valid mood_slot → ok + photo_url."""
        from app.api.web_routes.dashboard import (
            student_assign_wc_photo_from_mood,
            _WcFromMoodRequest,
        )
        user = _user()
        lfa_license = MagicMock()
        lfa_license.wc_photo_url = None
        mood_photo = _make_mood_photo("mood_happy_smile")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = lfa_license
        db.query.return_value.filter_by.return_value.first.return_value = mood_photo

        payload = _WcFromMoodRequest(mood_slot="mood_happy_smile")
        resp = _run(student_assign_wc_photo_from_mood(payload=payload, db=db, user=user))

        assert resp.status_code == 200
        import json
        body = json.loads(resp.body)
        assert body["ok"] is True
        assert body["photo_url"] == _MOCK_MOOD_PHOTO_URL
        assert lfa_license.wc_photo_url == _MOCK_MOOD_PHOTO_URL

    def test_cew_41_invalid_mood_slot_returns_422(self):
        """CEW-41: POST /dashboard/wc-photo/from-mood unknown mood_slot → 422."""
        from app.api.web_routes.dashboard import (
            student_assign_wc_photo_from_mood,
            _WcFromMoodRequest,
        )
        user = _user()
        db = MagicMock()
        payload = _WcFromMoodRequest(mood_slot="totally_invalid_slot")
        resp = _run(student_assign_wc_photo_from_mood(payload=payload, db=db, user=user))
        assert resp.status_code == 422

    def test_cew_42_mood_photo_not_found_returns_404(self):
        """CEW-42: POST /dashboard/wc-photo/from-mood mood photo not in DB → 404."""
        from app.api.web_routes.dashboard import (
            student_assign_wc_photo_from_mood,
            _WcFromMoodRequest,
        )
        user = _user()
        lfa_license = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = lfa_license
        db.query.return_value.filter_by.return_value.first.return_value = None

        payload = _WcFromMoodRequest(mood_slot="mood_happy_smile")
        resp = _run(student_assign_wc_photo_from_mood(payload=payload, db=db, user=user))
        assert resp.status_code == 404

    def test_cew_43_portrait_endpoint_assigns_portrait_url(self):
        """CEW-43a: portrait from-mood endpoint writes to wc_photo_portrait_url."""
        from app.api.web_routes.dashboard import (
            student_assign_wc_portrait_photo_from_mood,
            _WcFromMoodRequest,
        )
        user = _user()
        lfa_license = MagicMock()
        lfa_license.wc_photo_portrait_url = None
        mood_photo = _make_mood_photo("mood_celebration")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = lfa_license
        db.query.return_value.filter_by.return_value.first.return_value = mood_photo

        payload = _WcFromMoodRequest(mood_slot="mood_celebration")
        _run(student_assign_wc_portrait_photo_from_mood(payload=payload, db=db, user=user))
        assert lfa_license.wc_photo_portrait_url == _MOCK_MOOD_PHOTO_URL

    def test_cew_43b_landscape_endpoint_assigns_landscape_url(self):
        """CEW-43b: landscape from-mood endpoint writes to wc_photo_landscape_url."""
        from app.api.web_routes.dashboard import (
            student_assign_wc_landscape_photo_from_mood,
            _WcFromMoodRequest,
        )
        user = _user()
        lfa_license = MagicMock()
        lfa_license.wc_photo_landscape_url = None
        mood_photo = _make_mood_photo("mood_sad_disappointed")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = lfa_license
        db.query.return_value.filter_by.return_value.first.return_value = mood_photo

        payload = _WcFromMoodRequest(mood_slot="mood_sad_disappointed")
        _run(student_assign_wc_landscape_photo_from_mood(payload=payload, db=db, user=user))
        assert lfa_license.wc_photo_landscape_url == _MOCK_MOOD_PHOTO_URL


class TestCEW44to51TemplateMoodPicker:

    @classmethod
    def _src(cls) -> str:
        return (TEMPLATES_DIR / "card_studio_welcome.html").read_text(encoding="utf-8")

    @classmethod
    def _assign_section(cls) -> str:
        src = cls._src()
        start = src.find("wcs-mood-thumb")
        return src[start:] if start != -1 else ""

    def test_cew_44_template_has_mood_picker_element(self):
        """CEW-44: template contains Mood Photo picker with wcs-mood-chip (not wcs-mood-thumb)."""
        src = self._src()
        assert "wcs-mood-picker" in src, "template must contain wcs-mood-picker element"
        assert "wcs-mood-chip" in src, "template must use wcs-mood-chip (not thumbnail grid)"
        assert "wcs-mood-thumb" not in src, "old wcs-mood-thumb must be replaced by wcs-mood-chip"

    def test_cew_44b_template_has_mood_chip_empty_class(self):
        """CEW-44b: template contains wcs-mood-chip--empty class for disabled empty slots."""
        assert "wcs-mood-chip--empty" in self._src()

    def test_cew_44c_template_has_mood_chip_selected_class(self):
        """CEW-44c: template contains wcs-mood-chip--selected class for active slot."""
        assert "wcs-mood-chip--selected" in self._src()

    def test_cew_44d_template_has_disabled_aria_disabled(self):
        """CEW-44d: template contains disabled + aria-disabled logic for empty slots."""
        src = self._src()
        assert "disabled" in src
        assert 'aria-disabled="true"' in src

    def test_cew_44e_template_renders_emoji_from_meta(self):
        """CEW-44e: template renders emoji via meta.emoji from mood_slot_meta context."""
        src = self._src()
        assert "meta.emoji" in src, (
            "template must render emoji via {{ meta.emoji }} from mood_slot_meta"
        )

    def test_cew_45_template_references_all_three_from_mood_routes(self):
        """CEW-45: template references all three /from-mood routes."""
        src = self._src()
        assert "/dashboard/wc-photo/from-mood" in src
        assert "/dashboard/wc-photo-portrait/from-mood" in src
        assert "/dashboard/wc-photo-landscape/from-mood" in src

    def test_cew_46_template_has_link_to_mood_photos_page(self):
        """CEW-46: template contains link to /profile/my-mood-photos."""
        assert "/profile/my-mood-photos" in self._src()

    def test_cew_47_route_count_842(self):
        """CEW-47: CE-3.8 adds 3 from-mood routes → total 842."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 844, (
            f"Expected 844 routes (842 baseline + 2 CS-S0 routes), got {len(paths)}"
        )

    def test_cew_48_assign_js_has_csrf_header(self):
        """CEW-48: mood picker assign fetch() carries X-CSRF-Token header."""
        src = self._src()
        assign_section = src[src.find("assignUrl"):] if "assignUrl" in src else ""
        assert "'X-CSRF-Token'" in assign_section or '"X-CSRF-Token"' in assign_section, (
            "Mood picker assign fetch() must include X-CSRF-Token header"
        )

    def test_cew_49_assign_js_has_missing_csrf_guard(self):
        """CEW-49: assign JS checks for missing CSRF token before sending request."""
        src = self._src()
        assert "!csrf" in src, "assign JS must guard against missing CSRF token"

    def test_cew_50_template_has_no_bg_removal(self):
        """CEW-50: template must not reference BG removal (scope guard)."""
        src = self._src()
        assert "bg_removal" not in src.lower()
        assert "rembg" not in src.lower()
        assert "background removal" not in src.lower()

    def test_cew_51_template_does_not_use_processed_png_url(self):
        """CEW-51: template must not reference processed_png_url (scope guard)."""
        assert "processed_png_url" not in self._src()

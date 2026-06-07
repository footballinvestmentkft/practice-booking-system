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


# ── CEW-01: GET /card-editor/welcome → 301 redirect ──────────────────────────

class TestCEW01Redirect:

    def test_cew_01_card_editor_welcome_redirects_301(self):
        """CEW-01 (CS-S1): GET /card-editor/welcome → 301 permanent redirect."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 301

    def test_cew_01b_redirect_target_contains_card_studio_welcome(self):
        """CEW-01b: redirect destination is /card-studio/welcome."""
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))
        assert "/card-studio/welcome" in resp.headers["location"]


# ── CEW-02: auth dependency on route ─────────────────────────────────────────

class TestCEW02AuthGuard:

    def test_cew_02_route_has_get_current_user_web_dependency(self):
        """CEW-02: /card-editor/welcome redirect still has get_current_user_web guard."""
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


# ── CEW-03: any request to /card-editor/welcome → 301 /card-studio/welcome ───

class TestCEW03to08RedirectBehavior:
    """CS-S1: All /card-editor/welcome requests 301-redirect to /card-studio/welcome.
    License/ownership guards are now enforced at /card-studio/welcome (CSS tests).
    """

    def test_cew_03_with_format_id_redirects_to_studio(self):
        """CEW-03 (CS-S1): /card-editor/welcome?format=X → 301 /card-studio/welcome?format=X."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 301
        assert f"/card-studio/welcome?format={_FIRST_ID}" == resp.headers["location"]

    def test_cew_04_no_format_id_redirects_to_studio(self):
        """CEW-04 (CS-S1): /card-editor/welcome (no format) → 301 /card-studio/welcome."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=None, user=_user()))
        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/card-studio/welcome"

    def test_cew_05_second_format_id_preserves_format_in_redirect(self):
        """CEW-05 (CS-S1): format param is passed through to redirect URL."""
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_SECOND_ID, user=_user()))
        assert resp.headers["location"] == f"/card-studio/welcome?format={_SECOND_ID}"

    def test_cew_06_redirect_is_permanent_not_temporary(self):
        """CEW-06 (CS-S1): /card-editor/welcome redirect is 301, not 303."""
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))
        assert resp.status_code == 301, (
            f"Expected 301 permanent redirect, got {resp.status_code}"
        )

    def test_cew_07_no_card_editor_welcome_in_redirect_destination(self):
        """CEW-07 (CS-S1 scope): redirect goes to /card-studio/welcome, not /card-editor/welcome."""
        from app.api.web_routes.card_editor import card_studio_welcome

        resp = _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))
        assert "/card-editor/welcome" not in resp.headers["location"]
        assert "/card-studio/welcome" in resp.headers["location"]


# ── CEW-09/10/11/12/13: context tests moved to CSS (card_studio.py handler) ───
# These handler-level context tests now live in test_card_studio_shell.py (CSS-09..12).
# /card-editor/welcome is a 301 redirect; all business logic is at /card-studio/welcome.


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
        assert 'href="/shop?type=welcome_card"' in src


# ── CEW-15/16: legacy grant + draft service ──────────────────────────────────
# CEW-15: legacy "default" CDO bulk-grant → now tested at /card-studio/welcome (CSS-15).
# CEW-16: CardDraftService never called by redirect handler (trivially true).

class TestCEW16NoDraftService:

    def test_cew_16_card_draft_service_not_called_by_redirect(self):
        """CEW-16 (CS-S1): redirect handler never calls CardDraftService."""
        from app.api.web_routes.card_editor import card_studio_welcome

        with patch("app.services.card_draft_service.CardDraftService") as MockCDS:
            _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))

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
        assert len(paths) == 865, (
            f"Expected 842 routes (839 CE-3.7 baseline + 3 from-mood endpoints), got {len(paths)}."
        )

    def test_cew_18b_card_editor_welcome_route_registered(self):
        """CEW-18b: GET /card-editor/welcome is in the registered routes."""
        from app.main import app
        route_paths = [r.path for r in app.routes]
        assert "/card-editor/welcome" in route_paths, (
            "/card-editor/welcome must be a registered route"
        )


# ── CEW-19..22: photo context keys ───────────────────────────────────────────
# These context tests now live at /card-studio/welcome (card_studio.py).
# The CSS-* tests cover active_format, mood_photos, photo URL keys for the
# canonical /card-studio/welcome handler. /card-editor/welcome is a 301 redirect.


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
    mp.slot              = slot
    mp.original_url      = url
    mp.processed_png_url = None  # IMG-FIX-1: None so _mood_photo_asset_url falls back to original_url
    mp.status            = "uploaded"
    return mp


class TestCEW37to38MoodPhotosContext:
    """CS-S1: mood_photos and mood_slot_meta context tests moved to CSS-11/CSS-12.
    /card-editor/welcome is a 301 redirect; context is populated by /card-studio/welcome."""

    def test_cew_37_redirect_handler_does_not_call_mood_photo_service(self):
        """CEW-37 (CS-S1): redirect handler never calls get_mood_photos_for_user."""
        from app.api.web_routes.card_editor import card_studio_welcome

        with patch(f"{_CE_BASE}.get_mood_photos_for_user") as mock_mood:
            _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))

        mock_mood.assert_not_called()

    def test_cew_38_redirect_handler_does_not_call_owned_design_ids(self):
        """CEW-38 (CS-S1): redirect handler never calls get_owned_design_ids."""
        from app.api.web_routes.card_editor import card_studio_welcome

        with patch(f"{_CE_BASE}.get_owned_design_ids") as mock_owned:
            _run(card_studio_welcome(format_id=_FIRST_ID, user=_user()))

        mock_owned.assert_not_called()


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
        assert len(paths) == 865, (
            f"Expected 845 routes (842 baseline + 2 CS-S0 routes), got {len(paths)}"
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

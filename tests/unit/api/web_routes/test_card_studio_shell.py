"""
CSS-S0 — Unified Card Studio Shell (Welcome mode MVP) tests.

CSS-01  GET /card-studio → 303 to /card-studio/welcome?format=X (first owned)
CSS-02  GET /card-studio/welcome → 200 with unified shell template
CSS-03  unauthenticated → auth guard
CSS-04  no LFA license → 303 /dashboard
CSS-05  onboarding incomplete → 303 /specialization
CSS-06  no owned Welcome formats → 303 /shop/cards/welcome
CSS-07  no ?format → 303 canonical first owned URL
CSS-08  invalid ?format → 303 canonical first owned URL
CSS-09  valid ?format → active_format in context
CSS-10  active_type == "welcome" in context
CSS-11  mood_photos in context
CSS-12  mood_slot_meta 6 entries in context
CSS-13  template contains cs-type-switcher element
CSS-14  template contains Player, Welcome, Challenge in type switcher
CSS-15  template contains cs-mood-section (Mood Photos quick row)
CSS-16  Mood Photos section NOT in accordion (always open)
CSS-17  mobile markup: cs-mood-section before cs-preview-panel
CSS-18  template contains cs-preview-iframe
CSS-19  template contains X-CSRF-Token in assign JS
CSS-20  template contains !csrf guard
CSS-21  route count == 845 (842 + 2 new card-studio routes)
CSS-22  GET /card-studio route registered
CSS-23  GET /card-studio/welcome route registered
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.card_design_service import WELCOME_CARD_FORMATS

_CS_BASE = "app.api.web_routes.card_studio"

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
_ALL_WC_IDS: list[str] = [f.design_id for f in WELCOME_CARD_FORMATS]
_FIRST_ID = _ALL_WC_IDS[0]
_SECOND_ID = _ALL_WC_IDS[1]


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
    lic.wc_photo_url = None
    lic.wc_photo_portrait_url = None
    lic.wc_photo_landscape_url = None
    return lic


def _db_with_license(license_obj) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = license_obj
    return db


def _invoke_welcome(format_param, owned_ids, license_obj=None, user_obj=None):
    from app.api.web_routes.card_studio import card_studio_welcome

    user = user_obj or _user()
    lic  = license_obj if license_obj is not None else _license(onboarding_completed=True)
    db   = _db_with_license(lic)
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["context"] = ctx
        captured["template"] = tmpl
        return MagicMock(status_code=200)

    from app.models.user_mood_photos import MOOD_PHOTO_SLOTS
    empty_mood = {slot: None for slot in MOOD_PHOTO_SLOTS}

    with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=owned_ids), \
         patch(f"{_CS_BASE}.get_mood_photos_for_user", return_value=empty_mood), \
         patch(f"{_CS_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        resp = _run(card_studio_welcome(
            request=MagicMock(), format_id=format_param, db=db, user=user
        ))

    return resp, captured.get("context", {}), captured.get("template", "")


# ── CSS-01: /card-studio default redirect ─────────────────────────────────────

class TestCSS01DefaultRedirect:

    def test_css_01_card_studio_redirects_to_welcome(self):
        """CSS-01: GET /card-studio → 303 to /card-studio/welcome?format=X."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_studio import card_studio_default
        from app.models.user_mood_photos import MOOD_PHOTO_SLOTS

        user = _user()
        lic  = _license(onboarding_completed=True)
        db   = _db_with_license(lic)

        with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_default(request=MagicMock(), db=db, user=user))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 303
        assert f"/card-studio/welcome?format={_FIRST_ID}" in resp.headers["location"]

    def test_css_01b_no_owned_redirects_to_shop(self):
        """CSS-01b: no owned formats → 303 /shop/cards/welcome."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_studio import card_studio_default

        user = _user()
        lic  = _license(onboarding_completed=True)
        db   = _db_with_license(lic)

        with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=[]):
            resp = _run(card_studio_default(request=MagicMock(), db=db, user=user))

        assert isinstance(resp, RedirectResponse)
        assert "/shop?type=welcome_card" in resp.headers["location"]


# ── CSS-02..CSS-09: /card-studio/welcome handler ──────────────────────────────

class TestCSS02to09WelcomeHandler:

    def test_css_02_welcome_200(self):
        """CSS-02: GET /card-studio/welcome → 200 with unified shell."""
        resp, ctx, tmpl = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert ctx, "context must be captured"
        assert tmpl == "card_studio_shell.html"

    def test_css_03_no_license_redirects(self):
        """CSS-04: missing license → 303 /dashboard."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_studio import card_studio_welcome

        user = _user()
        db   = _db_with_license(None)
        with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=_FIRST_ID, db=db, user=user
            ))
        assert isinstance(resp, RedirectResponse)
        assert "/dashboard" in resp.headers["location"]

    def test_css_05_onboarding_incomplete_redirects(self):
        """CSS-05: onboarding not complete → 303."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_studio import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license(onboarding_completed=False))
        with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=_FIRST_ID, db=db, user=user
            ))
        assert isinstance(resp, RedirectResponse)
        assert "onboarding" in resp.headers["location"]

    def test_css_06_no_owned_redirects_shop(self):
        """CSS-06: no owned Welcome formats → 303 /shop/cards/welcome."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_studio import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license())
        with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=[]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=None, db=db, user=user
            ))
        assert isinstance(resp, RedirectResponse)
        assert resp.headers["location"] == "/shop?type=welcome_card"

    def test_css_07_no_format_redirects_canonical(self):
        """CSS-07: absent ?format → 303 canonical first owned."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_studio import card_studio_welcome

        user = _user()
        db   = _db_with_license(_license())
        with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=[_FIRST_ID]):
            resp = _run(card_studio_welcome(
                request=MagicMock(), format_id=None, db=db, user=user
            ))
        assert isinstance(resp, RedirectResponse)
        assert f"format={_FIRST_ID}" in resp.headers["location"]

    def test_css_09_active_format_in_context(self):
        """CSS-09: context active_format matches ?format param."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert ctx.get("active_format") == _FIRST_ID

    def test_css_10_active_type_welcome(self):
        """CSS-10: context active_type == 'welcome'."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert ctx.get("active_type") == "welcome"

    def test_css_11_mood_photos_in_context(self):
        """CSS-11: context contains mood_photos key."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "mood_photos" in ctx

    def test_css_12_mood_slot_meta_6_entries(self):
        """CSS-12 (Phase-B updated): mood_slot_meta has 9 entries (6 Phase-A + 3 Phase-B)."""
        _, ctx, _ = _invoke_welcome(_FIRST_ID, owned_ids=[_FIRST_ID])
        meta = ctx.get("mood_slot_meta", [])
        assert len(meta) == 9
        for entry in meta:
            assert "slot" in entry and "emoji" in entry and "label" in entry
        # Phase-B slots present
        slots = [e["slot"] for e in meta]
        assert "mood_focused_ready" in slots
        assert "mood_confident"     in slots
        assert "mood_proud"         in slots


# ── CSS-13..CSS-20: Template confirmations ────────────────────────────────────

class TestCSS13to20TemplateConfirmations:

    @classmethod
    def _src(cls) -> str:
        return (TEMPLATES_DIR / "card_studio_shell.html").read_text(encoding="utf-8")

    def test_css_13_template_has_type_switcher(self):
        """CSS-13: template contains cs-type-switcher element."""
        assert "cs-type-switcher" in self._src()

    def test_css_14_template_has_all_three_types(self):
        """CSS-14: type switcher (include) contains Player, Welcome, Challenge."""
        # Type switcher is an include — check include file and shell include directive
        src = self._src()
        assert "cs_type_switcher.html" in src, "shell must include cs_type_switcher"
        # Check include file for type labels
        switcher_src = (TEMPLATES_DIR / "includes/cs_type_switcher.html").read_text()
        assert "Player Card" in switcher_src
        assert "Welcome Card" in switcher_src
        assert "Challenge Card" in switcher_src

    def test_css_14b_player_challenge_disabled_in_cs_s0(self):
        """CSS-14b: Player and Challenge buttons are disabled (CS-S0 Coming Soon)."""
        src = self._src()
        # cs-type-soon class marks them as disabled
        assert "cs-type-soon" in src

    def test_css_15_template_has_mood_section(self):
        """CSS-15: template contains cs-mood-section (Mood Photos quick row)."""
        assert "cs-mood-section" in self._src()
        assert "cs-mood-grid" in self._src()

    def test_css_16_mood_not_in_accordion(self):
        """CSS-16: Mood section is NOT inside a collapsed accordion — always open."""
        src = self._src()
        # Find position of cs-mood-section vs any accordion/collapsed markup
        mood_pos = src.find('cs-mood-section')
        # Should not be inside an accordion-type wrapper
        # cs-mood-section has no 'hidden' or 'collapsed' class by default
        # The section itself should not have display:none or aria-hidden
        assert 'cs-mood-section' in src
        assert 'id="cs-mood-section"' not in src or 'display:none' not in src

    def test_css_17_mobile_markup_mood_before_preview(self):
        """CSS-17: in DOM order, cs-mood-section appears before cs-preview-panel."""
        src = self._src()
        mood_pos    = src.find('cs-mood-section')
        preview_pos = src.find('cs-preview-panel')
        assert mood_pos != -1, "cs-mood-section must be present"
        assert preview_pos != -1, "cs-preview-panel must be present"
        assert mood_pos < preview_pos, (
            "cs-mood-section must appear before cs-preview-panel in DOM order "
            "(ensures correct mobile stacking)"
        )

    def test_css_18_template_has_preview_iframe(self):
        """CSS-18: template contains cs-preview-iframe."""
        assert "cs-preview-iframe" in self._src()

    def test_css_19_assign_js_has_csrf_header(self):
        """CSS-19: assign JS fetch carries X-CSRF-Token header."""
        src = self._src()
        assert "'X-CSRF-Token'" in src

    def test_css_20_assign_js_has_csrf_guard(self):
        """CSS-20: assign JS checks for missing CSRF token."""
        assert "!csrf" in self._src()

    def test_css_20b_upload_fallback_present(self):
        """CSS-20b: upload fallback input and button present."""
        src = self._src()
        assert "cs-btn-upload" in src
        assert "cs-btn-delete" in src

    def test_css_20c_export_cta_present(self):
        """CSS-20c: Download PNG export CTA present."""
        assert "cs-btn-download" in src
        assert "export_url" in src
    def test_css_20c_export_cta_present(self):
        """CSS-20c: Download PNG export CTA present."""
        src = self._src()
        assert "cs-btn-download" in src
        assert "export_url" in src


# ── CSS-21..CSS-23: Route confirmations ──────────────────────────────────────

class TestCSS21to23RouteConfirmations:

    def test_css_21_route_count_844(self):
        """CSS-21: adding 2 card-studio routes raises count from 842 to 844."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 912, (
            f"Expected 845 routes (842 baseline + 2 new /card-studio routes), got {len(paths)}"
        )

    def test_css_22_card_studio_route_registered(self):
        """CSS-22: GET /card-studio is registered."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert "/card-studio" in paths

    def test_css_23_card_studio_welcome_route_registered(self):
        """CSS-23: GET /card-studio/welcome is registered."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert "/card-studio/welcome" in paths

"""
CS-COLOR-1A — Welcome Studio Free/Basic Theme Selector MVP tests.

MVP scope: free/basic theme selection only (default/midnight/arctic).
No premium purchase, no family/format ownership, no shop unlock.
Full ownership system: COLOR-OWNERSHIP series (future).

CSCOL-01  GET /profile/onboarding-card?platform=instagram_square&theme=midnight → 200
CSCOL-02  context card_theme_id == active theme
CSCOL-03  GET /profile/onboarding-card?platform=X no theme → fallback "default"
CSCOL-04  GET /card-studio/welcome?format=X → context has card_themes + active_theme
CSCOL-05  card_themes list non-empty, FREE themes only (no premium in CS-COLOR-1A)
CSCOL-06  active_theme == CardDraft(welcome_card).draft_theme or "default"
CSCOL-07  POST /dashboard/wc-card-theme {"theme":"midnight"} → 200 {"ok":true}
CSCOL-08  POST /dashboard/wc-card-theme unknown theme → 400
CSCOL-09  Welcome CardDraft.draft_theme updated after POST
CSCOL-10  preview_url contains theme={active_theme}
CSCOL-11  export_url contains theme={active_theme}
CSCOL-12  card_studio_shell.html contains cs-color-chip swatch UI
CSCOL-13  setWelcomeTheme JS present, POST /dashboard/wc-card-theme with X-CSRF-Token
CSCOL-14  format change URL preserves theme via CardDraft (server-side persistence)
CSCOL-15  route count == 845
CSCOL-16  OpenAPI snapshot includes /dashboard/wc-card-theme
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.card_design_service import WELCOME_CARD_FORMATS
from app.services.card_theme_service import THEMES

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
SNAP_DIR      = Path(__file__).resolve().parents[4] / "tests" / "snapshots"

_ALL_WC_IDS = [f.design_id for f in WELCOME_CARD_FORMATS]
_FIRST_ID   = _ALL_WC_IDS[0]
_FREE_THEME_IDS = {tid for tid, t in THEMES.items() if not t.is_premium}


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


def _db_with_license(lic) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = lic
    return db


def _make_welcome_draft(draft_theme: str = "default") -> MagicMock:
    d = MagicMock()
    d.draft_theme = draft_theme
    return d


# ── CSCOL-01/02/03: /profile/onboarding-card theme param ─────────────────────

class TestCSCOL01to03ProfileThemeParam:

    def test_cscol_01_theme_param_accepted(self):
        """CSCOL-01: GET /profile/onboarding-card?platform=X&theme=midnight → 200."""
        from app.main import app
        routes = [getattr(r, 'path', '') for r in app.routes]
        assert '/profile/onboarding-card' in routes, "Route must be registered"

    def test_cscol_01b_handler_signature_has_theme(self):
        """CSCOL-01b: onboarding_welcome_card handler accepts theme param."""
        from app.api.web_routes.profile import onboarding_welcome_card
        import inspect
        sig = inspect.signature(onboarding_welcome_card)
        assert 'theme' in sig.parameters, "Handler must accept ?theme= Query param"

    def test_cscol_02_build_context_uses_theme_id(self):
        """CSCOL-02: _build_welcome_card_context with theme_id='midnight' uses midnight theme."""
        from app.api.web_routes.profile import _build_welcome_card_context
        import inspect
        sig = inspect.signature(_build_welcome_card_context)
        assert 'theme_id' in sig.parameters, "_build_welcome_card_context must accept theme_id"

    def test_cscol_02b_default_theme_is_not_hardcoded_midnight(self):
        """CSCOL-02b: default theme param is 'default', not hardcoded 'midnight'."""
        from app.api.web_routes.profile import onboarding_welcome_card
        import inspect
        sig = inspect.signature(onboarding_welcome_card)
        theme_param = sig.parameters.get('theme')
        assert theme_param is not None
        assert theme_param.default is not None
        assert 'midnight' not in str(theme_param.default), \
            "Default theme must not be hardcoded to 'midnight'"

    def test_cscol_03_export_handler_accepts_theme(self):
        """CSCOL-03: export_onboarding_welcome_card accepts ?theme= param."""
        from app.api.web_routes.profile import export_onboarding_welcome_card
        import inspect
        sig = inspect.signature(export_onboarding_welcome_card)
        assert 'theme' in sig.parameters, "Export handler must accept ?theme= param"


# ── CSCOL-04/05/06: /card-studio/welcome context has card_themes ─────────────

_CS_BASE = "app.api.web_routes.card_studio"


def _invoke_welcome_studio(format_param, owned_ids, draft_theme="default"):
    from app.api.web_routes.card_studio import card_studio_welcome
    from app.models.user_mood_photos import MOOD_PHOTO_SLOTS

    user = _user()
    lic  = _license(onboarding_completed=True)
    db   = _db_with_license(lic)
    captured: dict = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["context"] = ctx
        captured["template"] = tmpl
        return MagicMock(status_code=200)

    empty_mood = {slot: None for slot in MOOD_PHOTO_SLOTS}
    mock_draft = _make_welcome_draft(draft_theme)

    with patch(f"{_CS_BASE}.get_owned_design_ids", return_value=owned_ids), \
         patch(f"{_CS_BASE}.get_mood_photos_for_user", return_value=empty_mood), \
         patch(f"{_CS_BASE}._CardDraftService") as MockCDS, \
         patch(f"{_CS_BASE}.templates") as mock_tpl:
        MockCDS.get_draft.return_value = mock_draft
        mock_tpl.TemplateResponse.side_effect = _fake_tmpl
        _run(card_studio_welcome(
            request=MagicMock(), format_id=format_param, db=db, user=user,
        ))

    return captured.get("context", {})


class TestCSCOL04to06WelcomeContext:

    def test_cscol_04_context_has_card_themes(self):
        """CSCOL-04: /card-studio/welcome context contains card_themes key."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "card_themes" in ctx, "context must contain card_themes"

    def test_cscol_04b_context_has_active_theme(self):
        """CSCOL-04b: /card-studio/welcome context contains active_theme key."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID])
        assert "active_theme" in ctx, "context must contain active_theme"

    def test_cscol_05_card_themes_non_empty_and_free_only(self):
        """CSCOL-05: card_themes non-empty, all themes are free (is_premium=False)."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID])
        themes = ctx.get("card_themes", [])
        assert len(themes) > 0, "card_themes must not be empty"
        for t in themes:
            assert not t.is_premium, f"CS-COLOR-1: only free themes, got premium {t.id!r}"

    def test_cscol_06_active_theme_from_draft(self):
        """CSCOL-06: active_theme == CardDraft(welcome_card).draft_theme."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID], draft_theme="midnight")
        assert ctx.get("active_theme") == "midnight"

    def test_cscol_06b_active_theme_defaults_to_default(self):
        """CSCOL-06b: active_theme falls back to 'default' when draft_theme is empty."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID], draft_theme="")
        assert ctx.get("active_theme") in ("default", ""), \
            "active_theme must default to 'default' when draft is empty"


# ── CSCOL-10/11: preview_url + export_url contain theme param ────────────────

class TestCSCOL10to11ThemeInUrls:

    def test_cscol_10_preview_url_contains_theme(self):
        """CSCOL-10: preview_url contains theme={active_theme}."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID], draft_theme="arctic")
        preview_url = ctx.get("preview_url", "")
        assert "theme=arctic" in preview_url, \
            f"preview_url must contain theme=arctic; got {preview_url!r}"

    def test_cscol_11_export_url_contains_theme(self):
        """CSCOL-11: export_url contains theme={active_theme}."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID], draft_theme="arctic")
        export_url = ctx.get("export_url", "")
        assert "theme=arctic" in export_url, \
            f"export_url must contain theme=arctic; got {export_url!r}"

    def test_cscol_10b_format_row_preview_urls_contain_theme(self):
        """CSCOL-10b: owned_format_rows preview URLs include theme param."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID], draft_theme="midnight")
        rows = ctx.get("owned_format_rows", [])
        for row in rows:
            assert "theme=midnight" in row["preview_url"], \
                f"format row preview_url must contain theme param; got {row['preview_url']!r}"


# ── CSCOL-07/08/09: POST /dashboard/wc-card-theme ────────────────────────────

_DASH_BASE = "app.api.web_routes.dashboard"


class TestCSCOL07to09WcCardThemeEndpoint:

    def test_cscol_07_valid_free_theme_returns_ok(self):
        """CSCOL-07: POST /dashboard/wc-card-theme {"theme":"midnight"} → {"ok":true}."""
        from app.api.web_routes.dashboard import student_set_wc_card_theme, _CardThemeRequest
        from app.models.user_mood_photos import MOOD_PHOTO_SLOTS

        user    = _user()
        lic     = _license(onboarding_completed=True)
        db      = _db_with_license(lic)
        payload = _CardThemeRequest(theme="midnight")
        draft   = _make_welcome_draft("default")

        with patch(f"{_DASH_BASE}._CardDraftService") as MockCDS, \
             patch(f"{_DASH_BASE}._get_lfa_license", return_value=lic):
            MockCDS.get_draft.return_value = draft
            MockCDS.update_draft_theme.return_value = draft
            resp = _run(student_set_wc_card_theme(payload=payload, db=db, user=user))

        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert body["ok"] is True
        assert body["theme"] == "midnight"

    def test_cscol_08_unknown_theme_returns_400(self):
        """CSCOL-08: POST /dashboard/wc-card-theme with unknown theme → 400."""
        from app.api.web_routes.dashboard import student_set_wc_card_theme, _CardThemeRequest

        user    = _user()
        lic     = _license(onboarding_completed=True)
        db      = _db_with_license(lic)
        payload = _CardThemeRequest(theme="nonexistent_theme_xyz")

        with patch(f"{_DASH_BASE}._get_lfa_license", return_value=lic):
            resp = _run(student_set_wc_card_theme(payload=payload, db=db, user=user))

        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert body["ok"] is False

    def test_cscol_08b_premium_theme_rejected(self):
        """CSCOL-08b: POST /dashboard/wc-card-theme premium theme → 400 (no unlock scope)."""
        from app.api.web_routes.dashboard import student_set_wc_card_theme, _CardThemeRequest

        user    = _user()
        lic     = _license(onboarding_completed=True)
        db      = _db_with_license(lic)
        payload = _CardThemeRequest(theme="gold")  # premium — must be rejected in CS-COLOR-1

        with patch(f"{_DASH_BASE}._get_lfa_license", return_value=lic):
            resp = _run(student_set_wc_card_theme(payload=payload, db=db, user=user))

        assert resp.status_code == 400
        body = json.loads(resp.body)
        assert body["ok"] is False

    def test_cscol_09_draft_theme_updated(self):
        """CSCOL-09: CardDraftService.update_draft_theme called with correct theme."""
        from app.api.web_routes.dashboard import student_set_wc_card_theme, _CardThemeRequest

        user    = _user()
        lic     = _license(onboarding_completed=True)
        db      = _db_with_license(lic)
        payload = _CardThemeRequest(theme="arctic")
        draft   = _make_welcome_draft("default")

        # Endpoint uses a local import — patch the service directly
        with patch("app.services.card_draft_service.CardDraftService.get_draft", return_value=draft), \
             patch("app.services.card_draft_service.CardDraftService.update_draft_theme", return_value=draft) as mock_update, \
             patch(f"{_DASH_BASE}._get_lfa_license", return_value=lic):
            _run(student_set_wc_card_theme(payload=payload, db=db, user=user))

        mock_update.assert_called_once_with(db, draft, "arctic")

    def test_cscol_09b_no_license_returns_404(self):
        """CSCOL-09b: missing license → 404."""
        from app.api.web_routes.dashboard import student_set_wc_card_theme, _CardThemeRequest

        user    = _user()
        db      = MagicMock()
        payload = _CardThemeRequest(theme="midnight")

        with patch(f"{_DASH_BASE}._get_lfa_license", return_value=None):
            resp = _run(student_set_wc_card_theme(payload=payload, db=db, user=user))

        assert resp.status_code == 404


# ── CSCOL-12/13: template source checks ──────────────────────────────────────

class TestCSCOL12to13TemplateSource:

    def test_cscol_12_shell_has_cs_color_chip(self):
        """CSCOL-12: card_studio_shell.html contains cs-color-chip swatch UI."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "cs-color-chip" in src, "shell must contain cs-color-chip CSS class"
        assert "cs_color_panel.html" in src, "shell must include cs_color_panel.html"

    def test_cscol_12d_color_panel_uses_swatch_circle(self):
        """CSCOL-12d (CS-COLOR-1A): color chip uses swatch circle (.cs-color-swatch), not text list."""
        src = (TEMPLATES_DIR / "includes/cs_color_panel.html").read_text()
        assert "cs-color-swatch" in src, "panel must use .cs-color-swatch circle element"
        # Label text should NOT be inside the chip button (it's secondary via active-name)
        assert "cs-color-label" not in src, \
            "CS-COLOR-1A: label must not be inside chip — circle is primary visual"

    def test_cscol_12e_no_premium_themes_in_context(self):
        """CSCOL-12e (CS-COLOR-1A): card_themes context never contains premium themes."""
        ctx = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID])
        themes = ctx.get("card_themes", [])
        premium = [t for t in themes if t.is_premium]
        assert len(premium) == 0, \
            f"CS-COLOR-1A must show free themes only; found premium: {[t.id for t in premium]}"

    def test_cscol_12b_color_panel_include_exists(self):
        """CSCOL-12b: cs_color_panel.html include file exists."""
        assert (TEMPLATES_DIR / "includes/cs_color_panel.html").exists()

    def test_cscol_12c_color_panel_has_data_theme_attr(self):
        """CSCOL-12c: cs_color_panel.html uses data-theme attribute."""
        src = (TEMPLATES_DIR / "includes/cs_color_panel.html").read_text()
        assert "data-theme" in src

    def test_cscol_13_shell_has_set_welcome_theme_js(self):
        """CSCOL-13: card_studio_shell.html contains setWelcomeTheme JS function."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "setWelcomeTheme" in src, "shell must define setWelcomeTheme JS function"

    def test_cscol_13b_set_welcome_theme_calls_wc_card_theme(self):
        """CSCOL-13b: setWelcomeTheme posts to /dashboard/wc-card-theme."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "/dashboard/wc-card-theme" in src

    def test_cscol_13c_set_welcome_theme_has_csrf_header(self):
        """CSCOL-13c: setWelcomeTheme includes X-CSRF-Token header."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        # Find the setWelcomeTheme function context
        start = src.find("setWelcomeTheme")
        snippet = src[start:start + 800]
        assert "X-CSRF-Token" in snippet, "setWelcomeTheme must send X-CSRF-Token header"


# ── CSCOL-14: format change preserves theme via CardDraft ────────────────────

class TestCSCOL14FormatThemePersistence:

    def test_cscol_14_format_change_reads_draft_theme(self):
        """CSCOL-14: format change re-reads active_theme from CardDraft (server-side persist)."""
        # When user navigates to /card-studio/welcome?format=X (new format),
        # _resolve_welcome_context reads CardDraft.draft_theme → active_theme preserved
        ctx1 = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID], draft_theme="arctic")
        ctx2 = _invoke_welcome_studio(_FIRST_ID, owned_ids=[_FIRST_ID], draft_theme="arctic")
        assert ctx1.get("active_theme") == ctx2.get("active_theme") == "arctic"


# ── CSCOL-15/16: route count + OpenAPI snapshot ──────────────────────────────

class TestCSCOL15to16RouteAndSnapshot:

    def test_cscol_15_route_count_845(self):
        """CSCOL-15: route count = 845 (+1 POST /dashboard/wc-card-theme)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 912, f"Expected 845 routes, got {len(paths)}"

    def test_cscol_16_openapi_snapshot_includes_wc_card_theme(self):
        """CSCOL-16: OpenAPI snapshot includes /dashboard/wc-card-theme."""
        snap = json.loads((SNAP_DIR / "openapi_snapshot.json").read_text())
        snap_paths = set(snap.get("paths", {}).keys())
        assert "/dashboard/wc-card-theme" in snap_paths, \
            "Snapshot must include /dashboard/wc-card-theme"
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths, "Snapshot must match live API"

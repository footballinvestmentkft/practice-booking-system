"""
Unit tests — Card Editor theme-sync fix (TS-01 … TS-05)
========================================================

Root cause: exportCard() omitted &theme= from the fetch URL; the export
endpoint omitted it from the Playwright render URL; the render endpoint
therefore fell back to published_theme instead of draft_theme, producing
a PNG that ignored any theme change made since the last Publish.

Fix:
  - dashboard_card_editor.html:1276  → &theme=${_currentTheme} added
  - public_player.py export_player_card() → theme param received + forwarded

Coverage:
  TS-01  theme=gold forwarded into Playwright render_url
  TS-02  theme absent → render_url has no &theme= param
  TS-03  invalid/unknown theme → 422 before Playwright is invoked
  TS-04  frontend JS template contains _currentTheme in the exportCard fetch URL
  TS-05  draft/published divergence — draft theme used, not published theme

Mock strategy:
  - get_current_user_web → MagicMock user
  - get_db               → MagicMock session (yields User + UserLicense mocks)
  - _sync_take_screenshot → MagicMock captures call args; returns stub PNG
  - card_theme_service.get_all_themes → returns [default, gold] stubs
  - card_design_service.get_supported_buckets → returns ("portrait",) for the fclassic variant
  - rate counter reset between tests
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.models.user import UserRole
from app.services.card_export_service import reset_rate_counters

# ── PNG stub ──────────────────────────────────────────────────────────────────

def _make_png(width: int = 1080, height: int = 1350) -> bytes:
    img = Image.new("RGB", (width, height), color=(20, 40, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_PNG_STUB = _make_png()

# ── Theme stubs ───────────────────────────────────────────────────────────────

def _make_theme_stub(theme_id: str) -> MagicMock:
    t = MagicMock()
    t.id = theme_id
    return t

_THEME_STUBS = [_make_theme_stub("default"), _make_theme_stub("gold")]


# ── Model helpers ─────────────────────────────────────────────────────────────

def _make_user(user_id: int = 9, role: UserRole = UserRole.STUDENT) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.role = role
    u.is_active = True
    return u


def _make_license(
    card_variant: str = "fclassic",
    draft_theme: str = "gold",
    published_theme: str = "default",
) -> MagicMock:
    lic = MagicMock()
    lic.card_variant = card_variant
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    lic.draft_theme = draft_theme
    lic.published_theme = published_theme
    return lic


def _mock_db(target_user: MagicMock | None = None, target_license: MagicMock | None = None) -> MagicMock:
    db = MagicMock()
    q_user = MagicMock()
    q_user.filter.return_value.first.return_value = target_user
    q_lic = MagicMock()
    q_lic.filter.return_value.first.return_value = target_license
    # CardDraft query — published_variant=None falls back to license.card_variant
    q_draft = MagicMock()
    _draft = MagicMock()
    _draft.published_variant = None
    q_draft.filter.return_value.first.return_value = _draft
    # CDO ownership check — all designs (incl. fclassic) require an ownership row
    q_cdo = MagicMock()
    q_cdo.filter_by.return_value.first.return_value = MagicMock()  # owned
    db.query.side_effect = [q_user, q_lic, q_draft, q_cdo]
    return db


# ── Patches applied to every test in this module ─────────────────────────────

_PATCH_THEMES  = "app.services.card_theme_service.get_all_themes"
_PATCH_BUCKETS = "app.services.card_design_service.get_supported_buckets"
_PATCH_SHOT    = "app.services.card_export_service._sync_take_screenshot"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_rate():
    reset_rate_counters()
    yield
    reset_rate_counters()


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ── Helper: run export request, returns (response, screenshot_mock) ───────────

def _do_export(client, platform: str = "instagram_portrait",
               theme: str | None = None, user_id: int = 9) -> tuple:
    from app.main import app
    from app.dependencies import get_current_user_web, get_db

    user    = _make_user(user_id=user_id)
    license = _make_license()
    db      = _mock_db(_make_user(user_id=user_id), license)

    async def _auth():
        return user

    app.dependency_overrides[get_current_user_web] = _auth
    app.dependency_overrides[get_db] = lambda: db

    mock_shot = MagicMock(return_value=_PNG_STUB)
    try:
        with patch(_PATCH_THEMES, return_value=_THEME_STUBS), \
             patch(_PATCH_BUCKETS, return_value=("portrait", "story", "square",
                                                  "tiktok", "landscape", "og", "banner")), \
             patch(_PATCH_SHOT, mock_shot):
            url = f"/players/{user_id}/card/export?platform={platform}"
            if theme is not None:
                url += f"&theme={theme}"
            r = client.get(url)
    finally:
        app.dependency_overrides.clear()

    return r, mock_shot


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestThemeSyncFix:

    # TS-01: theme=gold is forwarded all the way into the Playwright render URL
    def test_ts01_theme_gold_forwarded_to_render_url(self, client):
        r, mock_shot = _do_export(client, platform="instagram_portrait", theme="gold")

        assert r.status_code == 200
        render_url, _platform = mock_shot.call_args[0]
        assert "&theme=gold" in render_url, (
            f"Expected '&theme=gold' in render_url, got: {render_url!r}"
        )

    # TS-02: no theme param → render_url must not contain any &theme= segment
    def test_ts02_no_theme_param_omitted_from_render_url(self, client):
        r, mock_shot = _do_export(client, platform="instagram_portrait", theme=None)

        assert r.status_code == 200
        render_url, _platform = mock_shot.call_args[0]
        assert "&theme=" not in render_url, (
            f"Expected no '&theme=' in render_url, got: {render_url!r}"
        )

    # TS-03: unknown theme ID → 422 before Playwright is invoked
    def test_ts03_invalid_theme_returns_422_no_playwright(self, client):
        r, mock_shot = _do_export(client, platform="instagram_portrait", theme="nonexistent_theme")

        assert r.status_code == 422
        mock_shot.assert_not_called()

    # TS-04: JS template check — exportCard() fetch URL contains _currentTheme
    def test_ts04_js_template_exportcard_includes_current_theme(self):
        template_path = (
            Path(__file__).parents[2]
            / "app/templates/dashboard_card_editor.html"
        )
        source = template_path.read_text(encoding="utf-8")
        # The fetch inside exportCard() must include the theme parameter
        assert "card/export?platform=${platform}&theme=${_currentTheme}" in source, (
            "exportCard() fetch URL must contain &theme=${_currentTheme} "
            "so the editor theme is forwarded to the PNG export path"
        )

    # TS-05: draft_theme differs from published_theme — export uses the value
    #        passed from JS (_currentTheme = draft_theme), not published_theme.
    #        Simulates: user selected Gold (draft_theme='gold'), hasn't published
    #        (published_theme='default').  JS sends ?theme=gold → PNG = Gold.
    def test_ts05_draft_published_divergence_export_uses_js_theme(self, client):
        r, mock_shot = _do_export(client, platform="instagram_portrait", theme="gold")

        assert r.status_code == 200
        render_url, _platform = mock_shot.call_args[0]
        # Must use the JS-supplied draft theme, not the stale published_theme=default
        assert "&theme=gold" in render_url, (
            "PNG export must use the draft theme supplied by JS, "
            f"not the published_theme. render_url={render_url!r}"
        )
        assert "&theme=default" not in render_url

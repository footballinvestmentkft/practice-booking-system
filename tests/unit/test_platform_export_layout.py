"""
Platform export layout regression tests
========================================

Two layers of coverage:

LAYER 1 — Static: Verify that the rendered HTML contains the correct body class
  tokens (platform CSS class + export-mode) for every platform.
  Uses FastAPI TestClient + dependency overrides — no Playwright, no DB.

LAYER 2 — Playwright: Verify that the card-wrap fills the full viewport in
  export mode (no body padding strip, no 400px card in 1080px canvas).
  Skipped automatically when Playwright / Chromium binary is absent.
  Requires the full app server running at APP_INTERNAL_PORT.

Tests:
  PL-01  export-mode class absent from <body> when ?export=1 not set
  PL-02  export-mode class present in <body> when ?export=1 is set
  PL-03  platform CSS class applied to body for every preset
  PL-04  all CANVAS_SIZES platforms render 200 on card route
  PL-05  card-wrap fills viewport width/height (Playwright, skipped if absent)
  PL-06  no body background strip outside card-wrap (Playwright)
"""
from __future__ import annotations

import re
import pytest
from unittest.mock import MagicMock, patch

from app.services.card_platform_service import PLATFORM_PRESETS, LayoutStrategy
from app.services.card_export_service import CANVAS_SIZES

# ── Shared mock helpers ───────────────────────────────────────────────────────

def _make_user(user_id: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.name = "Test Player"
    u.nationality = "Hungarian"
    u.is_active = True
    u.date_of_birth = None  # prevents arithmetic error in age calculation
    u.skills = {}
    return u


def _make_license() -> MagicMock:
    lic = MagicMock()
    lic.card_variant = "compact"
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    lic.onboarding_completed = False  # skips get_skill_profile
    lic.card_theme_id = None
    lic.card_bg_compact_url = None
    lic.card_bg_showcase_url = None
    lic.card_photo_portrait_url = None
    lic.card_photo_landscape_url = None
    lic.compact_photo_position = "left"
    lic.compact_focus_x = 50
    lic.compact_focus_y = 20
    return lic


def _mock_db(user=None, license_=None):
    """Return a MagicMock db that handles the card route's full query sequence.

    Call order: (1) user lookup, (2) license lookup, (3+) participations /
    teams / any further queries — all return empty lists so the template renders.
    """
    db = MagicMock()
    _calls = [0]

    def _side_effect(*args):
        _calls[0] += 1
        q = MagicMock()
        if _calls[0] == 1:
            q.filter.return_value.first.return_value = user
        elif _calls[0] == 2:
            q.filter.return_value.first.return_value = license_
        else:
            # participations, teams, clubs — return empty collections
            q.filter.return_value.order_by.return_value.all.return_value = []
            q.filter.return_value.all.return_value = []
            q.join.return_value.filter.return_value.all.return_value = []
            q.outerjoin.return_value.filter.return_value.all.return_value = []
            q.all.return_value = []
        return q

    db.query.side_effect = _side_effect
    return db


@pytest.fixture()
def client():
    from app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _get_card_html(client, platform: str, export: bool = False, user_id: int = 7) -> str:
    from app.main import app
    from app.dependencies import get_db

    db = _mock_db(user=_make_user(user_id), license_=_make_license())
    app.dependency_overrides[get_db] = lambda: db
    try:
        url = f"/players/{user_id}/card?platform={platform}"
        if export:
            url += "&export=1"
        r = client.get(url)
        return r.text if r.status_code == 200 else ""
    finally:
        app.dependency_overrides.pop(get_db, None)


# ── Layer 1: Static HTML body-class checks ────────────────────────────────────

@pytest.mark.unit
class TestExportModeBodyClass:

    @staticmethod
    def _body_tag(html: str) -> str:
        """Extract the opening <body ...> tag from rendered HTML."""
        m = re.search(r"<body[^>]*>", html)
        return m.group(0) if m else ""

    def test_pl01_export_mode_absent_without_flag(self, client):
        """Preview URL (no &export=1) must NOT add export-mode class to <body>."""
        html = _get_card_html(client, "instagram_square", export=False)
        assert html, "Card route returned empty response"
        body_tag = self._body_tag(html)
        assert body_tag, "Could not find <body> tag in rendered HTML"
        assert "export-mode" not in body_tag

    def test_pl02_export_mode_present_with_flag(self, client):
        """Export URL (?export=1) must add export-mode class to <body>."""
        html = _get_card_html(client, "instagram_square", export=True)
        assert html, "Card route returned empty response"
        body_tag = self._body_tag(html)
        assert body_tag, "Could not find <body> tag in rendered HTML"
        assert "export-mode" in body_tag

    @pytest.mark.parametrize("preset_id,preset", [
        (pid, p) for pid, p in PLATFORM_PRESETS.items()
        if p.layout_strategy != LayoutStrategy.NATIVE
    ])
    def test_pl03_platform_css_class_in_body(self, client, preset_id, preset):
        """Every non-default platform must inject its css_class into <body>."""
        html = _get_card_html(client, preset_id, export=False)
        assert html, f"Card route returned empty response for platform={preset_id!r}"
        body_tag = self._body_tag(html)
        assert preset.css_class in body_tag, (
            f"Expected css_class {preset.css_class!r} in <body> tag for platform {preset_id!r}"
        )

    @pytest.mark.parametrize("platform_id", list(CANVAS_SIZES))
    def test_pl04_all_export_platforms_render_200(self, client, platform_id):
        """Every platform in CANVAS_SIZES must produce a 200 on the card route."""
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license())
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get(f"/players/7/card?platform={platform_id}&export=1")
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert r.status_code == 200, (
            f"platform={platform_id!r} returned {r.status_code}"
        )


# ── Layer 2: Playwright viewport fill checks ──────────────────────────────────

def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _chromium_available() -> bool:
    if not _playwright_available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


def _app_server_reachable() -> bool:
    """Return True if the app server is accepting connections at APP_INTERNAL_PORT."""
    import socket
    try:
        from app.config import settings
        port = int(settings.APP_INTERNAL_PORT)
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except Exception:
        return False


def _playwright_and_server_available() -> bool:
    return _chromium_available() and _app_server_reachable()


_PLAYWRIGHT_REASON = "Playwright / Chromium binary not available, or app server not running"

_PORTRAIT_PLATFORMS = [
    pid for pid, p in PLATFORM_PRESETS.items()
    if p.layout_strategy == LayoutStrategy.PORTRAIT
]
_LANDSCAPE_PLATFORMS = [
    pid for pid, p in PLATFORM_PRESETS.items()
    if p.layout_strategy == LayoutStrategy.LANDSCAPE
]
_BANNER_PLATFORMS = [
    pid for pid, p in PLATFORM_PRESETS.items()
    if p.layout_strategy == LayoutStrategy.BANNER
]


@pytest.mark.unit
@pytest.mark.skipif(not _playwright_and_server_available(), reason=_PLAYWRIGHT_REASON)
class TestPlaywrightCanvasFill:
    """Playwright tests — skipped when Chromium is not installed.

    These tests navigate to the card render URL (app must be reachable at
    APP_INTERNAL_PORT) with ?export=1 and assert:
      - card-wrap.getBoundingClientRect() matches viewport dimensions exactly
      - no overflow outside the viewport
    """

    def _check_canvas_fill(self, platform_id: str) -> None:
        from playwright.sync_api import sync_playwright
        from app.config import settings

        w, h = CANVAS_SIZES[platform_id]
        url = (
            f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
            f"/players/7/card?platform={platform_id}&export=1"
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(url, wait_until="networkidle", timeout=15_000)

                # card-wrap must fill the viewport exactly
                rect = page.eval_on_selector(
                    ".card-wrap",
                    "el => { const r = el.getBoundingClientRect(); "
                    "return {w: r.width, h: r.height, x: r.x, y: r.y}; }"
                )

                assert rect["x"] == pytest.approx(0, abs=1), (
                    f"{platform_id}: card-wrap left offset {rect['x']}px (expected 0)"
                )
                assert rect["y"] == pytest.approx(0, abs=1), (
                    f"{platform_id}: card-wrap top offset {rect['y']}px (expected 0)"
                )
                assert rect["w"] == pytest.approx(w, abs=2), (
                    f"{platform_id}: card-wrap width {rect['w']}px vs viewport {w}px"
                )
                assert rect["h"] == pytest.approx(h, abs=2), (
                    f"{platform_id}: card-wrap height {rect['h']}px vs viewport {h}px"
                )

                # body must not be wider than viewport (no horizontal scroll strip)
                body_w = page.evaluate("document.body.scrollWidth")
                assert body_w <= w + 2, (
                    f"{platform_id}: body.scrollWidth={body_w}px exceeds viewport {w}px"
                )
            finally:
                browser.close()

    @pytest.mark.parametrize("platform_id", _PORTRAIT_PLATFORMS)
    def test_pl05_portrait_canvas_fill(self, platform_id):
        self._check_canvas_fill(platform_id)

    @pytest.mark.parametrize("platform_id", _LANDSCAPE_PLATFORMS)
    def test_pl05_landscape_canvas_fill(self, platform_id):
        self._check_canvas_fill(platform_id)

    @pytest.mark.parametrize("platform_id", _BANNER_PLATFORMS)
    def test_pl05_banner_canvas_fill(self, platform_id):
        self._check_canvas_fill(platform_id)


@pytest.mark.unit
@pytest.mark.skipif(not _playwright_and_server_available(), reason=_PLAYWRIGHT_REASON)
class TestPlaywrightP0ComponentSizing:
    """P0 export component scaling tests.

    Verify that clamp()-based sizing rules scale OVR, photo, and skill bar
    elements relative to the viewport — replacing preview-size fixed px values.

    Selectors cover all card variants (compact, atlas, showcase, pulse, fifa).

    PL-07  OVR font-size >= 5% of viewport width (portrait/square)
    PL-08  Photo column/avatar width >= 25% of viewport width (portrait/square)
           Skipped automatically for variants without a photo column (showcase).
    PL-09  Skill bar width > 44px (max-width constraint removed)
    PL-10  Body does not scroll in export mode (scrollHeight <= viewport height)
    PL-11  Last content element bottom >= 50% viewport height (portrait/square)
           NOTE: 85% target is a P1 gate; P0 sets 50% as baseline
    """

    # Combined selectors — first match wins across all card variants
    _OVR_SELECTOR = (
        ".cmp-overall, .atl-ovr-num, .sc-overall, .pls-ovr-text, .fifa-overall"
    )
    # Photo *column* selectors: only variants where photo is a dedicated width column.
    # Atlas/showcase/pulse use full-bleed hero backgrounds — PL-08 skips for those.
    _PHOTO_COL_SELECTOR = ".cmp-photo-col, .fifa-left"

    def _open_card(self, platform_id: str):
        """Return (page, browser, pw, w, h). Caller must call pw.stop()."""
        from playwright.sync_api import sync_playwright
        from app.config import settings

        w, h = CANVAS_SIZES[platform_id]
        url = (
            f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
            f"/players/7/card?platform={platform_id}&export=1"
        )
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(url, wait_until="networkidle", timeout=15_000)
        return page, browser, pw, w, h

    def test_pl07_ovr_font_size_min_5pct_vw(self):
        """OVR number font-size must be >= 5% of viewport width on instagram_square."""
        page, browser, pw, vw, _vh = self._open_card("instagram_square")
        try:
            fs_px = page.evaluate(
                f"() => {{ const el = document.querySelector('{self._OVR_SELECTOR}');"
                " return el ? parseFloat(window.getComputedStyle(el).fontSize) : null; }"
            )
            assert fs_px is not None, (
                f"OVR element not found; tried: {self._OVR_SELECTOR!r}"
            )
            min_px = vw * 0.05
            assert fs_px >= min_px, (
                f"OVR font-size {fs_px:.1f}px < 5% of {vw}px viewport ({min_px:.1f}px)"
            )
        finally:
            browser.close()
            pw.stop()

    def test_pl08_photo_col_min_25pct_vw(self):
        """Photo column/avatar width must be >= 25% of viewport width on instagram_square.

        Skipped when the rendered card variant has no photo column (e.g. showcase).
        """
        page, browser, pw, vw, _vh = self._open_card("instagram_square")
        try:
            col_w = page.evaluate(
                f"() => {{ const el = document.querySelector('{self._PHOTO_COL_SELECTOR}');"
                " return el ? el.getBoundingClientRect().width : null; }"
            )
            if col_w is None:
                pytest.skip("No dedicated photo column present for this card variant")
            min_px = vw * 0.25
            assert col_w >= min_px, (
                f"Photo column {col_w:.1f}px < 25% of {vw}px viewport ({min_px:.1f}px)"
            )
        finally:
            browser.close()
            pw.stop()

    def test_pl09_skill_bar_wider_than_44px(self):
        """Skill bar width must exceed 44px (max-width constraint removed)."""
        page, browser, pw, _vw, _vh = self._open_card("instagram_square")
        try:
            bar_w = page.eval_on_selector(
                ".skill-bar-bg",
                "el => el.getBoundingClientRect().width"
            )
            assert bar_w > 44, (
                f"Skill bar width {bar_w:.1f}px not wider than 44px fixed constraint"
            )
        finally:
            browser.close()
            pw.stop()

    def test_pl10_no_body_scroll_in_export(self):
        """body.scrollHeight must not exceed viewport height (no overflow scroll)."""
        page, browser, pw, _vw, vh = self._open_card("instagram_square")
        try:
            scroll_h = page.evaluate("document.body.scrollHeight")
            assert scroll_h <= vh + 4, (
                f"body.scrollHeight={scroll_h}px exceeds viewport height {vh}px"
            )
        finally:
            browser.close()
            pw.stop()

    @pytest.mark.parametrize("platform_id", ["instagram_square"])
    def test_pl11_last_content_element_reaches_50pct_height(self, platform_id):
        """Last visible content element bottom >= 50% viewport height (P0 baseline).

        instagram_portrait removed until IG Portrait canvas-fill layout is implemented.
        The 85% target (full canvas fill, no dead space) is a P1 gate.
        """
        page, browser, pw, _vw, vh = self._open_card(platform_id)
        try:
            bottom = page.eval_on_selector(
                ".skills-section",
                "el => el.getBoundingClientRect().bottom"
            )
            min_bottom = vh * 0.50
            assert bottom >= min_bottom, (
                f"{platform_id}: skills-section bottom {bottom:.1f}px "
                f"< 50% of viewport height {vh}px ({min_bottom:.1f}px)"
            )
        finally:
            browser.close()
            pw.stop()

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
  EX-06  FIFA × instagram_portrait uses dedicated export template (ex-card present)
  EX-07  FIFA × instagram_portrait export HTML has no tab-bar
  EX-08  FIFA × instagram_portrait export HTML has no card-wrap
  EX-09  FIFA × instagram_portrait export HTML has .ex-skill-cats
  EX-10  FIFA × instagram_portrait export uses portrait_photo_url variable
  EX-11  FIFA × instagram_story uses dedicated export template (ex-card present)
  EX-12  FIFA × instagram_story export HTML has no tab-bar
  EX-13  FIFA × instagram_story export HTML has no card-wrap
  EX-14  FIFA × instagram_story export HTML has .ex-skill-cats
  EX-15  FIFA × instagram_story export uses portrait_photo_url variable
  EX-16  FIFA × tiktok uses its own dedicated tiktok export template (NOT the story template)
  EX-17  FIFA × facebook_landscape uses dedicated export template (ex-card present)
  EX-18  FIFA × facebook_landscape export HTML has no tab-bar
  EX-19  FIFA × facebook_landscape export HTML has no card-wrap
  EX-20  FIFA × facebook_landscape export HTML has .ex-skill-cats
  EX-21  FIFA × facebook_landscape export uses landscape_photo_url variable
  EX-22  FIFA × og uses the same landscape export template (ex-card present)
  EX-23  FIFA × banner_custom uses dedicated export template (ex-card present)
  EX-24  FIFA × banner_custom export HTML has no tab-bar
  EX-25  FIFA × banner_custom export HTML has no card-wrap
  EX-26  FIFA × banner_custom export HTML has .ex-skill-cats
  EX-27  FIFA × banner_custom export uses landscape_photo_url variable
  EX-28  FIFA × banner_custom uses banner template, not landscape template (420px left panel)
  EX-29  FIFA × instagram_square export HTML contains all 11 Outfield skill names
  EX-30  square/fifa.html template source has no skill slicing (no cat.skills[:)
  EX-31  FIFA × instagram_square export uses 2-column flex layout (v4 proportional columns)
  EX-31b FIFA × instagram_square export uses col-filler to bottom-align Set Pieces with Physical
  EX-31g FIFA × instagram_square skill columns have no vertical overlap (Playwright)
  EX-32  square/fifa.html template source contains {% if animated_mode %} branch
  EX-33  square/fifa.html rendered with animated_mode=True contains @keyframes
  EX-34  square/fifa.html rendered with animated_mode=False (default) contains NO @keyframes
  EX-35  animated_mode=False does not break PNG static layout (bar fill CSS present)
  EX-36  square/pulse.html template source contains {% if animated_mode %} branch
  EX-37  square/pulse.html rendered with animated_mode=True contains @keyframes
  EX-38  square/pulse.html rendered with animated_mode=False (default) contains NO @keyframes
  EX-39  animated_mode=False does not break Pulse static layout (pex-bar-fill CSS present)
  EX-40  pulse × instagram_square export uses dedicated pex-card template (not editor chrome)
  EX-47  FIFA × instagram_story preview (no export flag) uses story template (SoT — no drift)
  EX-48  story/fifa.html contains class="ex-sponsor-slot" HTML element
  EX-49  story sponsor logo renders when sponsor_logo_url provided; absent when None
  EX-50  story template renders height/weight meta items when provided
  EX-51  story template renders dominant foot badge when provided
  EX-52  FIFA × tiktok export uses dedicated tiktok template (ex-card present, NOT story)
  EX-53  FIFA × tiktok export HTML has no tab-bar
  EX-54  FIFA × tiktok export HTML has no card-wrap
  EX-55  FIFA × tiktok preview (no export flag) uses tiktok template (SoT — no drift)
  EX-56  tiktok and instagram_story export HTML are structurally different (separation)
  EX-57  tiktok/fifa.html template source contains ex-hero-photo (full-bleed hero)
  EX-58  tiktok/fifa.html template source contains ex-identity-strip
  EX-59  tiktok rendered HTML contains class="ex-sponsor-slot" element
  EX-60  tiktok sponsor logo renders when sponsor_logo_url provided; absent when None
  EX-61  dashboard_card_editor.html: _updateFullscreenLink does NOT append &export=1
  EX-62  portrait no-export request uses portrait export template (not default card)
  EX-63  banner_custom no-export request uses banner export template (not default card)
  EX-64  portrait no-export response has no tab-bar (confirms export template selected)
  EX-65  banner_custom no-export response has no tab-bar (confirms export template selected)
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


def _make_license(card_variant: str = "compact") -> MagicMock:
    lic = MagicMock()
    lic.card_variant = card_variant
    lic.card_theme   = "default"
    lic.public_card_platform = None
    # Published state mirrors draft for tests — public route now reads these fields
    lic.published_card_variant  = card_variant
    lic.published_card_theme    = "default"
    lic.published_card_platform = None
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    lic.onboarding_completed = False  # skips get_skill_profile
    lic.card_theme_id = None
    lic.card_bg_compact_url = None
    lic.card_bg_showcase_url = None
    lic.player_card_photo_url   = None
    lic.card_photo_portrait_url = None
    lic.card_photo_landscape_url = None
    lic.compact_photo_position = "left"
    lic.compact_focus_x = 50
    lic.compact_focus_y = 20
    lic.right_foot_score = None
    lic.left_foot_score = None
    lic.sponsor_logo_url = None
    lic.current_level = 1
    lic.max_achieved_level = 1
    return lic


def _mock_db(user=None, license_=None):
    """Return a MagicMock db that handles the card route's full query sequence.

    Call order: (1) user lookup, (2) license lookup, (3+) participations /
    teams / any further queries — all return empty lists so the template renders.

    CardDraft queries are detected by class argument and always return a draft
    that mirrors the license published state, so card_variant_id stays a string.
    """
    from app.models.card_draft import CardDraft as _CardDraft

    db = MagicMock()
    _calls = [0]

    def _side_effect(*args):
        _calls[0] += 1
        q = MagicMock()
        # Detect CardDraft query by inspecting the queried class.
        if args and args[0] is _CardDraft:
            _draft = MagicMock()
            _draft.published_theme    = (license_.published_card_theme    if license_ else None) or "default"
            _draft.published_variant  = (license_.published_card_variant  if license_ else None) or "fifa"
            _draft.published_platform = (license_.published_card_platform if license_ else None)
            _draft.draft_theme    = _draft.published_theme
            _draft.draft_variant  = _draft.published_variant
            _draft.draft_platform = _draft.published_platform
            q.filter.return_value.first.return_value = _draft
        elif _calls[0] == 1:
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

        _uid = 19310  # Rafael Cardoso — seeded dev user with portrait photo + valid card
        w, h = CANVAS_SIZES[platform_id]
        url = (
            f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
            f"/players/{_uid}/card?platform={platform_id}&export=1"
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(url, wait_until="networkidle", timeout=15_000)

                # Root card element must fill the viewport exactly.
                # Dedicated export templates use .ex-card; editor templates use .card-wrap.
                rect = page.evaluate(
                    "() => { "
                    "  const el = document.querySelector('.ex-card, .card-wrap'); "
                    "  if (!el) return null; "
                    "  const r = el.getBoundingClientRect(); "
                    "  return {w: r.width, h: r.height, x: r.x, y: r.y}; "
                    "}"
                )
                assert rect is not None, (
                    f"{platform_id}: neither .ex-card nor .card-wrap found in rendered HTML"
                )

                assert rect["x"] == pytest.approx(0, abs=1), (
                    f"{platform_id}: card-wrap left offset {rect['x']}px (expected 0)"
                )
                assert rect["y"] == pytest.approx(0, abs=1), (
                    f"{platform_id}: card-wrap top offset {rect['y']}px (expected 0)"
                )
                assert rect["w"] == pytest.approx(w, abs=2), (
                    f"{platform_id}: root card width {rect['w']}px vs viewport {w}px"
                )
                assert rect["h"] == pytest.approx(h, abs=2), (
                    f"{platform_id}: root card height {rect['h']}px vs viewport {h}px"
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

    # Combined selectors — first match wins across all card variants and templates.
    # ex- prefixed classes are from dedicated export templates (export render layer).
    _OVR_SELECTOR = (
        ".ex-ovr-badge > span:first-child, .ex-ovr-num, .cmp-overall, .atl-ovr-num, .sc-overall, .pls-ovr-text, .fifa-overall"
    )
    # Photo column: only variants with a dedicated width column.
    # Editor templates: .cmp-photo-col (compact) or .fifa-left (FIFA).
    # Export templates (.ex-*) use a circular avatar, not a column — PL-08 skips for those.
    # Atlas/showcase/pulse use full-bleed hero backgrounds — also skip.
    _PHOTO_COL_SELECTOR = ".cmp-photo-col, .fifa-left"

    def _open_card(self, platform_id: str):
        """Return (page, browser, pw, w, h). Caller must call pw.stop()."""
        from playwright.sync_api import sync_playwright
        from app.config import settings

        _uid = 19310  # Rafael Cardoso — seeded dev user with portrait photo + valid card
        w, h = CANVAS_SIZES[platform_id]
        url = (
            f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
            f"/players/{_uid}/card?platform={platform_id}&export=1"
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

    def test_pl09_skill_bar_renders_visible(self):
        """Skill bar element must be rendered and visible (width >= 10px).

        Export templates use .ex-bar-bg (compact: flex:0 1 40px, approved design).
        Editor templates use .skill-bar-bg.
        The old >44px assertion reflected a removed max-width constraint;
        the compact square layout uses a narrower bar intentionally.
        """
        page, browser, pw, _vw, _vh = self._open_card("instagram_square")
        try:
            bar_w = page.evaluate(
                "() => { const el = document.querySelector('.ex-bar-bg, .skill-bar-bg');"
                " return el ? el.getBoundingClientRect().width : null; }"
            )
            assert bar_w is not None, "No skill bar element found (.ex-bar-bg / .skill-bar-bg)"
            assert bar_w >= 10, (
                f"Skill bar width {bar_w:.1f}px is below visibility threshold (10px) — "
                "bar may be collapsed or hidden"
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

    @pytest.mark.parametrize("platform_id", ["instagram_square", "instagram_portrait", "instagram_story"])
    def test_pl11_last_content_element_reaches_50pct_height(self, platform_id):
        """Last visible content element bottom >= 50% viewport height (P0 baseline).

        instagram_square, instagram_portrait, and instagram_story all use dedicated
        export templates. The 85% target (full canvas fill, no dead space) is P1.
        """
        page, browser, pw, _vw, vh = self._open_card(platform_id)
        try:
            bottom = page.evaluate(
                "() => { const el = document.querySelector('.ex-skills-zone, .ex-skills, .skills-section, .ex-skills-right, .ex-outfield-col');"
                " return el ? el.getBoundingClientRect().bottom : null; }"
            )
            assert bottom is not None, "No skills section element found"
            min_bottom = vh * 0.50
            assert bottom >= min_bottom, (
                f"{platform_id}: skills section bottom {bottom:.1f}px "
                f"< 50% of viewport height {vh}px ({min_bottom:.1f}px)"
            )
        finally:
            browser.close()
            pw.stop()


# ── Export Render Layer: static HTML checks ───────────────────────────────────

@pytest.mark.unit
class TestExportRenderLayerStatic:
    """Static (non-Playwright) tests for the export render layer.

    EX-01  FIFA × instagram_square uses the dedicated export template (ex-card present)
    EX-02  FIFA × instagram_square export HTML has no tab-bar
    EX-03  FIFA × instagram_square export HTML has no events-section
    EX-04  FIFA × instagram_square export HTML has .ex-skill-cats (2×2 grid container)
    EX-05  Non-FIFA variant (compact) still uses editor template for instagram_square
    EX-06  FIFA × instagram_portrait uses dedicated export template (ex-card present)
    EX-07  FIFA × instagram_portrait export HTML has no tab-bar
    EX-08  FIFA × instagram_portrait export HTML has no card-wrap
    EX-09  FIFA × instagram_portrait export HTML has .ex-skill-cats
    EX-10  FIFA × instagram_portrait export uses portrait_photo_url variable
    """

    def _get_fifa_export_html(self, client, platform: str = "instagram_square") -> str:
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get(f"/players/7/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex01_fifa_square_uses_export_template(self, client):
        """FIFA × IG Square export must render the dedicated export template."""
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response"
        assert "ex-card" in html, (
            "Dedicated export template not used — expected .ex-card root element"
        )

    def test_ex01b_fifa_square_card_uses_min_sizing(self, client):
        """v16: Square export CSS must use min(100vw, 100vh) for .ex-card sizing.

        min(100vw, 100vh) guarantees 1:1 at any viewport:
          Playwright 1080×1080 → 1080px (PNG/WebM export unchanged).
          Browser 1440×900    →  900px (square, fully visible, no distortion).
        Plain 100vw/100vh produces a non-square card at typical browser viewports.
        """
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response for instagram_square"
        style_block = html[:html.find("</style>")]
        # rfind: Square's min() override comes after the base .ex-card {} rule in rendered HTML
        card_css_start = style_block.rfind(".ex-card {")
        assert card_css_start != -1, ".ex-card CSS rule not found in Square export"
        card_css_end = style_block.find("}", card_css_start)
        card_css = style_block[card_css_start: card_css_end + 1]
        assert "min(100vw, 100vh)" in card_css, (
            ".ex-card must use min(100vw, 100vh) for square-specific sizing — "
            "plain 100vw/100vh breaks the 1:1 aspect ratio in non-square browser viewports"
        )
        assert "aspect-ratio" not in card_css, (
            ".ex-card must not use aspect-ratio — replaced by explicit min() sizing in v16"
        )

    def test_ex02_no_tab_bar_in_export(self, client):
        """Export template must not contain a tab-bar."""
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response"
        assert "tab-bar" not in html, "tab-bar found in export template HTML"

    def test_ex03_no_events_section_in_export(self, client):
        """Export template must not contain an events section."""
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response"
        assert "events-section" not in html, "events-section found in export template HTML"

    def test_ex04_skill_cats_grid_present(self, client):
        """Export template must contain the 2x2 skill category grid container."""
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response"
        assert "ex-skill-cats" in html, ".ex-skill-cats grid container not found in export HTML"

    def test_ex05_compact_variant_uses_editor_template(self, client):
        """Compact variant has no export template yet — must use editor path (no .ex-card)."""
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="compact"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=instagram_square&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert html, "Export returned empty response for compact variant"
        assert "ex-card" not in html, (
            "Compact variant should use editor template, not export template"
        )

    def test_ex06_fifa_portrait_uses_export_template(self, client):
        """FIFA × IG Portrait export must render the dedicated export template."""
        html = self._get_fifa_export_html(client, "instagram_portrait")
        assert html, "Export returned empty response for instagram_portrait"
        assert "ex-card" in html, (
            "Dedicated portrait export template not used — expected .ex-card root element"
        )

    def test_ex07_no_tab_bar_in_portrait_export(self, client):
        """Portrait export template must not contain a tab-bar."""
        html = self._get_fifa_export_html(client, "instagram_portrait")
        assert html, "Export returned empty response for instagram_portrait"
        assert "tab-bar" not in html, "tab-bar found in portrait export template HTML"

    def test_ex08_no_card_wrap_in_portrait_export(self, client):
        """Portrait export template must not contain a card-wrap (editor chrome)."""
        html = self._get_fifa_export_html(client, "instagram_portrait")
        assert html, "Export returned empty response for instagram_portrait"
        assert "card-wrap" not in html, "card-wrap found in portrait export template HTML"

    def test_ex09_skill_cats_grid_present_in_portrait(self, client):
        """Portrait export template must contain the 2×2 skill category grid."""
        html = self._get_fifa_export_html(client, "instagram_portrait")
        assert html, "Export returned empty response for instagram_portrait"
        assert "ex-skill-cats" in html, ".ex-skill-cats not found in portrait export HTML"

    def test_ex10_portrait_photo_url_used_in_portrait(self, client):
        """Portrait export must render portrait_photo_url into the avatar img src."""
        from app.main import app
        from app.dependencies import get_db

        lic = _make_license(card_variant="fifa")
        lic.card_photo_portrait_url = "/static/test-portrait.jpg"
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=instagram_portrait&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert html, "Export returned empty response for instagram_portrait"
        assert "/static/test-portrait.jpg" in html, (
            "portrait_photo_url not rendered in portrait export avatar img src — "
            "expected portrait crop URL to appear in rendered HTML"
        )


@pytest.mark.unit
class TestFifaStoryExport:
    """Static tests for FIFA Classic × Instagram Story dedicated export template.

    Instagram Story uses export/story/fifa.html (Option A: conservative layout).
    TikTok uses export/tiktok/fifa.html (Option B: native redesign) — see TestFifaTikTokExport.

    EX-11  FIFA × instagram_story uses dedicated export template (ex-card present)
    EX-12  FIFA × instagram_story export HTML has no tab-bar
    EX-13  FIFA × instagram_story export HTML has no card-wrap (editor chrome)
    EX-14  FIFA × instagram_story export HTML has .ex-skill-cats (2×2 grid)
    EX-15  FIFA × instagram_story export uses portrait_photo_url variable
    EX-16  FIFA × tiktok uses its own dedicated tiktok export template (NOT the story template)
    """

    def _get_fifa_export_html(self, client, platform: str) -> str:
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get(f"/players/7/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex11_fifa_story_uses_export_template(self, client):
        """FIFA × IG Story export must render the dedicated export template."""
        html = self._get_fifa_export_html(client, "instagram_story")
        assert html, "Export returned empty response for instagram_story"
        assert "ex-card" in html, (
            "Dedicated story export template not used — expected .ex-card root element"
        )

    def test_ex12_no_tab_bar_in_story_export(self, client):
        """Story export template must not contain a tab-bar."""
        html = self._get_fifa_export_html(client, "instagram_story")
        assert html, "Export returned empty response for instagram_story"
        assert "tab-bar" not in html, "tab-bar found in story export template HTML"

    def test_ex13_no_card_wrap_in_story_export(self, client):
        """Story export template must not contain a card-wrap (editor chrome)."""
        html = self._get_fifa_export_html(client, "instagram_story")
        assert html, "Export returned empty response for instagram_story"
        assert "card-wrap" not in html, "card-wrap found in story export template HTML"

    def test_ex14_skill_cats_grid_present_in_story(self, client):
        """Story export template must contain the 2×2 skill category grid."""
        html = self._get_fifa_export_html(client, "instagram_story")
        assert html, "Export returned empty response for instagram_story"
        assert "ex-skill-cats" in html, ".ex-skill-cats not found in story export HTML"

    def test_ex15_portrait_photo_url_used_in_story(self, client):
        """Story export must render portrait_photo_url into the avatar img src."""
        from app.main import app
        from app.dependencies import get_db

        lic = _make_license(card_variant="fifa")
        lic.card_photo_portrait_url = "/static/test-portrait.jpg"
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=instagram_story&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert html, "Export returned empty response for instagram_story"
        assert "/static/test-portrait.jpg" in html, (
            "portrait_photo_url not rendered in story export avatar img src — "
            "expected portrait crop URL to appear in rendered HTML"
        )

    def test_ex16_tiktok_uses_tiktok_export_template_not_story(self, client):
        """TikTok must use its own dedicated tiktok template, NOT the story template.

        instagram_story → export/story/fifa.html
        tiktok          → export/tiktok/fifa.html  (separate bucket since split)
        """
        story_html  = self._get_fifa_export_html(client, "instagram_story")
        tiktok_html = self._get_fifa_export_html(client, "tiktok")
        assert tiktok_html, "Export returned empty response for tiktok"
        assert "ex-card" in tiktok_html, (
            "Dedicated tiktok export template not used — expected .ex-card root element"
        )
        # The two platforms must render different templates
        assert story_html != tiktok_html, (
            "tiktok and instagram_story rendered identical HTML — bucket split not effective"
        )
        # TikTok template has identity-strip; story template has meta-strip
        assert "ex-identity-strip" in tiktok_html, (
            "ex-identity-strip not found in tiktok HTML — expected tiktok/fifa.html identity layout"
        )
        assert "ex-meta-strip" in story_html, (
            "ex-meta-strip not found in instagram_story HTML — expected story/fifa.html meta layout"
        )


@pytest.mark.unit
class TestFifaStoryOptionA:
    """Instagram Story Option A additions — sponsor slot, foot badge, height/weight, SoT.

    EX-47  FIFA × instagram_story preview (no export flag) uses story template (SoT)
    EX-48  story/fifa.html contains class="ex-sponsor-slot" HTML element
    EX-49  story sponsor logo renders when sponsor_logo_url provided; absent when None
    EX-50  story template renders height/weight meta items when provided
    EX-51  story template renders dominant foot badge when provided
    """

    def _get_story_html(self, client, export: bool = True, sponsor: str | None = None,
                        height: int | None = None, weight: int | None = None,
                        right_foot: float | None = None, left_foot: float | None = None) -> str:
        from app.main import app
        from app.dependencies import get_db

        lic = _make_license(card_variant="fifa")
        lic.sponsor_logo_url = sponsor
        lic.right_foot_score = right_foot
        lic.left_foot_score  = left_foot
        if height is not None:
            lic.motivation_scores = {"height_cm": height, "weight_kg": weight, "position": "MIDFIELDER"}
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            url = f"/players/7/card?platform=instagram_story"
            if export:
                url += "&export=1"
            r = client.get(url)
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex47_story_preview_uses_story_template(self, client):
        """EX-47: instagram_story preview (no export flag) must load story template, not editor."""
        html = self._get_story_html(client, export=False)
        assert html, "Preview returned empty response for instagram_story"
        assert "ex-card" in html, (
            "EX-47: story preview did not use export/story/fifa.html — editor drift detected "
            "(expected .ex-card root from standalone template)"
        )
        assert "tab-bar" not in html, (
            "EX-47: tab-bar found in instagram_story preview — editor template was loaded instead"
        )

    def test_ex48_story_sponsor_slot_element_present(self):
        """EX-48: sponsor slot must be present in story/fifa.html source.

        Level C story template implements the sponsor slot inline (ex-sponsor-slot class).
        """
        import os, app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")

        with open(os.path.join(tpl_dir, "public/export/story/fifa.html")) as f:
            story_src = f.read()

        assert 'ex-sponsor-slot' in story_src, (
            "EX-48: ex-sponsor-slot not found in story/fifa.html — sponsor slot missing"
        )

    def test_ex49_story_sponsor_logo_conditional(self, client):
        """EX-49: logo renders only when sponsor_logo_url is provided."""
        with_logo    = self._get_story_html(client, sponsor="/static/test/logo.png")
        without_logo = self._get_story_html(client, sponsor=None)
        assert 'class="ex-sponsor-logo"' in with_logo, (
            "EX-49: sponsor logo img not rendered when sponsor_logo_url is set"
        )
        assert 'class="ex-sponsor-logo"' not in without_logo, (
            "EX-49: sponsor logo img rendered even when sponsor_logo_url is None"
        )

    def test_ex50_story_height_weight_rendered(self, client):
        """EX-50: height and weight appear in rendered HTML when provided via motivation_scores."""
        html = self._get_story_html(client, height=180, weight=75)
        assert "180 cm" in html, "EX-50: height not rendered in story export HTML"
        assert "75 kg" in html,  "EX-50: weight not rendered in story export HTML"

    def test_ex51_story_dominant_foot_badge_rendered(self, client):
        """EX-51: dominant foot badge renders in tag-row when foot scores are provided."""
        html = self._get_story_html(client, right_foot=68.0, left_foot=32.0)
        # 68/(68+32)*100 = 68% right → "Rl"
        assert "Rl" in html, (
            "EX-51: dominant foot badge 'Rl' not found in story export HTML "
            "(right_foot=68, left_foot=32 → 68% right → should render Rl)"
        )


@pytest.mark.unit
class TestFifaTikTokExport:
    """TikTok Option B — dedicated tiktok export template, full-bleed hero, identity strip.

    EX-52  FIFA × tiktok export uses dedicated tiktok template (ex-card present, NOT story)
    EX-53  FIFA × tiktok export HTML has no tab-bar
    EX-54  FIFA × tiktok export HTML has no card-wrap
    EX-55  FIFA × tiktok preview (no export flag) uses tiktok template (SoT)
    EX-56  tiktok and instagram_story export HTML are structurally different (separation)
    EX-57  tiktok/fifa.html template source contains ex-hero-photo (full-bleed hero)
    EX-58  tiktok/fifa.html template source contains ex-identity-strip
    EX-59  tiktok rendered HTML contains class="ex-sponsor-slot" element
    EX-60  tiktok sponsor logo renders when sponsor_logo_url provided; absent when None
    """

    def _get_tiktok_html(self, client, export: bool = True, sponsor: str | None = None,
                         right_foot: float | None = None, left_foot: float | None = None) -> str:
        from app.main import app
        from app.dependencies import get_db

        lic = _make_license(card_variant="fifa")
        lic.sponsor_logo_url = sponsor
        lic.right_foot_score = right_foot
        lic.left_foot_score  = left_foot
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            url = "/players/7/card?platform=tiktok"
            if export:
                url += "&export=1"
            r = client.get(url)
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex52_tiktok_uses_tiktok_template(self, client):
        """EX-52: tiktok export must use tiktok/fifa.html, not story/fifa.html."""
        html = self._get_tiktok_html(client)
        assert html, "Export returned empty response for tiktok"
        assert "ex-card" in html, (
            "EX-52: .ex-card not found in tiktok export — dedicated template not used"
        )
        assert "ex-hero-photo" in html, (
            "EX-52: ex-hero-photo not found — tiktok must use full-bleed hero template, "
            "not story/fifa.html"
        )

    def test_ex53_no_tab_bar_in_tiktok_export(self, client):
        """EX-53: tiktok export template must not contain a tab-bar."""
        html = self._get_tiktok_html(client)
        assert html, "Export returned empty response for tiktok"
        assert "tab-bar" not in html, "EX-53: tab-bar found in tiktok export HTML"

    def test_ex54_no_card_wrap_in_tiktok_export(self, client):
        """EX-54: tiktok export template must not contain card-wrap (editor chrome)."""
        html = self._get_tiktok_html(client)
        assert html, "Export returned empty response for tiktok"
        assert "card-wrap" not in html, "EX-54: card-wrap found in tiktok export HTML"

    def test_ex55_tiktok_preview_uses_tiktok_template(self, client):
        """EX-55: tiktok preview (no export flag) must load tiktok template, not editor."""
        html = self._get_tiktok_html(client, export=False)
        assert html, "Preview returned empty response for tiktok"
        assert "ex-card" in html, (
            "EX-55: tiktok preview did not use export/tiktok/fifa.html — editor drift detected"
        )
        assert "tab-bar" not in html, (
            "EX-55: tab-bar found in tiktok preview — editor template was loaded instead"
        )

    def test_ex56_tiktok_and_story_templates_differ(self, client):
        """EX-56: tiktok and instagram_story export must render structurally different HTML."""
        from app.main import app
        from app.dependencies import get_db

        # Each request needs its own fresh mock — a shared mock exhausts its call counter
        # after the first request and returns MagicMocks instead of user/license on the second.
        def _make_db():
            return _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))

        app.dependency_overrides[get_db] = lambda: _make_db()
        try:
            story_html  = client.get("/players/7/card?platform=instagram_story&export=1").text
            tiktok_html = client.get("/players/7/card?platform=tiktok&export=1").text
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert story_html != tiktok_html, (
            "EX-56: tiktok and instagram_story rendered identical HTML — bucket split not effective"
        )
        assert "ex-identity-strip" in tiktok_html, (
            "EX-56: ex-identity-strip missing from tiktok HTML"
        )
        assert "ex-identity-strip" not in story_html, (
            "EX-56: ex-identity-strip found in instagram_story HTML — templates have drifted"
        )

    def test_ex57_tiktok_template_has_hero_photo_class(self):
        """EX-57: tiktok/fifa.html source must define the full-bleed hero photo class."""
        import os, app as _app_pkg
        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/tiktok/fifa.html",
        )
        with open(tpl_path) as f:
            src = f.read()
        assert "ex-hero-photo" in src, (
            "EX-57: ex-hero-photo not found in tiktok/fifa.html — full-bleed hero missing"
        )

    def test_ex58_tiktok_template_has_identity_strip(self):
        """EX-58: tiktok/fifa.html source must define the identity strip."""
        import os, app as _app_pkg
        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/tiktok/fifa.html",
        )
        with open(tpl_path) as f:
            src = f.read()
        assert "ex-identity-strip" in src, (
            "EX-58: ex-identity-strip not found in tiktok/fifa.html source"
        )

    def test_ex59_tiktok_sponsor_slot_present(self, client):
        """EX-59: tiktok rendered HTML must contain the unconditional ex-sponsor-slot element."""
        html = self._get_tiktok_html(client)
        assert 'class="ex-sponsor-slot"' in html, (
            "EX-59: ex-sponsor-slot not found in tiktok export HTML — "
            "sponsor slot must be unconditional"
        )

    def test_ex60_tiktok_sponsor_logo_conditional(self, client):
        """EX-60: logo renders only when sponsor_logo_url is provided."""
        with_logo    = self._get_tiktok_html(client, sponsor="/static/test/logo.png")
        without_logo = self._get_tiktok_html(client, sponsor=None)
        assert 'class="ex-sponsor-slot-img"' in with_logo, (
            "EX-60: sponsor logo img not rendered when sponsor_logo_url is set"
        )
        assert 'class="ex-sponsor-slot-img"' not in without_logo, (
            "EX-60: sponsor logo img rendered even when sponsor_logo_url is None"
        )


@pytest.mark.unit
class TestFifaLandscapeExport:
    """Static tests for FIFA Classic × Landscape dedicated export template.

    EX-17  FIFA × facebook_landscape uses dedicated export template (ex-card present)
    EX-18  FIFA × facebook_landscape export HTML has no tab-bar
    EX-19  FIFA × facebook_landscape export HTML has no card-wrap (editor chrome)
    EX-20  FIFA × facebook_landscape export HTML has .ex-skill-cats (2×2 grid)
    EX-21  FIFA × facebook_landscape export uses landscape_photo_url variable
    EX-22  FIFA × og uses the same landscape export template (ex-card present)
    EX-41  landscape/fifa.html source references dominant_badge + ex-dom-badge CSS
    EX-42  dominant badge rendered in HTML when foot scores provided
    EX-43  3-col layout: .ex-center panel present in rendered HTML
    EX-44  OVR watermark (.ex-ovr-watermark) present in rendered HTML
    EX-45  template source has no cat.skills[:4] slicing (all skills rendered)
    EX-46  all 4 configured skill categories appear in rendered HTML
    """

    def _get_fifa_export_html(self, client, platform: str) -> str:
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get(f"/players/7/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex17_fifa_landscape_uses_export_template(self, client):
        """FIFA × FB Landscape export must render the dedicated export template."""
        html = self._get_fifa_export_html(client, "facebook_landscape")
        assert html, "Export returned empty response for facebook_landscape"
        assert "ex-card" in html, (
            "Dedicated landscape export template not used — expected .ex-card root element"
        )

    def test_ex18_no_tab_bar_in_landscape_export(self, client):
        """Landscape export template must not contain a tab-bar."""
        html = self._get_fifa_export_html(client, "facebook_landscape")
        assert html, "Export returned empty response for facebook_landscape"
        assert "tab-bar" not in html, "tab-bar found in landscape export template HTML"

    def test_ex19_no_card_wrap_in_landscape_export(self, client):
        """Landscape export template must not contain a card-wrap (editor chrome)."""
        html = self._get_fifa_export_html(client, "facebook_landscape")
        assert html, "Export returned empty response for facebook_landscape"
        assert "card-wrap" not in html, "card-wrap found in landscape export template HTML"

    def test_ex20_skill_cats_grid_present_in_landscape(self, client):
        """Landscape export template must contain the 2×2 skill category grid."""
        html = self._get_fifa_export_html(client, "facebook_landscape")
        assert html, "Export returned empty response for facebook_landscape"
        assert "ex-skill-cats" in html, ".ex-skill-cats not found in landscape export HTML"

    def test_ex21_landscape_photo_url_in_landscape(self):
        """Landscape export template source must reference landscape_photo_url (landscape crop first)."""
        import os, app as _app_pkg
        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/landscape/fifa.html",
        )
        with open(tpl_path, encoding="utf-8") as f:
            source = f.read()
        assert "landscape_photo_url" in source, (
            "landscape_photo_url not found in landscape/fifa.html source — "
            "landscape crop must be the primary photo fallback"
        )

    def test_ex22_og_uses_landscape_export_template(self, client):
        """OG shares the landscape bucket — must render the same dedicated export template."""
        html = self._get_fifa_export_html(client, "og")
        assert html, "Export returned empty response for og"
        assert "ex-card" in html, (
            "Landscape export template not used for og — expected .ex-card root element"
        )

    def test_ex41_landscape_template_source_has_dom_badge(self):
        """landscape/fifa.html source must reference the dominant_badge variable."""
        import os, app as _app_pkg
        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/landscape/fifa.html",
        )
        with open(tpl_path, encoding="utf-8") as f:
            source = f.read()
        assert "dominant_badge" in source, (
            "dominant_badge not referenced in landscape/fifa.html — "
            "dominant foot badge block missing from template"
        )
        assert "ex-dom-badge" in source, (
            ".ex-dom-badge CSS class not found in landscape/fifa.html — "
            "dominant foot badge CSS missing from template"
        )

    def test_ex42_dominant_badge_rendered_when_provided(self, client):
        """Landscape export must emit the .ex-dom-badge element when dominant_badge is set."""
        from app.main import app
        from app.dependencies import get_db
        from unittest.mock import patch

        lic = _make_license(card_variant="fifa")
        lic.right_foot_score = 75.0
        lic.left_foot_score  = 25.0  # → "Rl"
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=facebook_landscape&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert html, "Export returned empty response for facebook_landscape"
        assert "ex-dom-badge" in html, (
            ".ex-dom-badge not rendered — dominant foot badge missing from landscape export"
        )

    def test_ex43_center_panel_present(self, client):
        """3-col layout: .ex-center must exist in rendered landscape export HTML."""
        html = self._get_fifa_export_html(client, "facebook_landscape")
        assert html, "Export returned empty response for facebook_landscape"
        assert "ex-center" in html, (
            ".ex-center panel not found — landscape/fifa.html must use 3-column layout"
        )

    def test_ex44_ovr_watermark_present(self, client):
        """OVR watermark element (.ex-ovr-watermark) must exist in rendered landscape HTML."""
        html = self._get_fifa_export_html(client, "facebook_landscape")
        assert html, "Export returned empty response for facebook_landscape"
        assert "ex-ovr-watermark" in html, (
            ".ex-ovr-watermark not found — OVR decorative watermark missing from left panel"
        )

    def test_ex45_no_skill_slicing_in_template_source(self):
        """landscape/fifa.html source must NOT contain cat.skills[:N] slice — all skills render."""
        import os, re, app as _app_pkg
        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/landscape/fifa.html",
        )
        with open(tpl_path, encoding="utf-8") as f:
            source = f.read()
        slicing = re.search(r"cat\.skills\[:", source)
        assert slicing is None, (
            f"cat.skills[:N] slice found in landscape/fifa.html at position {slicing.start()} — "
            "all skills must be rendered; remove the slice"
        )

    def test_ex46_all_skill_categories_rendered(self, client):
        """All 4 skill category names must appear in the rendered landscape HTML."""
        from app.skills_config import SKILL_CATEGORIES as CATS
        html = self._get_fifa_export_html(client, "facebook_landscape")
        assert html, "Export returned empty response for facebook_landscape"
        for cat in CATS:
            assert cat["name_en"] in html, (
                f"Skill category '{cat['name_en']}' not found in rendered landscape HTML — "
                "all 4 categories must appear in the 2×2 skill grid"
            )


@pytest.mark.unit
class TestFifaBannerExport:
    """Static tests for FIFA Classic × Banner Custom dedicated export template.

    EX-23  FIFA × banner_custom uses dedicated export template (ex-card present)
    EX-24  FIFA × banner_custom export HTML has no tab-bar
    EX-25  FIFA × banner_custom export HTML has no card-wrap (editor chrome)
    EX-26  FIFA × banner_custom export HTML has .ex-skill-cats (2×2 grid)
    EX-27  FIFA × banner_custom export uses landscape_photo_url variable (landscape-first fallback)
    EX-28  FIFA × banner_custom uses banner template, not landscape template (420px left panel)
    """

    def _get_fifa_export_html(self, client, platform: str) -> str:
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get(f"/players/7/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex23_fifa_banner_uses_export_template(self, client):
        """FIFA × Banner Custom export must render the dedicated export template."""
        html = self._get_fifa_export_html(client, "banner_custom")
        assert html, "Export returned empty response for banner_custom"
        assert "ex-card" in html, (
            "Dedicated banner export template not used — expected .ex-card root element"
        )

    def test_ex24_no_tab_bar_in_banner_export(self, client):
        """Banner export template must not contain a tab-bar."""
        html = self._get_fifa_export_html(client, "banner_custom")
        assert html, "Export returned empty response for banner_custom"
        assert "tab-bar" not in html, "tab-bar found in banner export template HTML"

    def test_ex25_no_card_wrap_in_banner_export(self, client):
        """Banner export template must not contain a card-wrap (editor chrome)."""
        html = self._get_fifa_export_html(client, "banner_custom")
        assert html, "Export returned empty response for banner_custom"
        assert "card-wrap" not in html, "card-wrap found in banner export template HTML"

    def test_ex26_skill_cats_grid_present_in_banner(self, client):
        """Banner export template must contain the 2×2 skill category grid."""
        html = self._get_fifa_export_html(client, "banner_custom")
        assert html, "Export returned empty response for banner_custom"
        assert "ex-skill-cats" in html, ".ex-skill-cats not found in banner export HTML"

    def test_ex27_landscape_photo_url_in_banner(self, client):
        """Banner export must render landscape_photo_url into the avatar img src."""
        from app.main import app
        from app.dependencies import get_db

        lic = _make_license(card_variant="fifa")
        lic.card_photo_landscape_url = "/static/test-landscape.jpg"
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=banner_custom&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert html, "Export returned empty response for banner_custom"
        assert "/static/test-landscape.jpg" in html, (
            "landscape_photo_url not rendered in banner export avatar img src — "
            "expected landscape crop URL to appear in rendered HTML"
        )

    def test_ex28_banner_not_landscape_template(self, client):
        """Banner template must have the banner-specific 340px left panel, not landscape's 360px."""
        html = self._get_fifa_export_html(client, "banner_custom")
        assert html, "Export returned empty response for banner_custom"
        assert "0 0 340px" in html, (
            "Banner-specific 340px left panel not found — wrong banner template may have loaded"
        )


@pytest.mark.unit
class TestFifaSquareAllSkills:
    """All-skills regression tests for FIFA Classic × Square export template.

    EX-29  All 11 Outfield skill names present in rendered Square export HTML
    EX-30  square/fifa.html template source contains no skill slicing (cat.skills[:)
    EX-31  Square export uses 2-column flex layout (v4 proportional columns)
    EX-31b Square export uses logo-host to bottom-align Set Pieces with Physical Fitness + logo-slot placeholder
    """

    def _get_fifa_export_html(self, client, platform: str = "instagram_square") -> str:
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get(f"/players/7/card?platform={platform}&export=1")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex29_square_all_outfield_skills(self, client):
        """All 11 Outfield skill names must appear in the Square export HTML.

        Verifies that the [:4] slicing has been removed and Outfield skills
        (the largest category at 11 skills) are fully rendered.
        """
        from app.skills_config import SKILL_CATEGORIES

        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response for instagram_square"

        outfield = next(c for c in SKILL_CATEGORIES if c["name_en"] == "Outfield")
        for skill in outfield["skills"]:
            assert skill["name_en"] in html, (
                f"Outfield skill '{skill['name_en']}' not found in Square export HTML — "
                "skill slicing may still be active"
            )

    def test_ex30_square_no_skill_slicing(self, client):
        """Template source must not contain cat.skills[: — all skills rendered without slicing."""
        import os
        import app as _app_pkg

        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/square/fifa.html",
        )
        with open(tpl_path, encoding="utf-8") as f:
            source = f.read()

        assert "cat.skills[:" not in source, (
            "Skill slicing detected in export/square/fifa.html — "
            "use `cat.skills` (no slice) so all 44 skills are rendered"
        )

    def test_ex31_square_proportional_grid_flow(self, client):
        """Square export uses 2-column flex layout (v4): .ex-skill-col wrappers present.

        Each .ex-skill-col is an independent flex column — left=[Outfield, Set Pieces],
        right=[Mental, Physical Fitness]. Eliminates the 85px dead gap caused by CSS Grid
        sharing row heights across columns.
        """
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response for instagram_square"
        assert "ex-skill-col" in html, (
            "ex-skill-col not found in Square export HTML — "
            "2-column flex layout (v4) must be used, not grid-auto-flow"
        )

    def test_ex31b_square_proportional_row_heights(self, client):
        """Square export uses ex-col-sets-phys to co-host Set Pieces + Physical in col 3.

        v11 3-column layout: Set Pieces (3 rows, natural height) and Physical Fitness
        (8 rows, flex:1) live together in .ex-col-sets-phys — Physical expands to fill
        remaining column height via CSS `ex-col-sets-phys .ex-cat:last-child { flex:1 }`.
        The old v4 ex-cat--logo-host / ex-logo-slot mechanism was removed in v5.
        """
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response for instagram_square"
        assert "ex-col-sets-phys" in html, (
            "ex-col-sets-phys not found in Square export HTML — "
            "v11 col 3 must carry this class to co-host Set Pieces + Physical Fitness"
        )
        assert "ex-cat--logo-host" not in html, (
            "ex-cat--logo-host found in Square export HTML — "
            "this v4 class was removed in v5; template must not reference it"
        )
        assert "ex-logo-slot" not in html, (
            "ex-logo-slot found in Square export HTML — "
            "this v4 placeholder was removed in v5; template must not reference it"
        )

    def test_ex31c_sponsor_logo_slot_present_without_logo(self, client):
        """v14: ex-hero-sponsor removed; ex-outfield-logo is in Col 1; ex-sponsor-slot absent.

        v14 relocated the sponsor/app logo from the hero layer (.ex-hero-sponsor) into
        the Outfield column bottom (.ex-outfield-logo). The logo renders conditionally via
        Jinja2 gate (sponsor_logo_url or app_logo_url). ex-sponsor-slot was removed in v8.
        """
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response for instagram_square"
        assert "ex-sponsor-slot" not in html, (
            "ex-sponsor-slot found in Square export HTML — "
            "this class was removed in v8; sponsor moved to outfield column (v14)"
        )
        assert "ex-hero-sponsor" not in html, (
            "ex-hero-sponsor found in Square export HTML — "
            "v14 removed hero-layer sponsor; logo is now in ex-outfield-logo (Col 1 bottom)"
        )
        assert "ex-outfield-logo" in html, (
            "ex-outfield-logo not found in Square export HTML — "
            "v14 sponsor/app logo must render in .ex-outfield-logo inside .ex-col-outfield"
        )

    def test_ex31d_sponsor_logo_renders_img_when_url_present(self, client):
        """img.ex-outfield-logo-img renders inside ex-outfield-logo when sponsor_logo_url is set."""
        from app.main import app
        from app.dependencies import get_db

        lic = _make_license(card_variant="fifa")
        lic.sponsor_logo_url = "/static/uploads/lfa_player_photos/7_sponsor_logo_1234567890.png"
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=instagram_square&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert html, "Export returned empty response"
        assert 'class="ex-outfield-logo-img"' in html, (
            "ex-outfield-logo-img not found when sponsor_logo_url is set — "
            "img must render inside ex-outfield-logo (v14 outfield column)"
        )
        assert "/static/uploads/lfa_player_photos/7_sponsor_logo_1234567890.png" in html, (
            "sponsor_logo_url value not found in rendered img src"
        )

    def test_ex31e_sponsor_logo_css_constraints(self, client):
        """CSS for ex-outfield-logo-img contains object-fit: contain and max-height: 44px."""
        html = self._get_fifa_export_html(client, "instagram_square")
        assert html, "Export returned empty response for instagram_square"
        assert "object-fit: contain" in html, (
            "object-fit: contain not found in Square export CSS — "
            "required so sponsor/app logo preserves aspect ratio in ex-outfield-logo"
        )
        assert "max-height: 44px" in html, (
            "max-height: 44px not found in Square export CSS — "
            "v14 outfield logo must use 44px cap (was 28px in v8 hero layer)"
        )

    def test_ex31f_sponsor_logo_onerror_fallback(self, client):
        """img has onerror fallback so a broken logo URL hides the img without layout breakage."""
        from app.main import app
        from app.dependencies import get_db

        lic = _make_license(card_variant="fifa")
        lic.sponsor_logo_url = "/static/uploads/broken.png"
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=instagram_square&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

        assert html, "Export returned empty response"
        assert "onerror" in html, (
            "onerror attribute not found on sponsor logo img — "
            "broken URL must hide the img via onerror='this.style.display=\\'none\\''"
        )
        assert "this.style.display" in html, (
            "onerror handler body not found — must set display:none on broken img"
        )

    def test_ex31h_svg_no_green_css_background(self, client):
        """v15: .ex-pos-svg-landscape CSS block must not contain background: #1a5c2a.

        Green is provided by <rect fill='#1a5c2a'> inside the SVG viewBox only —
        consistent with Default card .pitch-svg (no CSS background on the SVG element).
        Setting background on the SVG element pollutes the letterbox areas with pitch green.
        """
        from app.main import app
        from app.dependencies import get_db
        lic = _make_license(card_variant="fifa")
        lic.motivation_scores = {"position": "midfielder"}  # valid pos → position map renders
        db = _mock_db(user=_make_user(), license_=lic)
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get("/players/7/card?platform=instagram_square&export=1")
            html = r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)
        assert html, "Export returned empty response for instagram_square"
        # Find the CSS block for ex-pos-svg-landscape
        style_block = html[:html.find("</style>")]
        svg_css_start = style_block.find(".ex-pos-svg-landscape {")
        assert svg_css_start != -1, ".ex-pos-svg-landscape CSS rule not found"
        svg_css_end = style_block.find("}", svg_css_start)
        svg_css = style_block[svg_css_start: svg_css_end + 1]
        assert "#1a5c2a" not in svg_css, (
            "background: #1a5c2a found in .ex-pos-svg-landscape CSS — "
            "v15 removes this so the letterbox shows panel background, not pitch green"
        )
        # The green rect must still be present in the SVG HTML section
        html_body = html[html.rfind("</style>"):]
        assert 'fill="#1a5c2a"' in html_body, (
            "<rect fill='#1a5c2a'> must remain inside the SVG for the pitch fill"
        )

    @pytest.mark.skipif(not _playwright_and_server_available(), reason=_PLAYWRIGHT_REASON)
    def test_ex31g_no_grid_cell_overlap(self):
        """Playwright: no vertical overlap between skill categories in either flex column.

        v4 flex-column layout DOM order: [0]=Outfield(col1), [1]=SetPieces(col1),
        [2]=Mental(col2), [3]=Physical(col2).

        Checks:
          - SetPieces.top >= Outfield.bottom      (left column: no overlap)
          - Physical.top  >= Mental.bottom        (right column: no overlap)
          - Mental–Physical gap ≈ 6–10px          (gap:6px CSS, allow 2px browser rounding)
        """
        from playwright.sync_api import sync_playwright
        from app.config import settings

        _uid = 19310  # Rafael Cardoso — seeded dev user with portrait photo + valid card
        url = (
            f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
            f"/players/{_uid}/card?platform=instagram_square&export=1"
        )
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1080, "height": 1080})
            page.goto(url, wait_until="networkidle", timeout=15_000)
            try:
                cats = page.query_selector_all(".ex-cat")
                assert len(cats) == 4, f"Expected 4 .ex-cat elements, got {len(cats)}"
                bbs = [c.bounding_box() for c in cats]
                # DOM order (square Level C): Outfield(col1) | Mental(col2) | SetPieces+Physical(col3)
                outfield, mental, set_pieces, physical = bbs
                # Col 3: Set Pieces on top, Physical below — verify no overlap
                gap_col3 = physical["y"] - (set_pieces["y"] + set_pieces["height"])
                assert gap_col3 >= 0, (
                    f"Col 3 overlap: Physical top={physical['y']:.0f} "
                    f"< Set Pieces bottom={set_pieces['y']+set_pieces['height']:.0f} (gap={gap_col3:.0f}px)"
                )
                assert gap_col3 <= 12, (
                    f"Col 3 gap {gap_col3:.0f}px > 12px — "
                    "gap should be ~6px (CSS gap property); large gap suggests layout regression"
                )
            finally:
                browser.close()


@pytest.mark.unit
class TestFifaSquareAnimatedMode:
    """Animated video export template tests for FIFA Classic × Square.

    EX-32  square/fifa.html source contains {%- if animated_mode %} branch
    EX-33  rendered HTML with animated_mode=True contains @keyframes
    EX-34  rendered HTML with animated_mode=False (default) contains NO @keyframes
    EX-35  animated_mode=False does not break static layout (bar fill CSS present)
    """

    def _render_square(self, client, animated: bool = False) -> str:
        """Render the square/fifa.html export template with animated_mode on or off."""
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            params = "platform=instagram_square&export=1"
            if animated:
                params += "&animated=1"
            r = client.get(f"/players/7/card?{params}")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex32_template_source_has_animated_mode_branch(self):
        """square/fifa.html source must contain the animated_mode Jinja2 conditional."""
        import os
        import app as _app_pkg

        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/square/fifa.html",
        )
        with open(tpl_path, encoding="utf-8") as f:
            source = f.read()

        assert "animated_mode" in source, (
            "{% if animated_mode %} branch not found in square/fifa.html — "
            "animation CSS block missing from template source"
        )

    def test_ex33_animated_mode_true_renders_keyframes(self, client):
        """When animated_mode=True, the rendered HTML must contain @keyframes."""
        html = self._render_square(client, animated=True)
        assert html, "Export returned empty response for animated=True render"
        assert "@keyframes" in html, (
            "@keyframes not found in animated render — "
            "animation CSS block may not be activating when animated=1 is passed"
        )

    def test_ex34_animated_mode_false_no_keyframes(self, client):
        """Default render (no animated param) must NOT contain @keyframes.

        This is the critical static-export isolation invariant: PNG renders
        must never include animation CSS, regardless of template changes.
        """
        html = self._render_square(client, animated=False)
        assert html, "Export returned empty response for animated=False render"
        assert "@keyframes" not in html, (
            "@keyframes found in static (non-animated) Square export render — "
            "animation CSS must be inside {% if animated_mode %} block only"
        )

    def test_ex35_static_layout_not_broken_by_animation_block(self, client):
        """Static render must still contain the skill bar fill CSS.

        Verifies that the animation block addition did not accidentally remove
        or displace the static layout CSS.
        """
        html = self._render_square(client, animated=False)
        assert html, "Export returned empty response"
        assert "ex-bar-fill" in html, (
            ".ex-bar-fill CSS class not found in static render — "
            "static layout may have been broken by template changes"
        )


@pytest.mark.unit
class TestPulseSquareAnimatedMode:
    """Animated video export template tests for Pulse × Instagram Square.

    EX-36  square/pulse.html source contains {% if animated_mode %} branch
    EX-37  rendered HTML with animated_mode=True contains @keyframes
    EX-38  rendered HTML with animated_mode=False (default) contains NO @keyframes
    EX-39  animated_mode=False does not break Pulse static layout (pex-bar-fill CSS present)
    EX-40  pulse × instagram_square export uses dedicated pex-card template (not editor chrome)
    """

    def _render_pulse_square(self, client, animated: bool = False) -> str:
        """Render the square/pulse.html export template with animated_mode on or off."""
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="pulse"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            params = "platform=instagram_square&export=1"
            if animated:
                params += "&animated=1"
            r = client.get(f"/players/7/card?{params}")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex36_template_source_has_animated_mode_branch(self):
        """square/pulse.html source must contain the animated_mode Jinja2 conditional."""
        import os
        import app as _app_pkg

        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/square/pulse.html",
        )
        with open(tpl_path, encoding="utf-8") as f:
            source = f.read()

        assert "animated_mode" in source, (
            "{% if animated_mode %} branch not found in square/pulse.html — "
            "animation CSS block missing from template source"
        )

    def test_ex37_animated_mode_true_renders_keyframes(self, client):
        """When animated_mode=True, the rendered Pulse HTML must contain @keyframes."""
        html = self._render_pulse_square(client, animated=True)
        assert html, "Export returned empty response for animated=True render"
        assert "@keyframes" in html, (
            "@keyframes not found in animated Pulse render — "
            "animation CSS block may not be activating when animated=1 is passed"
        )

    def test_ex38_animated_mode_false_no_keyframes(self, client):
        """Default render (no animated param) must NOT contain @keyframes in Pulse template.

        Critical static-export isolation invariant: PNG renders must never include
        animation CSS, regardless of template changes.
        """
        html = self._render_pulse_square(client, animated=False)
        assert html, "Export returned empty response for animated=False render"
        assert "@keyframes" not in html, (
            "@keyframes found in static (non-animated) Pulse export render — "
            "animation CSS must be inside {% if animated_mode %} block only"
        )

    def test_ex39_static_layout_not_broken_by_animation_block(self, client):
        """Static Pulse render must still contain the skill bar fill CSS.

        Verifies that the animation block addition did not accidentally remove
        or displace the static layout CSS.
        """
        html = self._render_pulse_square(client, animated=False)
        assert html, "Export returned empty response"
        assert "pex-bar-fill" in html, (
            ".pex-bar-fill CSS class not found in static Pulse render — "
            "static layout may have been broken by template changes"
        )

    def test_ex40_pulse_square_uses_dedicated_export_template(self, client):
        """Pulse × IG Square export must render the dedicated pex-card template.

        Verifies template routing: public/export/square/pulse.html is selected
        (not the editor template which has card-wrap / tab-bar chrome).
        """
        html = self._render_pulse_square(client, animated=False)
        assert html, "Export returned empty response for Pulse × instagram_square"
        assert "pex-card" in html, (
            "Dedicated Pulse export template not used — expected .pex-card root element"
        )
        assert "card-wrap" not in html, "Editor chrome (card-wrap) found in Pulse export HTML"
        assert "tab-bar" not in html, "tab-bar found in Pulse export HTML"


@pytest.mark.unit
class TestFullscreenLinkConsistency:
    """Open Fullscreen link URL contract — ensures no &export=1 leaks into human-browseable URLs.

    EX-61  dashboard_card_editor.html: _updateFullscreenLink does NOT append &export=1
    EX-62  portrait no-export request uses portrait export template (not default card)
    EX-63  banner_custom no-export request uses banner export template (not default card)
    EX-64  portrait no-export response has no tab-bar (confirms export template selected)
    EX-65  banner_custom no-export response has no tab-bar (confirms export template selected)
    """

    def test_ex61_fullscreen_link_js_has_no_export_flag(self):
        """EX-61: _updateFullscreenLink in dashboard_card_editor.html must NOT contain &export=1.

        The fullscreen link is for human browser preview; &export=1 is reserved for
        Playwright PNG/WebM rendering via card_export_service.py.
        """
        import os, app as _app_pkg
        tpl_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/dashboard_card_editor.html",
        )
        with open(tpl_path, encoding="utf-8") as f:
            src = f.read()
        fn_start = src.find("function _updateFullscreenLink()")
        fn_end   = src.find("}", fn_start)
        fn_body  = src[fn_start: fn_end + 1]
        assert fn_body, "EX-61: _updateFullscreenLink function not found in dashboard_card_editor.html"
        assert "&export=1" not in fn_body, (
            "EX-61: &export=1 found inside _updateFullscreenLink — "
            "fullscreen link must use ?platform=X (no export flag); "
            "Playwright export uses card_export_service.py directly"
        )

    def _get_fifa_preview_html(self, client, platform: str) -> str:
        """GET ?platform={platform} WITHOUT export=1 — simulates Open Fullscreen link."""
        from app.main import app
        from app.dependencies import get_db

        db = _mock_db(user=_make_user(), license_=_make_license(card_variant="fifa"))
        app.dependency_overrides[get_db] = lambda: db
        try:
            r = client.get(f"/players/7/card?platform={platform}")
            return r.text if r.status_code == 200 else ""
        finally:
            app.dependency_overrides.pop(get_db, None)

    def test_ex62_portrait_no_export_uses_portrait_template(self, client):
        """EX-62: GET ?platform=instagram_portrait (no export=1) must load portrait export template."""
        html = self._get_fifa_preview_html(client, "instagram_portrait")
        assert html, "EX-62: Card route returned empty response for instagram_portrait (no export)"
        assert "ex-card" in html, (
            "EX-62: ex-card not found — portrait preview did not load export/portrait/fifa.html; "
            "default card template was used instead (missing not-export branch)"
        )

    def test_ex63_banner_no_export_uses_banner_template(self, client):
        """EX-63: GET ?platform=banner_custom (no export=1) must load banner export template."""
        html = self._get_fifa_preview_html(client, "banner_custom")
        assert html, "EX-63: Card route returned empty response for banner_custom (no export)"
        assert "ex-card" in html, (
            "EX-63: ex-card not found — banner preview did not load export/banner/fifa.html; "
            "default card template was used instead (missing not-export branch)"
        )

    def test_ex64_portrait_no_export_has_no_editor_chrome(self, client):
        """EX-64: portrait no-export response must have no tab-bar (editor chrome absent)."""
        html = self._get_fifa_preview_html(client, "instagram_portrait")
        assert html, "EX-64: Card route returned empty response for instagram_portrait (no export)"
        assert "tab-bar" not in html, (
            "EX-64: tab-bar found in portrait no-export HTML — editor template loaded instead of "
            "export/portrait/fifa.html"
        )

    def test_ex65_banner_no_export_has_no_editor_chrome(self, client):
        """EX-65: banner_custom no-export response must have no tab-bar (editor chrome absent)."""
        html = self._get_fifa_preview_html(client, "banner_custom")
        assert html, "EX-65: Card route returned empty response for banner_custom (no export)"
        assert "tab-bar" not in html, (
            "EX-65: tab-bar found in banner_custom no-export HTML — editor template loaded instead "
            "of export/banner/fifa.html"
        )

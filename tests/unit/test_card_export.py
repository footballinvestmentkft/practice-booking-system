"""
Unit tests — GET /players/{user_id}/card/export
================================================

Coverage:
  EX-01  valid platform (instagram_square) → 200, image/png, PNG magic bytes
  EX-02  valid platform (instagram_story)  → 200
  EX-03  valid platform (facebook_landscape) → 200
  EX-04  valid platform (banner_custom)    → 200
  EX-05  valid platform (og)               → 200
  EX-06  missing platform param → defaults to "instagram_square", 200
  EX-07  invalid platform       → 422
  EX-08  "default" platform → 200 (native FClassic Player export via ?native_export=1)
  EX-09  player not found       → 404
  EX-10  no active LFA license  → 404
  EX-11  student exports other player → 403
  EX-12  admin exports any player → 200
  EX-13  Content-Disposition filename correct
  EX-14  Cache-Control: no-store header present
  EX-15  Playwright timeout → 504
  EX-16  rate limit exceeded    → 429
  EX-17  PNG dimensions correct (instagram_square = 1080×1080)
  EX-18  PNG magic bytes present
  EX-19  player without photo still returns PNG
  EX-20  instagram_portrait returns correct dimensions (1080×1350)
  EX-21  tiktok returns correct dimensions (1080×1920)
  EX-22  facebook_square returns correct dimensions (1080×1080)

Mock strategy:
  - get_current_user_web → MagicMock user (no DB, no cookie)
  - get_db              → MagicMock session returning preset user/license mocks
  - _export_svc._sync_take_screenshot → returns fixture PNG bytes (no Playwright)
  - rate counter reset between tests via reset_rate_counters()
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.models.user import UserRole
from app.services.card_export_service import (
    CANVAS_SIZES,
    CardExportTimeoutError,
    reset_rate_counters,
)

# ── PNG fixture factory ───────────────────────────────────────────────────────

def _make_png(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(30, 50, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# Pre-built PNG bytes for each canvas size
_PNG: dict[str, bytes] = {k: _make_png(*v) for k, v in CANVAS_SIZES.items()}
_PNG_DEFAULT = _PNG["instagram_square"]

_PNG_MAGIC = b"\x89PNG"


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_user(user_id: int = 4, role: UserRole = UserRole.STUDENT) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.role = role
    u.is_active = True
    return u


def _make_license() -> MagicMock:
    lic = MagicMock()
    lic.card_variant = "fclassic"  # fclassic supports all export buckets (CS-4a: supported_export_buckets validated)
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    return lic


def _mock_db(target_user=None, target_license=None, cdo_owned: bool = True):
    """Return a MagicMock db whose sequential .query() calls yield the given objects.

    cdo_owned=True (default) makes the CDO ownership check succeed (design is accessible).
    """
    db = MagicMock()
    q_user = MagicMock()
    q_user.filter.return_value.first.return_value = target_user
    q_license = MagicMock()
    q_license.filter.return_value.first.return_value = target_license
    # CardDraft query — get_or_create_singleton; published_variant=None falls back to license
    q_draft = MagicMock()
    _draft = MagicMock()
    _draft.published_variant = None
    q_draft.filter.return_value.first.return_value = _draft
    # CDO ownership check — every design (incl. fclassic) now requires a CDO row
    q_cdo = MagicMock()
    q_cdo.filter_by.return_value.first.return_value = MagicMock() if cdo_owned else None
    db.query.side_effect = [q_user, q_license, q_draft, q_cdo]
    return db


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


def _setup_overrides(app, current_user, db):
    """Apply dependency overrides; caller must restore afterward."""
    from app.dependencies import get_current_user_web, get_db

    async def _auth():
        return current_user

    app.dependency_overrides[get_current_user_web] = _auth
    app.dependency_overrides[get_db] = lambda: db


def _clear_overrides(app):
    app.dependency_overrides.clear()


# ── Helper: execute one export request with all mocks in place ────────────────

def _export(client, platform: str | None = "instagram_square", user_id: int = 4,
            current_user=None, db=None, png_bytes: bytes | None = None):
    from app.main import app

    if current_user is None:
        current_user = _make_user(user_id=user_id)
    if db is None:
        db = _mock_db(
            target_user=_make_user(user_id=user_id),
            target_license=_make_license(),
        )
    if png_bytes is None:
        png_bytes = _PNG.get(platform or "instagram_square", _PNG_DEFAULT)

    _setup_overrides(app, current_user, db)
    try:
        with patch("app.services.card_export_service._sync_take_screenshot",
                   return_value=png_bytes):
            url = f"/players/{user_id}/card/export"
            if platform is not None:
                url += f"?platform={platform}"
            return client.get(url)
    finally:
        _clear_overrides(app)


# ── Tests: happy path ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestExportHappyPath:

    def test_ex01_instagram_square_returns_200(self, client):
        r = _export(client, "instagram_square")
        assert r.status_code == 200

    def test_ex02_instagram_story_returns_200(self, client):
        r = _export(client, "instagram_story")
        assert r.status_code == 200

    def test_ex03_facebook_landscape_returns_200(self, client):
        r = _export(client, "facebook_landscape")
        assert r.status_code == 200

    def test_ex04_banner_custom_returns_200(self, client):
        r = _export(client, "banner_custom")
        assert r.status_code == 200

    def test_ex05_og_returns_200(self, client):
        r = _export(client, "og")
        assert r.status_code == 200

    def test_ex06_missing_platform_defaults_to_instagram_square(self, client):
        """No ?platform= param → defaults to instagram_square → 200."""
        r = _export(client, platform=None)
        assert r.status_code == 200

    def test_ex12_admin_exports_any_player(self, client):
        admin = _make_user(user_id=1, role=UserRole.ADMIN)
        r = _export(client, "instagram_square", user_id=4, current_user=admin)
        assert r.status_code == 200


# ── Tests: validation failures ────────────────────────────────────────────────

@pytest.mark.unit
class TestExportValidation:

    def test_ex07_invalid_platform_returns_422(self, client):
        r = _export(client, "foobar")
        assert r.status_code == 422

    def test_ex08_default_platform_returns_200(self, client):
        """'default' is the native FClassic Player export; uses ?native_export=1 render path."""
        r = _export(client, "default")
        assert r.status_code == 200

    def test_ex09_player_not_found_returns_404(self, client):
        from app.main import app
        from app.dependencies import get_current_user_web, get_db

        user = _make_user(user_id=4)
        db = _mock_db(target_user=None, target_license=None)

        async def _auth():
            return user

        app.dependency_overrides[get_current_user_web] = _auth
        app.dependency_overrides[get_db] = lambda: db
        try:
            with patch("app.services.card_export_service._sync_take_screenshot"):
                r = client.get("/players/4/card/export?platform=instagram_square")
        finally:
            _clear_overrides(app)

        assert r.status_code == 404

    def test_ex10_no_license_returns_404(self, client):
        from app.main import app
        from app.dependencies import get_current_user_web, get_db

        user = _make_user(user_id=4)
        db = _mock_db(
            target_user=_make_user(user_id=4),
            target_license=None,
        )

        async def _auth():
            return user

        app.dependency_overrides[get_current_user_web] = _auth
        app.dependency_overrides[get_db] = lambda: db
        try:
            with patch("app.services.card_export_service._sync_take_screenshot"):
                r = client.get("/players/4/card/export?platform=instagram_square")
        finally:
            _clear_overrides(app)

        assert r.status_code == 404

    def test_ex11_student_exports_other_player_returns_403(self, client):
        student = _make_user(user_id=99, role=UserRole.STUDENT)
        r = _export(client, "instagram_square", user_id=4, current_user=student)
        assert r.status_code == 403


# ── Tests: response headers ───────────────────────────────────────────────────

@pytest.mark.unit
class TestExportHeaders:

    def test_ex13_content_disposition_filename(self, client):
        r = _export(client, "instagram_square", user_id=4)
        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        assert 'filename="lfa_card_4_instagram_square.png"' in cd

    def test_ex14_cache_control_no_store(self, client):
        r = _export(client, "instagram_square")
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-store"

    def test_content_type_is_image_png(self, client):
        r = _export(client, "instagram_square")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"


# ── Tests: PNG output quality ─────────────────────────────────────────────────

@pytest.mark.unit
class TestExportPngOutput:

    def test_ex18_png_magic_bytes(self, client):
        r = _export(client, "instagram_square")
        assert r.status_code == 200
        assert r.content[:4] == _PNG_MAGIC

    def test_ex17_png_dimensions_instagram_square(self, client):
        r = _export(client, "instagram_square")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1080, 1080)

    def test_ex20_png_dimensions_instagram_portrait(self, client):
        r = _export(client, "instagram_portrait")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1080, 1350)

    def test_ex21_png_dimensions_tiktok(self, client):
        r = _export(client, "tiktok")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1080, 1920)

    def test_ex22_png_dimensions_facebook_square(self, client):
        r = _export(client, "facebook_square")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1080, 1080)

    def test_png_dimensions_facebook_landscape(self, client):
        r = _export(client, "facebook_landscape")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1200, 630)

    def test_png_dimensions_og(self, client):
        r = _export(client, "og")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1200, 630)

    def test_png_dimensions_instagram_story(self, client):
        r = _export(client, "instagram_story")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1080, 1920)

    def test_png_dimensions_banner_custom(self, client):
        r = _export(client, "banner_custom")
        assert r.status_code == 200
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (1500, 500)

    def test_ex19_player_without_photo_returns_png(self, client):
        """A player with no photo_url still renders (initials fallback)."""
        from app.main import app
        from app.dependencies import get_current_user_web, get_db

        user = _make_user(user_id=4)
        lic = _make_license()
        lic.player_card_photo_url = None  # no photo

        db = _mock_db(target_user=_make_user(user_id=4), target_license=lic)

        async def _auth():
            return user

        app.dependency_overrides[get_current_user_web] = _auth
        app.dependency_overrides[get_db] = lambda: db
        try:
            with patch("app.services.card_export_service._sync_take_screenshot",
                       return_value=_PNG["instagram_square"]):
                r = client.get("/players/4/card/export?platform=instagram_square")
        finally:
            _clear_overrides(app)

        assert r.status_code == 200
        assert r.content[:4] == _PNG_MAGIC


# ── Tests: error paths ────────────────────────────────────────────────────────

@pytest.mark.unit
class TestExportErrorPaths:

    def test_ex15_playwright_timeout_returns_504(self, client):
        from app.main import app
        from app.dependencies import get_current_user_web, get_db

        user = _make_user(user_id=4)
        db = _mock_db(
            target_user=_make_user(user_id=4),
            target_license=_make_license(),
        )

        async def _auth():
            return user

        app.dependency_overrides[get_current_user_web] = _auth
        app.dependency_overrides[get_db] = lambda: db
        try:
            with patch("app.services.card_export_service._sync_take_screenshot",
                       side_effect=CardExportTimeoutError("timeout")):
                r = client.get("/players/4/card/export?platform=instagram_square")
        finally:
            _clear_overrides(app)

        assert r.status_code == 504

    def test_ex16_rate_limit_exceeded_returns_429(self, client):
        """6th export from the same key within 60 s → 429."""
        from app.main import app
        from app.dependencies import get_current_user_web, get_db

        user = _make_user(user_id=4)

        async def _auth():
            return user

        def _fresh_db():
            return _mock_db(
                target_user=_make_user(user_id=4),
                target_license=_make_license(),
            )

        app.dependency_overrides[get_current_user_web] = _auth
        app.dependency_overrides[get_db] = _fresh_db
        try:
            with patch("app.services.card_export_service._sync_take_screenshot",
                       return_value=_PNG["instagram_square"]):
                for _ in range(5):
                    r = client.get("/players/4/card/export?platform=instagram_square")
                    assert r.status_code == 200, f"Expected 200 on warmup, got {r.status_code}"
                r = client.get("/players/4/card/export?platform=instagram_square")
        finally:
            _clear_overrides(app)

        assert r.status_code == 429


# ── Canvas size registry test ─────────────────────────────────────────────────

@pytest.mark.unit
class TestCanvasSizeRegistry:

    def test_all_presets_have_canvas_size(self):
        """Every non-native preset must have a CANVAS_SIZES entry.

        NATIVE ('default') has an entry too — it stores the documented baseline for
        the native-export BoundingClientRect clip path (not a template canvas target).
        """
        from app.services.card_platform_service import PLATFORM_PRESETS, LayoutStrategy
        for pid, preset in PLATFORM_PRESETS.items():
            if preset.layout_strategy == LayoutStrategy.NATIVE:
                # NATIVE platforms may or may not have a CANVAS_SIZES entry.
                # 'default' intentionally has one (820×800 documented baseline).
                pass
            else:
                assert pid in CANVAS_SIZES, f"Preset {pid!r} missing from CANVAS_SIZES"

    def test_canvas_sizes_all_positive(self):
        for pid, (w, h) in CANVAS_SIZES.items():
            assert w > 0 and h > 0, f"{pid}: dimensions must be positive"


# ── Playwright Chromium smoke test ────────────────────────────────────────────

@pytest.mark.unit
class TestPlaywrightEnvironment:

    def test_chromium_can_launch_and_screenshot(self):
        """Validates that the Playwright/Chromium binary is installed and functional.

        This test does NOT need the app server — it renders a minimal inline HTML page.
        Skipped automatically when the Chromium binary is not installed (e.g. in the
        general unit-test baseline check).  The Card Export Gate CI installs Chromium
        explicitly and exercises this test fully.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            pytest.skip("playwright package not installed")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 200, "height": 200})
                page.set_content("<html><body style='background:red'></body></html>")
                png = page.screenshot(
                    clip={"x": 0, "y": 0, "width": 200, "height": 200},
                    type="png",
                )
                browser.close()
        except Exception as exc:
            msg = str(exc).lower()
            if "executable" in msg or "not found" in msg or "browser" in msg:
                pytest.skip(f"Playwright Chromium binary not installed: {exc}")
            raise

        assert png[:4] == _PNG_MAGIC
        img = Image.open(io.BytesIO(png))
        assert img.size == (200, 200)

"""
Unit tests — GET /players/{user_id}/card/export/video
======================================================

Coverage:
  VX-01  fclassic + instagram_square → 200 video/webm + WebM magic bytes
  VX-02  fclassic + instagram_portrait → 422 (not animated capable)
  VX-03  compact + instagram_square → 422 (variant not animated capable)
  VX-04  invalid platform → 422
  VX-05  platform="default" → 422 (no canvas size, not an export target)
  VX-06  student exports other player's card → 403
  VX-07  admin exports any player's card → 200
  VX-08  video rate limit: 3rd request within 60s → 429 (limit=2)
  VX-09  Playwright/recording timeout → 504
  VX-10  player not found → 404
  VX-11  no active LFA Player license → 404
  VX-12  WebM magic bytes (\\x1aE\\xdf\\xa3) present in response body
  VX-13  PNG render URL never contains animated=1 (isolation invariant)
  VX-14  unsupported format → 422
  VX-15  unsupported duration → 422
  VX-16  Content-Disposition filename + Cache-Control headers correct
  VX-17  is_animated_capable() returns True for all registered pairs (fclassic + pulse)
  VX-18  ANIMATED_EXPORT_CAPABLE registry contains exactly fclassic+square AND pulse+square
  VX-19  pulse + instagram_square → 200 video/webm (new animated-capable pair)
  VX-20  pulse + instagram_portrait → 422 (pulse not capable on portrait)
  VX-21  pulse + instagram_story → 422 (pulse not capable on story)
  VX-22  format=mp4 → 200 video/mp4 (FFmpeg conversion mocked)
  VX-23  MP4 response body contains ftyp box at offset 4 (ISO Base Media magic)
  VX-24  FFmpeg failure → falls back to WebM + X-Export-Fallback: ffmpeg-failed header
  VX-25  unsupported format (avi) → 422
  VX-26  _sync_record_video source contains HTTP status guard (raises CardVideoRecordError on ≥400)

Mock strategy:
  - get_current_user_web → MagicMock user (no DB, no cookie)
  - get_db              → MagicMock session returning preset user/license mocks
  - _export_svc._sync_record_video → returns fixture WebM bytes (no Playwright)
  - _export_svc._webm_to_mp4      → returns fixture MP4 bytes (no FFmpeg)
  - video rate counters reset between tests via reset_video_rate_counters()
"""
from __future__ import annotations

import struct
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.user import UserRole
from app.services.card_export_service import (
    ANIMATED_EXPORT_CAPABLE,
    CardMp4ConvertError,
    CardVideoRecordError,
    is_animated_capable,
    reset_rate_counters,
    reset_video_rate_counters,
)

# ── WebM fixture ──────────────────────────────────────────────────────────────
# Minimal valid WebM magic bytes (EBML header start).
_WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
_WEBM_FIXTURE = _WEBM_MAGIC + b"\x00" * 64  # stub payload

# ── MP4 fixture ───────────────────────────────────────────────────────────────
# Minimal ISO Base Media File Format header: 4-byte box size + "ftyp" atom type.
# bytes[4:8] == b"ftyp" is the reliable magic-bytes check for MP4/ISOBMFF files.
_MP4_MAGIC   = b"ftyp"
_MP4_FIXTURE = b"\x00\x00\x00\x18" + _MP4_MAGIC + b"mp42" + b"\x00\x00\x00\x00" + b"mp42isom" + b"\x00" * 32


# ── Mock helpers ──────────────────────────────────────────────────────────────

def _make_user(user_id: int = 7, role: UserRole = UserRole.STUDENT) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.role = role
    u.is_active = True
    return u


def _make_license(card_variant: str = "fclassic") -> MagicMock:
    lic = MagicMock()
    lic.card_variant = card_variant
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    return lic


def _mock_db(target_user=None, target_license=None):
    db = MagicMock()
    q_user = MagicMock()
    q_user.filter.return_value.first.return_value = target_user
    q_license = MagicMock()
    q_license.filter.return_value.first.return_value = target_license
    q_license.filter_by.return_value.first.return_value = target_license

    q_ownership = MagicMock()
    q_ownership.filter_by.return_value.first.return_value = MagicMock()  # owned

    def _side_effect(model):
        from app.models.user import User
        from app.models.license import UserLicense
        from app.models.card_design_ownership import CardDesignOwnership
        if model is User:
            return q_user
        if model is UserLicense:
            return q_license
        if model is CardDesignOwnership:
            return q_ownership
        raise StopIteration  # CardDesign → caught by _load_cache → DESIGNS fallback

    db.query.side_effect = _side_effect
    return db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_counters():
    reset_rate_counters()
    reset_video_rate_counters()
    yield
    reset_rate_counters()
    reset_video_rate_counters()


@pytest.fixture()
def client():
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _setup_overrides(app, current_user, db):
    from app.dependencies import get_current_user_web, get_db

    async def _auth():
        return current_user

    app.dependency_overrides[get_current_user_web] = _auth
    app.dependency_overrides[get_db] = lambda: db


def _clear_overrides(app):
    app.dependency_overrides.clear()


def _video_export(
    client,
    platform: str = "instagram_square",
    user_id: int = 7,
    current_user=None,
    db=None,
    card_variant: str = "fclassic",
    webm_bytes: bytes = _WEBM_FIXTURE,
    format: str = "webm",
    duration: int = 5,
    mp4_bytes: bytes | None = None,
):
    """Make a video export request with Playwright and (optionally) FFmpeg mocked.

    mp4_bytes: when set, also mocks _webm_to_mp4 to return these bytes.
               Required for format=mp4 happy-path tests so no real FFmpeg is needed.
    """
    from contextlib import ExitStack
    from app.main import app

    if current_user is None:
        current_user = _make_user(user_id=user_id)
    if db is None:
        db = _mock_db(
            target_user=_make_user(user_id=user_id),
            target_license=_make_license(card_variant=card_variant),
        )

    _setup_overrides(app, current_user, db)
    try:
        url = (
            f"/players/{user_id}/card/export/video"
            f"?platform={platform}&format={format}&duration={duration}"
        )
        with ExitStack() as stack:
            stack.enter_context(patch(
                "app.services.card_export_service._sync_record_video",
                return_value=webm_bytes,
            ))
            if mp4_bytes is not None:
                stack.enter_context(patch(
                    "app.services.card_export_service._webm_to_mp4",
                    return_value=mp4_bytes,
                ))
            return client.get(url)
    finally:
        _clear_overrides(app)


# ── Tests: happy path ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestVideoExportHappyPath:

    def test_vx01_fifa_square_returns_200_webm(self, client):
        """fclassic + instagram_square is the only supported animated combo — must return 200."""
        r = _video_export(client, platform="instagram_square", card_variant="fclassic")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/webm")

    def test_vx07_admin_exports_any_player(self, client):
        """Admin may export any user's card regardless of ownership."""
        admin = _make_user(user_id=1, role=UserRole.ADMIN)
        r = _video_export(client, user_id=7, current_user=admin,
                          platform="instagram_square", card_variant="fclassic")
        assert r.status_code == 200

    def test_vx12_webm_magic_bytes_in_response(self, client):
        """Response body must start with WebM EBML magic bytes \\x1aE\\xdf\\xa3."""
        r = _video_export(client, platform="instagram_square", card_variant="fclassic")
        assert r.status_code == 200
        assert r.content[:4] == _WEBM_MAGIC

    def test_vx16_response_headers_correct(self, client):
        """Content-Disposition filename and Cache-Control must be set correctly."""
        r = _video_export(client, user_id=7, platform="instagram_square",
                          card_variant="fclassic")
        assert r.status_code == 200
        cd = r.headers.get("content-disposition", "")
        assert "lfa_card_7_instagram_square_animated.webm" in cd
        assert r.headers.get("cache-control") == "no-store"
        assert r.headers.get("x-export-platform") == "instagram_square"
        assert r.headers.get("x-export-format") == "webm"
        assert r.headers.get("x-export-duration") == "5"


# ── Tests: capability gating ──────────────────────────────────────────────────

@pytest.mark.unit
class TestVideoExportCapabilityGating:

    def test_vx02_fifa_portrait_returns_422(self, client):
        """fclassic + instagram_portrait is not animated-capable → 422, no video."""
        r = _video_export(client, platform="instagram_portrait", card_variant="fclassic")
        assert r.status_code == 422

    def test_vx03_compact_square_returns_422(self, client):
        """compact + instagram_square is not animated-capable → 422."""
        r = _video_export(client, platform="instagram_square", card_variant="compact")
        assert r.status_code == 422

    def test_vx04_invalid_platform_returns_422(self, client):
        """Unknown platform string → 422 before capability check."""
        r = _video_export(client, platform="foobar", card_variant="fclassic")
        assert r.status_code == 422

    def test_vx05_default_platform_returns_422(self, client):
        """'default' is not an export target (no canvas size) → 422."""
        r = _video_export(client, platform="default", card_variant="fclassic")
        assert r.status_code == 422

    def test_vx14_unsupported_format_returns_422(self, client):
        """format=avi is not supported → 422 (webm and mp4 are the only valid formats)."""
        r = _video_export(client, platform="instagram_square", card_variant="fclassic",
                          format="avi")
        assert r.status_code == 422

    def test_vx15_unsupported_duration_returns_422(self, client):
        """duration=10 is not supported in MVP → 422."""
        r = _video_export(client, platform="instagram_square", card_variant="fclassic",
                          duration=10)
        assert r.status_code == 422


# ── Tests: auth and ownership ─────────────────────────────────────────────────

@pytest.mark.unit
class TestVideoExportAuth:

    def test_vx06_student_exports_other_player_returns_403(self, client):
        """Student (id=99) requesting export for user_id=7 → 403."""
        attacker = _make_user(user_id=99, role=UserRole.STUDENT)
        db = _mock_db(
            target_user=_make_user(user_id=7),
            target_license=_make_license(card_variant="fclassic"),
        )
        r = _video_export(client, user_id=7, current_user=attacker, db=db,
                          platform="instagram_square", card_variant="fclassic")
        assert r.status_code == 403


# ── Tests: resource not found ─────────────────────────────────────────────────

@pytest.mark.unit
class TestVideoExportNotFound:

    def test_vx10_player_not_found_returns_404(self, client):
        """User not in DB → 404."""
        db = _mock_db(target_user=None, target_license=None)
        r = _video_export(client, user_id=7, db=db,
                          platform="instagram_square", card_variant="fclassic")
        assert r.status_code == 404

    def test_vx11_no_active_license_returns_404(self, client):
        """User exists but has no active LFA Player license → 404."""
        db = _mock_db(
            target_user=_make_user(user_id=7),
            target_license=None,
        )
        r = _video_export(client, user_id=7, db=db,
                          platform="instagram_square", card_variant="fclassic")
        assert r.status_code == 404


# ── Tests: rate limiting and errors ──────────────────────────────────────────

@pytest.mark.unit
class TestVideoExportRateAndErrors:

    def test_vx08_rate_limit_third_request_returns_429(self, client):
        """Video rate limit is 2/60s; 3rd request must return 429."""
        for _ in range(2):
            r = _video_export(client, platform="instagram_square", card_variant="fclassic")
            assert r.status_code == 200
        r = _video_export(client, platform="instagram_square", card_variant="fclassic")
        assert r.status_code == 429

    def test_vx09_recording_timeout_returns_504(self, client):
        """CardVideoRecordError from _sync_record_video → 504."""
        from app.main import app
        current_user = _make_user(user_id=7)
        db = _mock_db(
            target_user=_make_user(user_id=7),
            target_license=_make_license(card_variant="fclassic"),
        )
        _setup_overrides(app, current_user, db)
        try:
            with patch("app.services.card_export_service._sync_record_video",
                       side_effect=CardVideoRecordError("timed out")):
                r = client.get(
                    "/players/7/card/export/video"
                    "?platform=instagram_square&format=webm&duration=5"
                )
        finally:
            _clear_overrides(app)
        assert r.status_code == 504


# ── Tests: registry and isolation invariants ─────────────────────────────────

@pytest.mark.unit
class TestAnimatedCapabilityRegistry:

    def test_vx17_is_animated_capable_true_only_for_registered_pairs(self):
        """is_animated_capable must return True for all registered pairs and False for others."""
        assert is_animated_capable("fclassic", "instagram_square") is True
        assert is_animated_capable("pulse", "instagram_square") is True
        assert is_animated_capable("fclassic", "instagram_portrait") is False
        assert is_animated_capable("fclassic", "instagram_story") is False
        assert is_animated_capable("fclassic", "tiktok") is False
        assert is_animated_capable("pulse", "instagram_portrait") is False
        assert is_animated_capable("pulse", "instagram_story") is False
        assert is_animated_capable("compact",  "instagram_square") is False
        assert is_animated_capable("showcase", "instagram_square") is False
        assert is_animated_capable("atlas",    "instagram_square") is False
        assert is_animated_capable("", "") is False

    def test_vx18_registry_contains_exactly_fifa_and_pulse_square(self):
        """Registry must contain exactly two entries: fclassic+square and pulse+square.
        PR-FC-1B: canonical key is now 'fclassic'; 'fclassic' is a deprecated alias.
        """
        assert ANIMATED_EXPORT_CAPABLE == frozenset({
            ("fclassic", "instagram_square"),
            ("pulse",    "instagram_square"),
        })

    def test_vx13_png_render_url_never_contains_animated_param(self):
        """The PNG export endpoint must never include animated=1 in render_url.

        This is the key isolation invariant: static exports cannot accidentally
        activate the animated CSS block in the template.
        """
        import inspect
        import app.api.web_routes.public_player as _mod
        src = inspect.getsource(_mod.export_player_card)
        # The PNG endpoint render_url construction must not contain animated=1
        assert "animated=1" not in src, (
            "PNG export endpoint must never include animated=1 in render_url — "
            "this would activate animation CSS in static export templates"
        )
        # Confirm the video endpoint DOES include animated=1 (positive check)
        video_src = inspect.getsource(_mod.export_player_card_video)
        assert "animated=1" in video_src, (
            "Video export endpoint must include animated=1 in render_url"
        )


# ── Tests: Pulse × Instagram Square animated export ──────────────────────────

@pytest.mark.unit
class TestPulseVideoExport:
    """Video export tests for the Pulse × Instagram Square animated pair.

    VX-19  pulse + instagram_square → 200 video/webm
    VX-20  pulse + instagram_portrait → 422 (pulse not animated-capable on portrait)
    VX-21  pulse + instagram_story → 422 (pulse not animated-capable on story)
    """

    def test_vx19_pulse_square_returns_200_webm(self, client):
        """pulse + instagram_square is registered in ANIMATED_EXPORT_CAPABLE → 200."""
        r = _video_export(client, platform="instagram_square", card_variant="pulse")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/webm")

    def test_vx20_pulse_portrait_returns_422(self, client):
        """pulse + instagram_portrait is not animated-capable → 422."""
        r = _video_export(client, platform="instagram_portrait", card_variant="pulse")
        assert r.status_code == 422

    def test_vx21_pulse_story_returns_422(self, client):
        """pulse + instagram_story is not animated-capable → 422."""
        r = _video_export(client, platform="instagram_story", card_variant="pulse")
        assert r.status_code == 422


# ── Tests: MP4 export ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestMp4Export:
    """MP4 export tests — FFmpeg post-processing path.

    VX-22  format=mp4 → 200 video/mp4 (FFmpeg conversion mocked)
    VX-23  MP4 response body: ftyp box at bytes[4:8] (ISO Base Media magic)
    VX-24  FFmpeg failure → 200 video/webm (WebM fallback) + X-Export-Fallback header
    VX-25  unsupported format (avi) → 422
    """

    def test_vx22_mp4_format_returns_200_video_mp4(self, client):
        """format=mp4 with FFmpeg mocked must return 200 video/mp4."""
        r = _video_export(
            client, platform="instagram_square", card_variant="fclassic",
            format="mp4", mp4_bytes=_MP4_FIXTURE,
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/mp4")

    def test_vx23_mp4_magic_bytes_ftyp_at_offset_4(self, client):
        """MP4 response bytes[4:8] must equal b'ftyp' (ISO Base Media File Format marker)."""
        r = _video_export(
            client, platform="instagram_square", card_variant="fclassic",
            format="mp4", mp4_bytes=_MP4_FIXTURE,
        )
        assert r.status_code == 200
        assert r.content[4:8] == _MP4_MAGIC, (
            f"Expected ftyp at bytes[4:8], got {r.content[4:8]!r}"
        )

    def test_vx24_ffmpeg_failure_falls_back_to_webm(self, client):
        """CardMp4ConvertError from _webm_to_mp4 must fall back to WebM response.

        The response must be 200 (not 500), content-type video/webm, and include
        X-Export-Fallback: ffmpeg-failed so clients can detect the degradation.
        """
        from app.main import app
        current_user = _make_user(user_id=7)
        db = _mock_db(
            target_user=_make_user(user_id=7),
            target_license=_make_license(card_variant="fclassic"),
        )
        _setup_overrides(app, current_user, db)
        try:
            with patch("app.services.card_export_service._sync_record_video",
                       return_value=_WEBM_FIXTURE):
                with patch("app.services.card_export_service._webm_to_mp4",
                           side_effect=CardMp4ConvertError("ffmpeg binary not found")):
                    r = client.get(
                        "/players/7/card/export/video"
                        "?platform=instagram_square&format=mp4&duration=5"
                    )
        finally:
            _clear_overrides(app)

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/webm"), (
            "Fallback response must be video/webm, not mp4"
        )
        assert r.headers.get("x-export-fallback") == "ffmpeg-failed", (
            "X-Export-Fallback: ffmpeg-failed header must be set on fallback response"
        )
        assert r.headers.get("x-export-format") == "webm", (
            "X-Export-Format must reflect the actual delivered format (webm on fallback)"
        )

    def test_vx25_unsupported_format_avi_returns_422(self, client):
        """format=avi is not in _SUPPORTED_VIDEO_FORMATS → 422."""
        r = _video_export(
            client, platform="instagram_square", card_variant="fclassic",
            format="avi",
        )
        assert r.status_code == 422


# ── Tests: render URL HTTP guard ──────────────────────────────────────────────

@pytest.mark.unit
class TestRenderUrlHttpGuard:
    """VX-26: _sync_record_video must raise CardVideoRecordError when the render
    URL returns HTTP ≥ 400 instead of silently recording the white error page.

    Since _sync_record_video requires a live Playwright browser (# pragma: no cover),
    we verify the guard is present via source inspection.  The endpoint-level
    behaviour (CardVideoRecordError → 504) is already covered by VX-09.
    """

    def test_vx26_sync_record_video_has_http_status_guard(self):
        """_sync_record_video source must contain the HTTP ≥ 400 guard.

        This is the structural invariant that prevents Playwright from silently
        recording a white error page when the render URL returns 404 or 500.
        """
        import inspect
        from app.services import card_export_service as _svc

        src = inspect.getsource(_svc._sync_record_video)
        assert "http_status >= 400" in src, (
            "_sync_record_video must raise CardVideoRecordError when render URL "
            "returns HTTP >= 400 — missing guard means white error pages are "
            "silently recorded and returned as valid video"
        )
        assert "CardVideoRecordError" in src, (
            "_sync_record_video must raise CardVideoRecordError on HTTP error, "
            "not return silently"
        )
        assert "response.status" in src, (
            "_sync_record_video must capture the HTTP response status from page.goto()"
        )

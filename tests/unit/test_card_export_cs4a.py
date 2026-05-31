"""
Unit tests — CS-4a export routing: supported_export_buckets validation
======================================================================

Coverage:
  CS4-01  compact + instagram_square → 422 (bucket "square" not in compact's supported buckets)
  CS4-02  atlas   + instagram_story  → 422 (atlas has no supported export buckets)
  CS4-03  fifa    + instagram_square → 200 (square bucket declared for fifa)
  CS4-04  pulse   + instagram_square → 200 (square bucket declared for pulse)
  CS4-05  pulse   + instagram_portrait → 422 (portrait not in pulse's supported buckets)
  CS4-06  fifa    + "default"        → 200 (native export bypasses bucket check)
  CS4-07  422 detail contains design id, platform, and bucket name
  CS4-08  video: compact + instagram_square → 422 at is_animated_capable (pre-bucket)
  CS4-09  video: fifa + instagram_square mock_empty_buckets → 422 (new video bucket check)

Mock strategy:
  - get_current_user_web → MagicMock user (no DB, no cookie)
  - get_db              → MagicMock session (2-item side_effect; 3rd call raises StopIteration
                          → caught by card_design_service._maybe_reload → DESIGNS fallback)
  - _export_svc._sync_take_screenshot → returns fixture PNG bytes (no Playwright)
  - _export_svc._sync_record_video    → returns fixture WebM bytes (no Playwright)
  - rate counters reset between tests
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
    reset_rate_counters,
    reset_video_rate_counters,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

_WEBM_FIXTURE = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def _make_png(w: int, h: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


_PNG_SQ = _make_png(1080, 1080)


def _make_user(user_id: int = 4, role: UserRole = UserRole.STUDENT) -> MagicMock:
    u = MagicMock()
    u.id = user_id
    u.role = role
    u.is_active = True
    return u


def _make_license(card_variant: str = "fifa") -> MagicMock:
    lic = MagicMock()
    lic.card_variant = card_variant
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active = True
    return lic


def _mock_db(target_user=None, target_license=None) -> MagicMock:
    db = MagicMock()
    q_user = MagicMock()
    q_user.filter.return_value.first.return_value = target_user
    q_license = MagicMock()
    q_license.filter.return_value.first.return_value = target_license
    q_license.filter_by.return_value.first.return_value = target_license

    # Return a fake ownership row so the export guard (is_design_accessible) passes.
    # The existing 422/200 tests are about bucket validation, not ownership.
    q_ownership = MagicMock()
    q_ownership.filter_by.return_value.first.return_value = MagicMock()  # owned

    q_draft = MagicMock()
    _draft = MagicMock()
    _draft.published_variant = None  # falls back to license.card_variant
    q_draft.filter.return_value.first.return_value = _draft

    def _side_effect(model):
        from app.models.user import User
        from app.models.license import UserLicense
        from app.models.card_design_ownership import CardDesignOwnership
        from app.models.card_draft import CardDraft
        if model is User:
            return q_user
        if model is UserLicense:
            return q_license
        if model is CardDraft:
            return q_draft
        if model is CardDesignOwnership:
            return q_ownership
        # CardDesign or unknown: raise StopIteration, caught by _load_cache → DESIGNS fallback
        raise StopIteration

    db.query.side_effect = _side_effect
    return db


@pytest.fixture(autouse=True)
def _reset():
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


def _export(client, platform: str, card_variant: str = "fifa",
            user_id: int = 4, png: bytes = _PNG_SQ):
    from app.main import app
    from app.dependencies import get_current_user_web, get_db

    user = _make_user(user_id=user_id)
    db   = _mock_db(_make_user(user_id=user_id), _make_license(card_variant))

    async def _auth():
        return user

    app.dependency_overrides[get_current_user_web] = _auth
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch("app.services.card_export_service._sync_take_screenshot", return_value=png):
            return client.get(f"/players/{user_id}/card/export?platform={platform}")
    finally:
        app.dependency_overrides.clear()


def _video_export(client, platform: str, card_variant: str = "fifa", user_id: int = 7):
    from app.main import app
    from app.dependencies import get_current_user_web, get_db

    user = _make_user(user_id=user_id)
    db   = _mock_db(_make_user(user_id=user_id), _make_license(card_variant))

    async def _auth():
        return user

    app.dependency_overrides[get_current_user_web] = _auth
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch("app.services.card_export_service._sync_record_video",
                   return_value=_WEBM_FIXTURE):
            return client.get(
                f"/players/{user_id}/card/export/video"
                f"?platform={platform}&format=webm&duration=5"
            )
    finally:
        app.dependency_overrides.clear()


# ── PNG export: supported_export_buckets validation ───────────────────────────

@pytest.mark.unit
class TestCS4aPngBucketValidation:

    def test_cs4_01_compact_square_422(self, client):
        """compact has no supported_export_buckets → instagram_square → 422."""
        r = _export(client, "instagram_square", card_variant="compact")
        assert r.status_code == 422

    def test_cs4_02_atlas_story_422(self, client):
        """atlas has no supported_export_buckets → instagram_story → 422."""
        r = _export(client, "instagram_story", card_variant="atlas")
        assert r.status_code == 422

    def test_cs4_03_fifa_square_200(self, client):
        """fclassic declares square bucket → instagram_square → 200."""
        r = _export(client, "instagram_square", card_variant="fifa")
        assert r.status_code == 200

    def test_cs4_04_pulse_square_200(self, client):
        """pulse declares square bucket → instagram_square → 200."""
        r = _export(client, "instagram_square", card_variant="pulse",
                    png=_make_png(1080, 1080))
        assert r.status_code == 200

    def test_cs4_05_pulse_portrait_422(self, client):
        """pulse only supports square; portrait bucket not declared → 422."""
        r = _export(client, "instagram_portrait", card_variant="pulse")
        assert r.status_code == 422

    def test_cs4_06_fifa_default_200(self, client):
        """'default' platform bypasses bucket check → always allowed for any design."""
        r = _export(client, "default", card_variant="compact",
                    png=_make_png(820, 800))
        assert r.status_code == 200

    def test_cs4_07_422_detail_contains_design_and_bucket(self, client):
        """422 response detail must identify the design, platform, and bucket."""
        r = _export(client, "instagram_portrait", card_variant="compact")
        assert r.status_code == 422
        body = r.text
        assert "compact" in body
        assert "instagram_portrait" in body
        assert "portrait" in body  # bucket name


# ── Video export: supported_export_buckets validation ─────────────────────────

@pytest.mark.unit
class TestCS4aVideoBucketValidation:

    def test_cs4_08_compact_square_422_at_animated_check(self, client):
        """compact is not in ANIMATED_EXPORT_CAPABLE → 422 before bucket check."""
        r = _video_export(client, "instagram_square", card_variant="compact")
        assert r.status_code == 422

    def test_cs4_09_fifa_square_bucket_check_mocked_empty_422(self, client):
        """When supported_export_buckets is mocked empty for fclassic, video export → 422."""
        from app.main import app
        from app.dependencies import get_current_user_web, get_db

        user = _make_user(user_id=7)
        db   = _mock_db(_make_user(user_id=7), _make_license("fifa"))

        async def _auth():
            return user

        app.dependency_overrides[get_current_user_web] = _auth
        app.dependency_overrides[get_db] = lambda: db
        try:
            with patch("app.api.web_routes.public_player._get_supported_buckets",
                       return_value=()):  # mock empty → no buckets supported
                with patch("app.services.card_export_service._sync_record_video",
                           return_value=_WEBM_FIXTURE):
                    r = client.get(
                        "/players/7/card/export/video"
                        "?platform=instagram_square&format=webm&duration=5"
                    )
        finally:
            app.dependency_overrides.clear()

        assert r.status_code == 422
        assert "square" in r.text  # bucket name in error detail

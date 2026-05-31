"""
HVE — Highlight Video Editor tests.

Covers CardDraftService highlight-video methods + the two dashboard routes.
All tests use MagicMock — no real DB or HTTP server required.

Test list:
  HVE-01  Valid YouTube watch URL   → draft_data.highlight_video saved
  HVE-02  youtu.be short URL        → saved
  HVE-03  YouTube shorts URL        → saved
  HVE-04  Invalid URL               → ValueError / route returns 400, no save
  HVE-05  TikTok URL                → rejected (ValueError / 400)
  HVE-06  source_url never used as iframe src
  HVE-07  response embed_url uses youtube-nocookie domain
  HVE-08  Draft saved, publish not called → published_data untouched
  HVE-09  publish_draft copies draft_data.highlight_video → published_data
  HVE-10  remove_draft_highlight_video → draft_data key removed
  HVE-11  remove + publish → published_data.highlight_video removed
  HVE-12  CSRF: POST endpoint is not csrf_exempt (middleware enforces)
  HVE-13  Other user's draft cannot be modified (own-draft guard)
  HVE-14  published_data other keys not lost on publish
  HVE-15  Existing card publish flow not broken (theme/variant/platform still copied)
  HVE-16  is_published False when draft video differs from published video
  HVE-17  is_published True when all fields including video match
"""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from app.models.card_draft import CardDraft
from app.services.card_draft_service import CardDraftService

# ── Helpers ───────────────────────────────────────────────────────────────────

_TEST_USER_ID = 42


def _draft(
    draft_theme: str = "default",
    draft_variant: str = "fclassic",
    draft_platform: str | None = None,
    draft_data: dict | None = None,
    published_theme: str | None = "default",
    published_variant: str | None = "fclassic",
    published_platform: str | None = None,
    published_data: dict | None = None,
) -> CardDraft:
    d = CardDraft()
    d.id                = 7
    d.user_id           = _TEST_USER_ID
    d.card_type_id      = "player_card"
    d.instance_name     = "default"
    d.draft_theme       = draft_theme
    d.draft_variant     = draft_variant
    d.draft_platform    = draft_platform
    d.draft_data        = draft_data
    d.published_theme   = published_theme
    d.published_variant = published_variant
    d.published_platform = published_platform
    d.published_data    = published_data
    d.published_at      = datetime.now(timezone.utc)
    d.created_at        = datetime.now(timezone.utc)
    d.updated_at        = datetime.now(timezone.utc)
    return d


def _db() -> MagicMock:
    return MagicMock()


# ── HVE-01..03: valid YouTube URLs saved to draft_data ───────────────────────

class TestValidYouTubeUrls:

    def test_hve_01_watch_url_saved(self):
        """HVE-01: Standard YouTube watch URL → draft_data.highlight_video written."""
        draft = _draft()
        db = _db()
        CardDraftService.update_draft_highlight_video(
            db, draft, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        hv = (draft.draft_data or {}).get("highlight_video")
        assert hv is not None, "highlight_video key missing from draft_data"
        assert hv["video_id"] == "dQw4w9WgXcQ"
        assert hv["provider"] == "youtube"
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(draft)

    def test_hve_02_youtu_be_url_saved(self):
        """HVE-02: youtu.be short URL → video_id extracted and saved."""
        draft = _draft()
        db = _db()
        CardDraftService.update_draft_highlight_video(
            db, draft, "https://youtu.be/dQw4w9WgXcQ"
        )
        hv = (draft.draft_data or {}).get("highlight_video")
        assert hv is not None
        assert hv["video_id"] == "dQw4w9WgXcQ"

    def test_hve_03_shorts_url_saved(self):
        """HVE-03: YouTube Shorts URL → video_id extracted and saved."""
        draft = _draft()
        db = _db()
        CardDraftService.update_draft_highlight_video(
            db, draft, "https://www.youtube.com/shorts/dQw4w9WgXcQ"
        )
        hv = (draft.draft_data or {}).get("highlight_video")
        assert hv is not None
        assert hv["video_id"] == "dQw4w9WgXcQ"


# ── HVE-04/05: invalid URLs rejected ─────────────────────────────────────────

class TestInvalidUrls:

    def test_hve_04_invalid_url_raises_value_error(self):
        """HVE-04: Non-YouTube URL raises ValueError; draft_data untouched."""
        draft = _draft(draft_data=None)
        db = _db()
        with pytest.raises(ValueError):
            CardDraftService.update_draft_highlight_video(
                db, draft, "https://example.com/video"
            )
        assert draft.draft_data is None, "draft_data must not be modified on error"
        db.commit.assert_not_called()

    def test_hve_05_tiktok_short_url_rejected(self):
        """HVE-05: TikTok short URL (vm.tiktok.com) raises ValueError.

        Phase 1 rejected all TikTok URLs. Phase 2 accepts canonical TikTok URLs
        (tiktok.com/@user/video/{id}) but still rejects short-form URLs that
        would require a backend redirect to resolve.
        """
        draft = _draft(draft_data=None)
        db = _db()
        with pytest.raises(ValueError):
            CardDraftService.update_draft_highlight_video(
                db, draft, "https://vm.tiktok.com/ZMeABCDEF/"
            )
        assert draft.draft_data is None


# ── HVE-06: source_url not used as embed src ──────────────────────────────────

class TestSecurityInvariants:

    def test_hve_06_source_url_stored_not_used_as_iframe_src(self):
        """HVE-06: source_url stored for audit/prefill; embed_url built from video_id only."""
        draft = _draft()
        db = _db()
        raw_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share"
        CardDraftService.update_draft_highlight_video(db, draft, raw_url)
        hv = (draft.draft_data or {}).get("highlight_video")
        assert hv["source_url"] == raw_url, "source_url should be stored as-is"
        # Embed URL must be constructed from video_id, not source_url
        from app.services.highlight_video_service import build_youtube_embed_url
        expected_embed = build_youtube_embed_url(hv["video_id"])
        assert "youtube-nocookie.com" in expected_embed
        assert raw_url not in expected_embed, "source_url must never appear in embed_url"

    def test_hve_07_embed_url_uses_nocookie_domain(self):
        """HVE-07: build_youtube_embed_url always uses youtube-nocookie.com."""
        from app.services.highlight_video_service import build_youtube_embed_url
        url = build_youtube_embed_url("dQw4w9WgXcQ")
        assert "youtube-nocookie.com" in url
        assert url.startswith("https://")
        assert "dQw4w9WgXcQ" in url


# ── HVE-08: draft saved, published_data untouched before publish ─────────────

class TestDraftPublishedIsolation:

    def test_hve_08_saving_draft_does_not_touch_published_data(self):
        """HVE-08: update_draft_highlight_video never writes published_data."""
        draft = _draft(published_data={"some_key": "existing"})
        db = _db()
        CardDraftService.update_draft_highlight_video(
            db, draft, "https://youtu.be/dQw4w9WgXcQ"
        )
        assert draft.published_data == {"some_key": "existing"}, (
            "published_data must not be changed by update_draft_highlight_video"
        )

    def test_hve_09_publish_copies_highlight_video_to_published_data(self):
        """HVE-09: publish_draft merges draft_data.highlight_video → published_data."""
        draft = _draft(
            draft_data={"highlight_video": {
                "provider": "youtube", "video_id": "dQw4w9WgXcQ",
                "source_url": "https://youtu.be/dQw4w9WgXcQ",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }},
            published_data=None,
        )
        db = _db()
        CardDraftService.publish_draft(db, draft)
        pub_hv = (draft.published_data or {}).get("highlight_video")
        assert pub_hv is not None, "published_data.highlight_video must be set after publish"
        assert pub_hv["video_id"] == "dQw4w9WgXcQ"


# ── HVE-10/11: remove draft + publish ────────────────────────────────────────

class TestRemoveFlow:

    def test_hve_10_remove_deletes_draft_data_key(self):
        """HVE-10: remove_draft_highlight_video removes key from draft_data."""
        draft = _draft(draft_data={"highlight_video": {"video_id": "abc12345678"}})
        db = _db()
        CardDraftService.remove_draft_highlight_video(db, draft)
        assert "highlight_video" not in (draft.draft_data or {}), (
            "highlight_video key must be absent after removal"
        )
        db.commit.assert_called_once()

    def test_hve_11_remove_then_publish_clears_published_data(self):
        """HVE-11: remove + publish_draft → published_data.highlight_video deleted."""
        draft = _draft(
            draft_data=None,  # already removed from draft
            published_data={"highlight_video": {"video_id": "abc12345678"}},
        )
        db = _db()
        CardDraftService.publish_draft(db, draft)
        pub_hv = (draft.published_data or {}).get("highlight_video")
        assert pub_hv is None, (
            "After remove+publish, published_data.highlight_video must be absent"
        )


# ── HVE-12: CSRF not bypassed ─────────────────────────────────────────────────

class TestCsrfProtection:

    def test_hve_12_highlight_video_endpoints_are_not_csrf_exempt(self):
        """HVE-12: POST and DELETE endpoints do not carry _csrf_exempt=True attribute."""
        from app.api.web_routes.dashboard import (
            student_save_highlight_video,
            student_remove_highlight_video,
        )
        assert not getattr(student_save_highlight_video, "_csrf_exempt", False), (
            "POST endpoint must NOT be csrf_exempt — middleware enforces CSRF"
        )
        assert not getattr(student_remove_highlight_video, "_csrf_exempt", False), (
            "DELETE endpoint must NOT be csrf_exempt — middleware enforces CSRF"
        )


# ── HVE-13: own-draft guard via license check ─────────────────────────────────

class TestOwnDraftGuard:

    def test_hve_13_no_license_returns_404(self):
        """HVE-13: POST returns 404 when user has no active LFA Football Player license."""
        from app.api.web_routes.dashboard import (
            student_save_highlight_video,
            _HighlightVideoRequest,
        )
        mock_user = MagicMock()
        mock_user.id = _TEST_USER_ID
        mock_db = MagicMock()
        payload = MagicMock()
        payload.video_url = "https://youtu.be/dQw4w9WgXcQ"

        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=None):
            response = asyncio.run(student_save_highlight_video(
                payload=payload, db=mock_db, user=mock_user
            ))

        body = json.loads(response.body)
        assert response.status_code == 404
        assert body["ok"] is False


# ── HVE-14: other published_data keys preserved on publish ───────────────────

class TestPublishDataPreservation:

    def test_hve_14_other_published_data_keys_not_lost_on_publish(self):
        """HVE-14: publish_draft preserves other published_data keys (e.g., future fields)."""
        draft = _draft(
            draft_data={"highlight_video": {"video_id": "dQw4w9WgXcQ",
                                             "provider": "youtube"}},
            published_data={"some_other_feature": {"key": "value"}},
        )
        db = _db()
        CardDraftService.publish_draft(db, draft)
        assert draft.published_data.get("some_other_feature") == {"key": "value"}, (
            "publish_draft must not delete other keys from published_data"
        )
        assert draft.published_data.get("highlight_video") is not None


# ── HVE-15: existing card publish flow intact ─────────────────────────────────

class TestExistingPublishFlowIntact:

    def test_hve_15_publish_still_copies_theme_variant_platform(self):
        """HVE-15: publish_draft still copies theme/variant/platform (no regression)."""
        draft = _draft(
            draft_theme="midnight", draft_variant="compact", draft_platform="tiktok",
            draft_data=None,
        )
        db = _db()
        CardDraftService.publish_draft(db, draft)
        assert draft.published_theme    == "midnight"
        assert draft.published_variant  == "compact"
        assert draft.published_platform == "tiktok"
        assert draft.published_at is not None
        db.commit.assert_called_once()


# ── HVE-16/17: is_published with highlight video ─────────────────────────────

class TestIsPublishedWithVideo:

    def test_hve_16_is_published_false_when_video_differs(self):
        """HVE-16: is_published returns False when draft video_id differs from published."""
        draft = _draft(
            draft_data={"highlight_video": {"video_id": "NEW_VIDEO_ID_"}},
            published_data={"highlight_video": {"video_id": "OLD_VIDEO_ID_"}},
        )
        assert CardDraftService.is_published(draft) is False

    def test_hve_17_is_published_true_when_all_fields_match_including_video(self):
        """HVE-17: is_published returns True when theme/variant/platform + video_id all match."""
        draft = _draft(
            draft_theme="default", published_theme="default",
            draft_variant="fclassic",  published_variant="fclassic",
            draft_platform=None,   published_platform=None,
            draft_data={"highlight_video":    {"video_id": "dQw4w9WgXcQ"}},
            published_data={"highlight_video": {"video_id": "dQw4w9WgXcQ"}},
        )
        assert CardDraftService.is_published(draft) is True

    def test_hve_16b_is_published_false_when_draft_has_video_but_published_has_none(self):
        """HVE-16b: draft has video, published has none → is_published False."""
        draft = _draft(
            draft_data={"highlight_video": {"video_id": "dQw4w9WgXcQ"}},
            published_data=None,
        )
        # published_theme is None → always False (already covered by existing guard)
        # Set published_theme to simulate: had been published, then video added
        draft.published_theme = "default"
        assert CardDraftService.is_published(draft) is False

    def test_hve_16c_is_published_false_when_published_has_video_but_draft_removed(self):
        """HVE-16c: published has video, draft removed it → is_published False."""
        draft = _draft(
            draft_data=None,
            published_data={"highlight_video": {"video_id": "dQw4w9WgXcQ"}},
        )
        assert CardDraftService.is_published(draft) is False

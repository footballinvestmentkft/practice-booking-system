"""
TT — TikTok Highlight Video tests.

Covers TikTok support in highlight_video_service + CardDraftService.
All tests use MagicMock — no real DB or HTTP server required.

Test list:
  TT-01  Canonical TikTok URL → provider=tiktok, video_id correct
  TT-02  Canonical TikTok URL with query params → valid
  TT-03  TikTok video_id is numeric only, 15-25 digits
  TT-04  vm.tiktok.com short URL → ValueError with full-link message
  TT-05  vt.tiktok.com short URL → rejected (ValueError)
  TT-06  tiktok.com/t/ short URL → rejected (ValueError)
  TT-07  Invalid/XSS URL → None / ValueError, draft untouched
  TT-08  TikTok payload saved to draft_data via CardDraftService
  TT-09  publish_draft → published_data.provider=tiktok
  TT-10  get_published_highlight_video TikTok → watch_url returned, embed_url=None
  TT-11  TikTok state: embed_url is None (iframe will not be used)
  TT-12  embed.js script URL not present in TikTok embed_url
  TT-13  CSP in SecurityHeadersMiddleware does not contain tiktok domain
  TT-14  remove_draft_highlight_video + publish clears TikTok from published_data
  TT-15  is_published() False when provider differs (YouTube → TikTok)
  TT-16  YouTube regression: extract_youtube_id and HVE tests unaffected
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.services.highlight_video_service import (
    extract_tiktok_video_id,
    extract_any_video,
    build_tiktok_watch_url,
    get_published_highlight_video,
    extract_youtube_id,
    build_youtube_embed_url,
)
from app.services.card_draft_service import CardDraftService
from app.models.card_draft import CardDraft

# ── Helpers ───────────────────────────────────────────────────────────────────

_CANONICAL_URL = "https://www.tiktok.com/@someuser/video/7234567890123456789"
_VIDEO_ID = "7234567890123456789"


def _draft(**kwargs) -> CardDraft:
    d = CardDraft()
    d.id = 1
    d.user_id = 42
    d.card_type_id = "player_card"
    d.instance_name = "default"
    d.draft_theme = "default"
    d.draft_variant = "fclassic"
    d.draft_platform = None
    d.draft_data = kwargs.get("draft_data", None)
    d.published_theme = kwargs.get("published_theme", "default")
    d.published_variant = kwargs.get("published_variant", "fclassic")
    d.published_platform = None
    d.published_data = kwargs.get("published_data", None)
    d.published_at = datetime.now(timezone.utc)
    d.created_at = datetime.now(timezone.utc)
    d.updated_at = datetime.now(timezone.utc)
    return d


def _db() -> MagicMock:
    return MagicMock()


# ── TT-01: canonical TikTok URL → provider=tiktok ────────────────────────────

class TestCanonicalTikTokUrl:

    def test_tt_01_canonical_url_returns_correct_video_id(self):
        """TT-01: Canonical TikTok URL → provider=tiktok, video_id extracted."""
        result = extract_any_video(_CANONICAL_URL)
        assert result is not None
        assert result["provider"] == "tiktok"
        assert result["video_id"] == _VIDEO_ID

    def test_tt_02_canonical_url_with_query_params_valid(self):
        """TT-02: TikTok canonical URL with query parameters → video_id extracted."""
        url = f"{_CANONICAL_URL}?is_from_webapp=1&sender_device=pc"
        result = extract_any_video(url)
        assert result is not None
        assert result["provider"] == "tiktok"
        assert result["video_id"] == _VIDEO_ID

    def test_tt_03_video_id_is_numeric_and_correct_length(self):
        """TT-03: Extracted TikTok video_id is purely numeric, 15-25 digits."""
        vid = extract_tiktok_video_id(_CANONICAL_URL)
        assert vid is not None
        assert vid.isdigit(), "video_id must be numeric only"
        assert 15 <= len(vid) <= 25, f"video_id length {len(vid)} out of range [15,25]"


# ── TT-04..06: short URLs rejected ───────────────────────────────────────────

class TestShortUrlsRejected:

    def test_tt_04_vm_tiktok_raises_value_error_with_full_link_message(self):
        """TT-04: vm.tiktok.com → ValueError; message tells user to paste full link."""
        with pytest.raises(ValueError) as exc_info:
            extract_any_video("https://vm.tiktok.com/ZMeABCDEF/")
        assert "full TikTok video link" in str(exc_info.value).lower() or \
               "tiktok.com/@" in str(exc_info.value)

    def test_tt_05_vt_tiktok_raises_value_error(self):
        """TT-05: vt.tiktok.com → ValueError."""
        with pytest.raises(ValueError):
            extract_any_video("https://vt.tiktok.com/ZMeABCDEF/")

    def test_tt_06_tiktok_t_short_url_raises_value_error(self):
        """TT-06: tiktok.com/t/ short URL → ValueError."""
        with pytest.raises(ValueError):
            extract_any_video("https://www.tiktok.com/t/ZMeABCDEF/")


# ── TT-07: invalid / XSS URLs rejected ───────────────────────────────────────

class TestInvalidUrlsRejected:

    def test_tt_07a_arbitrary_url_returns_none(self):
        """TT-07a: Non-TikTok, non-YouTube URL → None (no exception)."""
        result = extract_any_video("https://example.com/video/1234567890123456789")
        assert result is None

    def test_tt_07b_javascript_xss_url_returns_none(self):
        """TT-07b: javascript: URL → None (scheme check rejects it)."""
        result = extract_any_video("javascript:alert(1)")
        assert result is None

    def test_tt_07c_data_uri_returns_none(self):
        """TT-07c: data: URI → None."""
        result = extract_any_video("data:text/html,<script>alert(1)</script>")
        assert result is None

    def test_tt_07d_draft_untouched_on_invalid_tiktok_url(self):
        """TT-07d: Invalid URL → ValueError; draft_data must not be modified."""
        draft = _draft(draft_data=None)
        db = _db()
        with pytest.raises(ValueError):
            CardDraftService.update_draft_highlight_video(db, draft, "https://example.com/bad")
        assert draft.draft_data is None
        db.commit.assert_not_called()


# ── TT-08: TikTok payload saved to draft_data ────────────────────────────────

class TestDraftDataSaved:

    def test_tt_08_tiktok_payload_saved_to_draft_data(self):
        """TT-08: update_draft_highlight_video saves provider=tiktok to draft_data."""
        draft = _draft()
        db = _db()
        CardDraftService.update_draft_highlight_video(db, draft, _CANONICAL_URL)
        hv = (draft.draft_data or {}).get("highlight_video")
        assert hv is not None
        assert hv["provider"] == "tiktok"
        assert hv["video_id"] == _VIDEO_ID
        assert hv["source_url"] == _CANONICAL_URL
        db.commit.assert_called_once()


# ── TT-09: publish_draft copies TikTok to published_data ─────────────────────

class TestPublishDraftTikTok:

    def test_tt_09_publish_sets_published_data_provider_tiktok(self):
        """TT-09: publish_draft copies draft_data.highlight_video → published_data with provider=tiktok."""
        draft = _draft(
            draft_data={"highlight_video": {
                "provider": "tiktok",
                "video_id": _VIDEO_ID,
                "source_url": _CANONICAL_URL,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }},
            published_data=None,
        )
        db = _db()
        CardDraftService.publish_draft(db, draft)
        pub_hv = (draft.published_data or {}).get("highlight_video")
        assert pub_hv is not None
        assert pub_hv["provider"] == "tiktok"
        assert pub_hv["video_id"] == _VIDEO_ID


# ── TT-10 / TT-11: get_published_highlight_video TikTok response ─────────────

class TestPublishedHighlightVideoTikTok:

    def _tiktok_draft(self) -> CardDraft:
        d = CardDraft()
        d.published_data = {
            "highlight_video": {
                "provider": "tiktok",
                "video_id": _VIDEO_ID,
                "source_url": _CANONICAL_URL,
            }
        }
        return d

    def test_tt_10_tiktok_returns_watch_url(self):
        """TT-10: get_published_highlight_video TikTok → watch_url present and correct."""
        result = get_published_highlight_video(self._tiktok_draft())
        assert result is not None
        assert result["provider"] == "tiktok"
        assert result["video_id"] == _VIDEO_ID
        assert result["watch_url"] is not None
        assert "tiktok.com" in result["watch_url"]

    def test_tt_11_tiktok_embed_url_is_none(self):
        """TT-11: TikTok embed_url is None → no iframe src will be rendered."""
        result = get_published_highlight_video(self._tiktok_draft())
        assert result is not None
        assert result["embed_url"] is None, (
            "embed_url must be None for TikTok — no iframe should be used"
        )


# ── TT-12: embed.js not referenced ───────────────────────────────────────────

class TestNoEmbedScript:

    def test_tt_12_tiktok_watch_url_does_not_contain_embed_js(self):
        """TT-12: watch_url for TikTok never references embed.js or TikTok script path."""
        d = CardDraft()
        d.published_data = {
            "highlight_video": {
                "provider": "tiktok",
                "video_id": _VIDEO_ID,
                "source_url": _CANONICAL_URL,
            }
        }
        result = get_published_highlight_video(d)
        watch = result["watch_url"] or ""
        assert "embed.js" not in watch
        assert "/embed/" not in watch


# ── TT-13: CSP does not include TikTok domain ────────────────────────────────

class TestCspNoTikTok:

    def test_tt_13_csp_does_not_include_tiktok_domain(self):
        """TT-13: SecurityHeadersMiddleware CSP does not whitelist any TikTok domain."""
        from app.middleware.security import SecurityHeadersMiddleware
        mw = SecurityHeadersMiddleware(app=MagicMock())
        csp = mw.csp_policy
        assert "tiktok.com" not in csp, (
            f"CSP must not contain tiktok.com — Option A is link-only. Got: {csp}"
        )
        assert "tiktokcdn.com" not in csp, (
            "CSP must not contain tiktokcdn.com"
        )


# ── TT-14: remove + publish clears TikTok from published_data ────────────────

class TestRemoveFlowTikTok:

    def test_tt_14_remove_then_publish_clears_tiktok_from_published_data(self):
        """TT-14: remove_draft_highlight_video + publish_draft removes TikTok from published_data."""
        draft = _draft(
            draft_data=None,  # already removed from draft
            published_data={"highlight_video": {
                "provider": "tiktok",
                "video_id": _VIDEO_ID,
                "source_url": _CANONICAL_URL,
            }},
        )
        db = _db()
        CardDraftService.publish_draft(db, draft)
        pub_hv = (draft.published_data or {}).get("highlight_video")
        assert pub_hv is None, (
            "After remove+publish, published_data.highlight_video must be absent"
        )


# ── TT-15: is_published False when provider differs ──────────────────────────

class TestIsPublishedProviderCheck:

    def test_tt_15a_is_published_false_when_provider_differs(self):
        """TT-15: is_published() returns False when draft=tiktok but published=youtube."""
        draft = _draft(
            draft_data={"highlight_video": {"provider": "tiktok", "video_id": _VIDEO_ID}},
            published_data={"highlight_video": {"provider": "youtube", "video_id": "dQw4w9WgXcQ"}},
        )
        assert CardDraftService.is_published(draft) is False

    def test_tt_15b_is_published_false_youtube_to_tiktok_switch(self):
        """TT-15b: Switching from YouTube to TikTok with same video_id → is_published False."""
        shared_id = "7234567890123456789"
        draft = _draft(
            draft_data={"highlight_video": {"provider": "tiktok", "video_id": shared_id}},
            published_data={"highlight_video": {"provider": "youtube", "video_id": shared_id}},
        )
        assert CardDraftService.is_published(draft) is False, (
            "Provider mismatch must make is_published False even if video_id is equal"
        )

    def test_tt_15c_is_published_true_when_both_tiktok_same_video(self):
        """TT-15c: is_published True when both draft and published are tiktok with same video_id."""
        draft = _draft(
            draft_data={"highlight_video": {"provider": "tiktok", "video_id": _VIDEO_ID}},
            published_data={"highlight_video": {"provider": "tiktok", "video_id": _VIDEO_ID}},
        )
        assert CardDraftService.is_published(draft) is True


# ── TT-16: YouTube regression ─────────────────────────────────────────────────

class TestYouTubeRegression:

    def test_tt_16a_youtube_watch_url_still_extracted(self):
        """TT-16: extract_youtube_id still works for standard watch URL."""
        vid = extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert vid == "dQw4w9WgXcQ"

    def test_tt_16b_youtube_embed_url_uses_nocookie(self):
        """TT-16: build_youtube_embed_url still returns youtube-nocookie.com."""
        url = build_youtube_embed_url("dQw4w9WgXcQ")
        assert "youtube-nocookie.com" in url

    def test_tt_16c_extract_any_video_youtube_still_works(self):
        """TT-16: extract_any_video correctly identifies YouTube URLs as provider=youtube."""
        result = extract_any_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert result is not None
        assert result["provider"] == "youtube"
        assert result["video_id"] == "dQw4w9WgXcQ"

    def test_tt_16d_get_published_highlight_video_youtube_still_returns_embed_url(self):
        """TT-16: get_published_highlight_video YouTube → embed_url present (not None)."""
        d = CardDraft()
        d.published_data = {
            "highlight_video": {"provider": "youtube", "video_id": "dQw4w9WgXcQ"}
        }
        result = get_published_highlight_video(d)
        assert result is not None
        assert result["provider"] == "youtube"
        assert result["embed_url"] is not None
        assert "youtube-nocookie.com" in result["embed_url"]

    def test_tt_16e_tiktok_url_does_not_match_youtube_extractor(self):
        """TT-16: extract_youtube_id returns None for TikTok URLs."""
        assert extract_youtube_id(_CANONICAL_URL) is None

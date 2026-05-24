"""HV-01..HV-11 — Highlight Video service (YouTube-only Phase 1).

HV-01  youtube.com/watch?v= → valid video_id
HV-02  youtu.be/ → valid video_id
HV-03  youtube.com/shorts/ → valid video_id
HV-04  Invalid URL → None
HV-05  Non-allowlisted domain → None
HV-06  javascript: XSS attempt → None
HV-07  published_data with youtube video → embed_url uses youtube-nocookie.com
HV-08  No highlight_video in published_data → None
HV-09  Template contains responsive 16:9 aspect-ratio wrapper
HV-10  CSP contains youtube-nocookie frame-src
HV-11  TikTok URL → None (Option A: YouTube-only)
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from app.services.highlight_video_service import (
    build_youtube_embed_url,
    extract_youtube_id,
    get_published_highlight_video,
    make_highlight_video_published_data,
)

_VALID_ID = "dQw4w9WgXcQ"


# ── HV-01..HV-06, HV-11: extract_youtube_id ───────────────────────────────────

class TestExtractYoutubeId:

    # HV-01 — watch URL
    def test_hv01_watch_url(self):
        url = f"https://www.youtube.com/watch?v={_VALID_ID}"
        assert extract_youtube_id(url) == _VALID_ID

    # HV-01b — watch URL with extra params
    def test_hv01b_watch_url_extra_params(self):
        url = f"https://www.youtube.com/watch?v={_VALID_ID}&t=30s&feature=share"
        assert extract_youtube_id(url) == _VALID_ID

    # HV-02 — youtu.be short URL
    def test_hv02_youtu_be_short_url(self):
        url = f"https://youtu.be/{_VALID_ID}"
        assert extract_youtube_id(url) == _VALID_ID

    # HV-03 — youtube.com/shorts/
    def test_hv03_shorts_url(self):
        url = f"https://www.youtube.com/shorts/{_VALID_ID}"
        assert extract_youtube_id(url) == _VALID_ID

    # HV-03b — shorts without www
    def test_hv03b_shorts_no_www(self):
        url = f"https://youtube.com/shorts/{_VALID_ID}"
        assert extract_youtube_id(url) == _VALID_ID

    # HV-04 — invalid URL (no YouTube domain)
    def test_hv04_invalid_url_returns_none(self):
        assert extract_youtube_id("not-a-url") is None

    # HV-04b — empty string
    def test_hv04b_empty_string(self):
        assert extract_youtube_id("") is None

    # HV-04c — None type
    def test_hv04c_none_input(self):
        assert extract_youtube_id(None) is None  # type: ignore[arg-type]

    # HV-05 — non-allowlisted domain
    def test_hv05_vimeo_url_rejected(self):
        assert extract_youtube_id("https://vimeo.com/123456789AB") is None

    def test_hv05b_arbitrary_domain_rejected(self):
        assert extract_youtube_id("https://evil.com/watch?v=dQw4w9WgXcQ") is None

    # HV-06 — javascript: XSS attempt
    def test_hv06_javascript_scheme_rejected(self):
        assert extract_youtube_id("javascript:alert(1)") is None

    def test_hv06b_data_scheme_rejected(self):
        assert extract_youtube_id("data:text/html,<script>alert(1)</script>") is None

    # HV-11 — TikTok URL → None (Option A: YouTube-only)
    def test_hv11_tiktok_url_rejected(self):
        assert extract_youtube_id("https://www.tiktok.com/@user/video/1234567890123456789") is None

    def test_hv11b_tiktok_vm_url_rejected(self):
        assert extract_youtube_id("https://vm.tiktok.com/ZMabcdef/") is None


# ── build_youtube_embed_url ────────────────────────────────────────────────────

class TestBuildYoutubeEmbedUrl:

    def test_embed_url_uses_nocookie_domain(self):
        url = build_youtube_embed_url(_VALID_ID)
        assert "youtube-nocookie.com" in url

    def test_embed_url_contains_video_id(self):
        url = build_youtube_embed_url(_VALID_ID)
        assert _VALID_ID in url

    def test_embed_url_format(self):
        url = build_youtube_embed_url(_VALID_ID)
        assert url == f"https://www.youtube-nocookie.com/embed/{_VALID_ID}"


# ── HV-07..HV-08: get_published_highlight_video ───────────────────────────────

class TestGetPublishedHighlightVideo:

    def _draft(self, published_data):
        d = MagicMock()
        d.published_data = published_data
        return d

    # HV-07 — published_data with valid youtube entry → embed_url
    def test_hv07_youtube_video_in_published_data(self):
        draft = self._draft({"highlight_video": {"provider": "youtube", "video_id": _VALID_ID}})
        result = get_published_highlight_video(draft)
        assert result is not None
        assert result["video_id"] == _VALID_ID
        assert "youtube-nocookie.com" in result["embed_url"]
        assert "youtube.com/watch?v=" in result["watch_url"]

    # HV-08 — no highlight_video key → None
    def test_hv08_no_highlight_video_key(self):
        draft = self._draft({})
        assert get_published_highlight_video(draft) is None

    # HV-08b — published_data is None → None
    def test_hv08b_null_published_data(self):
        draft = self._draft(None)
        assert get_published_highlight_video(draft) is None

    # HV-08c — card_draft is None → None
    def test_hv08c_null_draft(self):
        assert get_published_highlight_video(None) is None

    # HV-08d — wrong provider → None
    def test_hv08d_wrong_provider(self):
        draft = self._draft({"highlight_video": {"provider": "tiktok", "video_id": _VALID_ID}})
        assert get_published_highlight_video(draft) is None

    # HV-08e — invalid video_id (too short) → None
    def test_hv08e_invalid_video_id(self):
        draft = self._draft({"highlight_video": {"provider": "youtube", "video_id": "tooshort"}})
        assert get_published_highlight_video(draft) is None

    # HV-08f — video_id with HTML injection attempt → None (regex blocks it)
    def test_hv08f_xss_video_id_rejected(self):
        draft = self._draft({"highlight_video": {"provider": "youtube", "video_id": "<script>alert"}})
        assert get_published_highlight_video(draft) is None


# ── make_highlight_video_published_data (seeding helper) ──────────────────────

class TestMakeHighlightVideoPublishedData:

    def test_valid_watch_url_produces_payload(self):
        payload = make_highlight_video_published_data(f"https://youtu.be/{_VALID_ID}")
        assert payload is not None
        assert payload["highlight_video"]["video_id"] == _VALID_ID
        assert payload["highlight_video"]["provider"] == "youtube"
        assert "added_at" in payload["highlight_video"]

    def test_invalid_url_returns_none(self):
        assert make_highlight_video_published_data("https://vimeo.com/123") is None

    def test_tiktok_url_returns_none(self):
        assert make_highlight_video_published_data("https://tiktok.com/@u/video/123") is None


# ── HV-09: template structural assertions ────────────────────────────────────

_TMPL_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..",
    "app", "templates", "public", "player_profile.html",
))


class TestHighlightVideoTemplate:

    def _src(self):
        with open(_TMPL_PATH, encoding="utf-8") as f:
            return f.read()

    # HV-09 — 16:9 responsive wrapper present in CSS
    def test_hv09_video_wrapper_aspect_ratio(self):
        src = self._src()
        assert ".psp-video-wrapper" in src
        assert "aspect-ratio: 16 / 9" in src

    # HV-09b — iframe is inside psp-video-wrapper conditional
    def test_hv09b_iframe_inside_highlight_conditional(self):
        src = self._src()
        assert "{% if highlight_video %}" in src
        assert "highlight_video.embed_url" in src

    # HV-09c — empty state preserved for no-video case
    def test_hv09c_empty_state_in_else_branch(self):
        src = self._src()
        assert "Coming Soon" in src

    # HV-09d — sandbox attribute present on video iframe
    def test_hv09d_iframe_has_sandbox(self):
        src = self._src()
        assert 'sandbox="allow-scripts allow-same-origin allow-presentation allow-popups"' in src

    # HV-09e — allowfullscreen present
    def test_hv09e_iframe_allowfullscreen(self):
        src = self._src()
        assert "allowfullscreen" in src

    # HV-09f — loading=lazy present
    def test_hv09f_iframe_loading_lazy(self):
        src = self._src()
        assert 'loading="lazy"' in src


# ── HV-10: CSP structural assertion ──────────────────────────────────────────

_SEC_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..",
    "app", "middleware", "security.py",
))


class TestCspHighlightVideo:

    def _src(self):
        with open(_SEC_PATH, encoding="utf-8") as f:
            return f.read()

    # HV-10 — CSP contains youtube-nocookie in frame-src
    def test_hv10_csp_contains_youtube_nocookie_frame_src(self):
        src = self._src()
        assert "frame-src" in src
        assert "youtube-nocookie.com" in src

    # HV-10b — CSP does not allow arbitrary wildcard in frame-src
    def test_hv10b_no_wildcard_frame_src(self):
        src = self._src()
        assert "frame-src *" not in src
        assert "frame-src 'unsafe" not in src

    # HV-10c — no TikTok script-src (Option A: TikTok excluded)
    def test_hv10c_no_tiktok_script_src(self):
        src = self._src()
        assert "tiktok.com" not in src

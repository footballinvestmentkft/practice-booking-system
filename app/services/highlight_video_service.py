"""Highlight video helper — YouTube-only Phase 1.

Supported URL formats:
  youtube.com/watch?v={11-char id}
  youtu.be/{11-char id}
  youtube.com/shorts/{11-char id}

Security contract:
  - User-supplied URL is NEVER passed as iframe src.
  - Only the extracted video_id (regex-validated to [A-Za-z0-9_-]{11}) is used.
  - Embed URL is always constructed from the allowlisted youtube-nocookie.com domain.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_YT_PATTERNS = [
    re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/))([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtu\.be/)([A-Za-z0-9_-]{11})"),
]

_EMBED_BASE = "https://www.youtube-nocookie.com/embed/"


def extract_youtube_id(url: str) -> str | None:
    """Return the 11-char YouTube video ID from a watch/short/youtu.be URL, or None."""
    if not isinstance(url, str):
        return None
    # Reject obviously dangerous schemes before regex
    stripped = url.strip().lower()
    if not stripped.startswith(("http://", "https://")):
        return None
    for pattern in _YT_PATTERNS:
        m = pattern.search(url)
        if m:
            vid_id = m.group(1)
            if _YT_ID_RE.match(vid_id):
                return vid_id
    return None


def build_youtube_embed_url(video_id: str) -> str:
    """Construct the privacy-enhanced embed URL from a validated video ID."""
    return f"{_EMBED_BASE}{video_id}"


def get_published_highlight_video(card_draft: Any) -> dict | None:
    """Read highlight_video from CardDraft.published_data; return structured dict or None.

    Expected published_data shape:
        {"highlight_video": {"provider": "youtube", "video_id": "...", ...}}
    """
    if card_draft is None:
        return None
    try:
        pub_data = card_draft.published_data
    except AttributeError:
        return None
    if not isinstance(pub_data, dict):
        return None
    hv = pub_data.get("highlight_video")
    if not isinstance(hv, dict):
        return None
    provider = hv.get("provider")
    video_id = hv.get("video_id")
    if provider != "youtube" or not isinstance(video_id, str):
        return None
    if not _YT_ID_RE.match(video_id):
        return None
    return {
        "provider":  "youtube",
        "video_id":  video_id,
        "embed_url": build_youtube_embed_url(video_id),
        "watch_url": f"https://www.youtube.com/watch?v={video_id}",
    }


def make_highlight_video_published_data(video_url: str) -> dict | None:
    """Build the published_data payload from a user-supplied URL (for seeding/testing).

    Returns None if URL is invalid or not a supported YouTube format.
    """
    video_id = extract_youtube_id(video_url)
    if video_id is None:
        return None
    return {
        "highlight_video": {
            "provider":  "youtube",
            "video_id":  video_id,
            "added_at":  datetime.now(timezone.utc).isoformat(),
        }
    }

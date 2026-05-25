"""Profile Grid Service — slot registry and module validation.

Slot naming is layout-neutral: slot_id never encodes physical position.
Desktop renders: side_a | side_b | featured_card | side_c | side_d
Responsive layouts may reorder columns — slot_ids stay stable.

Zones:
  side_a_1/2/3 → Side A lane (3 slots, top→bottom)
  side_b_1/2/3 → Side B lane (3 slots, top→bottom)
  side_c_1/2/3 → Side C lane (3 slots, top→bottom)
  side_d_1/2/3 → Side D lane (3 slots, top→bottom)
  bottom_a/b/c → Bottom row (left→right)

  MAX_SLOTS = 15

Phase 1 module types: video_youtube, video_tiktok (link-only).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.services.highlight_video_service import extract_any_video

SLOT_REGISTRY: list[dict] = [
    {"slot_id": "side_a_1", "zone": "side_a", "label": "Side A — 1", "sort_order":  1},
    {"slot_id": "side_a_2", "zone": "side_a", "label": "Side A — 2", "sort_order":  2},
    {"slot_id": "side_a_3", "zone": "side_a", "label": "Side A — 3", "sort_order":  3},
    {"slot_id": "side_b_1", "zone": "side_b", "label": "Side B — 1", "sort_order": 10},
    {"slot_id": "side_b_2", "zone": "side_b", "label": "Side B — 2", "sort_order": 11},
    {"slot_id": "side_b_3", "zone": "side_b", "label": "Side B — 3", "sort_order": 12},
    {"slot_id": "side_c_1", "zone": "side_c", "label": "Side C — 1", "sort_order": 20},
    {"slot_id": "side_c_2", "zone": "side_c", "label": "Side C — 2", "sort_order": 21},
    {"slot_id": "side_c_3", "zone": "side_c", "label": "Side C — 3", "sort_order": 22},
    {"slot_id": "side_d_1", "zone": "side_d", "label": "Side D — 1", "sort_order": 30},
    {"slot_id": "side_d_2", "zone": "side_d", "label": "Side D — 2", "sort_order": 31},
    {"slot_id": "side_d_3", "zone": "side_d", "label": "Side D — 3", "sort_order": 32},
    {"slot_id": "bottom_a", "zone": "bottom", "label": "Bottom — A",  "sort_order": 40},
    {"slot_id": "bottom_b", "zone": "bottom", "label": "Bottom — B",  "sort_order": 41},
    {"slot_id": "bottom_c", "zone": "bottom", "label": "Bottom — C",  "sort_order": 42},
]

SLOT_IDS: frozenset[str] = frozenset(s["slot_id"] for s in SLOT_REGISTRY)
VALID_ZONES: frozenset[str] = frozenset(s["zone"] for s in SLOT_REGISTRY)
MAX_SLOTS: int = 15
TITLE_MAX_LEN: int = 80

_HTML_TAG_RE = re.compile(r"<[^>]+>")


# ── Validation helpers ─────────────────────────────────────────────────────────

def validate_slot_id(slot_id: str) -> None:
    """Raise ValueError if slot_id is not in the Phase 1 registry."""
    if slot_id not in SLOT_IDS:
        raise ValueError(
            f"Unknown slot_id: {slot_id!r}. Valid slot IDs: {sorted(SLOT_IDS)}"
        )


def sanitize_title(title: str) -> str:
    """Strip HTML tags and enforce max length. Raises ValueError if too long."""
    cleaned = _HTML_TAG_RE.sub("", title).strip()
    if len(cleaned) > TITLE_MAX_LEN:
        raise ValueError(f"Title must be {TITLE_MAX_LEN} characters or fewer.")
    return cleaned


def build_video_module(video_url: str, title: str = "") -> dict[str, Any]:
    """Validate video_url, build and return a module dict.

    Accepts YouTube (watch/shorts/youtu.be) and canonical TikTok URLs.
    Short TikTok URLs (vm./vt.tiktok.com) raise ValueError.
    source_url is stored for audit only and is never used as an iframe src.
    """
    try:
        parsed = extract_any_video(video_url)
    except ValueError:
        raise
    if parsed is None:
        raise ValueError(
            "Invalid or unsupported video URL. Paste a YouTube link "
            "(youtube.com/watch?v=… or youtu.be/…) "
            "or the full TikTok video link (tiktok.com/@user/video/…)."
        )
    clean_title = sanitize_title(title) if title else ""
    provider = parsed["provider"]
    return {
        "type":       f"video_{provider}",
        "title":      clean_title,
        "provider":   provider,
        "video_id":   parsed["video_id"],
        "source_url": video_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Profile grid mutation helpers (pure — return new dicts, never mutate) ─────

def set_slot(profile_grid: dict | None, slot_id: str, module: dict) -> dict:
    """Return a new profile_grid dict with slot_id set to module.

    Replaces an existing entry for slot_id or appends a new one.
    Raises ValueError if slot count would exceed MAX_SLOTS.
    """
    validate_slot_id(slot_id)
    slots: list[dict] = list((profile_grid or {}).get("slots", []))
    existing_ids = {s["slot_id"] for s in slots}
    if slot_id in existing_ids:
        slots = [
            {"slot_id": slot_id, "module": module} if s["slot_id"] == slot_id else s
            for s in slots
        ]
    else:
        if len(slots) >= MAX_SLOTS:
            raise ValueError(
                f"Maximum {MAX_SLOTS} slots already filled. Remove one before adding."
            )
        slots.append({"slot_id": slot_id, "module": module})
    return {"version": 1, "slots": slots}


def remove_slot(profile_grid: dict | None, slot_id: str) -> dict | None:
    """Return a new profile_grid dict with slot_id removed.

    Returns None when the resulting slot list is empty (no profile_grid key stored).
    """
    validate_slot_id(slot_id)
    if not profile_grid:
        return None
    slots = [s for s in profile_grid.get("slots", []) if s["slot_id"] != slot_id]
    return {"version": 1, "slots": slots} if slots else None


# ── Grid state builders (read-only) ───────────────────────────────────────────

def _slot_map(profile_grid: dict | None) -> dict[str, dict | None]:
    """Return {slot_id: module_or_None} for valid occupied slots."""
    if not profile_grid:
        return {}
    return {
        s["slot_id"]: s.get("module")
        for s in profile_grid.get("slots", [])
        if isinstance(s.get("slot_id"), str) and s["slot_id"] in SLOT_IDS
    }


def build_draft_grid_state(draft: Any) -> list[dict]:
    """Return all 9 slots with their draft module state.

    Each entry: {slot_id, zone, label, sort_order, module, is_empty}.
    Used by the designer page GET handler.
    """
    occupied = _slot_map((draft.draft_data or {}).get("profile_grid"))
    return [
        {
            **slot_def,
            "module":   occupied.get(slot_def["slot_id"]),
            "is_empty": slot_def["slot_id"] not in occupied,
        }
        for slot_def in SLOT_REGISTRY
    ]


def build_published_grid_state(draft: Any) -> list[dict] | None:
    """Return occupied published slots, or None if no published profile_grid.

    Returns None (not an empty list) when profile_grid is absent so callers can
    distinguish "no grid configured" from "grid exists but all slots empty".
    Only filled slots are returned — used for public profile rendering.
    """
    pg = (draft.published_data or {}).get("profile_grid") if draft is not None else None
    if not pg:
        return None
    occupied = _slot_map(pg)
    if not occupied:
        return None
    return [
        {
            **slot_def,
            "module":   occupied[slot_def["slot_id"]],
            "is_empty": False,
        }
        for slot_def in SLOT_REGISTRY
        if slot_def["slot_id"] in occupied
    ]


def _zone_slot_ids(zone: str) -> list[str]:
    """Return slot_ids for a zone ordered by sort_order."""
    return [
        s["slot_id"]
        for s in sorted(
            (s for s in SLOT_REGISTRY if s["zone"] == zone),
            key=lambda s: s["sort_order"],
        )
    ]


def reorder_zone(
    profile_grid: dict | None,
    zone: str,
    slot_ids: list[str],
) -> dict | None:
    """Reorder filled modules within a zone by reassigning to sorted slot positions.

    slot_ids: the slot_ids of the zone's slots in their desired visual order
    (may include empty slots; they are ignored — only filled entries are moved).
    Returns the same profile_grid object unchanged when ≤1 filled slot (no-op).
    Raises ValueError for unknown zone or slot_ids not belonging to that zone.
    """
    if zone not in VALID_ZONES:
        raise ValueError(
            f"Unknown zone: {zone!r}. Valid zones: {sorted(VALID_ZONES)}"
        )
    zone_sid_set = frozenset(_zone_slot_ids(zone))
    for sid in slot_ids:
        if sid not in SLOT_IDS:
            raise ValueError(
                f"Unknown slot_id: {sid!r}. Valid slot IDs: {sorted(SLOT_IDS)}"
            )
        if sid not in zone_sid_set:
            raise ValueError(
                f"slot_id {sid!r} does not belong to zone {zone!r}."
            )

    if not profile_grid:
        return None

    occupied = _slot_map(profile_grid)
    filled_ordered = [
        (sid, occupied[sid])
        for sid in slot_ids
        if sid in occupied and occupied[sid] is not None
    ]

    if len(filled_ordered) <= 1:
        return profile_grid  # no-op — return same object so caller can detect

    target_slots = _zone_slot_ids(zone)
    other_slots = [
        s for s in profile_grid.get("slots", [])
        if s["slot_id"] not in zone_sid_set
    ]
    new_zone_entries = [
        {"slot_id": target_slots[i], "module": filled_ordered[i][1]}
        for i in range(len(filled_ordered))
    ]
    return {"version": 1, "slots": other_slots + new_zone_entries}


def grid_fingerprint(profile_grid: dict | None) -> frozenset:
    """Stable fingerprint for is_published() comparison.

    Format per slot: "slot_id:provider:video_id"
    """
    if not profile_grid:
        return frozenset()
    return frozenset(
        "{sid}:{prov}:{vid}".format(
            sid=s.get("slot_id", ""),
            prov=(s.get("module") or {}).get("provider", ""),
            vid=(s.get("module") or {}).get("video_id", ""),
        )
        for s in profile_grid.get("slots", [])
    )

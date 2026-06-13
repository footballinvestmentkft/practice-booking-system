"""
Juggling contact taxonomy service.

Single source of truth: datasets/juggling/contact_types_v1.json

Provides:
  - Taxonomy loading and caching (loaded once at import, immutable)
  - Side derivation for stable contact types
  - Contact type validation (422 on unknown/thigh/etc.)
  - Taxonomy response building for GET /taxonomy endpoint

Side policy (from taxonomy JSON):
  right_prefix_keys → side="right"   (fixed)
  left_prefix_keys  → side="left"    (fixed)
  center_keys       → side="center"  (fixed)
  custom_other      → side=None      (explicit_required — caller must supply)
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from functools import lru_cache
from typing import Any

# ── Taxonomy file location ────────────────────────────────────────────────────

_TAXONOMY_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "datasets" / "juggling" / "contact_types_v1.json"
)

# ── Module-level singletons (loaded once) ────────────────────────────────────

_taxonomy_data: dict[str, Any] | None = None
_etag: str | None = None


def _load() -> dict[str, Any]:
    global _taxonomy_data, _etag
    if _taxonomy_data is None:
        raw = _TAXONOMY_PATH.read_bytes()
        _taxonomy_data = json.loads(raw)
        _etag = f'"v1-{hashlib.sha256(raw).hexdigest()[:16]}"'
    return _taxonomy_data


# ── Side policy sets (derived once at import) ─────────────────────────────────

@lru_cache(maxsize=1)
def _side_policy() -> tuple[frozenset, frozenset, frozenset, frozenset]:
    """Returns (right_keys, left_keys, center_keys, custom_keys)."""
    d = _load()
    sp = d["side_policy"]
    return (
        frozenset(sp["right_prefix_keys"]),
        frozenset(sp["left_prefix_keys"]),
        frozenset(sp["center_keys"]),
        frozenset(sp["custom_explicit"]),
    )


# ── Public helpers ────────────────────────────────────────────────────────────

def get_all_keys() -> frozenset[str]:
    """All 18 contact type keys (stable + custom_other)."""
    return frozenset(_load()["all_keys"])


def get_stable_keys() -> frozenset[str]:
    """17 stable contact type keys (excludes custom_other)."""
    return frozenset(_load()["stable_keys"])


def is_stable(contact_type: str) -> bool:
    return contact_type in get_stable_keys()


def get_etag() -> str:
    _load()
    return _etag  # type: ignore[return-value]


def derive_side(contact_type: str) -> str | None:
    """
    Derive the canonical side for a stable contact type.
    Returns None for custom_other (caller must supply explicit side).
    Raises ValueError for unknown contact types.
    """
    right_keys, left_keys, center_keys, custom_keys = _side_policy()
    if contact_type in right_keys:
        return "right"
    if contact_type in left_keys:
        return "left"
    if contact_type in center_keys:
        return "center"
    if contact_type in custom_keys:
        return None
    raise ValueError(f"Unknown contact type: {contact_type!r}")


def validate_contact_type(contact_type: str) -> None:
    """
    Raises ValueError with a descriptive message if contact_type is invalid.
    Explicit thigh rejection with governance note.
    """
    if contact_type in ("right_thigh", "left_thigh", "thigh"):
        raise ValueError(
            f"contact_type {contact_type!r} is not a valid v1 key. "
            "Thigh and hip are anatomically distinct. "
            "Use right_hip or left_hip. Auto-migration is FORBIDDEN."
        )
    if contact_type not in get_all_keys():
        raise ValueError(
            f"contact_type {contact_type!r} is not in taxonomy v1. "
            f"Valid keys: {sorted(get_all_keys())}"
        )


def build_taxonomy_response() -> dict[str, Any]:
    """
    Build the full taxonomy response payload for GET /taxonomy.
    Returns only the fields safe to expose to the client:
      version, stable_count, total_count, groups (with contact_types).
    """
    d = _load()
    safe_groups = []
    for g in d["groups"]:
        safe_types = []
        for ct in g["contact_types"]:
            safe_types.append({
                "key":                       ct["key"],
                "label_hu":                  ct["label_hu"],
                "label_en":                  ct["label_en"],
                "side":                      ct.get("side"),
                "side_policy":               ct["side_policy"],
                "is_stable":                 ct["is_stable"],
                "sort_order":                ct["sort_order"],
                "ios_icon":                  ct.get("ios_icon"),
                "excluded_from_training_auto": ct["excluded_from_training_auto"],
                # custom_other extras
                "requires_explicit_side":    ct.get("requires_explicit_side", False),
                "requires_custom_label":     ct.get("requires_custom_label", False),
                "requires_custom_description": ct.get("requires_custom_description", False),
            })
        safe_groups.append({
            "group_id":        g["group_id"],
            "group_label_hu":  g["group_label_hu"],
            "group_label_en":  g["group_label_en"],
            "sort_order":      g["group_sort_order"],
            "ios_section_icon": g.get("ios_section_icon"),
            "contact_types":   safe_types,
        })
    return {
        "version":       d["taxonomy_version"],
        "stable_count":  d["stable_count"],
        "total_count":   d["total_count"],
        "groups":        safe_groups,
    }

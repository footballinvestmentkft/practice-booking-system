"""
Card Design Service
===================
DB-backed registry of player card design families (formerly "variants").

A *design* controls the card's visual structure (layout, component arrangement,
template).  It is orthogonal to the colour *theme* — any design × theme
combination is valid.

Two-layer lookup:
  1. DB cache (60 s TTL) — loaded from the card_designs table via _maybe_reload().
  2. DESIGNS fallback dict — used when the cache is empty or db is None/mock.

This mirrors the pattern established by card_theme_service.py.

Architecture note (CS-1):
  - archetype_id is nullable on all designs until CS-4a introduces parameterizable
    base templates.  When populated it enables manifest-only new design creation
    without a Jinja2 template file deployment.
  - supported_export_buckets replaces the implicit file-existence check.
  - animated_platforms replaces the hardcoded ANIMATED_EXPORT_CAPABLE frozenset
    in card_constants.py (which is now derived from this dict at import time).

card_variant_service.py acts as a backward-compatibility shim over this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CardDesignDefinition:
    id:          str
    label:       str
    description: str
    is_premium:  bool
    credit_cost: int
    template:    str   # browser_template path, relative to templates/
    available:   bool = True   # maps from is_active in DB
    sort_order:  int  = 0
    # CS-4a bridge: None until archetype templates exist
    archetype_id: Optional[str] = None
    # Explicit export coverage (replaces implicit file-existence check)
    supported_export_buckets: tuple[str, ...] = ()
    # Platform IDs that support animated video export for this design
    animated_platforms: tuple[str, ...] = ()
    # CS-4c: bucket-keyed driver config; {} = file-based Level C routing for all buckets
    component_config: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.component_config is None:
            object.__setattr__(self, "component_config", {})


# ── Fallback registry (warm-start when DB is empty / unavailable) ─────────────
# Mirrors the card_designs table seed exactly.
# Display order: free first, then premium ascending by credit_cost.
_FIFA_COMPONENT_CONFIG: dict = {
    "portrait": {
        "skill_slice": 6,
        "show_dominant_badge": False,
        "show_height_weight": False,
        "show_sponsor": False,
        "platform_vars": {},
    },
    "story": {
        "skill_slice": 8,
        "show_dominant_badge": True,
        "show_height_weight": True,
        "show_sponsor": True,
        "platform_vars": {
            "--ex-hero-h":      "460px",
            "--ex-avatar-sz":   "180px",
            "--ex-avatar-font": "60px",
            "--ex-ovr-font":    "96px",
            "--ex-name-font":   "48px",
            "--ex-row-max-h":   "66px",
            "--ex-sname-w":     "155px",
            "--ex-font-skill":  "14px",
        },
    },
}

DESIGNS: dict[str, CardDesignDefinition] = {
    "fifa": CardDesignDefinition(
        id="fifa",
        label="FIFA Classic",
        description="The original LFA player card with full skill breakdown and event history.",
        is_premium=False,
        credit_cost=0,
        template="public/player_card_fifa.html",
        sort_order=0,
        supported_export_buckets=("square", "portrait", "story", "tiktok", "landscape", "banner"),
        animated_platforms=("instagram_square",),
        component_config=_FIFA_COMPONENT_CONFIG,
    ),
    "compact": CardDesignDefinition(
        id="compact",
        label="Compact",
        description="Mobile-first card designed for sharing. Shows category averages at a glance.",
        is_premium=True,
        credit_cost=300,
        template="public/player_card_compact.html",
        sort_order=1,
    ),
    "compact_bg": CardDesignDefinition(
        id="compact_bg",
        label="Compact + BG",
        description="Compact layout with a custom background image behind your player photo.",
        is_premium=True,
        credit_cost=400,
        template="public/player_card_compact_bg.html",
        sort_order=2,
    ),
    "showcase": CardDesignDefinition(
        id="showcase",
        label="Showcase",
        description="Premium landscape trading card with category highlights and recent events strip.",
        is_premium=True,
        credit_cost=500,
        template="public/player_card_showcase.html",
        sort_order=3,
    ),
    "showcase_bg": CardDesignDefinition(
        id="showcase_bg",
        label="Showcase + BG",
        description="Showcase layout with a custom background image behind your player photo.",
        is_premium=True,
        credit_cost=600,
        template="public/player_card_showcase_bg.html",
        sort_order=4,
    ),
    "atlas": CardDesignDefinition(
        id="atlas",
        label="Atlas",
        description="Modern vertical card with hero section, stat strip, and three-tab layout including player profile.",
        is_premium=True,
        credit_cost=400,
        template="public/player_card_atlas.html",
        sort_order=5,
    ),
    "pulse": CardDesignDefinition(
        id="pulse",
        label="Pulse",
        description="Radar skill chart with animated OVR ring, pulse effects, and HUD aesthetic.",
        is_premium=True,
        credit_cost=600,
        template="public/player_card_pulse.html",
        sort_order=6,
        supported_export_buckets=("square",),
        animated_platforms=("instagram_square",),
    ),
}

# Display order for the dashboard picker (free first, then premium)
DESIGN_ORDER: list[str] = [
    "fifa", "compact", "compact_bg", "showcase", "showcase_bg", "atlas", "pulse",
]


# ── In-process cache ──────────────────────────────────────────────────────────
_design_cache: dict[str, CardDesignDefinition] = {}
_cache_loaded_at: float = 0.0
_CACHE_TTL = 60.0


def _invalidate_cache() -> None:
    global _design_cache, _cache_loaded_at
    _design_cache = {}
    _cache_loaded_at = 0.0


def _row_to_definition(row) -> CardDesignDefinition:
    return CardDesignDefinition(
        id=row.id,
        label=row.label,
        description=row.description or "",
        is_premium=row.is_premium,
        credit_cost=row.credit_cost,
        template=row.browser_template,
        available=row.is_active,
        sort_order=row.sort_order,
        archetype_id=row.archetype_id,
        supported_export_buckets=tuple(row.supported_export_buckets or []),
        animated_platforms=tuple(row.animated_platforms or []),
        component_config=dict(row.component_config or {}),
    )


def _load_cache(db) -> dict[str, CardDesignDefinition]:
    try:
        from app.models.card_design import CardDesign as _CardDesign
        rows = db.query(_CardDesign).all()
        return {r.id: _row_to_definition(r) for r in rows}
    except Exception:
        return {}


def _maybe_reload(db=None) -> dict[str, CardDesignDefinition]:
    global _design_cache, _cache_loaded_at
    if db is not None and (time.monotonic() - _cache_loaded_at) > _CACHE_TTL:
        new_cache = _load_cache(db)
        if new_cache:
            _design_cache = new_cache
            _cache_loaded_at = time.monotonic()
    return _design_cache if _design_cache else DESIGNS


# ── Public API ────────────────────────────────────────────────────────────────

def get_design(design_id: str, db=None) -> CardDesignDefinition:
    """Return design by ID, falling back to 'fifa' for unknown IDs.

    The available flag is NOT used here — render path must never be gated.
    """
    cache = _maybe_reload(db)
    return cache.get(design_id, cache.get("fifa", DESIGNS["fifa"]))


def get_all_designs(db=None) -> list[CardDesignDefinition]:
    """Return all designs sorted by (sort_order, id)."""
    cache = _maybe_reload(db)
    return sorted(cache.values(), key=lambda d: (d.sort_order, d.id))


def is_design_available(design_id: str, db=None) -> bool:
    """Return True if the design is active/available in the current cache."""
    cache = _maybe_reload(db)
    d = cache.get(design_id)
    return d is not None and d.available


def get_supported_buckets(design_id: str, db=None) -> tuple[str, ...]:
    """Return the explicit export bucket list for a design.

    Returns () for designs with no export templates (browser-only designs).
    Use this instead of the implicit file-existence check.
    """
    return get_design(design_id, db).supported_export_buckets


def is_animated_capable(design_id: str, platform_id: str, db=None) -> bool:
    """Return True if (design_id, platform_id) supports animated video export."""
    return platform_id in get_design(design_id, db).animated_platforms

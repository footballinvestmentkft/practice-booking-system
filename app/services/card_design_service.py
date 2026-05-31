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
class NonPlayerCardFormatDefinition:
    """Format/variant definition for Welcome Card and Challenge Card families."""
    design_id:        str
    label:            str
    style_tag:        str
    dims:             str
    credit_cost:      int
    preview_platform: str
    sort_order:       int = 0
    family_id:        str = "fclassic"  # PR-FC-1A: all current WC/CC formats belong to FClassic


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
        "skill_slice":          None,   # all 44 skills
        "show_dominant_badge":  True,
        "show_height_weight":   True,
        "show_extended_profile": True,
        "show_position_map":    True,
        "show_sponsor":         False,
        "platform_vars": {
            "--ex-posmap-h": "200px",
        },
    },
    "story": {
        "skill_slice":          None,   # all 44 skills
        "show_dominant_badge":  True,
        "show_height_weight":   True,
        "show_extended_profile": True,
        "show_position_map":    True,
        "show_sponsor":         True,
        "platform_vars": {
            "--ex-hero-h":      "460px",
            "--ex-avatar-sz":   "180px",
            "--ex-avatar-font": "60px",
            "--ex-ovr-font":    "96px",
            "--ex-name-font":   "48px",
            "--ex-row-max-h":   "66px",
            "--ex-sname-w":     "155px",
            "--ex-font-skill":  "14px",
            "--ex-posmap-h":    "250px",
        },
    },
    "og": {
        "skill_slice":          None,   # category averages, not sliced rows
        "show_dominant_badge":  True,
        "show_height_weight":   True,
        "show_extended_profile": True,
        "show_position_map":    True,
        "show_sponsor":         False,
    },
}

DESIGNS: dict[str, CardDesignDefinition] = {
    "fclassic": CardDesignDefinition(
        id="fclassic",
        label="FClassic Player",
        description="The original LFA player card with full skill breakdown and event history.",
        is_premium=True,
        credit_cost=300,
        template="public/player_card_fifa.html",
        sort_order=0,
        archetype_id="column",
        supported_export_buckets=("square", "portrait", "story", "tiktok", "landscape", "og", "banner"),
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
        archetype_id="pulse",
        supported_export_buckets=("square",),
        animated_platforms=("instagram_square",),
        component_config={
            "square": {
                "skill_slice": None,
                "show_dominant_badge": False,
                "show_height_weight": False,
                "show_sponsor": False,
                "platform_vars": {},
            }
        },
    ),
}

# Display order for the dashboard picker (free first, then premium)
DESIGN_ORDER: list[str] = [
    "fclassic", "compact", "compact_bg", "showcase", "showcase_bg", "atlas", "pulse",
]

# Deprecated alias — both keys point to the same CardDesignDefinition(id="fclassic").
# Kept until PR-FC-1F removes the alias entirely.
DESIGNS["fifa"] = DESIGNS["fclassic"]

# ── FClassic family — PR-FC-1A ────────────────────────────────────────────────
# "fclassic" is the canonical family / design identifier going forward.
# "fifa" is a deprecated alias kept alive for backward compatibility until
# PR-FC-1B completes the DB PK migration.

FCLASSIC_FAMILY_ID: str = "fclassic"

# Player Card design IDs that belong to the FClassic family.
# Includes both the legacy "fifa" key (current DB PK) and the canonical "fclassic"
# key (post-PR-FC-1B DB state).
FCLASSIC_PLAYER_DESIGN_IDS: frozenset[str] = frozenset({"fifa", "fclassic"})

# Deprecated design ID → canonical design ID.
# Read path: resolve before lookup. Write path: new data must use canonical ID.
_DESIGN_ID_ALIAS: dict[str, str] = {
    "fifa": "fclassic",
}

# Design IDs that are deprecated and must not be used for new canonical writes.
_DEPRECATED_DESIGN_IDS: frozenset[str] = frozenset(_DESIGN_ID_ALIAS.keys())


def resolve_design_id(design_id: str) -> str:
    """Resolve a deprecated design ID to its canonical form.

    "fifa" → "fclassic".  All other IDs pass through unchanged.
    Safe to call on any design_id without knowing whether it is current.
    """
    return _DESIGN_ID_ALIAS.get(design_id, design_id)


def get_card_family(card_type_id: str, design_id: str | None = None) -> str | None:
    """Return the family_id for a card_type + design combination, or None.

    FClassic family covers:
      - player_card with design_id in FCLASSIC_PLAYER_DESIGN_IDS (includes alias "fifa")
      - all welcome_card formats
      - all challenge_card formats
    Other Player Card designs (compact, showcase, atlas, pulse …) return None.
    """
    if card_type_id == "player_card":
        if design_id is not None and resolve_design_id(design_id) == FCLASSIC_FAMILY_ID:
            return FCLASSIC_FAMILY_ID
        return None
    if card_type_id in ("welcome_card", "challenge_card"):
        return FCLASSIC_FAMILY_ID
    return None


def assert_new_design_id_is_canonical(design_id: str, context: str = "") -> None:
    """Raise ValueError if a deprecated design ID is used for a new canonical write.

    PR-FC-1A no-new-fifa guard: new data must use 'fclassic', not legacy 'fifa'.
    Call this before any DB write that creates or updates design_id associations.
    """
    if design_id in _DEPRECATED_DESIGN_IDS:
        canonical = resolve_design_id(design_id)
        suffix = f" Context: {context}" if context else ""
        raise ValueError(
            f"Deprecated design ID {design_id!r} must not be used for new writes. "
            f"Use {canonical!r} instead.{suffix}"
        )


# ── Non-player-card format registries ────────────────────────────────────────
# Format/variant definitions for Welcome Card and Challenge Card families.
# design_id maps directly to CardDesignOwnership.design_id.
# Prices are DEV DEFAULT — update via product decision before production.

WELCOME_CARD_FORMATS: list[NonPlayerCardFormatDefinition] = [
    NonPlayerCardFormatDefinition("instagram_portrait",  "Instagram Portrait",   "IDENTITY CARD", "1080 × 1350",  75,  "instagram_portrait",  0),
    NonPlayerCardFormatDefinition("instagram_story",     "Instagram Story",      "IDENTITY CARD", "1080 × 1920",  75,  "instagram_story",     1),
    NonPlayerCardFormatDefinition("instagram_square",    "Instagram Square",     "IDENTITY CARD", "1080 × 1080",  75,  "instagram_square",    2),
    NonPlayerCardFormatDefinition("tiktok",              "TikTok",               "CINEMATIC",     "1080 × 1920", 100,  "tiktok",              3),
    NonPlayerCardFormatDefinition("facebook_square",     "Facebook Square",      "EDITORIAL",     "1080 × 1080",  75,  "facebook_square",     4),
    NonPlayerCardFormatDefinition("facebook_landscape",  "Facebook Landscape",   "LANDSCAPE",     "1200 × 630",   75,  "facebook_landscape",  5),
    NonPlayerCardFormatDefinition("banner_custom",       "Wide Banner",          "WIDE BANNER",   "1500 × 500",  100,  "banner_custom",       6),
]

CHALLENGE_CARD_FORMATS: list[NonPlayerCardFormatDefinition] = [
    NonPlayerCardFormatDefinition("challenge_post_16_9",   "Post (16:9)",  "POST",  "1280 × 720",  100,  "challenge_post_16_9",   0),
    NonPlayerCardFormatDefinition("challenge_story_9_16",  "Story (9:16)", "STORY", "1080 × 1920", 100,  "challenge_story_9_16",  1),
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
    """Return design by ID. 'fifa' is a deprecated alias for 'fclassic'.

    Resolution order: canonical ID → original ID → 'fifa' fallback → hardcoded.
    The available flag is NOT used here — render path must never be gated.
    """
    canonical = resolve_design_id(design_id)
    cache = _maybe_reload(db)
    return (
        cache.get(canonical)
        or cache.get(design_id)
        or cache.get("fclassic")
        or DESIGNS["fclassic"]
    )


def get_all_designs(db=None) -> list[CardDesignDefinition]:
    """Return all designs sorted by (sort_order, id), deduplicated by canonical id.

    The DESIGNS fallback dict may contain deprecated alias keys pointing to the
    same CardDesignDefinition object (e.g. "fifa" → same object as "fclassic").
    Deduplication ensures each design appears exactly once in the returned list.
    """
    cache = _maybe_reload(db)
    unique = {d.id: d for d in cache.values()}   # dedup by canonical id
    return sorted(unique.values(), key=lambda d: (d.sort_order, d.id))


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


# ── Card Design Ownership / Entitlement ───────────────────────────────────────

# Valid card type IDs — checked at service entry points.
_VALID_CARD_TYPE_IDS: frozenset[str] = frozenset(
    {"player_card", "welcome_card", "challenge_card"}
)

# Service-level price map for WC/CC format designs.
# player_card prices come from CardDesign.credit_cost (DB-backed).
# Update these constants when pricing changes — do NOT hardcode in templates.
_NON_PLAYER_CARD_PRICES: dict[tuple[str, str], int] = {
    # Welcome Card formats — 75 CR standard, 100 CR premium
    ("welcome_card", "instagram_portrait"):  75,
    ("welcome_card", "instagram_story"):     75,
    ("welcome_card", "instagram_square"):    75,
    ("welcome_card", "tiktok"):             100,
    ("welcome_card", "facebook_square"):     75,
    ("welcome_card", "facebook_landscape"):  75,
    ("welcome_card", "banner_custom"):      100,
    # Challenge Card formats
    ("challenge_card", "challenge_post_16_9"):  100,
    ("challenge_card", "challenge_story_9_16"): 100,
    # Legacy sentinel keys — NOT purchasable (FreeDesignError), backward compat only
    ("welcome_card",   "default"):    0,
    ("challenge_card", "challenge"):  0,
}


class FreeDesignError(Exception):
    """Raised when attempting to purchase a sentinel/non-purchasable design (price == 0)."""


class AlreadyOwnedError(Exception):
    """Raised when the user already owns the requested design."""


def _resolve_price(card_type_id: str, design_id: str, db=None) -> int:
    """Return the credit cost for a card type + design combination.

    player_card: reads from CardDesign.credit_cost (DB/cache).
    welcome_card / challenge_card: reads from _NON_PLAYER_CARD_PRICES constant.

    Raises ValueError for unknown card_type_id or design_id.
    """
    if card_type_id == "player_card":
        cache = _maybe_reload(db)
        design = cache.get(design_id)
        if design is None:
            raise ValueError(f"Unknown player card design: {design_id!r}")
        return design.credit_cost
    key = (card_type_id, design_id)
    if key not in _NON_PLAYER_CARD_PRICES:
        raise ValueError(
            f"Unknown card type/design combination: {card_type_id!r}/{design_id!r}"
        )
    return _NON_PLAYER_CARD_PRICES[key]


def is_design_accessible(db, user_id: int, card_type_id: str, design_id: str) -> bool:
    """Return True if the user has a CardDesignOwnership row for this design.

    Every design (including player_card/fifa) requires an ownership row.

    Access rules:
      1. CardDesignOwnership row exists → True
      2. player_card legacy JSON shim: unlocked_card_variants contains design_id → True
      3. WC/CC legacy CDO shim: user owns old family-level key → True
      4. Everything else → False
    """
    from app.models.card_design_ownership import CardDesignOwnership
    from app.models.license import UserLicense

    # Rule 1: ownership table — primary source
    owned = (
        db.query(CardDesignOwnership)
        .filter_by(user_id=user_id, card_type_id=card_type_id, design_id=design_id)
        .first()
    )
    if owned:
        return True

    # Rule 2: legacy JSON shim — player_card only, read-only
    if card_type_id == "player_card":
        lic = (
            db.query(UserLicense)
            .filter_by(user_id=user_id, specialization_type="LFA_FOOTBALL_PLAYER")
            .first()
        )
        if lic and design_id in (lic.unlocked_card_variants or []):
            return True

    # Rule 3: legacy CDO shim — if user owns the old family-level key, grant access
    # to all format-level designs in that family (backward compat, no migration needed).
    _LEGACY_CDO_MAP: dict[str, str] = {
        "welcome_card":   "default",
        "challenge_card": "challenge",
    }
    legacy_key = _LEGACY_CDO_MAP.get(card_type_id)
    if legacy_key:
        legacy_row = (
            db.query(CardDesignOwnership)
            .filter_by(user_id=user_id, card_type_id=card_type_id, design_id=legacy_key)
            .first()
        )
        if legacy_row:
            return True

    return False


def get_owned_design_ids(db, user_id: int, card_type_id: str) -> list[str]:
    """Return design_ids owned by the user for a specific card_type_id.

    Every design requires an ownership row — no free designs are auto-included.
    """
    from app.models.card_design_ownership import CardDesignOwnership

    owned = (
        db.query(CardDesignOwnership.design_id)
        .filter_by(user_id=user_id, card_type_id=card_type_id)
        .all()
    )
    result = {row.design_id for row in owned}

    # Legacy JSON shim for player_card
    if card_type_id == "player_card":
        from app.models.license import UserLicense
        lic = (
            db.query(UserLicense)
            .filter_by(user_id=user_id, specialization_type="LFA_FOOTBALL_PLAYER")
            .first()
        )
        if lic:
            for d in (lic.unlocked_card_variants or []):
                result.add(d)

    # Legacy CDO shim: if user owns legacy WC/CC family key, grant all format IDs
    elif card_type_id == "welcome_card" and "default" in result:
        for fmt in WELCOME_CARD_FORMATS:
            result.add(fmt.design_id)
    elif card_type_id == "challenge_card" and "challenge" in result:
        for fmt in CHALLENGE_CARD_FORMATS:
            result.add(fmt.design_id)

    return sorted(result)


def purchase_design(db, user, card_type_id: str, design_id: str) -> "CardDesignOwnership":
    """Acquire a card design entitlement by deducting credits.

    Atomic: CreditService.deduct() (SAVEPOINT) + CardDesignOwnership INSERT
    committed together in a single outer db.commit().

    Raises:
        ValueError              — unknown card_type_id or design_id
        FreeDesignError         — design is always free, no purchase needed
        AlreadyOwnedError       — user already owns this design
        InsufficientCreditsError — not enough credits (raised by CreditService)
        sqlalchemy.exc.IntegrityError — caught internally, re-raised as AlreadyOwnedError
    """
    from sqlalchemy.exc import IntegrityError

    from app.models.card_design_ownership import CardDesignOwnership
    from app.services.credit_service import CreditService

    if card_type_id not in _VALID_CARD_TYPE_IDS:
        raise ValueError(f"Unknown card_type_id: {card_type_id!r}")

    price = _resolve_price(card_type_id, design_id, db)  # ValueError if unknown

    if price == 0:
        raise FreeDesignError(
            f"Design {card_type_id!r}/{design_id!r} is always accessible — no purchase needed"
        )

    already = (
        db.query(CardDesignOwnership)
        .filter_by(user_id=user.id, card_type_id=card_type_id, design_id=design_id)
        .first()
    )
    if already:
        raise AlreadyOwnedError(
            f"Design {card_type_id!r}/{design_id!r} already owned by user {user.id}"
        )

    idempotency_key = f"card_design_unlock_{user.id}_{card_type_id}_{design_id}"

    # SAVEPOINT: balance UPDATE + CreditTransaction flush (no outer commit inside deduct)
    credit_tx = CreditService(db).deduct(
        user=user,
        amount=price,
        transaction_type="CARD_DESIGN_UNLOCK",
        description=f"Card design: {card_type_id}/{design_id}",
        idempotency_key=idempotency_key,
    )

    try:
        ownership = CardDesignOwnership(
            user_id=user.id,
            card_type_id=card_type_id,
            design_id=design_id,
            source="purchase",
            credit_transaction_id=credit_tx.id,
        )
        db.add(ownership)
        db.commit()  # OUTER COMMIT: balance + CreditTransaction + Ownership
        return ownership
    except IntegrityError:
        db.rollback()  # rolls back balance update too (outer tx)
        raise AlreadyOwnedError(
            f"Design {card_type_id!r}/{design_id!r} already owned (concurrent request)"
        )


def grant_design(
    db,
    user_id: int,
    card_type_id: str,
    design_id: str,
    source: str = "admin_grant",
) -> "CardDesignOwnership | None":
    """Grant a card design ownership without credit deduction.

    Idempotent: if ownership already exists, returns None without error.
    For use by: admin panel, backfill scripts, promo flows.
    Must NOT be called from normal user flows (onboarding, challenge completion, etc.)
    """
    from app.models.card_design_ownership import CardDesignOwnership

    existing = (
        db.query(CardDesignOwnership)
        .filter_by(user_id=user_id, card_type_id=card_type_id, design_id=design_id)
        .first()
    )
    if existing:
        return None  # idempotent — already granted

    ownership = CardDesignOwnership(
        user_id=user_id,
        card_type_id=card_type_id,
        design_id=design_id,
        source=source,
        credit_transaction_id=None,
    )
    db.add(ownership)
    db.commit()
    return ownership

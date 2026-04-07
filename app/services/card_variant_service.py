"""
Player Card Variant Service
===========================
Manages layout variants for the public LFA Football Player card.

A *variant* controls the card's visual structure (layout, component arrangement).
It is orthogonal to the colour *theme* — any variant × theme combination is valid.

Free variants (fifa) are always available.
Premium variants (compact, showcase) require credit purchase.

Adding a new variant requires only:
  1. A new entry in VARIANTS below
  2. A new template at the path listed in VariantDefinition.template
"""
from __future__ import annotations

from dataclasses import dataclass

from .credit_service import CreditService


@dataclass(frozen=True)
class VariantDefinition:
    id:          str
    label:       str          # dashboard picker label
    description: str          # tooltip / preview text
    is_premium:  bool
    credit_cost: int          # 0 for free variants
    template:    str          # path relative to templates/ directory
    available:   bool = True  # False = coming soon; blocks unlock/apply/preview


# ── Variant registry ──────────────────────────────────────────────────────────
VARIANTS: dict[str, VariantDefinition] = {
    "fifa": VariantDefinition(
        id="fifa",
        label="FIFA Classic",
        description="The original LFA player card with full skill breakdown and event history.",
        is_premium=False,
        credit_cost=0,
        template="public/player_card_fifa.html",
    ),
    "compact": VariantDefinition(
        id="compact",
        label="Compact",
        description="Mobile-first card designed for sharing. Shows category averages at a glance.",
        is_premium=True,
        credit_cost=300,
        template="public/player_card_compact.html",
    ),
    "showcase": VariantDefinition(
        id="showcase",
        label="Showcase",
        description="Premium landscape trading card with category highlights and recent events strip.",
        is_premium=True,
        credit_cost=500,
        template="public/player_card_showcase.html",
    ),
    "compact_bg": VariantDefinition(
        id="compact_bg",
        label="Compact + BG",
        description="Compact layout with a custom background image behind your player photo.",
        is_premium=True,
        credit_cost=400,
        template="public/player_card_compact_bg.html",
    ),
    "showcase_bg": VariantDefinition(
        id="showcase_bg",
        label="Showcase + BG",
        description="Showcase layout with a custom background image behind your player photo.",
        is_premium=True,
        credit_cost=600,
        template="public/player_card_showcase_bg.html",
    ),
}

# Display order for the dashboard picker (free first, then premium)
VARIANT_ORDER = ["fifa", "compact", "compact_bg", "showcase", "showcase_bg"]


# ── Public API ────────────────────────────────────────────────────────────────

def get_variant(variant_id: str) -> VariantDefinition:
    """Return variant by ID, falling back to 'fifa' for unknown IDs.
    The available flag is NOT used here — render path must never be feature-gated.
    """
    return VARIANTS.get(variant_id, VARIANTS["fifa"])


def get_all_variants() -> list[VariantDefinition]:
    """Return all variants in display order."""
    return [VARIANTS[vid] for vid in VARIANT_ORDER if vid in VARIANTS]


def is_variant_unlocked(user_license, variant_id: str) -> bool:
    """
    Return True if the user may use this variant.
    Free variants are always unlocked.
    Premium variants require variant_id in user_license.unlocked_card_variants.
    """
    variant = VARIANTS.get(variant_id)
    if variant is None:
        return False
    if not variant.is_premium:
        return True
    unlocked = user_license.unlocked_card_variants or []
    return variant_id in unlocked


def apply_variant(db, user_license, variant_id: str) -> None:
    """
    Set the active variant on user_license and commit.
    Raises ValueError if variant unknown, not yet available, or not yet unlocked.
    """
    if variant_id not in VARIANTS:
        raise ValueError(f"Unknown variant: {variant_id!r}")
    if not VARIANTS[variant_id].available:
        raise ValueError(f"Variant '{VARIANTS[variant_id].label}' is not yet available")
    if not is_variant_unlocked(user_license, variant_id):
        variant = VARIANTS[variant_id]
        raise ValueError(
            f"Variant '{variant.label}' is locked. Required: {variant.credit_cost} CR"
        )
    user_license.card_variant = variant_id
    db.commit()


def unlock_variant(db, user, user_license, variant_id: str) -> None:
    """
    Unlock a premium variant for the user.

    Uses CreditService.deduct() which wraps the balance UPDATE and the CT INSERT
    inside a single SAVEPOINT — both succeed or both roll back together.

    Raises ValueError (InsufficientCreditsError) if variant unknown, already
    unlocked, or insufficient credit balance.
    """
    if variant_id not in VARIANTS:
        raise ValueError(f"Unknown variant: {variant_id!r}")
    variant = VARIANTS[variant_id]
    if not variant.available:
        raise ValueError(f"Variant '{variant.label}' is not yet available")
    if not variant.is_premium:
        return  # free variants don't need unlocking
    unlocked: list = list(user_license.unlocked_card_variants or [])
    if variant_id in unlocked:
        return  # already unlocked — idempotent

    CreditService(db).deduct(
        user=user,
        amount=variant.credit_cost,
        transaction_type="VARIANT_UNLOCK",
        description=f"Card variant unlock: {variant.label}",
        idempotency_key=f"variant_unlock_{user.id}_{variant_id}",
    )

    unlocked.append(variant_id)
    user_license.unlocked_card_variants = unlocked
    db.commit()

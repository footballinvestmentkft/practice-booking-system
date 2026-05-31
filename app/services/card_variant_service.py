"""
Player Card Variant Service — backward-compatibility shim (CS-1).
=================================================================
All design metadata is now stored in card_design_service.py (DB-backed).

VariantDefinition is an alias for CardDesignDefinition.
VARIANTS is an alias for DESIGNS.
VARIANT_ORDER is an alias for DESIGN_ORDER.

Lookup helpers (get_variant, get_all_variants) delegate to card_design_service.
Business logic (is_variant_unlocked, apply_variant, unlock_variant) remains
here because it involves credits and CardDraft — not pure design metadata.

Adding a new design:
  1. Add a row to the card_designs DB table (via admin manifest upload or migration).
  2. Add the browser template at the path listed in browser_template.
  (No code changes required in this file.)
"""
from __future__ import annotations

from .card_design_service import (
    CardDesignDefinition as VariantDefinition,
    DESIGNS as VARIANTS,
    DESIGN_ORDER as VARIANT_ORDER,
    get_design as _get_design,
    get_all_designs as _get_all_designs,
    _invalidate_cache,
)
from .credit_service import CreditService
from .card_draft_service import CardDraftService


# ── Public API ────────────────────────────────────────────────────────────────

def get_variant(variant_id: str) -> VariantDefinition:
    """Return variant by ID, falling back to 'fclassic' for unknown IDs.
    The available flag is NOT used here — render path must never be feature-gated.
    """
    return _get_design(variant_id)


def get_all_variants() -> list[VariantDefinition]:
    """Return all variants in display order."""
    return _get_all_designs()


def is_variant_unlocked(user_license, variant_id: str) -> bool:
    """
    Return True if variant_id is in user_license.unlocked_card_variants.
    All variants (including non-premium) require explicit entitlement.
    """
    variant = VARIANTS.get(variant_id)
    if variant is None:
        return False
    unlocked = user_license.unlocked_card_variants or []
    return variant_id in unlocked


def apply_variant(db, user_license, variant_id: str) -> None:
    """
    Set the active draft variant on the player CardDraft and commit.
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
    draft = CardDraftService.get_player_card_draft(db, user_id=user_license.user_id)
    CardDraftService.update_draft_variant(db, draft, variant_id)


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

    # Ensure the draft row exists before the credit SAVEPOINT.
    draft = CardDraftService.get_player_card_draft(db, user_id=user_license.user_id)

    CreditService(db).deduct(
        user=user,
        amount=variant.credit_cost,
        transaction_type="VARIANT_UNLOCK",
        description=f"Card variant unlock: {variant.label}",
        idempotency_key=f"variant_unlock_{user.id}_{variant_id}",
    )

    # Update unlocked list and stage draft variant (commit=False keeps one outer commit)
    unlocked.append(variant_id)
    user_license.unlocked_card_variants = unlocked
    CardDraftService.update_draft_variant(db, draft, variant_id, commit=False)

    db.commit()

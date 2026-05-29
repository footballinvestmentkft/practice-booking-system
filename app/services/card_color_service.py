"""
Card Color Service — Family-Specific Color Ownership (TS-1)
===========================================================
Manages color ownership for card families.

Key concepts:
  - Each card family (player_card, welcome_card, …) has its own color palette.
  - The same color_id in different families is a distinct product.
  - Ownership key: (user_id, card_type_id, color_id) in card_color_ownership.
  - Free colors (is_premium=False) are always unlocked — no DB row required.
  - Premium colors require an ownership row; purchased via unlock_color().

TS-1 scope: player_card family only. Additional families added in TS-2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .credit_service import CreditService, InsufficientCreditsError  # noqa: F401 — re-exported
from ..models.card_color_ownership import CardColorOwnership
from ..models.user import User


# ── Color definition ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ColorDefinition:
    id: str
    label: str
    is_premium: bool
    credit_cost: int
    dot_color: str
    sort_order: int = 0
    available: bool = True


# ── Family color catalog ──────────────────────────────────────────────────────
# Source of truth for TS-1. Only player_card family populated.
# welcome_card and challenge_card are added in TS-2 once palettes are defined.

FAMILY_COLORS: dict[str, dict[str, ColorDefinition]] = {
    "player_card": {
        "default": ColorDefinition(
            id="default", label="Slate", is_premium=False, credit_cost=0,
            dot_color="#667eea", sort_order=0,
        ),
        "midnight": ColorDefinition(
            id="midnight", label="Midnight", is_premium=False, credit_cost=0,
            dot_color="#00d4ff", sort_order=1,
        ),
        "arctic": ColorDefinition(
            id="arctic", label="Arctic", is_premium=False, credit_cost=0,
            dot_color="#4299e1", sort_order=2,
        ),
        "gold": ColorDefinition(
            id="gold", label="Gold", is_premium=True, credit_cost=500,
            dot_color="#f6ad3c", sort_order=3,
        ),
        "emerald": ColorDefinition(
            id="emerald", label="Emerald", is_premium=True, credit_cost=500,
            dot_color="#4cde82", sort_order=4,
        ),
        "crimson": ColorDefinition(
            id="crimson", label="Crimson", is_premium=True, credit_cost=500,
            dot_color="#ff6b6b", sort_order=5,
        ),
    },
    # TS-2: "welcome_card": {...},
    # TS-2: "challenge_card": {...},
}

# Supported families in TS-1 — used for route validation.
_SUPPORTED_FAMILIES: frozenset[str] = frozenset(FAMILY_COLORS.keys())


# ── Public API ────────────────────────────────────────────────────────────────

def get_colors_for_family(card_type_id: str) -> list[ColorDefinition]:
    """Return all available colors for a family, ordered by sort_order.

    Returns an empty list for unknown or not-yet-supported families
    (never raises KeyError).
    """
    palette = FAMILY_COLORS.get(card_type_id, {})
    return sorted(
        (c for c in palette.values() if c.available),
        key=lambda c: c.sort_order,
    )


def get_owned_color_ids(db: Session, user_id: int, card_type_id: str) -> set[str]:
    """Return the set of color_ids the user owns for the given family.

    Does NOT include free colors (those are always implicitly owned).
    A single DB query — use this for batch checks (e.g. editor filter loop).
    """
    rows = (
        db.query(CardColorOwnership.color_id)
        .filter(
            CardColorOwnership.user_id == user_id,
            CardColorOwnership.card_type_id == card_type_id,
        )
        .all()
    )
    return {r[0] for r in rows}


def is_color_unlocked(db: Session, user_id: int, card_type_id: str, color_id: str) -> bool:
    """Return True if the user may use this color.

    Free colors always return True (no DB query).
    Premium colors require a card_color_ownership row.
    Unknown color_ids return False.
    """
    palette = FAMILY_COLORS.get(card_type_id, {})
    color = palette.get(color_id)
    if color is None:
        return False
    if not color.is_premium:
        return True
    return (
        db.query(CardColorOwnership.id)
        .filter(
            CardColorOwnership.user_id == user_id,
            CardColorOwnership.card_type_id == card_type_id,
            CardColorOwnership.color_id == color_id,
        )
        .first()
    ) is not None


@dataclass
class UnlockColorResult:
    ok: bool
    already_owned: bool
    credits_charged: int
    credit_balance: int
    color_id: str
    card_type_id: str


def unlock_color(
    db: Session,
    user: User,
    card_type_id: str,
    color_id: str,
    pack_id: Optional[str] = None,
) -> UnlockColorResult:
    """Unlock a premium color for the user.

    Idempotency contract:
      1. Free color → already_owned=True, 0 CR, no DB write.
      2. Already owned premium → already_owned=True, 0 CR, no DB write.
      3. Insufficient credits → raises InsufficientCreditsError, no DB write.
      4. Valid purchase → credit deduction + ownership INSERT in one SAVEPOINT.
         If INSERT conflicts (race condition), credit deduction is rolled back.

    Raises:
      ValueError("unsupported_family")   — family not in FAMILY_COLORS
      ValueError("color_not_found")      — color_id not in family palette
      InsufficientCreditsError           — user balance < credit_cost
    """
    # Step 1: validate family
    if card_type_id not in _SUPPORTED_FAMILIES:
        raise ValueError("unsupported_family")

    palette = FAMILY_COLORS[card_type_id]

    # Step 2: validate color
    color = palette.get(color_id)
    if color is None:
        raise ValueError("color_not_found")

    # Step 3: free color — always owned, no charge
    if not color.is_premium:
        return UnlockColorResult(
            ok=True,
            already_owned=True,
            credits_charged=0,
            credit_balance=user.credit_balance,
            color_id=color_id,
            card_type_id=card_type_id,
        )

    # Step 4: already owned premium — idempotent return
    existing = (
        db.query(CardColorOwnership.id)
        .filter(
            CardColorOwnership.user_id == user.id,
            CardColorOwnership.card_type_id == card_type_id,
            CardColorOwnership.color_id == color_id,
        )
        .first()
    )
    if existing:
        return UnlockColorResult(
            ok=True,
            already_owned=True,
            credits_charged=0,
            credit_balance=user.credit_balance,
            color_id=color_id,
            card_type_id=card_type_id,
        )

    # Step 5 + 6: credit deduction + ownership INSERT — atomic SAVEPOINT.
    # If INSERT conflicts (concurrent purchase), the SAVEPOINT rolls back both.
    with db.begin_nested():
        # InsufficientCreditsError propagates; SAVEPOINT rolls back automatically.
        CreditService(db).deduct(
            user=user,
            amount=color.credit_cost,
            transaction_type="COLOR_UNLOCK",
            description=f"Card color unlock: {color.label} ({card_type_id})",
            idempotency_key=f"color_unlock_{user.id}_{card_type_id}_{color_id}",
        )

        ownership = CardColorOwnership(
            user_id=user.id,
            card_type_id=card_type_id,
            color_id=color_id,
            pack_id=pack_id,
            purchased_at=datetime.now(timezone.utc),
        )
        db.add(ownership)
        db.flush()

    db.commit()

    return UnlockColorResult(
        ok=True,
        already_owned=False,
        credits_charged=color.credit_cost,
        credit_balance=user.credit_balance,
        color_id=color_id,
        card_type_id=card_type_id,
    )

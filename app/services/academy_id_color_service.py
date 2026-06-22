"""
Academy ID dedicated colour service — Phase 2 (free + premium colours).

This module is completely isolated from the Player Card, Welcome Card, and
Challenge Card colour/theme systems.  It must NOT import from:
  - card_color_service
  - card_theme_service
  - shop_catalog_service

Ownership model:
  Free colours (official / ivory / charcoal) are always accessible — no DB row.
  Premium colours require a card_color_ownership row with
  card_type_id = 'academy_id'.  Purchased once, owned forever.

Active colour is stored on user_licenses.academy_id_color (String 50).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models.card_color_ownership import CardColorOwnership
from ..models.license import UserLicense
from ..models.user import User
from .credit_service import CreditService, InsufficientCreditsError  # noqa: F401 — re-exported


# ── Palette definition ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AcademyIDColor:
    id:          str
    label:       str
    dot_color:   str   # hex — shown in the iOS swatch picker
    sort_order:  int
    is_premium:  bool = False
    credit_cost: int  = 0


# Free colours (sort_order 0–2) + Phase 2 premium colours (sort_order 10–12).

ACADEMY_ID_COLORS: list[AcademyIDColor] = [
    AcademyIDColor(id="official", label="Official",  dot_color="#b8a06a", sort_order=0),
    AcademyIDColor(id="ivory",    label="Ivory",     dot_color="#d4c5a0", sort_order=1),
    AcademyIDColor(id="charcoal", label="Charcoal",  dot_color="#3a3a3a", sort_order=2),
    # Phase 2 premium colours (300 CR each)
    AcademyIDColor(id="navy",     label="Navy",      dot_color="#0d1b2a", sort_order=10, is_premium=True, credit_cost=300),
    AcademyIDColor(id="burgundy", label="Burgundy",  dot_color="#2a0a14", sort_order=11, is_premium=True, credit_cost=300),
    AcademyIDColor(id="forest",   label="Forest",    dot_color="#0f2419", sort_order=12, is_premium=True, credit_cost=300),
]

_COLOR_BY_ID: dict[str, AcademyIDColor] = {c.id: c for c in ACADEMY_ID_COLORS}
_VALID_COLOR_IDS: frozenset[str] = frozenset(_COLOR_BY_ID)
_CARD_TYPE_ID = "academy_id"


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_all_colors() -> list[AcademyIDColor]:
    return ACADEMY_ID_COLORS


def get_color_by_id(color_id: str) -> Optional[AcademyIDColor]:
    return _COLOR_BY_ID.get(color_id)


def get_active_color_id(user_license: UserLicense) -> str:
    stored = getattr(user_license, "academy_id_color", None) or "official"
    return stored if stored in _VALID_COLOR_IDS else "official"


def is_valid_color(color_id: str) -> bool:
    return color_id in _VALID_COLOR_IDS


def get_owned_premium_color_ids(db: Session, user_id: int) -> frozenset[str]:
    """Return set of premium color_ids this user has purchased (no DB row for free colours)."""
    rows = (
        db.query(CardColorOwnership.color_id)
        .filter(
            CardColorOwnership.user_id == user_id,
            CardColorOwnership.card_type_id == _CARD_TYPE_ID,
        )
        .all()
    )
    return frozenset(r[0] for r in rows)


def get_all_colors_with_ownership(db: Session, user_id: int) -> list[dict]:
    """Return full palette with per-user is_owned. Free colours always True."""
    owned = get_owned_premium_color_ids(db, user_id)
    return [
        {
            "id":          c.id,
            "label":       c.label,
            "dot_color":   c.dot_color,
            "is_premium":  c.is_premium,
            "credit_cost": c.credit_cost,
            "is_owned":    True if not c.is_premium else (c.id in owned),
            "sort_order":  c.sort_order,
        }
        for c in ACADEMY_ID_COLORS
    ]


def is_color_accessible(db: Session, user_id: int, color_id: str) -> bool:
    color = _COLOR_BY_ID.get(color_id)
    if color is None:
        return False
    if not color.is_premium:
        return True
    return color_id in get_owned_premium_color_ids(db, user_id)


# ── Write helpers ─────────────────────────────────────────────────────────────

def set_active_color(db: Session, user_license: UserLicense, color_id: str) -> str:
    """Persist selected colour. Caller must verify accessibility first."""
    if color_id not in _VALID_COLOR_IDS:
        raise ValueError(f"Unknown Academy ID colour: {color_id!r}. Valid options: {sorted(_VALID_COLOR_IDS)}")
    user_license.academy_id_color = color_id
    db.commit()
    return color_id


@dataclass
class UnlockColorResult:
    ok: bool
    already_owned: bool
    credits_charged: int
    credit_balance: int
    color_id: str


def unlock_academy_id_color(db: Session, user: User, color_id: str) -> UnlockColorResult:
    """Unlock a premium Academy ID colour for the user.

    Idempotency contract:
      1. Unknown color_id → raises ValueError("color_unknown")
      2. Free colour      → raises ValueError("color_is_free")
      3. Already owned    → UnlockColorResult(already_owned=True, credits_charged=0)
      4. Insufficient CR  → raises InsufficientCreditsError (no DB write)
      5. Valid purchase   → credit deduction + ownership INSERT in one SAVEPOINT;
                            IntegrityError on INSERT (race) propagates to caller
    """
    color = _COLOR_BY_ID.get(color_id)
    if color is None:
        raise ValueError("color_unknown")
    if not color.is_premium:
        raise ValueError("color_is_free")

    existing_row = (
        db.query(CardColorOwnership.id)
        .filter(
            CardColorOwnership.user_id == user.id,
            CardColorOwnership.card_type_id == _CARD_TYPE_ID,
            CardColorOwnership.color_id == color_id,
        )
        .first()
    )
    if existing_row:
        return UnlockColorResult(ok=True, already_owned=True, credits_charged=0,
                                  credit_balance=user.credit_balance, color_id=color_id)

    with db.begin_nested():
        CreditService(db).deduct(
            user=user,
            amount=color.credit_cost,
            transaction_type="ACADEMY_ID_COLOR_UNLOCK",
            description=f"Academy ID color unlock: {color.label}",
            idempotency_key=f"academy_id_color_unlock_{user.id}_{color_id}",
        )
        db.add(CardColorOwnership(
            user_id=user.id,
            card_type_id=_CARD_TYPE_ID,
            color_id=color_id,
            pack_id=None,
            purchased_at=datetime.now(timezone.utc),
        ))
        db.flush()

    db.commit()
    return UnlockColorResult(ok=True, already_owned=False, credits_charged=color.credit_cost,
                              credit_balance=user.credit_balance, color_id=color_id)

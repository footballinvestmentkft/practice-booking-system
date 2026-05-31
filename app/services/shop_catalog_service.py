"""
shop_catalog_service — Unified Shop Catalog (SHOP-1).

Builds a single list[ShopItem] spanning all three card families
(player_card, welcome_card, challenge_card) with CDO-based ownership.

SHOP-1 scope: listing + filter only, no color/premium purchase, no TS-2.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .card_design_service import (
    CHALLENGE_CARD_FORMATS,
    WELCOME_CARD_FORMATS,
    get_all_designs,
    get_owned_design_ids,
)


# ── ShopItem ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ShopItem:
    id:              str
    card_type_id:    str           # "player_card" | "welcome_card" | "challenge_card"
    family_id:       str           # "fclassic" for all current items
    label:           str
    description:     str
    style_tag:       str | None    # "IDENTITY CARD" | "STORY" stb.
    dims:            str | None    # "1080 × 1350"
    preview_url:     str | None
    card_type_label: str           # "Player Card" | "Welcome Card" | "Challenge Card"
    sort_group:      int           # 0=player, 1=welcome, 2=challenge
    sort_order:      int
    price_credits:   int
    is_premium:      bool
    is_owned:        bool
    state:           str           # "owned" | "get_card" | "locked" | "not_available"
    buy_url:         str
    studio_url:      str | None
    detail_url:      str | None
    tags:            tuple[str, ...] = field(default_factory=tuple)


# ── Builder ───────────────────────────────────────────────────────────────────

def build_shop_catalog(
    db: Session,
    user_id: int,
    credit_balance: int = 0,
    type_filter: str | None = None,
) -> list[ShopItem]:
    """Return all ShopItems, optionally filtered by card_type_id.

    Uses three batch CDO queries (one per family) — not N+1.
    """
    items: list[ShopItem] = []

    # Batch ownership per family (3 DB queries total)
    if type_filter is None or type_filter == "player_card":
        pc_owned = get_owned_design_ids(db, user_id, "player_card")
        items.extend(_build_player_items(db, credit_balance, pc_owned))

    if type_filter is None or type_filter == "welcome_card":
        wc_owned = get_owned_design_ids(db, user_id, "welcome_card")
        items.extend(_build_welcome_items(credit_balance, wc_owned))

    if type_filter is None or type_filter == "challenge_card":
        cc_owned = get_owned_design_ids(db, user_id, "challenge_card")
        items.extend(_build_challenge_items(credit_balance, cc_owned))

    return items


def _state(credit_cost: int, is_premium: bool, owned: bool, credits: int) -> str:
    if owned:
        return "owned"
    if credit_cost == 0:
        return "not_available"
    return "get_card" if credits >= credit_cost else "locked"


def _build_player_items(
    db: Session, credits: int, owned_ids: set[str]
) -> list[ShopItem]:
    designs = get_all_designs(db)
    result = []
    for d in designs:
        owned = d.id in owned_ids
        st    = _state(d.credit_cost, d.is_premium, owned, credits)
        result.append(ShopItem(
            id             = d.id,
            card_type_id   = "player_card",
            family_id      = "fclassic",   # all current player designs are FClassic family
            label          = d.label,
            description    = d.description or "",
            style_tag      = None,
            dims           = None,
            preview_url    = None,
            card_type_label= "Player Card",
            sort_group     = 0,
            sort_order     = d.sort_order,
            price_credits  = d.credit_cost,
            is_premium     = d.is_premium,
            is_owned       = owned,
            state          = st,
            buy_url        = f"/shop/cards/player_card/buy/{d.id}",
            studio_url     = "/card-studio" if owned else None,
            detail_url     = f"/shop/cards/player/{d.id}",
            tags           = ("player",),
        ))
    return result


def _build_welcome_items(credits: int, owned_ids: set[str]) -> list[ShopItem]:
    result = []
    for i, fmt in enumerate(WELCOME_CARD_FORMATS):
        owned = fmt.design_id in owned_ids
        st    = _state(fmt.credit_cost, True, owned, credits)
        result.append(ShopItem(
            id             = fmt.design_id,
            card_type_id   = "welcome_card",
            family_id      = fmt.family_id,
            label          = fmt.label,
            description    = f"Welcome Card — {fmt.dims}",
            style_tag      = fmt.style_tag,
            dims           = fmt.dims,
            preview_url    = f"/profile/onboarding-card?platform={fmt.preview_platform}",
            card_type_label= "Welcome Card",
            sort_group     = 1,
            sort_order     = fmt.sort_order if hasattr(fmt, "sort_order") else i,
            price_credits  = fmt.credit_cost,
            is_premium     = True,
            is_owned       = owned,
            state          = st,
            buy_url        = f"/shop/cards/welcome_card/buy/{fmt.design_id}",
            studio_url     = f"/card-studio/welcome?format={fmt.design_id}" if owned else None,
            detail_url     = None,
            tags           = ("welcome", fmt.preview_platform),
        ))
    return result


def _build_challenge_items(credits: int, owned_ids: set[str]) -> list[ShopItem]:
    result = []
    for i, fmt in enumerate(CHALLENGE_CARD_FORMATS):
        owned = fmt.design_id in owned_ids
        st    = _state(fmt.credit_cost, True, owned, credits)
        result.append(ShopItem(
            id             = fmt.design_id,
            card_type_id   = "challenge_card",
            family_id      = fmt.family_id,
            label          = fmt.label,
            description    = f"Challenge Card — {fmt.dims}",
            style_tag      = fmt.style_tag,
            dims           = fmt.dims,
            preview_url    = None,
            card_type_label= "Challenge Card",
            sort_group     = 2,
            sort_order     = fmt.sort_order if hasattr(fmt, "sort_order") else i,
            price_credits  = fmt.credit_cost,
            is_premium     = True,
            is_owned       = owned,
            state          = st,
            buy_url        = f"/shop/cards/challenge_card/buy/{fmt.design_id}",
            studio_url     = None,
            detail_url     = None,
            tags           = ("challenge",),
        ))
    return result


# ── Filter helper ─────────────────────────────────────────────────────────────

_VALID_TYPE_FILTERS: frozenset[str] = frozenset(
    {"player_card", "welcome_card", "challenge_card"}
)


def resolve_type_filter(type_param: str | None) -> str | None:
    """Return a valid card_type_id filter or None (=All).

    Invalid values return None (fallback to All — no 400 to avoid breaking
    old bookmarks that might pass stale type values).
    """
    if type_param and type_param in _VALID_TYPE_FILTERS:
        return type_param
    return None

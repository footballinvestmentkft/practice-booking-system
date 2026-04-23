"""
Player Card Theme Service
=========================
Manages colour themes for the public LFA Football Player card.

Free themes (default, midnight, arctic) are always available.
Premium themes (gold, emerald, crimson) require credit purchase.

Adding a new theme requires only:
  1. A new entry in THEMES below
  2. A new .theme-<id> CSS block in player_card.html
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .credit_service import CreditService


@dataclass(frozen=True)
class ThemeDefinition:
    id: str
    label: str
    is_premium: bool
    credit_cost: int
    # CSS custom-property values sent to the template
    panel_bg: str       # --card-panel-bg  (fifa-left gradient)
    body_bg: str        # --card-body-bg   (skills + events section)
    tab_bg: str         # --card-tab-bg    (tab bar)
    accent: str         # --card-accent    (active tab underline, badge tints)
    # Dot colour shown in the dashboard theme picker
    dot_color: str


# ── Theme registry ────────────────────────────────────────────────────────────
THEMES: dict[str, ThemeDefinition] = {
    "default": ThemeDefinition(
        id="default", label="Slate", is_premium=False, credit_cost=0,
        panel_bg="linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)",
        body_bg="#1a202c", tab_bg="#2d3748", accent="#667eea",
        dot_color="#667eea",
    ),
    "midnight": ThemeDefinition(
        id="midnight", label="Midnight", is_premium=False, credit_cost=0,
        panel_bg="linear-gradient(155deg, #0d0d0d 0%, #1a1a2e 60%, #16213e 100%)",
        body_bg="#0f0f0f", tab_bg="#1a1a1a", accent="#00d4ff",
        dot_color="#00d4ff",
    ),
    "arctic": ThemeDefinition(
        id="arctic", label="Arctic", is_premium=False, credit_cost=0,
        panel_bg="linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)",
        body_bg="#f7fafc", tab_bg="#edf2f7", accent="#4299e1",
        dot_color="#4299e1",
    ),
    "gold": ThemeDefinition(
        id="gold", label="Gold", is_premium=True, credit_cost=500,
        panel_bg="linear-gradient(155deg, #3d2200 0%, #5c3500 60%, #3d2200 100%)",
        body_bg="#1e1500", tab_bg="#2d1f00", accent="#f6ad3c",
        dot_color="#f6ad3c",
    ),
    "emerald": ThemeDefinition(
        id="emerald", label="Emerald", is_premium=True, credit_cost=500,
        panel_bg="linear-gradient(155deg, #0a2d0a 0%, #144d1e 60%, #0a2d14 100%)",
        body_bg="#0d1f0d", tab_bg="#142b14", accent="#4cde82",
        dot_color="#4cde82",
    ),
    "crimson": ThemeDefinition(
        id="crimson", label="Crimson", is_premium=True, credit_cost=500,
        panel_bg="linear-gradient(155deg, #3d0a0a 0%, #5c1414 60%, #3d0a14 100%)",
        body_bg="#1e0d0d", tab_bg="#2d1010", accent="#ff6b6b",
        dot_color="#ff6b6b",
    ),
}

# Ordered list for the picker UI (free first, then premium)
THEME_ORDER = ["default", "midnight", "arctic", "gold", "emerald", "crimson"]


# ── Public API ────────────────────────────────────────────────────────────────

def get_theme(theme_id: str) -> ThemeDefinition:
    """Return theme by ID, falling back to 'default' for unknown IDs."""
    return THEMES.get(theme_id, THEMES["default"])


def get_all_themes() -> list[ThemeDefinition]:
    """Return all themes in display order."""
    return [THEMES[tid] for tid in THEME_ORDER if tid in THEMES]


def is_unlocked(user_license, theme_id: str) -> bool:
    """
    Return True if the user may use this theme.
    Free themes are always unlocked.
    Premium themes require the theme_id to be in user_license.unlocked_card_themes.
    """
    theme = THEMES.get(theme_id)
    if theme is None:
        return False
    if not theme.is_premium:
        return True
    unlocked = user_license.unlocked_card_themes or []
    return theme_id in unlocked


def apply_theme(db, user_license, theme_id: str) -> None:
    """
    Set the active theme on user_license and commit.
    Raises ValueError if theme unknown or not yet unlocked.
    """
    if theme_id not in THEMES:
        raise ValueError(f"Unknown theme: {theme_id!r}")
    if not is_unlocked(user_license, theme_id):
        theme = THEMES[theme_id]
        raise ValueError(
            f"Theme '{theme.label}' is locked. Required: {theme.credit_cost} CR"
        )
    user_license.card_theme = theme_id
    db.commit()


def unlock_theme(db, user, user_license, theme_id: str) -> None:
    """
    Unlock a premium theme for the user.

    Uses CreditService.deduct() which wraps the balance UPDATE and the CT INSERT
    inside a single SAVEPOINT — both succeed or both roll back together.

    Raises ValueError (InsufficientCreditsError) if theme unknown, already
    unlocked, or insufficient balance.
    """
    if theme_id not in THEMES:
        raise ValueError(f"Unknown theme: {theme_id!r}")
    theme = THEMES[theme_id]
    if not theme.is_premium:
        return  # free themes don't need unlocking
    unlocked: list = list(user_license.unlocked_card_themes or [])
    if theme_id in unlocked:
        return  # already unlocked — idempotent

    # Atomic deduction + CT insert (SAVEPOINT guarantees coupling).
    # InsufficientCreditsError propagates to the caller unchanged.
    CreditService(db).deduct(
        user=user,
        amount=theme.credit_cost,
        transaction_type="THEME_UNLOCK",
        description=f"Card theme unlock: {theme.label}",
        idempotency_key=f"theme_unlock_{user.id}_{theme_id}",
    )

    # Update unlocked themes list (same outer transaction)
    unlocked.append(theme_id)
    user_license.unlocked_card_themes = unlocked

    db.commit()

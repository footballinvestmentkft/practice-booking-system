"""
Player Card Theme Service
=========================
Manages colour themes for the public LFA Football Player card.

Free themes (default, midnight, arctic) are always available.
Premium themes (gold, emerald, crimson) require credit purchase.

Theme resolution order:
  1. DB cache (_theme_cache, 60-second TTL)
  2. Hardcoded THEMES dict (fallback when DB is unavailable or cache empty)

Adding a new theme requires only a new row in the card_themes DB table.
The browser card reads ThemeDefinition fields directly via a Jinja2 :root
injection block in player_card_base.html — no separate CSS class needed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .credit_service import CreditService
from .card_draft_service import CardDraftService


@dataclass(frozen=True)
class ThemeDefinition:
    id: str
    label: str
    is_premium: bool
    credit_cost: int
    # Core CSS custom-property values — injected as inline :root vars in templates
    panel_bg: str       # --card-panel-bg  (fifa-left gradient)
    body_bg: str        # --card-body-bg   (skills + events section)
    tab_bg: str         # --card-tab-bg    (tab bar)
    accent: str         # --card-accent    (active tab underline, badge tints)
    page_bg: str        # --card-page-bg   (page chrome behind the card)
    # Dot colour shown in the dashboard theme picker
    dot_color: str
    # True when body_bg is light-coloured — templates emit dark-on-light tokens
    is_light_body_bg: bool = False
    # Per-theme tokens that vary within the dark palette (light themes use rgba(0,0,0,...))
    text_faint: str = 'rgba(255,255,255,0.35)'   # --card-text-faint  (labels, titles)
    val_neutral: str = 'rgba(255,255,255,0.85)'  # --card-val-neutral (skill values)
    skill_up: str = '#48bb78'                     # --card-skill-up
    skill_dn: str = '#fc8181'                     # --card-skill-dn
    # Display order — used by get_all_themes() when DB-backed; 0 in fallback THEMES dict
    sort_order: int = 0


# ── Hardcoded fallback registry ───────────────────────────────────────────────
# Used when DB is unavailable or cache is empty (e.g. unit tests with MagicMock).
# Must stay in sync with the seed rows in 2026_05_17_1000_add_card_themes.py.
THEMES: dict[str, ThemeDefinition] = {
    "default": ThemeDefinition(
        id="default", label="Slate", is_premium=False, credit_cost=0,
        panel_bg="linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)",
        body_bg="#1a202c", tab_bg="#2d3748", accent="#667eea",
        page_bg="#0f1923", dot_color="#667eea", sort_order=0,
    ),
    "midnight": ThemeDefinition(
        id="midnight", label="Midnight", is_premium=False, credit_cost=0,
        panel_bg="linear-gradient(155deg, #0d0d0d 0%, #1a1a2e 60%, #16213e 100%)",
        body_bg="#0f0f0f", tab_bg="#1a1a1a", accent="#00d4ff",
        page_bg="#050505", dot_color="#00d4ff",
        text_faint='rgba(255,255,255,0.35)', val_neutral='rgba(255,255,255,0.85)',
        sort_order=1,
    ),
    "arctic": ThemeDefinition(
        id="arctic", label="Arctic", is_premium=False, credit_cost=0,
        panel_bg="linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)",
        body_bg="#f7fafc", tab_bg="#edf2f7", accent="#4299e1",
        page_bg="#e2e8f0", dot_color="#4299e1",
        is_light_body_bg=True,
        text_faint='rgba(0,0,0,0.30)', val_neutral='rgba(0,0,0,0.70)',
        skill_up='#276749', skill_dn='#c53030', sort_order=2,
    ),
    "gold": ThemeDefinition(
        id="gold", label="Gold", is_premium=True, credit_cost=500,
        panel_bg="linear-gradient(155deg, #3d2200 0%, #5c3500 60%, #3d2200 100%)",
        body_bg="#1e1500", tab_bg="#2d1f00", accent="#f6ad3c",
        page_bg="#120d00", dot_color="#f6ad3c",
        text_faint='rgba(255,255,255,0.48)', val_neutral='rgba(255,255,255,0.68)',
        sort_order=3,
    ),
    "emerald": ThemeDefinition(
        id="emerald", label="Emerald", is_premium=True, credit_cost=500,
        panel_bg="linear-gradient(155deg, #0a2d0a 0%, #144d1e 60%, #0a2d14 100%)",
        body_bg="#0d1f0d", tab_bg="#142b14", accent="#4cde82",
        page_bg="#060f06", dot_color="#4cde82",
        text_faint='rgba(255,255,255,0.48)', val_neutral='rgba(255,255,255,0.68)',
        sort_order=4,
    ),
    "crimson": ThemeDefinition(
        id="crimson", label="Crimson", is_premium=True, credit_cost=500,
        panel_bg="linear-gradient(155deg, #3d0a0a 0%, #5c1414 60%, #3d0a14 100%)",
        body_bg="#1e0d0d", tab_bg="#2d1010", accent="#ff6b6b",
        page_bg="#120404", dot_color="#ff6b6b",
        text_faint='rgba(255,255,255,0.38)', val_neutral='rgba(255,255,255,0.65)',
        skill_up='#68d391', skill_dn='#ffb3b3', sort_order=5,
    ),
}

# Ordered list for the THEMES fallback dict only (used when DB unavailable).
# The live picker ordering is determined exclusively by card_themes.sort_order ASC.
THEME_ORDER = ["default", "midnight", "arctic", "gold", "emerald", "crimson"]


# ── DB-backed cache ───────────────────────────────────────────────────────────
_CACHE_TTL = 60.0  # seconds

_theme_cache: dict[str, ThemeDefinition] = {}
_cache_loaded_at: float = 0.0


def _invalidate_cache() -> None:
    """Flush the in-process cache. Useful in tests and after admin updates."""
    global _theme_cache, _cache_loaded_at
    _theme_cache = {}
    _cache_loaded_at = 0.0


def _row_to_definition(row) -> ThemeDefinition:
    return ThemeDefinition(
        id=row.id,
        label=row.label,
        is_premium=row.is_premium,
        credit_cost=row.credit_cost,
        panel_bg=row.panel_bg,
        body_bg=row.body_bg,
        tab_bg=row.tab_bg,
        accent=row.accent,
        page_bg=row.page_bg,
        dot_color=row.dot_color,
        is_light_body_bg=row.is_light_body_bg,
        text_faint=row.text_faint,
        val_neutral=row.val_neutral,
        skill_up=row.skill_up,
        skill_dn=row.skill_dn,
        sort_order=row.sort_order,
    )


def _load_cache(db) -> dict[str, ThemeDefinition]:
    """Query all active themes from DB and return as id→ThemeDefinition dict."""
    from app.models.card_theme import CardTheme
    rows = db.query(CardTheme).filter(CardTheme.is_active.is_(True)).all()
    return {r.id: _row_to_definition(r) for r in rows}


def _maybe_reload(db=None) -> dict[str, ThemeDefinition]:
    """Return the live cache, refreshing from DB if TTL has expired."""
    global _theme_cache, _cache_loaded_at
    if db is not None and (time.monotonic() - _cache_loaded_at) > _CACHE_TTL:
        new_cache = _load_cache(db)
        if new_cache:
            _theme_cache = new_cache
            _cache_loaded_at = time.monotonic()
    # Fallback to hardcoded THEMES when cache is empty (DB unavailable / no db arg)
    return _theme_cache if _theme_cache else THEMES


# ── Public API ────────────────────────────────────────────────────────────────

def get_theme(theme_id: str, db=None) -> ThemeDefinition:
    """Return theme by ID, falling back to 'default' for unknown/inactive IDs."""
    cache = _maybe_reload(db)
    return cache.get(theme_id, cache.get("default", THEMES["default"]))


def get_all_themes(db=None) -> list[ThemeDefinition]:
    """Return all active themes ordered by sort_order ASC then id ASC.

    When DB is unavailable (cache empty, db=None) falls back to THEMES dict
    which already carries sort_order values matching the DB seed.
    """
    cache = _maybe_reload(db)
    return sorted(cache.values(), key=lambda t: (t.sort_order, t.id))


def is_unlocked(user_license, theme_id: str, db=None) -> bool:
    """
    Return True if the user may use this theme.
    Free themes are always unlocked.
    Premium themes require the theme_id to be in user_license.unlocked_card_themes.
    Unknown or inactive theme IDs return False.
    """
    cache = _maybe_reload(db)
    theme = cache.get(theme_id)
    if theme is None:
        return False
    if not theme.is_premium:
        return True
    unlocked = user_license.unlocked_card_themes or []
    return theme_id in unlocked


def apply_theme(db, user_license, theme_id: str) -> None:
    """
    Set the active draft theme on the player CardDraft and commit.
    Raises ValueError if theme unknown, inactive, or not yet unlocked.
    """
    cache = _maybe_reload(db)
    if theme_id not in cache:
        raise ValueError(f"Unknown or inactive theme: {theme_id!r}")
    if not is_unlocked(user_license, theme_id, db=db):
        theme = cache[theme_id]
        raise ValueError(
            f"Theme '{theme.label}' is locked. Required: {theme.credit_cost} CR"
        )
    draft = CardDraftService.get_player_card_draft(db, user_id=user_license.user_id)
    CardDraftService.update_draft_theme(db, draft, theme_id)


def unlock_theme(db, user, user_license, theme_id: str) -> None:
    """
    Unlock a premium theme for the user.

    Uses CreditService.deduct() which wraps the balance UPDATE and the CT INSERT
    inside a single SAVEPOINT — both succeed or both roll back together.

    Raises ValueError (InsufficientCreditsError) if theme unknown, already
    unlocked, or insufficient balance.
    """
    cache = _maybe_reload(db)
    if theme_id not in cache:
        raise ValueError(f"Unknown or inactive theme: {theme_id!r}")
    theme = cache[theme_id]
    if not theme.is_premium:
        return  # free themes don't need unlocking
    unlocked: list = list(user_license.unlocked_card_themes or [])
    if theme_id in unlocked:
        return  # already unlocked — idempotent

    # Ensure the draft row exists before the credit SAVEPOINT so any draft-creation
    # commit is isolated from the credit + unlock transaction below.
    draft = CardDraftService.get_player_card_draft(db, user_id=user_license.user_id)

    # Atomic deduction + CT insert (SAVEPOINT guarantees coupling).
    # InsufficientCreditsError propagates to the caller unchanged.
    CreditService(db).deduct(
        user=user,
        amount=theme.credit_cost,
        transaction_type="THEME_UNLOCK",
        description=f"Card theme unlock: {theme.label}",
        idempotency_key=f"theme_unlock_{user.id}_{theme_id}",
    )

    # Update unlocked list and stage draft theme (commit=False keeps one outer commit)
    unlocked.append(theme_id)
    user_license.unlocked_card_themes = unlocked
    CardDraftService.update_draft_theme(db, draft, theme_id, commit=False)

    db.commit()

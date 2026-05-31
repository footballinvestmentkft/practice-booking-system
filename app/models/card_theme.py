"""CardTheme — DB-backed registry of player card colour themes.

Each row represents one theme available in the card editor.
Free themes (is_premium=False) are always available.
Premium themes require credit purchase (tracked on UserLicense.unlocked_card_themes).

The THEMES dict in card_theme_service.py acts as a warm-start fallback when the
DB is unavailable or the cache has not yet been populated.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from ..database import Base


class CardTheme(Base):
    __tablename__ = "card_themes"

    # ── Identity ──────────────────────────────────────────────────────────────
    id    = Column(String(50), primary_key=True,
                   comment="Stable slug, e.g. 'default', 'midnight', 'gold'")
    label = Column(String(80), nullable=False,
                   comment="Human-readable name shown in the picker UI")

    # ── Availability ──────────────────────────────────────────────────────────
    is_premium  = Column(Boolean, nullable=False, default=False)
    credit_cost = Column(Integer, nullable=False, default=0)
    is_active   = Column(Boolean, nullable=False, default=True,
                         comment="Soft-delete; False hides the theme from the picker")
    sort_order  = Column(Integer, nullable=False, default=0,
                         comment="Ascending; free themes first, then premium")

    # ── CSS custom-property values ────────────────────────────────────────────
    panel_bg = Column(Text, nullable=False,
                      comment="--card-panel-bg: left panel gradient (FClassic Player)")
    body_bg  = Column(String(100), nullable=False,
                      comment="--card-body-bg: skills/events background")
    tab_bg   = Column(String(100), nullable=False,
                      comment="--card-tab-bg: tab bar background")
    accent   = Column(String(100), nullable=False,
                      comment="--card-accent: active tab + badge tints")
    page_bg  = Column(String(100), nullable=False,
                      comment="--card-page-bg: page chrome behind the card")
    dot_color = Column(String(100), nullable=False,
                       comment="Dot shown in the dashboard theme picker")

    # ── Palette flags ─────────────────────────────────────────────────────────
    is_light_body_bg = Column(Boolean, nullable=False, default=False,
                               comment="True → light-on-dark token set in templates")
    text_faint = Column(String(100), nullable=False,
                        server_default='rgba(255,255,255,0.35)')
    val_neutral = Column(String(100), nullable=False,
                         server_default='rgba(255,255,255,0.85)')
    skill_up = Column(String(100), nullable=False, server_default='#48bb78')
    skill_dn = Column(String(100), nullable=False, server_default='#fc8181')

    # ── Audit ─────────────────────────────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<CardTheme id={self.id!r} label={self.label!r} active={self.is_active}>"

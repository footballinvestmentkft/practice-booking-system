"""CardDesign — DB-backed registry of player card design families.

Each row represents one card design available in the card editor.
Free designs (is_premium=False) are always available.
Premium designs require credit purchase (tracked on UserLicense.unlocked_card_variants).

The DESIGNS dict in card_design_service.py acts as a warm-start fallback when the
DB is unavailable or the cache has not yet been populated.

CS-4a note: archetype_id is NULL for all designs until parameterizable base
templates are introduced. When populated it enables manifest-only new design
creation without template deployment.
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from ..database import Base


class CardDesign(Base):
    __tablename__ = "card_designs"

    # ── Identity ──────────────────────────────────────────────────────────────
    id    = Column(String(50), primary_key=True,
                   comment="Stable slug, e.g. 'fifa', 'pulse', 'compact'")
    label = Column(String(80), nullable=False,
                   comment="Human-readable name shown in the picker UI")
    description = Column(Text, nullable=False, default="",
                         comment="Tooltip / preview text shown in the card editor")

    # ── Availability ──────────────────────────────────────────────────────────
    is_premium  = Column(Boolean, nullable=False, default=False)
    credit_cost = Column(Integer, nullable=False, default=0)
    is_active   = Column(Boolean, nullable=False, default=True,
                         comment="Soft-delete; False hides the design from the picker")
    sort_order  = Column(Integer, nullable=False, default=0,
                         comment="Ascending display order in the card editor picker")

    # ── Template routing ──────────────────────────────────────────────────────
    browser_template = Column(
        String(300), nullable=False,
        comment="Jinja2 template path for browser preview, relative to templates/",
    )

    # ── CS-4a bridge ─────────────────────────────────────────────────────────
    # NULL until parameterizable base templates are introduced (CS-4a).
    # When set, the export router uses archetype template resolution instead of
    # the {bucket}/{design_id}.html path.
    archetype_id = Column(
        String(50), nullable=True,
        comment="Foreign key into the archetype template registry; NULL until CS-4a",
    )

    # ── Export capability ─────────────────────────────────────────────────────
    # Explicit list of export bucket names this design supports.
    # Replaces the implicit file-existence check ("does {bucket}/{design_id}.html exist?").
    # Valid bucket names: square, portrait, story, tiktok, landscape, banner
    supported_export_buckets = Column(
        JSONB, nullable=False, default=list,
        comment='JSON list of bucket names, e.g. ["square","portrait","story","tiktok","landscape","banner"]',
    )

    # Platform IDs that support animated video export for this design.
    # Replaces the hardcoded ANIMATED_EXPORT_CAPABLE frozenset in card_constants.py.
    animated_platforms = Column(
        JSONB, nullable=False, default=list,
        comment='JSON list of platform_ids, e.g. ["instagram_square"]',
    )

    # ── CS-4c driver config ───────────────────────────────────────────────────
    # Bucket-keyed config enabling column_driver.html routing without Level C files.
    # Presence of a bucket key signals driver routing; absence → file-based fallback.
    # Schema: {"portrait": {"skill_slice": 6, "show_dominant_badge": false,
    #          "show_height_weight": false, "show_sponsor": false, "platform_vars": {}}}
    component_config = Column(
        JSONB, nullable=False, default=dict,
        comment='Bucket-keyed driver config; {} = file-based Level C routing for all buckets',
    )

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
        return f"<CardDesign id={self.id!r} label={self.label!r} active={self.is_active}>"

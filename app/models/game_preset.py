"""
Game Preset Model
Defines pre-configured game types with associated skills, weights, and match rules.
"""

from sqlalchemy import Column, Integer, String, Text, Boolean, TIMESTAMP, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base


class GamePreset(Base):
    """
    Game Preset - Pre-configured game type templates

    Examples: GanFootvolley, GanFoottennis, Stole My Goal
    Each preset defines:
    - Skills tested and their weights
    - Match simulation parameters (draw probability, home advantage, etc.)
    - Ranking rules and tiebreakers
    - Performance variation and distribution patterns
    """
    __tablename__ = "game_presets"

    # Primary Key
    id = Column(Integer, primary_key=True, index=True)

    # Identification
    code = Column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="Unique code identifier (e.g., 'gan_footvolley', 'stole_my_goal')"
    )
    name = Column(
        String(100),
        nullable=False,
        comment="Display name (e.g., 'GanFootvolley', 'Stole My Goal')"
    )
    description = Column(
        Text,
        nullable=True,
        comment="Description of the game type and its characteristics"
    )

    # Configuration (JSONB)
    game_config = Column(
        JSONB,
        nullable=False,
        index=True,
        comment=(
            "Complete game configuration including: "
            "format_config (match simulation, ranking rules), "
            "skill_config (skills tested, weights), "
            "simulation_config (variation, distribution), "
            "metadata (category, player count, difficulty)"
        )
    )

    # Status
    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
        comment="Whether this preset is available for selection"
    )
    is_recommended = Column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
        comment="Whether this preset is recommended as default choice"
    )
    is_locked = Column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
        comment="Whether this preset's configuration is locked (cannot be overridden)"
    )

    # Audit Fields
    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="When this preset was created"
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="When this preset was last updated"
    )
    created_by = Column(
        Integer,
        ForeignKey("users.id", name="fk_game_presets_created_by"),
        nullable=True,
        comment="Admin user who created this preset"
    )

    # Relationships
    # P3: game_preset is now accessed via GameConfiguration, not directly from Semester
    # Old relationship: semesters = relationship("Semester", back_populates="game_preset")
    # New relationship path: GameConfiguration.game_preset
    game_configurations = relationship(
        "GameConfiguration",
        back_populates="game_preset"
    )

    def __repr__(self):
        return f"<GamePreset(id={self.id}, code='{self.code}', name='{self.name}', active={self.is_active})>"

    def to_dict(self):
        """Convert preset to dictionary representation"""
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "game_config": self.game_config,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @property
    def skills_tested(self):
        """Extract skills tested from game_config"""
        return self.game_config.get("skill_config", {}).get("skills_tested", [])

    @property
    def skill_weights(self):
        """Extract skill weights from game_config"""
        return self.game_config.get("skill_config", {}).get("skill_weights", {})

    @property
    def game_category(self):
        """Extract game category from metadata"""
        return self.game_config.get("metadata", {}).get("game_category", "general")

    @property
    def difficulty_level(self):
        """Extract difficulty level from metadata"""
        return self.game_config.get("metadata", {}).get("difficulty_level", "intermediate")

    @property
    def recommended_player_count(self):
        """Extract recommended player count range from metadata"""
        return self.game_config.get("metadata", {}).get("recommended_player_count", {})

    # Valid foot context values — kept as a frozenset for O(1) lookup and immutability.
    _VALID_FOOT_CONTEXTS = frozenset({"right", "left", "neutral"})

    @property
    def foot_context(self) -> str:
        """Return the foot-laterality context for this preset.

        Stored in game_config.skill_config.foot_context.
        Valid values: "right" | "left" | "neutral".
        Any missing or invalid stored value silently falls back to "neutral"
        so callers never receive an unexpected value.
        """
        raw = (self.game_config or {}).get("skill_config", {}).get("foot_context", "neutral")
        return raw if raw in self._VALID_FOOT_CONTEXTS else "neutral"

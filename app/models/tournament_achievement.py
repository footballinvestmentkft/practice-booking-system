"""
Tournament Participation & Badge Models

Two separate systems:
1. TournamentParticipation: Tracks skill points and XP rewards (data-focused)
2. TournamentBadge: Visual achievements with icons, titles, descriptions (UI-focused)
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint, Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# ============================================================================
# SKILL/XP TRACKING SYSTEM (Data-focused)
# ============================================================================

class TournamentSkillMapping(Base):
    """
    Maps tournaments to skills they develop.

    Each tournament can develop multiple skills with different weights.
    Example: Speed Test → Agility (1.0), Physical Fitness (0.5)
    """
    __tablename__ = "tournament_skill_mappings"

    id = Column(Integer, primary_key=True, index=True)
    semester_id = Column(Integer, ForeignKey("semesters.id", ondelete="CASCADE"), nullable=False, index=True)
    skill_name = Column(String(100), nullable=False)
    skill_category = Column(String(50), nullable=True)  # 'football_skill', 'Technical', 'Physical', etc.
    weight = Column(Numeric(3, 2), server_default="1.0", nullable=False)  # Numeric weight for skill importance
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    tournament = relationship("Semester", back_populates="skill_mappings")

    __table_args__ = (
        {'extend_existing': True}
    )


class TournamentParticipation(Base):
    """
    Tracks tournament participation with skill points and XP rewards.

    This is the DATA layer - tracks numerical rewards, not visual achievements.
    Records placement, skill points awarded, and XP/credits earned.
    One participation record per player per tournament.
    """
    __tablename__ = "tournament_participations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    semester_id = Column(Integer, ForeignKey("semesters.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True,
                     comment="For TEAM tournaments: which team this member's reward came from.")
    placement = Column(Integer, nullable=True)  # 1, 2, 3, or NULL for participation
    skill_points_awarded = Column(JSONB, nullable=True)  # {"agility": 4.3, "physical_fitness": 2.2}
    skill_rating_delta = Column(JSONB, nullable=True)   # {"passing": 1.2, "dribbling": -0.4} — V3 EMA per-tournament delta
    xp_awarded = Column(Integer, nullable=False, default=0)
    credits_awarded = Column(Integer, nullable=False, default=0)
    achieved_at = Column(DateTime(timezone=True), server_default=func.now())
    # Laterality context: which foot the tournament preset targets.
    # Values: "right" | "left" | "neutral". Populated from GamePreset.foot_context
    # at participation-record creation time. CHECK constraint enforces valid values.
    foot_context = Column(
        String(10),
        nullable=False,
        server_default="neutral",
        comment="Foot-laterality context of the tournament preset: right | left | neutral",
    )

    # Relationships
    user = relationship("User", back_populates="tournament_participations")
    tournament = relationship("Semester", back_populates="participations")
    team = relationship("Team", foreign_keys=[team_id])

    __table_args__ = (
        UniqueConstraint('user_id', 'semester_id', name='uq_user_semester_participation'),
        {'extend_existing': True}
    )


class SkillPointConversionRate(Base):
    """
    Defines XP conversion rates per skill category.

    Example: Technical skills = 10 XP per point, Physical = 8 XP per point
    """
    __tablename__ = "skill_point_conversion_rates"

    id = Column(Integer, primary_key=True, index=True)
    skill_category = Column(String(50), unique=True, nullable=False)
    xp_per_point = Column(Integer, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        {'extend_existing': True}
    )


# ============================================================================
# VISUAL ACHIEVEMENT SYSTEM (UI-focused)
# ============================================================================

class TournamentBadgeType:
    """Tournament badge type constants"""
    # Placement badges
    CHAMPION = "CHAMPION"                    # 1st place - 🥇
    RUNNER_UP = "RUNNER_UP"                  # 2nd place - 🥈
    THIRD_PLACE = "THIRD_PLACE"              # 3rd place - 🥉
    PODIUM_FINISH = "PODIUM_FINISH"          # Top 3 finish - 🏆

    # Participation badges
    TOURNAMENT_PARTICIPANT = "TOURNAMENT_PARTICIPANT"  # Completed tournament - ⚽
    FIRST_TOURNAMENT = "FIRST_TOURNAMENT"              # First ever tournament - 🌟

    # Achievement badges
    UNDEFEATED = "UNDEFEATED"                # Won all rounds - 💪
    COMEBACK_KING = "COMEBACK_KING"          # Improved significantly - 📈
    CONSISTENCY = "CONSISTENCY"              # Consistent performance - 🎯
    RECORD_BREAKER = "RECORD_BREAKER"        # Set new record - ⚡

    # Milestone badges
    TOURNAMENT_VETERAN = "TOURNAMENT_VETERAN"    # 5+ tournaments - 🎖️
    TOURNAMENT_LEGEND = "TOURNAMENT_LEGEND"      # 10+ tournaments - 👑
    TRIPLE_CROWN = "TRIPLE_CROWN"                # 3 consecutive wins - 🔥

    # Specialization badges
    SPEED_DEMON = "SPEED_DEMON"              # Fastest time in speed tournament - 🏃
    ENDURANCE_MASTER = "ENDURANCE_MASTER"    # Longest hold in endurance tournament - 🧘
    MARKSMAN = "MARKSMAN"                    # Highest accuracy in shooting tournament - 🎯


class TournamentBadgeCategory:
    """Tournament badge category constants"""
    PLACEMENT = "PLACEMENT"          # Based on final ranking (1st/2nd/3rd)
    PARTICIPATION = "PARTICIPATION"  # For completing/joining tournaments
    ACHIEVEMENT = "ACHIEVEMENT"      # Special accomplishments during tournament
    MILESTONE = "MILESTONE"          # Long-term tournament participation milestones
    SPECIALIZATION = "SPECIALIZATION"  # Skill/specialization-specific achievements


class TournamentBadgeRarity:
    """Badge rarity levels (affects visual presentation)"""
    COMMON = "COMMON"          # Everyone can earn (e.g., participation)
    UNCOMMON = "UNCOMMON"      # Moderate achievement (e.g., top 50%)
    RARE = "RARE"              # Significant achievement (e.g., podium)
    EPIC = "EPIC"              # Exceptional achievement (e.g., 1st place)
    LEGENDARY = "LEGENDARY"    # Extremely rare (e.g., undefeated, record breaker)


class TournamentBadge(Base):
    """
    Visual tournament achievement badge.

    This is the UI layer - displays in profile as icon + title + description.
    Separate from skill/XP rewards. One player can earn multiple badges per tournament.
    """
    __tablename__ = "tournament_badges"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    semester_id = Column(Integer, ForeignKey("semesters.id", ondelete="CASCADE"), nullable=False, index=True)

    # Badge identity
    badge_type = Column(String(50), nullable=False, index=True)  # TournamentBadgeType constant
    badge_category = Column(String(50), nullable=False, index=True)  # TournamentBadgeCategory constant

    # Visual presentation
    title = Column(String(200), nullable=False)           # e.g., "Tournament Champion"
    description = Column(Text, nullable=True)             # e.g., "Claimed victory in Speed Test 2026"
    icon = Column(String(10), nullable=True)              # Emoji: 🥇, 🥈, 🥉, 🏆, ⚽, etc.
    rarity = Column(String(20), server_default="COMMON", nullable=False)  # TournamentBadgeRarity constant

    # Additional context
    badge_metadata = Column(JSONB, nullable=True)  # {"placement": 1, "total_participants": 12, "time": "10.5s"}
    earned_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="tournament_badges")
    tournament = relationship("Semester", back_populates="badges")

    __table_args__ = (
        {'extend_existing': True}
    )

    def to_dict(self):
        """Convert to dictionary for API responses"""
        # Include tournament/semester metadata for frontend display
        semester_name = None
        tournament_status = None
        tournament_start_date = None

        if self.tournament:
            semester_name = self.tournament.name
            tournament_status = self.tournament.tournament_status
            tournament_start_date = self.tournament.start_date.isoformat() if self.tournament.start_date else None

        return {
            "id": self.id,
            "user_id": self.user_id,
            "semester_id": self.semester_id,
            "semester_name": semester_name,  # NEW: Tournament name for display
            "tournament_status": tournament_status,  # NEW: Tournament status for UI logic
            "tournament_start_date": tournament_start_date,  # NEW: For sorting
            "badge_type": self.badge_type,
            "badge_category": self.badge_category,
            "title": self.title,
            "description": self.description,
            "icon": self.icon,
            "rarity": self.rarity,
            "badge_metadata": self.badge_metadata,  # FIX: was "metadata", must match frontend expectation
            "earned_at": self.earned_at.isoformat() if self.earned_at else None
        }

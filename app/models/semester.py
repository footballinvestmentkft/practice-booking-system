from sqlalchemy import Column, Integer, String, Date, Boolean, DateTime, ForeignKey, Table, Enum, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship, validates
from datetime import datetime, timezone
import enum

from ..database import Base


class SemesterStatus(str, enum.Enum):
    """Semester lifecycle phases"""
    DRAFT = "DRAFT"  # Admin created, no instructor, no sessions
    SEEKING_INSTRUCTOR = "SEEKING_INSTRUCTOR"  # Admin looking for instructor
    INSTRUCTOR_ASSIGNED = "INSTRUCTOR_ASSIGNED"  # Has instructor, no sessions yet
    READY_FOR_ENROLLMENT = "READY_FOR_ENROLLMENT"  # Has instructor + sessions, students can enroll
    ONGOING = "ONGOING"  # Past enrollment deadline, classes in progress
    COMPLETED = "COMPLETED"  # All sessions finished
    CANCELLED = "CANCELLED"  # Admin cancelled


class SemesterCategory(str, enum.Enum):
    """Top-level category for a semester, drives access control and reporting."""
    ACADEMY_SEASON  = "ACADEMY_SEASON"   # Jul-Jun multi-month program
    MINI_SEASON     = "MINI_SEASON"      # Short academy season (4-8 weeks)
    TOURNAMENT      = "TOURNAMENT"       # Competitive tournament
    CAMP            = "CAMP"             # Short-term intensive camp (e.g. summer/winter camp)
    PROMOTION_EVENT = "PROMOTION_EVENT"  # Scouting / showcase event (uses tournament pipeline, separate UI)


# Many-to-many association table for additional instructors
semester_instructors = Table(
    'semester_instructors',
    Base.metadata,
    Column('semester_id', Integer, ForeignKey('semesters.id', ondelete='CASCADE'), primary_key=True),
    Column('instructor_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
)


class Semester(Base):
    __tablename__ = "semesters"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False, index=True)  # "2024/1"
    name = Column(String, nullable=False)  # "2024/25 őszi félév"
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    # Lifecycle status (new, preferred)
    status = Column(Enum(SemesterStatus, name='semester_status'), nullable=False, default=SemesterStatus.DRAFT, index=True,
                   comment="Current lifecycle phase of the semester")

    # Tournament-specific status (for tournament lifecycle)
    tournament_status = Column(String(50), nullable=True, index=True,
                              comment="Tournament-specific status: DRAFT, SEEKING_INSTRUCTOR, READY_FOR_ENROLLMENT, etc.")

    # Tournament winner count (for INDIVIDUAL_RANKING tournaments)
    winner_count = Column(Integer, nullable=True,
                         comment="Number of winners for INDIVIDUAL_RANKING tournaments (E2E testing)")

    # 💰 CREDIT SYSTEM: Enrollment cost for this semester
    enrollment_cost = Column(Integer, nullable=False, default=500,
                            comment="Credit cost to enroll in this semester (admin adjustable)")

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # ⏰ CHECK-IN SCHEDULING
    checkin_opens_at = Column(
        DateTime(timezone=True), nullable=True,
        comment="UTC datetime when check-in auto-opens (NULL = manual only). "
                "APScheduler transitions ENROLLMENT_CLOSED → CHECK_IN_OPEN when this <= NOW()."
    )

    # 📦 CATEGORY & HIERARCHY (M-01 / M-02 — 2026-03-15)
    semester_category = Column(
        Enum(SemesterCategory, name='semester_category_type'),
        nullable=True,
        index=True,
        comment="Program category: ACADEMY_SEASON | MINI_SEASON | TOURNAMENT | CAMP | PROMOTION_EVENT"
    )
    parent_semester_id = Column(
        Integer,
        ForeignKey('semesters.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
        comment="Parent semester for nested programs (e.g. training inside a CAMP). "
                "Access control: only enrollees of parent can enroll in children."
    )

    # 🥋 Master Instructor (Grandmaster who approves enrollments)
    master_instructor_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
                                  comment="Master instructor who approves enrollment requests for this semester")

    # 🎯 SPECIALIZATION & AGE GROUP FIELDS (for semester filtering)
    specialization_type = Column(String(50), nullable=True, index=True,
                                 comment="Specialization type (SEASON types: LFA_PLAYER_PRE/YOUTH/AMATEUR/PRO, GANCUJU_PLAYER, LFA_COACH, INTERNSHIP, OR user license for tournaments: LFA_FOOTBALL_PLAYER)")
    age_group = Column(String(20), nullable=True, index=True,
                      comment="Age group (PRE, YOUTH, AMATEUR, PRO)")
    theme = Column(String(200), nullable=True,
                  comment="Marketing theme (e.g., 'New Year Challenge', 'Q1', 'Fall')")
    focus_description = Column(String(500), nullable=True,
                              comment="Focus description (e.g., 'Újévi fogadalmak, friss kezdés')")

    # 📍 LOCATION FIELDS (for semester-level location)
    # NEW: Use campus_id FK for most specific location
    campus_id = Column(Integer, ForeignKey('campuses.id', ondelete='SET NULL'), nullable=True, index=True,
                      comment="FK to campuses table (most specific location - preferred)")

    # Use location_id FK instead of denormalized city/venue/address fields
    location_id = Column(Integer, ForeignKey('locations.id', ondelete='SET NULL'), nullable=True, index=True,
                        comment="FK to locations table (less specific than campus_id, preferred over legacy location_city/venue/address)")

    # 🏢 ORGANIZER FIELDS (P2-A: dual nullable FK — at most one may be set)
    organizer_club_id = Column(
        Integer, ForeignKey('clubs.id', ondelete='SET NULL'), nullable=True, index=True,
        comment="FK to clubs — set when a club organizes this promotion event"
    )
    organizer_sponsor_id = Column(
        Integer, ForeignKey('sponsors.id', ondelete='SET NULL'), nullable=True, index=True,
        comment="FK to sponsors — set when a sponsor organizes this promotion event"
    )
    organizer_campaign_id = Column(
        Integer, ForeignKey('sponsor_campaigns.id', ondelete='SET NULL'), nullable=True, index=True,
        comment="FK to sponsor_campaigns — the campaign whose audience feeds this promotion event (sponsor events only)"
    )
    # DB-level CHECKs: chk_semester_single_organizer (at most one of club/sponsor)
    #                  chk_campaign_requires_sponsor (campaign requires sponsor)

    # 🏆 TOURNAMENT CONFIGURATION FIELDS (P2 refactoring)
    # ⚠️ DEPRECATED in P2: All tournament configuration moved to separate table (tournament_configurations)
    # Use tournament_config_obj relationship instead
    # Configuration properties (@property) provide backward compatibility
    #
    # Moved fields:
    # - tournament_type_id, participant_type, is_multi_day, max_players
    # - match_duration_minutes, break_duration_minutes, parallel_fields
    # - scoring_type, measurement_unit, ranking_direction, number_of_rounds
    # - assignment_type, sessions_generated, sessions_generated_at, enrollment_snapshot

    # 🎁 REWARD POLICY FIELDS (tournament reward system)
    # ⚠️ DEPRECATED in P1: reward_config moved to separate table (tournament_reward_configs)
    # Use reward_config_obj relationship instead
    # reward_config @property provides backward compatibility

    # 🎮 GAME CONFIGURATION FIELDS (tournament simulation rules)
    # ⚠️ DEPRECATED in P3: All game configuration moved to separate table (game_configurations)
    # Use game_config_obj relationship instead
    # Configuration properties (@property) provide backward compatibility
    #
    # Moved fields:
    # - game_preset_id, game_config, game_config_overrides

    # Relationships
    campus = relationship("Campus", foreign_keys=[campus_id],
                         backref="semesters",
                         doc="Campus where this semester takes place (most specific)")
    location = relationship("Location", foreign_keys=[location_id],
                           backref="semesters",
                           doc="Location where this semester takes place")
    master_instructor = relationship("User", foreign_keys=[master_instructor_id],
                                    backref="mastered_semesters")
    # Hierarchy (M-02)
    parent_semester = relationship(
        "Semester",
        foreign_keys=[parent_semester_id],
        remote_side="Semester.id",
        backref="child_semesters",
        doc="Parent program (e.g. the CAMP this training sub-season belongs to)"
    )
    assistant_instructors = relationship("User", secondary=semester_instructors,
                                        backref="assisted_semesters")
    groups = relationship("Group", back_populates="semester")
    sessions = relationship("Session", back_populates="semester")
    projects = relationship("Project", back_populates="semester")
    enrollments = relationship("SemesterEnrollment", back_populates="semester", cascade="all, delete-orphan")

    # 🏆 Tournament participation & badge relationships
    skill_mappings = relationship("TournamentSkillMapping", back_populates="tournament", cascade="all, delete-orphan")
    participations = relationship("TournamentParticipation", back_populates="tournament", cascade="all, delete-orphan")
    badges = relationship("TournamentBadge", back_populates="tournament", cascade="all, delete-orphan")

    # 🏆 Tournament configuration (P2: separate table)
    tournament_config_obj = relationship(
        "TournamentConfiguration",
        uselist=False,
        back_populates="tournament",
        cascade="all, delete-orphan",
        doc="Tournament configuration (1:1 relationship)"
    )

    # 🎁 Reward configuration (P1: separate table)
    reward_config_obj = relationship(
        "TournamentRewardConfig",
        uselist=False,
        back_populates="tournament",
        cascade="all, delete-orphan",
        doc="Reward configuration for this tournament (1:1 relationship)"
    )

    # 🎮 Game configuration (P3: separate table)
    game_config_obj = relationship(
        "GameConfiguration",
        uselist=False,
        back_populates="tournament",
        cascade="all, delete-orphan",
        doc="Game configuration for this tournament (1:1 relationship)"
    )

    # 📅 Schedule configuration (Phase 2: MINI_SEASON + ACADEMY_SEASON session generation)
    schedule_config_obj = relationship(
        "SemesterScheduleConfig",
        uselist=False,
        back_populates="semester",
        cascade="all, delete-orphan",
        doc="Weekly schedule config for session generation (MINI_SEASON / ACADEMY_SEASON)"
    )

    # 🏢 Organizer relationships (P2-A + P4)
    organizer_club = relationship(
        "Club",
        foreign_keys=[organizer_club_id],
        back_populates="organized_promotion_events",
    )
    organizer_sponsor = relationship(
        "Sponsor",
        foreign_keys=[organizer_sponsor_id],
        back_populates="promotion_events",
    )
    organizer_campaign = relationship(
        "SponsorCampaign",
        foreign_keys=[organizer_campaign_id],
        back_populates="semesters",
    )

    @validates("organizer_club_id", "organizer_sponsor_id")
    def _guard_single_organizer_fk(self, key: str, value):
        if value is None:
            return value
        conflict = "organizer_sponsor_id" if key == "organizer_club_id" else "organizer_club_id"
        if getattr(self, conflict) is not None:
            raise ValueError(f"Cannot set {key}: {conflict} is already set.")
        return value

    @validates("organizer_club", "organizer_sponsor")
    def _guard_single_organizer_rel(self, key: str, value):
        if value is None:
            return value
        conflict_fk = "organizer_sponsor_id" if key == "organizer_club" else "organizer_club_id"
        if getattr(self, conflict_fk) is not None:
            raise ValueError(f"Cannot set {key}: {conflict_fk} is already set.")
        return value

    @property
    def format(self) -> str:
        """
        Derive tournament format from tournament_type.format (single source of truth).

        🔄 P2 Refactoring: tournament_type_id moved to TournamentConfiguration table.
        🔄 P3 Refactoring: game_preset_id moved to GameConfiguration table.

        Priority:
        1. tournament_type.format (via tournament_config_obj.tournament_type)
        2. game_preset's format_config (via game_config_obj.game_preset)
        3. Default: INDIVIDUAL_RANKING

        Returns:
            str: Either "HEAD_TO_HEAD" or "INDIVIDUAL_RANKING"
        """
        # Priority 1: tournament_type.format (via P2 config relationship)
        if self.tournament_config_obj:
            if self.tournament_config_obj.tournament_type_id and self.tournament_config_obj.tournament_type:
                return self.tournament_config_obj.tournament_type.format
            # If scoring_type is set and is not HEAD_TO_HEAD → INDIVIDUAL_RANKING
            # (H2H always stores scoring_type="HEAD_TO_HEAD"; IR uses TIME_BASED, SCORE_BASED, etc.)
            if (self.tournament_config_obj.scoring_type
                    and self.tournament_config_obj.scoring_type != "HEAD_TO_HEAD"):
                return "INDIVIDUAL_RANKING"

        # Priority 2: game_preset's format_config (via P3 game config relationship)
        if self.game_config_obj and self.game_config_obj.game_preset_id and self.game_config_obj.game_preset:
            format_config = self.game_config_obj.game_preset.game_config.get('format_config', {})
            if format_config:
                # format_config is a dict with format as key: {"HEAD_TO_HEAD": {...}} or {"INDIVIDUAL_RANKING": {...}}
                return list(format_config.keys())[0]

        # Priority 3: Default
        return "INDIVIDUAL_RANKING"

    def validate_tournament_format_logic(self):
        """
        Validate tournament format and type consistency:
        - INDIVIDUAL_RANKING: tournament_type_id MUST be NULL (no structure needed)
        - HEAD_TO_HEAD: tournament_type_id MUST be set (Swiss, League, Knockout, etc.)
        """
        if self.format == "INDIVIDUAL_RANKING":
            if self.tournament_type_id is not None:
                raise ValueError(
                    "INDIVIDUAL_RANKING tournaments cannot have a tournament_type. "
                    "INDIVIDUAL_RANKING is a simple competition format where all players compete "
                    "and are ranked by their results (time, score, distance, or placement). "
                    "Set tournament_type_id to NULL."
                )
        elif self.format == "HEAD_TO_HEAD":
            if self.tournament_type_id is None:
                raise ValueError(
                    "HEAD_TO_HEAD tournaments MUST have a tournament_type (Swiss, Round Robin, Knockout, etc.). "
                    "Tournament type defines how 1v1 matches are structured and scheduled."
                )
        else:
            raise ValueError(f"Invalid format: {self.format}. Must be 'INDIVIDUAL_RANKING' or 'HEAD_TO_HEAD'.")

    # ========================================================================
    # 🏆 P2 REFACTORING: BACKWARD COMPATIBILITY PROPERTIES (Tournament Configuration)
    # ========================================================================

    @property
    def tournament_type_id(self) -> int:
        """Backward compatible property for tournament_type_id (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.tournament_type_id
        return None

    @property
    def participant_type(self) -> str:
        """Backward compatible property for participant_type (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.participant_type
        return "INDIVIDUAL"

    @property
    def is_multi_day(self) -> bool:
        """Backward compatible property for is_multi_day (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.is_multi_day
        return False

    @property
    def max_players(self) -> int:
        """Backward compatible property for max_players (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.max_players
        return None

    @property
    def match_duration_minutes(self) -> int:
        """Backward compatible property for match_duration_minutes (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.match_duration_minutes
        return None

    @property
    def break_duration_minutes(self) -> int:
        """Backward compatible property for break_duration_minutes (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.break_duration_minutes
        return None

    @property
    def parallel_fields(self) -> int:
        """Backward compatible property for parallel_fields (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.parallel_fields
        return 1

    @property
    def scoring_type(self) -> str:
        """Backward compatible property for scoring_type (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.scoring_type
        return "PLACEMENT"

    @property
    def measurement_unit(self) -> str:
        """Backward compatible property for measurement_unit (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.measurement_unit
        return None

    @property
    def ranking_direction(self) -> str:
        """Backward compatible property for ranking_direction (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.ranking_direction
        return None

    @property
    def number_of_rounds(self) -> int:
        """Backward compatible property for number_of_rounds (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.number_of_rounds
        return 1

    @property
    def assignment_type(self) -> str:
        """Backward compatible property for assignment_type (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.assignment_type
        return None

    @property
    def sessions_generated(self) -> bool:
        """Backward compatible property for sessions_generated (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.sessions_generated
        return False

    @property
    def sessions_generated_at(self):
        """Backward compatible property for sessions_generated_at (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.sessions_generated_at
        return None

    @property
    def enrollment_snapshot(self) -> dict:
        """Backward compatible property for enrollment_snapshot (P2)"""
        if self.tournament_config_obj:
            return self.tournament_config_obj.enrollment_snapshot or {}
        return {}

    # ========================================================================
    # 🎁 P1 REFACTORING: BACKWARD COMPATIBILITY PROPERTIES (Reward Configuration)
    # ========================================================================

    @property
    def reward_config(self) -> dict:
        """
        Backward compatible property for reward_config.

        🔄 P1 Refactoring: reward_config moved to separate table.
        This property provides transparent access to the new structure.

        Returns:
            Dict: Reward configuration (TournamentRewardConfig schema)
        """
        if self.reward_config_obj:
            return self.reward_config_obj.reward_config or {}
        return {}

    @property
    def reward_policy_name(self) -> str:
        """
        Backward compatible property for reward_policy_name.

        Returns:
            str: Reward policy name (default: "default")
        """
        if self.reward_config_obj:
            return self.reward_config_obj.reward_policy_name
        return "default"

    @property
    def reward_policy_snapshot(self) -> dict:
        """
        Backward compatible property for reward_policy_snapshot.

        Returns:
            Dict: Reward policy snapshot (or empty dict)
        """
        if self.reward_config_obj:
            return self.reward_config_obj.reward_policy_snapshot or {}
        return {}

    # ========================================================================
    # 🎮 P3 REFACTORING: BACKWARD COMPATIBILITY PROPERTIES (Game Configuration)
    # ========================================================================

    @property
    def game_preset_id(self) -> int:
        """Backward compatible property for game_preset_id (P3)"""
        if self.game_config_obj:
            return self.game_config_obj.game_preset_id
        return None

    @property
    def game_config(self) -> dict:
        """
        Backward compatible property for game_config (P3).

        Returns:
            Dict: Merged game configuration (or empty dict)
        """
        if self.game_config_obj:
            return self.game_config_obj.game_config or {}
        return {}

    @property
    def game_config_overrides(self) -> dict:
        """
        Backward compatible property for game_config_overrides (P3).

        Returns:
            Dict: Game configuration overrides (or empty dict)
        """
        if self.game_config_obj:
            return self.game_config_obj.game_config_overrides or {}
        return {}

    @property
    def game_preset(self):
        """
        Backward compatible property for game_preset relationship (P3).

        Returns:
            GamePreset: Game preset object (or None)
        """
        if self.game_config_obj:
            return self.game_config_obj.game_preset
        return None
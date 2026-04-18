from sqlalchemy import Column, Integer, SmallInteger, String, Text, DateTime, ForeignKey, Enum, Boolean, ARRAY, case
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone
import enum

from ..database import Base
from .specialization import SpecializationType
from .tournament_enums import TournamentPhase  # Phase 2.1: Import canonical enum


class SessionType(enum.Enum):
    """Professional session type classification for edtech/sporttech platforms"""
    on_site = "on_site"    # Physical presence required at venue
    virtual = "virtual"    # Remote attendance via online platform
    hybrid = "hybrid"      # Both on-site and virtual attendance options available


class EventCategory(str, enum.Enum):
    """What kind of event this session is — replaces boolean is_tournament_game (M-03)."""
    TRAINING = "TRAINING"  # Practice, drill, skills session
    MATCH = "MATCH"        # Competitive game / tournament match


class SessionParticipantType(str, enum.Enum):
    """How participants attend this session (M-04)."""
    INDIVIDUAL = "INDIVIDUAL"  # Solo athlete (default for training)
    GROUP = "GROUP"            # Open group class (fixed roster per session)
    TEAM = "TEAM"              # Team vs team (requires Team records)


class DeliveryMode(str, enum.Enum):
    """Physical delivery mode — successor to SessionType (M-05)."""
    ON_SITE = "ON_SITE"    # Physical presence at venue
    VIRTUAL = "VIRTUAL"    # Remote / online
    HYBRID = "HYBRID"      # Both on-site and virtual attendance options available


class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    date_start = Column(DateTime, nullable=False, index=True,
                        comment="Indexed for schedule range queries (upcoming sessions, calendar views)")
    date_end = Column(DateTime, nullable=False)
    session_type = Column(Enum(SessionType), default=SessionType.on_site, nullable=False)
    capacity = Column(Integer, default=20)
    location = Column(String, nullable=True)  # for on-site sessions
    meeting_link = Column(String, nullable=True)  # for virtual sessions
    sport_type = Column(String, default='General')  # Enhanced field for UI
    level = Column(String, default='All Levels')  # Enhanced field for UI
    instructor_name = Column(String, nullable=True)  # Enhanced field for UI
    semester_id = Column(Integer, ForeignKey("semesters.id"), nullable=False, index=True,
                         comment="Indexed — all sessions in a semester is the most common query pattern")
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)  # FIXED: Made nullable to allow sessions without groups
    instructor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    campus_id = Column(Integer, ForeignKey("campuses.id"), nullable=True, comment="Campus/venue for multi-campus tournaments")
    pitch_id = Column(Integer, ForeignKey("pitches.id"), nullable=True, index=True, comment="Specific pitch/field within the campus")

    # 🎓 NEW: Specialization fields
    target_specialization = Column(
        Enum(SpecializationType),
        nullable=True,
        comment="Target specialization for this session (null = all specializations)"
    )
    
    mixed_specialization = Column(
        Boolean,
        default=False,
        comment="Whether this session is open to all specializations"
    )

    # ⏱️ Session Timer/Tracker fields (On-Site & Hybrid only)
    actual_start_time = Column(
        DateTime,
        nullable=True,
        comment="Actual start time when instructor starts the session"
    )
    actual_end_time = Column(
        DateTime,
        nullable=True,
        comment="Actual end time when instructor stops the session"
    )
    session_status = Column(
        String(20),
        default="scheduled",
        comment="Session status: scheduled, in_progress, completed"
    )
    postponed_reason = Column(
        String(500),
        nullable=True,
        comment="Reason for postponing this match (set by admin; NULL = not postponed)",
    )

    # 🔓 Quiz Access Control (HYBRID sessions only)
    quiz_unlocked = Column(
        Boolean,
        default=False,
        comment="Whether the quiz is unlocked for students (HYBRID sessions)"
    )

    # 🎯 XP/Gamification
    base_xp = Column(
        Integer,
        default=50,
        comment="Base XP awarded for completing this session (HYBRID=100, ON-SITE=75, VIRTUAL=50)"
    )

    # 💳 Credit System
    credit_cost = Column(
        Integer,
        default=1,
        nullable=False,
        comment="Number of credits required to book this session (default: 1, workshops may cost more)"
    )

    # 🏆 DEPRECATED — is_tournament_game (backward-compat bridge, scheduled for removal)
    # ────────────────────────────────────────────────────────────────────────────
    # The DB column was dropped in M-10 (2026-03-15). This hybrid_property
    # provides a transparent compatibility bridge so existing callers continue
    # to work without changes.
    #
    # Replacement: use `event_category` / `EventCategory` directly.
    #   Read:   session.event_category == EventCategory.MATCH
    #   Filter: SessionModel.event_category == EventCategory.MATCH
    #   Write:  session.event_category = EventCategory.MATCH
    #
    # TODO: remove after all call-sites have been migrated to event_category.
    @hybrid_property
    def is_tournament_game(self) -> bool:
        """DEPRECATED — use event_category == EventCategory.MATCH instead."""
        return self.event_category == EventCategory.MATCH

    @is_tournament_game.setter
    def is_tournament_game(self, value: bool) -> None:
        """DEPRECATED — use event_category = EventCategory.MATCH instead."""
        self.event_category = EventCategory.MATCH if value else EventCategory.TRAINING

    @is_tournament_game.expression
    def is_tournament_game(cls):  # noqa: N805
        """DEPRECATED — use SessionModel.event_category == EventCategory.MATCH instead."""
        return cls.event_category == EventCategory.MATCH

    game_type = Column(
        String(100),
        nullable=True,
        comment="Type/name of tournament game (user-defined, e.g., 'Skills Challenge')"
    )

    game_results = Column(
        Text,
        nullable=True,
        comment="JSON array of game results: [{user_id: 1, score: 95, rank: 1}, ...]"
    )

    # 🎯 AUTO-GENERATED TOURNAMENT SESSION METADATA
    auto_generated = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True if this session was auto-generated from tournament type config"
    )

    # Phase 2.1: Use PostgreSQL enum type for tournament_phase
    tournament_phase = Column(
        Enum(TournamentPhase, name='tournament_phase_enum', native_enum=True, create_constraint=True, validate_strings=True),
        nullable=True,
        comment="Tournament phase: canonical TournamentPhase enum values (GROUP_STAGE, KNOCKOUT, etc.)"
    )

    tournament_round = Column(
        Integer,
        nullable=True,
        comment="Round number within the tournament (1, 2, 3, ...)"
    )

    leg_number = Column(
        SmallInteger,
        nullable=True,
        comment="Leg number within a multi-leg round robin (1-N). NULL for knockout/swiss/INDIVIDUAL_RANKING."
    )

    tournament_match_number = Column(
        Integer,
        nullable=True,
        comment="Match number within the round (1, 2, 3, ...)"
    )

    # 🎯 MULTI-PLAYER RANKING METADATA (Phase 1 - Unified Ranking System)
    ranking_mode = Column(
        String(50),
        nullable=True,
        comment="Ranking mode: ALL_PARTICIPANTS, GROUP_ISOLATED, TIERED, QUALIFIED_ONLY, PERFORMANCE_POD"
    )

    group_identifier = Column(
        String(10),
        nullable=True,
        comment="Group identifier for group stage sessions (A, B, C, D)"
    )

    round_number = Column(
        Integer,
        nullable=True,
        comment="Round number within the group/phase (1, 2, 3, ...)"
    )

    expected_participants = Column(
        Integer,
        nullable=True,
        comment="Expected number of participants for this session (used for validation)"
    )

    participant_filter = Column(
        String(50),
        nullable=True,
        comment="Participant filter logic: group_membership, top_group_qualifiers, dynamic_swiss_pairing"
    )

    pod_tier = Column(
        Integer,
        nullable=True,
        comment="Performance tier for Swiss System pods (1=top performers, 2=middle, etc.)"
    )

    # 🏅 MATCH STRUCTURE METADATA (Phase 2 - Performance/Results Layer)
    match_format = Column(
        String(50),
        nullable=True,
        comment="Match format: INDIVIDUAL_RANKING, HEAD_TO_HEAD, TEAM_MATCH, TIME_BASED, SKILL_RATING"
    )

    scoring_type = Column(
        String(50),
        nullable=True,
        comment="Scoring type: PLACEMENT, WIN_LOSS, SCORE_BASED, TIME_BASED, SKILL_RATING"
    )

    structure_config = Column(
        JSONB,
        nullable=True,
        comment="Match structure configuration (pairings, teams, performance criteria, etc.)"
    )

    # 🔄 ROUNDS DATA: Multi-round results storage (INDIVIDUAL_RANKING tournaments)
    rounds_data = Column(
        JSONB,
        nullable=False,
        default={},
        server_default='{}',
        comment="Round-by-round results for INDIVIDUAL_RANKING tournaments. "
                "Structure: {'total_rounds': 3, 'completed_rounds': 1, 'round_results': {'1': {'user_123': '12.5s', 'user_456': '13.2s'}}}"
    )

    # ✅ MATCH PARTICIPANTS: Explicit participant list (NOT runtime filtering!)
    participant_user_ids = Column(
        ARRAY(Integer),
        nullable=True,
        comment="Explicit list of user_ids participating in THIS MATCH (not tournament-wide). "
                "This fixes the architectural issue where participants were determined at runtime."
    )

    # ✅ TEAM PARTICIPANTS: For TEAM tournaments — list of team_ids in this session
    participant_team_ids = Column(
        ARRAY(Integer),
        nullable=True,
        comment="For TEAM tournaments: list of team_ids in this session. "
                "Mutually exclusive with participant_user_ids."
    )

    # ─── NEW SEMANTIC DIMENSIONS (M-03 to M-06, 2026-03-15) ───────────────────

    # M-03: Replaces is_tournament_game boolean with a proper categorical discriminator
    event_category = Column(
        Enum(EventCategory, name='event_category_type'),
        nullable=True,
        index=True,
        comment="Event category: TRAINING | MATCH. Supersedes is_tournament_game boolean."
    )

    # M-04: How participants are organised for this session
    session_participant_type = Column(
        Enum(SessionParticipantType, name='session_participant_type'),
        nullable=True,
        comment="Participant organisation: INDIVIDUAL | GROUP | TEAM"
    )

    # M-05: Delivery mode — mirrors session_type but uses uppercase values.
    #        Both coexist until session_type is dropped in a later migration.
    delivery_mode = Column(
        Enum(DeliveryMode, name='delivery_mode_type'),
        nullable=True,
        comment="Physical delivery: ON_SITE | VIRTUAL | HYBRID. Successor to session_type."
    )

    # M-06: Per-session reward configuration (versioned JSONB)
    #        Schema: {"v": 1, "base_xp": 50, "skill_areas": [], "multipliers": {}}
    session_reward_config = Column(
        JSONB,
        nullable=True,
        comment="Per-session reward config (versioned JSONB). "
                "Schema v1: {\"v\":1, \"base_xp\":50, \"skill_areas\":[], \"multipliers\":{}}"
    )

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    semester = relationship("Semester", back_populates="sessions")
    group = relationship("Group", back_populates="sessions")
    instructor = relationship("User", back_populates="taught_sessions")
    pitch = relationship("Pitch", back_populates="sessions")
    bookings = relationship("Booking", back_populates="session")
    attendances = relationship("Attendance", back_populates="session")
    feedbacks = relationship("Feedback", back_populates="session")
    notifications = relationship("Notification", back_populates="related_session")
    project_sessions = relationship("ProjectSession", back_populates="session")

    # Performance review relationships (On-Site sessions only)
    student_reviews = relationship("StudentPerformanceReview", back_populates="session")
    instructor_reviews = relationship("InstructorSessionReview", back_populates="session")

    @property
    def related_projects(self):
        return [ps.project for ps in self.project_sessions]
    
    @property
    def is_project_session(self):
        return len(self.project_sessions) > 0
    
    # 🎓 NEW: Specialization helper properties
    @property
    def specialization_info(self) -> str:
        """Get user-friendly specialization information (HYBRID: loads from JSON)"""
        if self.mixed_specialization:
            return "Vegyes (Player + Coach)"
        elif self.target_specialization:
            from app.services.specialization_config_loader import SpecializationConfigLoader
            loader = SpecializationConfigLoader()
            try:
                display_info = loader.get_display_info(self.target_specialization)
                return display_info.get('name', str(self.target_specialization.value))
            except Exception:
                return str(self.target_specialization.value)
        return "Minden szakirány"

    @property
    def specialization_badge(self) -> str:
        """Get specialization badge/icon (HYBRID: loads from JSON)"""
        if self.mixed_specialization:
            return "⚽👨‍🏫"
        elif self.target_specialization:
            from app.services.specialization_config_loader import SpecializationConfigLoader
            loader = SpecializationConfigLoader()
            try:
                display_info = loader.get_display_info(self.target_specialization)
                return display_info.get('icon', '🎯')
            except Exception:
                return "🎯"
        return "🎯"
    
    @property
    def is_accessible_to_all(self) -> bool:
        """Check if session is accessible to all specializations"""
        return self.mixed_specialization or self.target_specialization is None

    @property
    def type_display(self) -> str:
        """Get user-friendly session type display name"""
        type_map = {
            SessionType.on_site: "On-Site",
            SessionType.virtual: "Virtual",
            SessionType.hybrid: "Hybrid"
        }
        return type_map.get(self.session_type, "On-Site")

    @property
    def type_icon(self) -> str:
        """Get session type icon"""
        icon_map = {
            SessionType.on_site: "🏟️",
            SessionType.virtual: "💻",
            SessionType.hybrid: "🔄"
        }
        return icon_map.get(self.session_type, "🏟️")

    @property
    def type_badge_color(self) -> str:
        """Get session type badge color for UI"""
        color_map = {
            SessionType.on_site: "#3498db",  # Blue
            SessionType.virtual: "#9b59b6",  # Purple
            SessionType.hybrid: "#e67e22"    # Orange
        }
        return color_map.get(self.session_type, "#3498db")
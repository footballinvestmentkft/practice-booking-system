"""
Tournament Configuration Model

Separate table for tournament configuration (P2 refactoring).
Extracted from Semester model to achieve clean separation of concerns.

Architecture:
- Tournament Information: Semester (location, dates, theme, status)
- Tournament Configuration: TournamentConfiguration (THIS TABLE - type, schedule, scoring)
- Game Configuration: game_config, game_preset_id (skills, weights, match rules)
- Reward Configuration: TournamentRewardConfig (badges, XP, credits)
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from ..database import Base


class TournamentConfiguration(Base):
    """
    Configuration for tournament (separate entity from Semester).

    🏆 Manages tournament-specific settings:
    - Tournament type & format (HEAD_TO_HEAD vs INDIVIDUAL_RANKING)
    - Participant configuration (max players, participant type)
    - Schedule configuration (match duration, breaks, parallel fields)
    - Scoring configuration (scoring type, measurement unit, ranking direction)
    - Session generation tracking

    ✅ Benefits of separation:
    - Clarity: Configuration isolated from tournament information
    - Auditability: Track configuration changes over time
    - Flexibility: Configuration can be changed without affecting tournament info
    - Reusability: Future - share configurations across tournaments

    Schema follows the configuration layer design from TOURNAMENT_ARCHITECTURE_AUDIT.md
    """
    __tablename__ = "tournament_configurations"

    id = Column(Integer, primary_key=True, index=True)

    # FK to semester (tournament)
    semester_id = Column(
        Integer,
        ForeignKey('semesters.id', ondelete='CASCADE'),
        unique=True,
        nullable=False,
        index=True,
        comment="Tournament this configuration belongs to (1:1 relationship)"
    )

    # Tournament Type & Format
    tournament_type_id = Column(
        Integer,
        ForeignKey('tournament_types.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
        comment="FK to tournament_types table (defines format: HEAD_TO_HEAD or INDIVIDUAL_RANKING)"
    )

    # Participant Configuration
    participant_type = Column(
        String(50),
        nullable=False,
        default="INDIVIDUAL",
        comment="Participant type: INDIVIDUAL, TEAM, MIXED"
    )

    is_multi_day = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True if tournament spans multiple days"
    )

    max_players = Column(
        Integer,
        nullable=True,
        comment="Maximum tournament participants (explicit capacity, independent of session capacity sum)"
    )

    # Schedule Configuration
    match_duration_minutes = Column(
        Integer,
        nullable=True,
        comment="Duration of each match in minutes (overrides tournament_type default)"
    )

    break_duration_minutes = Column(
        Integer,
        nullable=True,
        comment="Break time between matches in minutes (overrides tournament_type default)"
    )

    parallel_fields = Column(
        Integer,
        nullable=False,
        default=1,
        comment="Number of parallel fields/pitches available (1-4) for simultaneous matches"
    )

    # Scoring Configuration (INDIVIDUAL_RANKING only)
    scoring_type = Column(
        String(50),
        nullable=False,
        default="PLACEMENT",
        comment="Scoring type for INDIVIDUAL_RANKING: TIME_BASED, DISTANCE_BASED, SCORE_BASED, PLACEMENT. Ignored for HEAD_TO_HEAD."
    )

    measurement_unit = Column(
        String(50),
        nullable=True,
        comment="Unit of measurement for INDIVIDUAL_RANKING results: seconds/minutes (TIME_BASED), meters/centimeters (DISTANCE_BASED), points/repetitions (SCORE_BASED). NULL for PLACEMENT or HEAD_TO_HEAD."
    )

    ranking_direction = Column(
        String(10),
        nullable=True,
        comment="Ranking direction for INDIVIDUAL_RANKING: ASC (lowest wins, e.g. 100m sprint), DESC (highest wins, e.g. plank). HEAD_TO_HEAD always DESC. NULL for PLACEMENT."
    )

    number_of_rounds = Column(
        Integer,
        nullable=False,
        default=1,
        comment="Number of rounds for INDIVIDUAL_RANKING tournaments (1-10). Each round is a separate session. HEAD_TO_HEAD ignores this."
    )

    number_of_legs = Column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
        comment="Number of legs for HEAD_TO_HEAD round robin. 1=single, 2=home+away, 3=triple, etc."
    )

    track_home_away = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="If True, even legs reverse each pairing so the home team becomes away in leg 2."
    )

    # Team cost
    team_enrollment_cost = Column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Credits deducted from captain when creating a team for this tournament. 0 = free."
    )

    # Assignment Configuration
    assignment_type = Column(
        String(30),
        nullable=True,
        comment="Tournament instructor assignment strategy: OPEN_ASSIGNMENT (admin assigns directly) or APPLICATION_BASED (instructors apply)"
    )

    # Session Delivery Type
    session_type_config = Column(
        String(20),
        nullable=True,
        default="on_site",
        server_default="on_site",
        comment="Session delivery type for generated sessions: on_site / virtual / hybrid (default: on_site)"
    )

    # Meeting URL for virtual/hybrid tournament sessions — propagated to all generated sessions
    meeting_link = Column(
        String,
        nullable=True,
        comment="Meeting URL for virtual/hybrid sessions — propagated to all generated sessions"
    )

    # Session Generation Tracking
    sessions_generated = Column(
        Boolean,
        default=False,
        nullable=False,
        comment="True if tournament sessions have been auto-generated (prevents duplicate generation)"
    )

    sessions_generated_at = Column(
        DateTime,
        nullable=True,
        comment="Timestamp when sessions were auto-generated"
    )

    enrollment_snapshot = Column(
        JSONB,
        nullable=True,
        comment="📸 Snapshot of enrollment state before session generation (for regeneration if needed)"
    )

    campus_schedule_overrides = Column(
        JSONB,
        nullable=True,
        comment=(
            "Per-campus schedule overrides for multi-venue tournaments. "
            "Schema: {campus_id: {match_duration_minutes: int, break_duration_minutes: int, parallel_fields: int}}. "
            "Each campus can independently configure its own schedule parameters."
        )
    )

    # Audit timestamps
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="When this configuration was created"
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
        comment="When this configuration was last updated"
    )

    # Relationships
    tournament = relationship(
        "Semester",
        back_populates="tournament_config_obj",
        doc="Tournament this configuration belongs to"
    )

    tournament_type = relationship(
        "TournamentType",
        foreign_keys=[tournament_type_id],
        doc="Tournament type configuration (for format and session structure)"
    )

    @property
    def format(self) -> str:
        """
        Derive tournament format from tournament_type.

        Returns:
            str: Either "HEAD_TO_HEAD" or "INDIVIDUAL_RANKING"
        """
        if self.tournament_type_id and self.tournament_type:
            return self.tournament_type.format
        return "INDIVIDUAL_RANKING"

    def __repr__(self):
        return (
            f"<TournamentConfiguration(id={self.id}, "
            f"semester_id={self.semester_id}, "
            f"type_id={self.tournament_type_id}, "
            f"max_players={self.max_players})>"
        )

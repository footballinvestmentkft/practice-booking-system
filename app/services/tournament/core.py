"""
Tournament Core Service - CRUD operations for tournaments

This module contains core business logic for tournament creation, session management,
and basic CRUD operations. Extracted from the monolithic tournament_service.py
for better maintainability.

Functions:
    - create_tournament_semester: Create a 1-day tournament semester
    - create_tournament_sessions: Create multiple sessions for a tournament
    - get_tournament_summary: Get summary statistics for a tournament
    - delete_tournament: Delete a tournament and all associated data
"""

from datetime import date, datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session

from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.booking import Booking
from app.models.specialization import SpecializationType
from app.services.tournament.reward_policy_loader import load_policy

# Force reload - custom reward policy support
def create_tournament_semester(
    db: Session,
    tournament_date: date,
    name: str,
    specialization_type: SpecializationType,
    campus_id: Optional[int] = None,
    location_id: Optional[int] = None,
    age_group: Optional[str] = None,
    reward_policy_name: str = "default",
    custom_reward_policy: Optional[dict] = None,  # ✅ NEW: Custom reward policy support
    # 🎯 NEW: Explicit business attributes (DOMAIN GAP RESOLUTION)
    assignment_type: str = "APPLICATION_BASED",
    max_players: Optional[int] = None,
    enrollment_cost: int = 500,
    instructor_id: Optional[int] = None,
    tournament_type_id: Optional[int] = None,  # ✅ ONLY for HEAD_TO_HEAD
    format: str = "HEAD_TO_HEAD",  # ✅ NEW: Tournament format
    scoring_type: str = "PLACEMENT",  # ✅ NEW: Scoring type for INDIVIDUAL_RANKING
    measurement_unit: Optional[str] = None,  # ✅ NEW: Measurement unit for INDIVIDUAL_RANKING
    ranking_direction: Optional[str] = None,  # ✅ NEW: Ranking direction (ASC/DESC)
    game_preset_id: Optional[int] = None  # ✅ NEW: Game preset reference
) -> Semester:
    """
    Create a 1-day semester for tournament (Admin only)

    IMPORTANT: Tournament behavior depends on assignment_type:
    - OPEN_ASSIGNMENT: instructor_id required, tournament immediately READY_FOR_ENROLLMENT
    - APPLICATION_BASED: instructor_id should be None, tournament starts as SEEKING_INSTRUCTOR

    Args:
        db: Database session
        tournament_date: Date of tournament (start_date == end_date)
        name: Tournament name (e.g., "Holiday Football Cup")
        specialization_type: Specialization type for the tournament
        campus_id: Optional campus ID (preferred - most specific location)
        location_id: Optional location ID (fallback if campus not specified)
        age_group: Optional age group
        reward_policy_name: Name of reward policy to use (default: "default")
        custom_reward_policy: Optional custom reward policy dict (overrides reward_policy_name)
        assignment_type: Instructor assignment strategy (OPEN_ASSIGNMENT | APPLICATION_BASED)
        max_players: Maximum tournament participants (explicit capacity)
        enrollment_cost: Enrollment fee in credits (explicit pricing)
        instructor_id: Instructor ID (required for OPEN_ASSIGNMENT, None for APPLICATION_BASED)
        tournament_type_id: Tournament type ID (ONLY for HEAD_TO_HEAD format)
        format: Tournament format (INDIVIDUAL_RANKING | HEAD_TO_HEAD)
        scoring_type: Scoring type for INDIVIDUAL_RANKING (TIME_BASED, SCORE_BASED, etc.)
        measurement_unit: Measurement unit for INDIVIDUAL_RANKING (seconds, meters, points, etc.)
        ranking_direction: Ranking direction (ASC for lowest wins, DESC for highest wins)
        game_preset_id: Optional game preset ID - references pre-configured game type

    Returns:
        Created semester object with appropriate status based on assignment_type

    Raises:
        RewardPolicyError: If reward policy cannot be loaded
        ValueError: If instructor_id is not None at creation or game_preset_id not found
    """
    # ⚠️ BUSINESS LOGIC: instructor_id must be None at creation
    # Instructor assignment happens AFTER via Tournament Management:
    # - OPEN_ASSIGNMENT: Admin invites specific instructor (invitation flow)
    # - APPLICATION_BASED: Instructors apply, admin selects one
    if instructor_id is not None:
        raise ValueError(
            "instructor_id must be None at tournament creation. "
            "Instructor assignment happens AFTER creation via Tournament Management."
        )

    # Generate code: TOURN-YYYYMMDD-XXX (e.g., TOURN-20260113-001)
    # Multiple tournaments can exist on the same date, so we need a sequence number
    date_prefix = f"TOURN-{tournament_date.strftime('%Y%m%d')}"

    # Find existing tournaments on this date
    existing_tournaments = db.query(Semester).filter(
        Semester.code.like(f"{date_prefix}%")
    ).count()

    # Generate unique code with sequence number
    sequence_num = existing_tournaments + 1
    code = f"{date_prefix}-{sequence_num:03d}"

    # Load and snapshot the reward policy
    # If custom_reward_policy is provided, use it directly; otherwise load from file
    if custom_reward_policy:
        reward_policy = custom_reward_policy
    else:
        reward_policy = load_policy(reward_policy_name)

    # ⚠️ BUSINESS LOGIC: ALL tournaments start as SEEKING_INSTRUCTOR
    # Status transitions:
    # - OPEN_ASSIGNMENT: Admin invites instructor → instructor accepts → READY_FOR_ENROLLMENT
    # - APPLICATION_BASED: Instructors apply → admin selects → instructor accepts → READY_FOR_ENROLLMENT
    status = SemesterStatus.SEEKING_INSTRUCTOR
    tournament_status = "SEEKING_INSTRUCTOR"

    # P2: Create tournament WITHOUT configuration fields (will be added as separate entity)
    semester = Semester(
        code=code,
        name=name,
        start_date=tournament_date,
        end_date=tournament_date,  # 1-day tournament
        status=status,
        tournament_status=tournament_status,
        master_instructor_id=None,  # ⚠️ ALWAYS None at creation
        specialization_type=specialization_type.value if hasattr(specialization_type, 'value') else specialization_type,
        age_group=age_group,
        campus_id=campus_id,
        location_id=location_id,
        enrollment_cost=enrollment_cost
    )

    db.add(semester)
    db.commit()
    db.refresh(semester)

    # 🏆 P2: Create separate TournamentConfiguration
    from app.models.tournament_configuration import TournamentConfiguration
    tournament_config_obj = TournamentConfiguration(
        semester_id=semester.id,
        tournament_type_id=tournament_type_id,  # ✅ ONLY for HEAD_TO_HEAD
        participant_type="INDIVIDUAL",
        is_multi_day=False,
        max_players=max_players,
        parallel_fields=1,
        scoring_type=scoring_type,  # ✅ NEW: Scoring type for INDIVIDUAL_RANKING
        measurement_unit=measurement_unit,  # ✅ NEW: Measurement unit for INDIVIDUAL_RANKING
        ranking_direction=ranking_direction,  # ✅ NEW: Ranking direction (ASC/DESC)
        number_of_rounds=1,
        assignment_type=assignment_type,
        sessions_generated=False
    )
    db.add(tournament_config_obj)
    db.commit()
    db.refresh(tournament_config_obj)

    # ✅ CRITICAL: Validate format and tournament_type_id consistency
    # INDIVIDUAL_RANKING: tournament_type_id MUST be NULL
    # HEAD_TO_HEAD: tournament_type_id MUST be set
    # Access via backward-compatible property
    if semester.format == "INDIVIDUAL_RANKING":
        if semester.tournament_type_id is not None:
            raise ValueError(
                "INDIVIDUAL_RANKING tournaments cannot have a tournament_type. "
                "Set tournament_type_id to null."
            )
    elif semester.format == "HEAD_TO_HEAD":
        if semester.tournament_type_id is None:
            raise ValueError(
                "HEAD_TO_HEAD tournaments MUST have a tournament_type (Swiss, League, Knockout, etc.)."
            )

    # 🎁 P1: Create separate TournamentRewardConfig
    from app.models.tournament_reward_config import TournamentRewardConfig
    reward_config_obj = TournamentRewardConfig(
        semester_id=semester.id,
        reward_policy_name=reward_policy_name,
        reward_policy_snapshot=reward_policy,  # Immutable snapshot of policy
        reward_config=reward_policy  # Store the actual config
    )
    db.add(reward_config_obj)
    db.commit()

    # 🎮 P3: Create separate GameConfiguration
    from app.models.game_configuration import GameConfiguration
    from app.models.game_preset import GamePreset

    # If game preset is provided, load it and use its config as template
    final_game_config = None
    if game_preset_id:
        preset = db.query(GamePreset).filter(GamePreset.id == game_preset_id).first()
        if preset:
            # Use preset's game_config as the template
            final_game_config = preset.game_config.copy() if preset.game_config else {}
        else:
            raise ValueError(f"Game preset with ID {game_preset_id} not found")

    # Create GameConfiguration entity
    game_config_obj = GameConfiguration(
        semester_id=semester.id,
        game_preset_id=game_preset_id,
        game_config=final_game_config,
        game_config_overrides=None  # No overrides at creation - can be added later
    )
    db.add(game_config_obj)
    db.commit()
    db.refresh(game_config_obj)

    # Refresh to load relationships
    db.refresh(semester)

    return semester


def create_tournament_sessions(
    db: Session,
    semester_id: int,
    session_configs: List[Dict[str, Any]],
    tournament_date: date
) -> List[SessionModel]:
    """
    Create multiple sessions for tournament (Admin only)

    IMPORTANT: Sessions are created WITHOUT instructor assignment.
    Sessions will inherit master_instructor_id from semester when assigned.

    Args:
        db: Database session
        semester_id: Tournament semester ID
        session_configs: List of session configurations, each with:
            - time: str (e.g., "09:00")
            - duration_minutes: int (default: 90)
            - title: str
            - capacity: int (default: 20)
            - credit_cost: int (default: 1)
            - game_type: str (optional, user-defined game type)
        tournament_date: Date of tournament

    Returns:
        List of created session objects
    """
    created_sessions = []

    for config in session_configs:
        # Parse time
        session_time = datetime.strptime(config["time"], "%H:%M").time()
        start_datetime = datetime.combine(tournament_date, session_time)

        duration = config.get("duration_minutes", 90)
        end_datetime = start_datetime + timedelta(minutes=duration)

        session = SessionModel(
            title=config["title"],
            description=config.get("description", ""),
            date_start=start_datetime,
            date_end=end_datetime,
            session_type=SessionType.on_site,  # Tournaments are typically on-site
            capacity=config.get("capacity", 20),
            instructor_id=None,  # No instructor yet - will be assigned via semester
            semester_id=semester_id,
            credit_cost=config.get("credit_cost", 1),
            # Tournament game fields
            event_category=EventCategory.MATCH,
            game_type=config.get("game_type")  # User-defined game type (optional)
        )

        db.add(session)
        created_sessions.append(session)

    db.commit()

    # Refresh all sessions to get IDs
    for session in created_sessions:
        db.refresh(session)

    return created_sessions


def _game_preset_fields(semester) -> Dict[str, Any]:
    """Extract game preset and skill mapping data for the summary response."""
    _gp = semester.game_preset  # property — None if no preset linked
    _preset_id = getattr(semester, "game_preset_id", None)
    _preset_name = _gp.name if _gp is not None else None
    _skills = (
        {sm.skill_name: float(sm.weight) for sm in semester.skill_mappings}
        if semester.skill_mappings
        else None
    )
    return {
        "game_preset_id": _preset_id,
        "game_preset_name": _preset_name,
        "skills_config": _skills,
    }


def get_tournament_summary(db: Session, semester_id: int) -> Dict[str, Any]:
    """
    Get summary of tournament

    Args:
        db: Database session
        semester_id: Tournament semester ID

    Returns:
        Dictionary with tournament summary including:
        - id, tournament_id, semester_id
        - code, name, date
        - status, tournament_status, specialization_type, age_group
        - location_id, campus_id
        - session_count, total_capacity, total_bookings
        - fill_percentage
        - sessions list with details
        - rankings_count: Count of tournament_rankings entries
    """
    from app.models.tournament_ranking import TournamentRanking

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester:
        return {}

    sessions = db.query(SessionModel).filter(SessionModel.semester_id == semester_id).all()

    total_capacity = sum(s.capacity for s in sessions)

    # ✅ FIX: Count BOTH bookings AND participant_user_ids for sandbox tournaments
    booking_count = db.query(Booking).filter(
        Booking.session_id.in_([s.id for s in sessions])
    ).count()

    # Count participants from sessions (for INDIVIDUAL_RANKING tournaments without bookings)
    participant_count = 0
    for session in sessions:
        if session.participant_user_ids:
            participant_count += len(session.participant_user_ids)

    # Use whichever is greater (bookings or participants)
    total_bookings = max(booking_count, participant_count)

    # ✅ NEW: Count tournament rankings
    rankings_count = db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == semester_id
    ).count()

    return {
        "id": semester.id,
        "tournament_id": semester.id,
        "semester_id": semester.id,
        "code": semester.code,
        "name": semester.name,
        "start_date": semester.start_date.isoformat(),
        "date": semester.start_date.isoformat(),
        "status": semester.status.value if semester.status else None,
        "tournament_status": semester.tournament_status if hasattr(semester, 'tournament_status') else None,  # ✅ NEW
        "specialization_type": semester.specialization_type,
        "age_group": semester.age_group,
        "location_id": semester.location_id,
        "campus_id": semester.campus_id,
        "reward_policy_name": semester.reward_policy_name,
        "reward_policy_snapshot": semester.reward_policy_snapshot,
        "session_count": len(sessions),
        "sessions_count": len(sessions),
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "time": s.date_start.strftime("%H:%M"),
                "capacity": s.capacity,
                # ✅ FIX: Count BOTH bookings AND participants
                "bookings": max(
                    db.query(Booking).filter(Booking.session_id == s.id).count(),
                    len(s.participant_user_ids) if s.participant_user_ids else 0
                )
            }
            for s in sessions
        ],
        "total_capacity": total_capacity,
        "total_bookings": total_bookings,
        "fill_percentage": round((total_bookings / total_capacity * 100) if total_capacity > 0 else 0, 1),
        "rankings_count": rankings_count,
        # Game preset + skill config (for monitoring card display)
        **_game_preset_fields(semester),
    }


def delete_tournament(db: Session, semester_id: int) -> bool:
    """
    Delete tournament and all associated data

    **CRITICAL BUSINESS LOGIC**:
    - Refunds 100% of enrollment cost to all enrolled users
    - Creates credit transaction records for audit trail
    - Handles cascading deletes for all dependencies

    This ensures users are NOT financially harmed when admin deletes a tournament.

    Args:
        db: Database session
        semester_id: Tournament semester ID

    Returns:
        True if deleted successfully, False if not found
    """
    from app.models.semester_enrollment import SemesterEnrollment
    from app.models.user import User
    from app.models.credit_transaction import CreditTransaction
    from app.models.notification import Notification
    from app.models.tournament_status_history import TournamentStatusHistory
    from app.models.instructor_assignment import InstructorAssignmentRequest
    from app.models.session import Session as SessionModel
    from app.models.project import Project
    from app.models.track import Track
    from app.models.group import Group

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester:
        return False

    # ============================================================================
    # STEP 1: REFUND CREDITS TO ALL ENROLLED USERS (100% refund)
    # ============================================================================
    enrollments = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == semester_id,
        SemesterEnrollment.is_active == True
    ).all()

    enrollment_cost = semester.enrollment_cost if semester.enrollment_cost is not None else 500
    refunded_users_count = 0

    for enrollment in enrollments:
        user = db.query(User).filter(User.id == enrollment.user_id).first()
        if user:
            # Refund 100% of enrollment cost
            user.credit_balance = user.credit_balance + enrollment_cost
            db.add(user)

            # Create refund transaction record
            refund_transaction = CreditTransaction(
                user_id=user.id,
                context_user_license_id=enrollment.user_license_id,
                transaction_type="TOURNAMENT_DELETED_REFUND",
                amount=enrollment_cost,  # Positive amount (refund)
                balance_after=user.credit_balance,
                description=f"Tournament deleted by admin - Full refund: {semester.name} ({semester.code})",
                semester_id=None,  # Will be deleted
                enrollment_id=None,  # Will be deleted
                idempotency_key=f"tournament-delete-refund-{semester.id}-{enrollment.id}",
            )
            db.add(refund_transaction)
            refunded_users_count += 1

    # Commit refunds before deleting data
    if refunded_users_count > 0:
        db.flush()

    # ============================================================================
    # STEP 2: CASCADE DELETE ALL DEPENDENCIES
    # ============================================================================

    # Delete notifications
    db.query(Notification).filter(
        Notification.related_semester_id == semester_id
    ).delete(synchronize_session=False)

    # Delete tournament status history
    db.query(TournamentStatusHistory).filter(
        TournamentStatusHistory.tournament_id == semester_id
    ).delete(synchronize_session=False)

    # Delete instructor assignment requests
    db.query(InstructorAssignmentRequest).filter(
        InstructorAssignmentRequest.semester_id == semester_id
    ).delete(synchronize_session=False)

    # Delete sessions (bookings cascade via DB)
    db.query(SessionModel).filter(
        SessionModel.semester_id == semester_id
    ).delete(synchronize_session=False)

    # Delete projects
    db.query(Project).filter(
        Project.semester_id == semester_id
    ).delete(synchronize_session=False)

    # Delete groups
    db.query(Group).filter(
        Group.semester_id == semester_id
    ).delete(synchronize_session=False)

    # Note: semester_enrollments, tournament_rankings, teams have CASCADE in DB
    # Note: credit_transactions have SET NULL for semester_id/enrollment_id

    # Finally delete the tournament
    db.delete(semester)
    db.commit()

    return True

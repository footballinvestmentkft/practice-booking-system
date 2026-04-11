"""
Tournament-specific validation logic.

This module centralizes all tournament validation rules to ensure DRY principle
and maintainability. Previously, age category validation was duplicated across
available.py and enroll.py endpoints.
"""

from typing import List, Optional, Tuple
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.models.semester import Semester, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment


def get_visible_tournament_age_groups(player_age_category: str) -> List[str]:
    """
    Determine which tournament age groups a player can see/enroll in.

    Age Category Rules (UPWARD ENROLLMENT - no instructor approval needed):
    - PRE (5-13): Can enroll in PRE, YOUTH, AMATEUR, PRO (all above)
    - YOUTH (14-18): Can enroll in YOUTH, AMATEUR, PRO (all above)
    - AMATEUR (18+): Can enroll in AMATEUR, PRO (all above)
    - PRO (18+): Can enroll in PRO only (already at top)

    This allows players to "move up" to higher age categories for tournaments
    without requiring instructor approval, making the system more transparent
    and user-friendly.

    Args:
        player_age_category: The player's age category (PRE, YOUTH, AMATEUR, PRO)

    Returns:
        List of age group strings the player can access

    Example:
        >>> get_visible_tournament_age_groups("YOUTH")
        ["YOUTH", "AMATEUR", "PRO"]

        >>> get_visible_tournament_age_groups("PRE")
        ["PRE", "YOUTH", "AMATEUR", "PRO"]

        >>> get_visible_tournament_age_groups("AMATEUR")
        ["AMATEUR", "PRO"]
    """
    # Age category hierarchy: PRE → YOUTH → AMATEUR → PRO
    age_hierarchy = ["PRE", "YOUTH", "AMATEUR", "PRO"]

    if player_age_category not in age_hierarchy:
        # Invalid category - return empty list
        return []

    # Find player's position in hierarchy
    player_index = age_hierarchy.index(player_age_category)

    # Return all categories from player's level and above
    return age_hierarchy[player_index:]


def validate_tournament_enrollment_age(
    player_age_category: str,
    tournament_age_group: str
) -> Tuple[bool, Optional[str]]:
    """
    Validate if a player can enroll in a tournament based on age category.

    This function enforces upward enrollment rules for tournament age categories.
    Players can always enroll in their own age category or higher categories.

    Args:
        player_age_category: The player's age category (PRE, YOUTH, AMATEUR, PRO)
        tournament_age_group: The tournament's age group (PRE, YOUTH, AMATEUR, PRO)

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
        - (True, None) if enrollment is allowed
        - (False, error_message) if enrollment is not allowed

    Example:
        >>> validate_tournament_enrollment_age("YOUTH", "AMATEUR")
        (True, None)

        >>> validate_tournament_enrollment_age("YOUTH", "PRO")
        (True, None)

        >>> validate_tournament_enrollment_age("AMATEUR", "YOUTH")
        (False, "AMATEUR category players cannot enroll in YOUTH tournaments (lower age category)")
    """
    visible_groups = get_visible_tournament_age_groups(player_age_category)

    if tournament_age_group not in visible_groups:
        # Build error message for downward enrollment attempt
        age_hierarchy = ["PRE", "YOUTH", "AMATEUR", "PRO"]
        player_index = age_hierarchy.index(player_age_category) if player_age_category in age_hierarchy else -1
        tournament_index = age_hierarchy.index(tournament_age_group) if tournament_age_group in age_hierarchy else -1

        if player_index > tournament_index:
            # Trying to enroll in a lower age category
            return False, f"{player_age_category} category players cannot enroll in {tournament_age_group} tournaments (lower age category). You can only enroll in your age category or higher."
        else:
            return False, f"Invalid age category combination: {player_age_category} → {tournament_age_group}"

    return True, None


def validate_tournament_ready_for_enrollment(
    tournament: Semester
) -> Tuple[bool, Optional[str]]:
    """
    Validate that a tournament is in READY_FOR_ENROLLMENT status.

    Args:
        tournament: The tournament semester object

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
    """
    if tournament.status != SemesterStatus.READY_FOR_ENROLLMENT:
        return False, f"Tournament not ready for enrollment (status: {tournament.status.value})"

    return True, None


def validate_enrollment_deadline(
    first_session_start: datetime
) -> Tuple[bool, Optional[str]]:
    """
    Validate that enrollment is before the deadline (1 hour before tournament start).

    Args:
        first_session_start: The start time of the first tournament session

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
    """
    if first_session_start:
        enrollment_deadline = first_session_start - timedelta(hours=1)
        now = datetime.now(first_session_start.tzinfo) if first_session_start.tzinfo else datetime.utcnow()

        if now >= enrollment_deadline:
            deadline_str = enrollment_deadline.strftime('%Y-%m-%d %H:%M')
            timezone_str = 'UTC' if not first_session_start.tzinfo else str(first_session_start.tzinfo)
            return False, f"Enrollment closed - tournament starting soon (deadline: {deadline_str} {timezone_str})"

    return True, None


def check_duplicate_enrollment(
    db: Session,
    user_id: int,
    tournament_id: int
) -> Tuple[bool, Optional[str]]:
    """
    Check if a user is already enrolled in a tournament.

    Args:
        db: Database session
        user_id: The user's ID
        tournament_id: The tournament (semester) ID

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
        - (True, None) if not enrolled (enrollment allowed)
        - (False, error_message) if already enrolled
    """
    existing = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user_id,
        SemesterEnrollment.semester_id == tournament_id
    ).first()

    if existing:
        return False, "You are already enrolled in this tournament"

    return True, None


def validate_tournament_session_type(session_type: str) -> Tuple[bool, Optional[str]]:
    """
    Validate session delivery type for tournament sessions.

    Tournaments support on_site, virtual, and hybrid session types.

    Args:
        session_type: The session type (on_site, hybrid, virtual)

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
    """
    valid = {"on_site", "virtual", "hybrid"}
    if session_type not in valid:
        return False, f"Invalid session type '{session_type}'. Must be one of: {sorted(valid)}"

    return True, None


def validate_tournament_attendance_status(status: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that tournament attendance status is ONLY present or absent.

    Tournaments do not support 'late' or 'excused' attendance statuses.

    Args:
        status: The attendance status (present, absent, late, excused)

    Returns:
        Tuple of (is_valid: bool, error_message: Optional[str])
    """
    TOURNAMENT_ALLOWED_STATUSES = {'present', 'absent'}

    if status not in TOURNAMENT_ALLOWED_STATUSES:
        return False, (
            f"Invalid tournament attendance status: {status}. "
            f"Tournaments only support: {', '.join(sorted(TOURNAMENT_ALLOWED_STATUSES))}"
        )

    return True, None

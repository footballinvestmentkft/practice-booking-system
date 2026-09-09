"""
Coach Level Service

Centralized service for coach level validation and requirements.
Consolidates logic that was previously duplicated across multiple modules.
"""

from typing import Dict
from sqlalchemy.orm import Session

from ..models.license import UserLicense
from app.services.canonical_policy import AgeCategory, CoachRole, coach_can_teach


# ============================================================================
# COACH LEVEL REQUIREMENTS
# ============================================================================

MINIMUM_COACH_LEVELS: Dict[str, int] = {
    "PRE": 1,       # Level 1 (lowest) - Pre-Academy (U6-U8)
    "YOUTH": 3,     # Level 3 - Youth Academy (U9-U12)
    "AMATEUR": 5,   # Level 5 - Amateur League (U13-U16)
    "PRO": 7,       # Level 7+ - Professional (U17+)
}


# ============================================================================
# LEVEL VALIDATION FUNCTIONS
# ============================================================================

def check_coach_level_sufficient(coach_level: int, age_group: str) -> bool:
    """
    Check if instructor's coach level is sufficient for tournament age group.

    Args:
        coach_level: Instructor's coach level (1-8)
        age_group: Tournament age group (e.g., 'PRE', 'YOUTH', 'AMATEUR', 'PRO')

    Returns:
        bool: True if level is sufficient, False otherwise

    Examples:
        >>> check_coach_level_sufficient(1, "PRE")
        True
        >>> check_coach_level_sufficient(1, "YOUTH")
        False
        >>> check_coach_level_sufficient(5, "AMATEUR")
        True
    """
    try:
        category = AgeCategory(age_group)
    except ValueError:
        return False
    return coach_can_teach(coach_level, category, CoachRole.ASSISTANT)


def get_required_level_for_age_group(age_group: str) -> int:
    """
    Get the minimum required coach level for an age group.

    Args:
        age_group: Tournament age group (e.g., 'PRE', 'YOUTH', 'AMATEUR', 'PRO')

    Returns:
        int: Minimum required coach level

    Examples:
        >>> get_required_level_for_age_group("PRE")
        1
        >>> get_required_level_for_age_group("YOUTH")
        3
    """
    return MINIMUM_COACH_LEVELS.get(age_group, 1)


def get_eligible_age_groups(coach_level: int) -> list[str]:
    """
    Get list of age groups that a coach is eligible to teach.

    Args:
        coach_level: Instructor's coach level (1-8)

    Returns:
        List of eligible age group codes

    Examples:
        >>> get_eligible_age_groups(1)
        ['PRE']
        >>> get_eligible_age_groups(3)
        ['PRE', 'YOUTH']
        >>> get_eligible_age_groups(7)
        ['PRE', 'YOUTH', 'AMATEUR', 'PRO']
    """
    eligible = []
    for age_group in MINIMUM_COACH_LEVELS:
        if coach_can_teach(coach_level, AgeCategory(age_group), CoachRole.ASSISTANT):
            eligible.append(age_group)
    return eligible


def get_instructor_coach_level(db: Session, user_id: int) -> int:
    """
    Get the instructor's highest LFA_COACH license level from database.

    Args:
        db: Database session
        user_id: ID of the instructor user

    Returns:
        int: Highest coach level (1-8), or 0 if no LFA_COACH license found

    Note:
        This queries the user_licenses table directly.
        Ideally this would be an API endpoint, but for now we access DB directly.
    """
    try:
        # Query for highest LFA_COACH license level
        result = db.query(UserLicense).filter(
            UserLicense.user_id == user_id,
            UserLicense.specialization_type == 'LFA_COACH'
        ).order_by(UserLicense.current_level.desc()).first()

        if result and result.current_level:
            return int(result.current_level)

        return 0
    except Exception as e:
        # Log error and return 0 as fallback
        print(f"Error getting coach level for user {user_id}: {e}")
        return 0


def get_level_display_name(level: int) -> str:
    """
    Get a display-friendly name for a coach level.

    Args:
        level: Coach level (1-8)

    Returns:
        str: Display name for the level

    Examples:
        >>> get_level_display_name(1)
        "Level 1 (Beginner)"
        >>> get_level_display_name(5)
        "Level 5 (Advanced)"
    """
    level_names = {
        1: "Level 1 (Beginner)",
        2: "Level 2 (Basic)",
        3: "Level 3 (Intermediate)",
        4: "Level 4 (Intermediate+)",
        5: "Level 5 (Advanced)",
        6: "Level 6 (Advanced+)",
        7: "Level 7 (Expert)",
        8: "Level 8 (Master)",
    }
    return level_names.get(level, f"Level {level}")


def get_age_group_display_name(age_group: str) -> str:
    """
    Get a display-friendly name for an age group.

    Args:
        age_group: Age group code (e.g., 'PRE', 'YOUTH')

    Returns:
        str: Display name for the age group

    Examples:
        >>> get_age_group_display_name("PRE")
        "Pre-Academy (U6-U8)"
        >>> get_age_group_display_name("YOUTH")
        "Youth Academy (U9-U12)"
    """
    age_group_names = {
        "PRE": "Pre-Academy (U6-U8)",
        "YOUTH": "Youth Academy (U9-U12)",
        "AMATEUR": "Amateur League (U13-U16)",
        "PRO": "Professional (U17+)",
    }
    return age_group_names.get(age_group, age_group)

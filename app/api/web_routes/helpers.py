"""
Helper functions for web routes
"""
from sqlalchemy.orm import Session
from datetime import date

from fastapi.responses import RedirectResponse
from ...models.user import User, UserRole
from ...services.canonical_policy import calculate_age, football_base_category, football_season


def require_student_onboarding(user: User):
    """Guard: return 303 redirect to /dashboard if the user is not an onboarded student.

    Covers:
        - user.role != STUDENT  →  redirect (instructors, admins)
        - user.onboarding_completed == False  →  redirect

    Note: per-spec UserLicense checks remain the responsibility of spec_dashboard().
    """
    if user.role != UserRole.STUDENT or not user.onboarding_completed:
        return RedirectResponse(url="/dashboard", status_code=303)
    return None


def update_specialization_xp(
    db: Session,
    student_id: int,
    specialization_id: str,
    xp_earned: int,
    session_id: int,
    is_update: bool = False
):
    """
    Update or create specialization_progress record with XP

    Args:
        db: Database session
        student_id: Student user ID
        specialization_id: Specialization type (e.g., 'INTERNSHIP')
        xp_earned: XP amount to award
        session_id: Session ID for tracking
        is_update: If True, recalculate XP (don't add); if False, add new XP
    """
def get_lfa_age_category(date_of_birth):
    """
    Determine LFA Player age category based on date of birth.

    Returns tuple: (category_code, category_name, age_range, description)

    Categories:
    - PRE (5-13 years): Foundation Years - Monthly semesters
    - YOUTH (14-18 years): Technical Development - Quarterly semesters
    - AMATEUR (14+ years): Competitive Play - Bi-annual semesters (instructor assigned)
    - PRO (14+ years): Professional Track - Annual semesters (instructor assigned)
    """
    if not date_of_birth:
        return None, None, None, "Date of birth not set"

    today = date.today()
    season = football_season(today)
    age = calculate_age(date_of_birth, season.start)
    category = football_base_category(date_of_birth, season_start=season.start)

    if category and category.value == "PRE":
        return "PRE", "PRE (Foundation Years)", "5-13 years", f"Age {age} - Monthly training blocks"
    elif category and category.value == "YOUTH":
        return "YOUTH", "YOUTH (Technical Development)", "14-18 years", f"Age {age} - Quarterly programs"
    elif category and category.value == "AMATEUR":
        return "AMATEUR", "AMATEUR (Adult)", "19+ years", f"Age {age} - Adult base category"
    else:
        return None, None, None, f"Age {age} - Below minimum age requirement (5 years)"

"""
Age-based specialization requirements
Automatically filters available specializations based on user's age
"""
from typing import List, Dict, Optional

from app.services.canonical_policy import is_program_age_eligible, resolve_program_id


def get_available_specializations(age: Optional[int]) -> List[Dict]:
    """
    Get list of available specializations based on user's age

    Returns list of dicts with:
    - type: SpecializationType value
    - name: Display name
    - icon: Emoji icon
    - color: Hex color
    - description: Short description
    - age_requirement: Age requirement text (optional)
    """

    if age is None:
        # No age provided - show all (shouldn't happen in new flow)
        return []

    available = []

    # INTERNSHIP: 18+ (no upper limit)
    if age >= 18:
        available.append({
            "type": "INTERNSHIP",
            "name": "Internship",
            "icon": "💼",
            "color": "#e74c3c",
            "description": "Build your startup career from zero to co-founder (5-semester journey)",
            "age_requirement": "Age 18+"
        })

    # GANCUJU_PLAYER: 5+ (no upper limit)
    if age >= 5:
        available.append({
            "type": "GANCUJU_PLAYER",
            "name": "GānCuju Player",
            "icon": "🥋",
            "color": "#8e44ad",
            "description": "Master the 4000-year-old Chinese football art with authentic Ganball™ equipment",
            "age_requirement": "Age 5+"
        })

    # LFA FOOTBALL PLAYER: 5+ with different levels
    if age >= 5:
        if 5 <= age <= 13:
            level_info = "Pre Level (Ages 5-13)"
        elif 14 <= age <= 18:
            level_info = "Youth Level (Ages 14-18)"
        elif age > 18:
            level_info = "Amateur/Pro Level (Age 14+, instructor assigned)"
        else:
            level_info = "Age 5+"

        available.append({
            "type": "LFA_FOOTBALL_PLAYER",
            "name": "LFA Football Player",
            "icon": "⚽",
            "color": "#f1c40f",
            "description": "Modern football training and professional player development",
            "age_requirement": level_info
        })

    # LFA COACH: 14+
    if age >= 14:
        available.append({
            "type": "LFA_COACH",
            "name": "LFA Coach",
            "icon": "👨‍🏫",
            "color": "#27ae60",
            "description": "Become a certified football coach with LFA methodology",
            "age_requirement": "Age 14+"
        })

    return available


def validate_specialization_for_age(spec_type: str, age: Optional[int]) -> bool:
    """
    Validate if a specialization is available for the given age

    Args:
        spec_type: Specialization type (e.g., 'INTERNSHIP')
        age: User's age in years

    Returns:
        True if specialization is available for this age, False otherwise
    """
    if age is None:
        return False

    resolution = resolve_program_id(spec_type)
    if not resolution.usable:
        return False
    return is_program_age_eligible(resolution.canonical_program, age)

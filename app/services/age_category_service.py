"""Compatibility facade for the canonical WS1 football age policy."""
from datetime import date
from typing import Optional

from app.services.canonical_policy import (
    AgeCategory,
    calculate_age,
    football_season,
    validate_football_movement,
)


def calculate_age_at_season_start(date_of_birth: date, season_year: int) -> int:
    return calculate_age(date_of_birth, date(season_year, 7, 1))


def get_automatic_age_category(age_at_season_start: int) -> Optional[str]:
    if age_at_season_start < 5:
        return None
    if age_at_season_start <= 13:
        return AgeCategory.PRE.value
    if age_at_season_start <= 18:
        return AgeCategory.YOUTH.value
    return AgeCategory.AMATEUR.value


def get_current_season_year() -> int:
    return football_season(date.today()).start.year


def can_override_age_category(age_at_season_start: int) -> bool:
    return age_at_season_start >= 5


def validate_age_category_override(
    age_at_season_start: int,
    new_category: str,
) -> tuple[bool, Optional[str]]:
    base_value = get_automatic_age_category(age_at_season_start)
    if base_value is None:
        return False, "Player does not meet the canonical football minimum age"
    try:
        target = AgeCategory(new_category)
    except ValueError:
        return False, "Invalid category. Must be one of: PRE, YOUTH, AMATEUR, PRO"
    if target.value == base_value:
        return True, None
    decision = validate_football_movement(
        AgeCategory(base_value),
        AgeCategory(base_value),
        target,
        current_age=age_at_season_start,
    )
    return decision.allowed, decision.reason

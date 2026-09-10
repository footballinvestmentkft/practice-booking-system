"""Canonical WS1 business policy for program identity, age and football roles.

This module is deliberately free of HTTP and persistence concerns. API, web and
native-facing adapters must consume these decisions instead of reimplementing
them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class CanonicalProgram(str, Enum):
    LFA_FOOTBALL_PLAYER = "LFA_FOOTBALL_PLAYER"
    LFA_COACH = "LFA_COACH"
    GANCUJU_PLAYER = "GANCUJU_PLAYER"
    INTERNSHIP = "INTERNSHIP"


class ProgramResolutionStatus(str, Enum):
    CANONICAL = "CANONICAL"
    LEGACY_ALIAS = "LEGACY_ALIAS"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    INVALID = "INVALID"


class AgeCategory(str, Enum):
    PRE = "PRE"
    YOUTH = "YOUTH"
    AMATEUR = "AMATEUR"
    PRO = "PRO"


class CoachRole(str, Enum):
    ASSISTANT = "ASSISTANT"
    HEAD = "HEAD"


@dataclass(frozen=True)
class ProgramResolution:
    source_id: str
    status: ProgramResolutionStatus
    canonical_program: Optional[CanonicalProgram]

    @property
    def usable(self) -> bool:
        return self.canonical_program is not None and self.status in {
            ProgramResolutionStatus.CANONICAL,
            ProgramResolutionStatus.LEGACY_ALIAS,
        }


@dataclass(frozen=True)
class ProfileAgeDecision:
    usable: bool
    age: Optional[int]
    reason: Optional[str] = None


@dataclass(frozen=True)
class FootballSeason:
    start: date
    end: date

    @property
    def key(self) -> str:
        return f"{self.start.year}/{str(self.end.year)[-2:]}"


@dataclass(frozen=True)
class FootballMovementDecision:
    allowed: bool
    base_participation_retained: bool
    reason: Optional[str] = None


LEGACY_PROGRAM_ALIASES = {
    "PLAYER": CanonicalProgram.GANCUJU_PLAYER,
    "COACH": CanonicalProgram.LFA_COACH,
    "INTERNSHIP": CanonicalProgram.INTERNSHIP,
}

AMBIGUOUS_PROGRAM_IDS = {
    "LFA_PLAYER",
    "LFA_PLAYER_PRE",
    "LFA_PLAYER_YOUTH",
    "LFA_PLAYER_AMATEUR",
    "LFA_PLAYER_PRO",
    "LFA_PLAYER_PRE_ACADEMY",
    "LFA_PLAYER_YOUTH_ACADEMY",
    "LFA_PLAYER_AMATEUR_ACADEMY",
    "LFA_PLAYER_PRO_ACADEMY",
}

PROGRAM_MINIMUM_AGES = {
    CanonicalProgram.LFA_FOOTBALL_PLAYER: 5,
    CanonicalProgram.LFA_COACH: 14,
    CanonicalProgram.GANCUJU_PLAYER: 5,
    CanonicalProgram.INTERNSHIP: 18,
}


def resolve_program_id(raw_program_id: object) -> ProgramResolution:
    raw = getattr(raw_program_id, "value", raw_program_id)
    source = str(raw).strip().upper() if raw is not None else ""
    try:
        canonical = CanonicalProgram(source)
    except ValueError:
        canonical = None
    if canonical is not None:
        return ProgramResolution(source, ProgramResolutionStatus.CANONICAL, canonical)
    if source in LEGACY_PROGRAM_ALIASES:
        return ProgramResolution(source, ProgramResolutionStatus.LEGACY_ALIAS, LEGACY_PROGRAM_ALIASES[source])
    if source in AMBIGUOUS_PROGRAM_IDS:
        return ProgramResolution(source, ProgramResolutionStatus.MANUAL_REVIEW, None)
    return ProgramResolution(source, ProgramResolutionStatus.INVALID, None)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def calculate_age(date_of_birth: date | datetime, on_date: date | datetime) -> int:
    dob = _as_date(date_of_birth)
    current = _as_date(on_date)
    return current.year - dob.year - ((current.month, current.day) < (dob.month, dob.day))


def evaluate_profile_age_policy(
    date_of_birth: Optional[date | datetime],
    guardian_consent_active: bool,
    *,
    on_date: Optional[date | datetime] = None,
    biometric: bool = False,
) -> ProfileAgeDecision:
    if date_of_birth is None:
        return ProfileAgeDecision(False, None, "DATE_OF_BIRTH_REQUIRED")
    age = calculate_age(date_of_birth, on_date or date.today())
    if age < 0:
        return ProfileAgeDecision(False, age, "DATE_OF_BIRTH_IN_FUTURE")
    if age < 5:
        return ProfileAgeDecision(False, age, "MINIMUM_ACCOUNT_AGE")
    if age < 18 and not guardian_consent_active:
        return ProfileAgeDecision(False, age, "GUARDIAN_CONSENT_REQUIRED")
    if biometric and age < 18:
        return ProfileAgeDecision(False, age, "BIOMETRIC_ADULT_ONLY")
    return ProfileAgeDecision(True, age)


def resolve_license_program(
    canonical_program_id: object,
    legacy_specialization_type: object,
) -> ProgramResolution:
    """Resolve both entitlement representations and fail closed on conflict."""
    canonical = resolve_program_id(canonical_program_id) if canonical_program_id else None
    legacy = resolve_program_id(legacy_specialization_type)
    if canonical is None:
        return legacy
    if canonical.status is not ProgramResolutionStatus.CANONICAL:
        return ProgramResolution(
            str(canonical_program_id), ProgramResolutionStatus.MANUAL_REVIEW, None
        )
    if not legacy.usable or legacy.canonical_program is not canonical.canonical_program:
        return ProgramResolution(
            f"{canonical.source_id}|{legacy.source_id}",
            ProgramResolutionStatus.MANUAL_REVIEW,
            None,
        )
    return canonical


def is_program_age_eligible(
    program: CanonicalProgram | str,
    age: int,
    *,
    progression_level: Optional[int] = None,
) -> bool:
    resolution = resolve_program_id(program)
    if not resolution.usable:
        return False
    # Progression levels intentionally do not add age thresholds after entry.
    _ = progression_level
    return age >= PROGRAM_MINIMUM_AGES[resolution.canonical_program]


def football_season(on_date: date | datetime) -> FootballSeason:
    current = _as_date(on_date)
    start_year = current.year if current.month >= 7 else current.year - 1
    return FootballSeason(date(start_year, 7, 1), date(start_year + 1, 6, 30))


def football_base_category(
    date_of_birth: date | datetime,
    *,
    season_start: date | datetime,
) -> Optional[AgeCategory]:
    age = calculate_age(date_of_birth, season_start)
    if age < 5:
        return None
    if age <= 13:
        return AgeCategory.PRE
    if age <= 18:
        return AgeCategory.YOUTH
    return AgeCategory.AMATEUR


def validate_football_movement(
    base_category: AgeCategory | str,
    current_category: AgeCategory | str,
    target_category: AgeCategory | str,
    *,
    current_age: int,
    base_participation_retained: bool = False,
) -> FootballMovementDecision:
    try:
        base = AgeCategory(base_category)
        current = AgeCategory(current_category)
        target = AgeCategory(target_category)
    except ValueError:
        return FootballMovementDecision(False, base_participation_retained, "INVALID_CATEGORY")
    categories = list(AgeCategory)
    if categories.index(target) < categories.index(base):
        return FootballMovementDecision(False, base_participation_retained, "BELOW_SEASON_BASE")
    if target in {AgeCategory.AMATEUR, AgeCategory.PRO} and current_age < 14:
        return FootballMovementDecision(False, base_participation_retained, "TARGET_MINIMUM_AGE_14")
    if current == target:
        return FootballMovementDecision(False, base_participation_retained, "NO_CATEGORY_CHANGE")
    return FootballMovementDecision(True, base_participation_retained)


def football_participation_categories(
    base_category: AgeCategory | str,
    effective_category: AgeCategory | str,
    *,
    base_participation_retained: bool,
) -> frozenset[AgeCategory]:
    """Return exactly the categories granted by an assignment projection."""
    base = AgeCategory(base_category)
    effective = AgeCategory(effective_category)
    if effective is base or not base_participation_retained:
        return frozenset({effective})
    return frozenset({base, effective})


def coach_level_scope(level: int) -> tuple[AgeCategory, CoachRole]:
    if level not in range(1, 9):
        raise ValueError("LFA Coach level must be between 1 and 8")
    return list(AgeCategory)[(level - 1) // 2], CoachRole.ASSISTANT if level % 2 else CoachRole.HEAD


def coach_can_teach(
    level: int,
    target_category: AgeCategory | str,
    target_role: CoachRole | str,
) -> bool:
    try:
        category = AgeCategory(target_category)
        role = CoachRole(target_role)
        qualification_category, qualification_role = coach_level_scope(level)
    except ValueError:
        return False
    if list(AgeCategory).index(category) > list(AgeCategory).index(qualification_category):
        return False
    if role is CoachRole.HEAD and qualification_role is not CoachRole.HEAD:
        return False
    return True

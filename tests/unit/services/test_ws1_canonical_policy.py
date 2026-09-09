from datetime import date

import pytest

from app.services.canonical_policy import (
    AgeCategory,
    CanonicalProgram,
    CoachRole,
    ProgramResolutionStatus,
    calculate_age,
    coach_can_teach,
    evaluate_profile_age_policy,
    football_base_category,
    football_participation_categories,
    football_season,
    is_program_age_eligible,
    resolve_program_id,
    resolve_license_program,
    validate_football_movement,
)
from app.services.specs.session_based.lfa_player_service import LFAPlayerService


@pytest.mark.parametrize(
    ("raw", "status", "canonical"),
    [
        ("LFA_FOOTBALL_PLAYER", ProgramResolutionStatus.CANONICAL, CanonicalProgram.LFA_FOOTBALL_PLAYER),
        ("LFA_COACH", ProgramResolutionStatus.CANONICAL, CanonicalProgram.LFA_COACH),
        ("GANCUJU_PLAYER", ProgramResolutionStatus.CANONICAL, CanonicalProgram.GANCUJU_PLAYER),
        ("INTERNSHIP", ProgramResolutionStatus.CANONICAL, CanonicalProgram.INTERNSHIP),
        ("PLAYER", ProgramResolutionStatus.LEGACY_ALIAS, CanonicalProgram.GANCUJU_PLAYER),
        ("COACH", ProgramResolutionStatus.LEGACY_ALIAS, CanonicalProgram.LFA_COACH),
        ("LFA_PLAYER", ProgramResolutionStatus.MANUAL_REVIEW, None),
        ("LFA_PLAYER_PRE", ProgramResolutionStatus.MANUAL_REVIEW, None),
        ("unknown", ProgramResolutionStatus.INVALID, None),
    ],
)
def test_program_resolution_is_explicit_and_fail_closed(raw, status, canonical):
    resolution = resolve_program_id(raw)
    assert resolution.status is status
    assert resolution.canonical_program is canonical


def test_global_age_dob_consent_and_biometric_boundaries():
    on_date = date(2026, 7, 1)
    assert evaluate_profile_age_policy(None, False, on_date=on_date).usable is False
    assert evaluate_profile_age_policy(date(2022, 7, 1), True, on_date=on_date).usable is False
    assert evaluate_profile_age_policy(date(2021, 7, 1), False, on_date=on_date).usable is False
    assert evaluate_profile_age_policy(date(2021, 7, 1), True, on_date=on_date).usable is True
    assert evaluate_profile_age_policy(date(2009, 7, 1), True, on_date=on_date, biometric=True).usable is False
    assert evaluate_profile_age_policy(date(2008, 7, 1), False, on_date=on_date, biometric=True).usable is True
    assert evaluate_profile_age_policy(date(2027, 7, 1), True, on_date=on_date).reason == "DATE_OF_BIRTH_IN_FUTURE"


def test_canonical_and_legacy_license_representations_must_agree():
    assert resolve_license_program("LFA_COACH", "COACH").canonical_program is CanonicalProgram.LFA_COACH
    conflict = resolve_license_program("LFA_COACH", "PLAYER")
    assert conflict.status is ProgramResolutionStatus.MANUAL_REVIEW
    assert conflict.usable is False
    assert resolve_license_program(None, "PLAYER").canonical_program is CanonicalProgram.GANCUJU_PLAYER


@pytest.mark.parametrize(
    ("program", "age", "eligible"),
    [
        (CanonicalProgram.LFA_FOOTBALL_PLAYER, 4, False),
        (CanonicalProgram.LFA_FOOTBALL_PLAYER, 5, True),
        (CanonicalProgram.GANCUJU_PLAYER, 4, False),
        (CanonicalProgram.GANCUJU_PLAYER, 5, True),
        (CanonicalProgram.LFA_COACH, 13, False),
        (CanonicalProgram.LFA_COACH, 14, True),
        (CanonicalProgram.INTERNSHIP, 17, False),
        (CanonicalProgram.INTERNSHIP, 18, True),
    ],
)
def test_program_age_boundaries(program, age, eligible):
    assert is_program_age_eligible(program, age) is eligible


def test_football_season_june_30_and_july_1_boundary():
    previous = football_season(date(2026, 6, 30))
    current = football_season(date(2026, 7, 1))
    assert (previous.start, previous.end) == (date(2025, 7, 1), date(2026, 6, 30))
    assert (current.start, current.end) == (date(2026, 7, 1), date(2027, 6, 30))


@pytest.mark.parametrize(
    ("dob", "expected"),
    [
        (date(2013, 7, 1), AgeCategory.PRE),
        (date(2012, 7, 1), AgeCategory.YOUTH),
        (date(2008, 7, 1), AgeCategory.YOUTH),
        (date(2007, 7, 1), AgeCategory.AMATEUR),
    ],
)
def test_football_base_uses_age_on_season_start(dob, expected):
    assert football_base_category(dob, season_start=date(2026, 7, 1)) is expected


def test_birthday_does_not_change_base_mid_season():
    dob = date(2012, 10, 2)
    base_at_start = football_base_category(dob, season_start=date(2026, 7, 1))
    assert calculate_age(dob, date(2026, 7, 1)) == 13
    assert calculate_age(dob, date(2026, 10, 2)) == 14
    assert base_at_start is AgeCategory.PRE


@pytest.mark.parametrize(
    ("base", "current", "target", "age", "valid"),
    [
        (AgeCategory.PRE, AgeCategory.PRE, AgeCategory.YOUTH, 13, True),
        (AgeCategory.PRE, AgeCategory.PRE, AgeCategory.AMATEUR, 14, True),
        (AgeCategory.YOUTH, AgeCategory.YOUTH, AgeCategory.PRO, 14, True),
        (AgeCategory.PRE, AgeCategory.PRO, AgeCategory.AMATEUR, 19, True),
        (AgeCategory.YOUTH, AgeCategory.PRO, AgeCategory.PRE, 19, False),
        (AgeCategory.PRE, AgeCategory.PRE, AgeCategory.PRO, 13, False),
    ],
)
def test_football_movement_boundaries(base, current, target, age, valid):
    assert validate_football_movement(base, current, target, current_age=age).allowed is valid


def test_base_participation_retained_is_preserved_as_command_data():
    retained = validate_football_movement(
        AgeCategory.PRE, AgeCategory.PRE, AgeCategory.YOUTH, current_age=13,
        base_participation_retained=True,
    )
    not_retained = validate_football_movement(
        AgeCategory.PRE, AgeCategory.PRE, AgeCategory.YOUTH, current_age=13,
        base_participation_retained=False,
    )
    assert retained.base_participation_retained is True
    assert not_retained.base_participation_retained is False
    assert football_participation_categories("PRE", "PRO", base_participation_retained=True) == {
        AgeCategory.PRE, AgeCategory.PRO,
    }
    assert football_participation_categories("PRE", "PRO", base_participation_retained=False) == {
        AgeCategory.PRO,
    }


@pytest.mark.parametrize("level", range(1, 9))
@pytest.mark.parametrize("category", list(AgeCategory))
@pytest.mark.parametrize("role", list(CoachRole))
def test_complete_coach_8_by_8_authorization_matrix(level, category, role):
    qualification_category = list(AgeCategory)[(level - 1) // 2]
    qualification_role = CoachRole.ASSISTANT if level % 2 else CoachRole.HEAD
    expected = list(AgeCategory).index(category) <= list(AgeCategory).index(qualification_category)
    if role is CoachRole.HEAD:
        expected = expected and qualification_role is CoachRole.HEAD
    assert coach_can_teach(level, category, role) is expected


def test_assistant_level_cannot_be_used_as_head_even_for_lower_category():
    assert coach_can_teach(3, AgeCategory.PRE, CoachRole.HEAD) is False
    assert coach_can_teach(3, AgeCategory.PRE, CoachRole.ASSISTANT) is True


def test_unknown_coach_scope_fails_closed():
    assert coach_can_teach(8, "UNKNOWN", CoachRole.HEAD) is False
    assert coach_can_teach(8, AgeCategory.PRO, "UNKNOWN") is False


def test_age_independent_progression_after_program_entry():
    assert is_program_age_eligible(CanonicalProgram.GANCUJU_PLAYER, 5, progression_level=8)
    assert is_program_age_eligible(CanonicalProgram.INTERNSHIP, 18, progression_level=5)


def test_lfa_player_compatibility_service_uses_canonical_season_base():
    service = LFAPlayerService()
    season = football_season(date.today())
    assert service.calculate_age_group(date(season.start.year - 5, 7, 1)) == "PRE"
    assert service.calculate_age_group(date(season.start.year - 14, 7, 1)) == "YOUTH"
    assert service.calculate_age_group(date(season.start.year - 19, 7, 1)) == "AMATEUR"

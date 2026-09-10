"""Contract tests for the compatibility validator's canonical WS1 policy."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.specialization import SpecializationType
from app.services.specialization_validation import (
    SpecializationValidationError,
    SpecializationValidator,
)


_PATCH_LOADER = "app.services.specialization_validation.get_config_loader"


def _user(dob: date | None, *, user_id: int = 7):
    return SimpleNamespace(id=user_id, date_of_birth=dob)


def _validator(*, consent: bool = False, loader_error: Exception | None = None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = (1,) if consent else None
    loader = MagicMock()
    if loader_error:
        loader.load_config.side_effect = loader_error
    else:
        loader.load_config.return_value = {"name": "Test", "age_groups": []}
    with patch(_PATCH_LOADER, return_value=loader):
        return SpecializationValidator(db)


def _validate(dob, program, *, consent=False, on_error=False):
    validator = _validator(consent=consent)
    return validator.validate_user_for_specialization(
        _user(dob), program, raise_exception=on_error
    )


def test_config_load_failure_is_reported_without_policy_bypass():
    result = _validator(loader_error=FileNotFoundError("missing")).validate_user_for_specialization(
        _user(date(1990, 1, 1)), SpecializationType.GANCUJU_PLAYER, raise_exception=False
    )
    assert result["valid"] is False
    assert result["warnings"] == []
    assert result["requirements"] == {}
    assert "Failed to load specialization config" in result["errors"][0]


@pytest.mark.parametrize(
    ("program", "allowed_dob", "denied_dob"),
    [
        (SpecializationType.LFA_FOOTBALL_PLAYER, date(1990, 1, 1), date(2022, 1, 1)),
        (SpecializationType.GANCUJU_PLAYER, date(1990, 1, 1), date(2022, 1, 1)),
        (SpecializationType.LFA_COACH, date(2000, 1, 1), date(2014, 1, 1)),
        (SpecializationType.INTERNSHIP, date(2000, 1, 1), date(2010, 1, 1)),
    ],
)
def test_program_entry_age_comes_from_canonical_policy(program, allowed_dob, denied_dob):
    assert _validate(allowed_dob, program)["valid"] is True
    denied = _validate(denied_dob, program)
    assert denied["valid"] is False
    assert denied["errors"][0] in {
        "MINIMUM_ACCOUNT_AGE", "PROGRAM_MINIMUM_AGE", "GUARDIAN_CONSENT_REQUIRED"
    }


def test_missing_dob_is_denied():
    result = _validate(None, SpecializationType.GANCUJU_PLAYER)
    assert result["errors"] == ["DATE_OF_BIRTH_REQUIRED"]


def test_guardian_consent_is_global_for_every_minor_program():
    dob = date(2012, 1, 1)
    for program in (
        SpecializationType.LFA_FOOTBALL_PLAYER,
        SpecializationType.GANCUJU_PLAYER,
        SpecializationType.LFA_COACH,
    ):
        assert _validate(dob, program, consent=False)["errors"] == ["GUARDIAN_CONSENT_REQUIRED"]
        assert _validate(dob, program, consent=True)["valid"] is True


def test_ambiguous_legacy_program_fails_closed():
    result = _validate(date(1990, 1, 1), SpecializationType.LFA_PLAYER_PRE)
    assert result["errors"] == ["PROGRAM_ID_MANUAL_REVIEW_OR_INVALID"]


def test_raise_exception_exposes_only_structured_canonical_reason():
    with pytest.raises(SpecializationValidationError, match="DATE_OF_BIRTH_REQUIRED"):
        _validate(None, SpecializationType.GANCUJU_PLAYER, on_error=True)


def test_requirements_are_descriptive_and_not_a_second_policy_authority():
    result = _validate(date(1990, 1, 1), SpecializationType.INTERNSHIP)
    assert result["requirements"] == {
        "min_age": 18,
        "specialization_name": "Test",
        "has_age_groups": False,
        "age_groups": None,
        "canonical_policy": True,
        "requires_parental_consent_under_18": True,
    }

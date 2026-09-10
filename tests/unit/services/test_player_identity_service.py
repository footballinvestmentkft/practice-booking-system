from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _user(*, dob: date | None, user_id: int = 42, credits: int = 500):
    return SimpleNamespace(
        id=user_id,
        date_of_birth=dob,
        credit_balance=credits,
        specialization=None,
        parental_consent=False,
        parental_consent_at=None,
        parental_consent_by=None,
        email="player@example.test",
    )


def _locking_db(user):
    db = MagicMock()
    db.query.return_value.filter.return_value.with_for_update.return_value.one.return_value = user
    return db


def test_profile_update_rejects_minor_without_canonical_consent_evidence():
    from app.services.player_identity_service import (
        PlayerIdentityPolicyError,
        update_identity_profile,
    )

    user = _user(dob=date(2000, 1, 1))
    db = _locking_db(user)
    with patch(
        "app.services.player_identity_service.has_active_guardian_consent",
        return_value=False,
    ), pytest.raises(PlayerIdentityPolicyError) as exc:
        update_identity_profile(db, user=user, date_of_birth=date(2012, 1, 1), on_date=date(2026, 1, 1))

    assert exc.value.code == "GUARDIAN_CONSENT_REQUIRED"
    assert user.date_of_birth == date(2000, 1, 1)
    db.flush.assert_not_called()


@pytest.mark.parametrize("dob,code", [(None, "DATE_OF_BIRTH_REQUIRED"), (date(2022, 1, 2), "MINIMUM_ACCOUNT_AGE")])
def test_profile_update_rejects_missing_or_under_five_dob(dob, code):
    from app.services.player_identity_service import (
        PlayerIdentityPolicyError,
        update_identity_profile,
    )

    user = _user(dob=date(2000, 1, 1))
    db = _locking_db(user)
    with patch(
        "app.services.player_identity_service.has_active_guardian_consent",
        return_value=False,
    ), pytest.raises(PlayerIdentityPolicyError) as exc:
        update_identity_profile(db, user=user, date_of_birth=dob, on_date=date(2026, 1, 1))

    assert exc.value.code == code
    assert user.date_of_birth == date(2000, 1, 1)


def test_profile_update_uses_evidence_not_legacy_parental_boolean():
    from app.services.player_identity_service import update_identity_profile

    user = _user(dob=date(2012, 1, 1))
    user.parental_consent = False
    db = _locking_db(user)
    with patch(
        "app.services.player_identity_service.has_active_guardian_consent",
        return_value=True,
    ), patch(
        "app.services.player_identity_service.get_current_player_assignment",
        return_value=None,
    ):
        decision = update_identity_profile(
            db,
            user=user,
            date_of_birth=date(2012, 1, 1),
            on_date=date(2026, 1, 1),
        )

    assert decision.age == 14
    assert user.parental_consent is True
    db.flush.assert_called_once()


def test_football_unlock_rejects_ineligible_profile_before_financial_write():
    from app.services.player_identity_service import (
        PlayerIdentityPolicyError,
        unlock_football_player,
    )

    user = _user(dob=None)
    db = _locking_db(user)
    with patch(
        "app.services.player_identity_service.is_user_eligible_for_program",
        return_value=(False, "DATE_OF_BIRTH_REQUIRED"),
    ), patch("app.services.player_identity_service.CreditService") as credit:
        with pytest.raises(PlayerIdentityPolicyError) as exc:
            unlock_football_player(db, user=user, duration_months=1)

    assert exc.value.code == "DATE_OF_BIRTH_REQUIRED"
    credit.assert_not_called()
    db.add.assert_not_called()


def test_web_and_api_adapters_delegate_to_canonical_player_commands():
    import inspect
    from app.api.api_v1.endpoints.specializations.user import set_user_specialization
    from app.api.api_v1.endpoints.users.profile import update_own_profile
    from app.api.web_routes.onboarding import specialization_select_submit
    from app.api.web_routes.onboarding import onboarding_set_birthdate
    from app.api.web_routes.auth import age_verification_submit
    from app.api.web_routes.profile import profile_edit_submit
    from app.api.web_routes.specialization import specialization_unlock

    unlock_sources = "\n".join(
        inspect.getsource(fn)
        for fn in (specialization_select_submit, specialization_unlock)
    )
    assert unlock_sources.count("unlock_football_player") == 2
    assert "activate_football_player" in inspect.getsource(set_user_specialization)
    assert "update_identity_profile" in inspect.getsource(update_own_profile)
    assert "update_identity_profile" in inspect.getsource(profile_edit_submit)
    assert "update_identity_profile" in inspect.getsource(onboarding_set_birthdate)
    assert "update_identity_profile" in inspect.getsource(age_verification_submit)

    from app.api.api_v1.endpoints.semester_enrollments.crud import create_enrollment
    assert "prepare_football_player_enrollment" in inspect.getsource(create_enrollment)


def test_self_profile_schema_does_not_accept_legacy_authority_fields():
    from app.schemas.user import UserUpdateSelf

    fields = set(UserUpdateSelf.model_fields)
    assert "date_of_birth" in fields
    assert "parental_consent" not in fields
    assert "parental_consent_by" not in fields
    assert "specialization" not in fields
    assert "onboarding_completed" not in fields


def test_player_license_contract_exposes_canonical_season_assignment():
    from app.api.api_v1.endpoints.lfa_player.licenses import LicenseResponse

    fields = set(LicenseResponse.model_fields)
    assert {
        "canonical_program_id",
        "season_start",
        "season_end",
        "season_base_category",
        "effective_category",
        "base_participation_retained",
    } <= fields


def test_all_runtime_player_entitlement_writers_use_canonical_service():
    from pathlib import Path

    root = Path(__file__).parents[3]
    for relative in (
        "app/api/api_v1/endpoints/admin_players.py",
        "app/services/csv_import_service.py",
        "app/services/sponsor_promote_service.py",
    ):
        source = (root / relative).read_text()
        assert "issue_football_player_entitlement" in source, relative


def test_legacy_generic_writers_cannot_create_football_entitlements():
    from pathlib import Path

    root = Path(__file__).parents[3]
    canonical_adapters = (
        "app/api/api_v1/endpoints/payment_verification.py",
        "app/api/web_routes/admin/credits.py",
    )
    for relative in canonical_adapters:
        source = (root / relative).read_text()
        assert "issue_football_player_entitlement" in source, relative

    fail_closed_services = (
        "app/services/parallel_specialization_service.py",
        "app/services/progress_license_coupling.py",
        "app/services/progress_license_sync_service.py",
    )
    for relative in fail_closed_services:
        source = (root / relative).read_text()
        assert "FOOTBALL_ENTITLEMENT_REQUIRES_CANONICAL_COMMAND" in source, relative


def test_account_creation_and_player_switch_use_canonical_identity_commands():
    import inspect

    from app.api.api_v1.endpoints.auth import register_with_invitation
    from app.api.api_v1.endpoints.users.crud import create_user
    from app.api.web_routes.auth import register_submit
    from app.api.web_routes.admin.users import admin_create_user
    from app.api.web_routes.specialization import specialization_switch

    for adapter in (
        register_with_invitation,
        create_user,
        register_submit,
        admin_create_user,
    ):
        assert "update_identity_profile" in inspect.getsource(adapter), adapter.__name__
    assert "activate_football_player" in inspect.getsource(specialization_switch)


def test_player_profile_and_web_enrollments_use_ws1_assignment_authority():
    import inspect

    from app.api.api_v1.endpoints.public_profile import get_lfa_player_profile
    from app.api.web_routes.dashboard import spec_dashboard
    from app.api.web_routes.tournaments.browse import tournament_enroll
    from app.api.web_routes.tournaments.camps import camp_enroll

    assert "get_current_player_assignment" in inspect.getsource(get_lfa_player_profile)
    assert "get_current_player_assignment" in inspect.getsource(spec_dashboard)
    for adapter in (tournament_enroll, camp_enroll):
        source = inspect.getsource(adapter)
        assert "prepare_football_player_enrollment" in source
        assert "football_category_assignment_id" in source


def test_player_session_service_uses_canonical_entitlement_lookup():
    from pathlib import Path

    source = (
        Path(__file__).parents[3]
        / "app/services/specs/session_based/lfa_player_service.py"
    ).read_text()
    assert "get_active_football_entitlement" in source

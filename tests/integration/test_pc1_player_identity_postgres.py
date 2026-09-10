from datetime import date
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.models.credit_transaction import CreditTransaction
from app.models.license import UserLicense
from app.models.specialization import SpecializationType
from app.models.user import User, UserRole
from app.models.ws1_domain import FootballSeasonCategoryAssignment
from app.services.player_identity_service import (
    PlayerEntitlementExistsError,
    PlayerEntitlementRequiredError,
    PlayerIdentityPolicyError,
    activate_football_player,
    issue_football_player_entitlement,
    prepare_football_player_enrollment,
    unlock_football_player,
    update_identity_profile,
)
from app.services.program_eligibility_service import record_guardian_consent


def _player(db, *, email: str, dob: date | None, credits: int = 500) -> User:
    user = User(
        name="PC1 Player",
        email=email,
        password_hash="test-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=dob,
        credit_balance=credits,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_unlock_creates_one_canonical_entitlement_and_season_assignment(test_db):
    player = _player(test_db, email="pc1-adult@example.test", dob=date(2000, 1, 1))

    result = unlock_football_player(test_db, user=player, duration_months=1, on_date=date(2026, 9, 10))
    test_db.commit()

    assert result.created is True
    assert result.license.canonical_program_id == "LFA_FOOTBALL_PLAYER"
    assert result.license.specialization_type == "LFA_FOOTBALL_PLAYER"
    assert result.assignment.season_start == date(2026, 7, 1)
    assert result.assignment.season_base_category == "AMATEUR"
    assert result.assignment.effective_category == "AMATEUR"
    assert player.specialization is SpecializationType.LFA_FOOTBALL_PLAYER
    assert player.credit_balance == 400
    assert test_db.query(UserLicense).filter_by(user_id=player.id).count() == 1
    assert test_db.query(FootballSeasonCategoryAssignment).filter_by(player_user_id=player.id).count() == 1
    transaction = test_db.query(CreditTransaction).filter_by(user_id=player.id).one()
    assert transaction.amount == -100
    assert transaction.context_user_license_id == result.license.id


def test_unlock_minor_without_consent_rolls_back_entitlement_and_credit(test_db):
    player = _player(test_db, email="pc1-minor-denied@example.test", dob=date(2012, 1, 1))

    with pytest.raises(PlayerIdentityPolicyError) as exc:
        unlock_football_player(test_db, user=player, duration_months=1, on_date=date(2026, 9, 10))
    test_db.rollback()

    assert exc.value.code == "GUARDIAN_CONSENT_REQUIRED"
    assert test_db.query(UserLicense).filter_by(user_id=player.id).count() == 0
    assert test_db.query(CreditTransaction).filter_by(user_id=player.id).count() == 0
    assert test_db.get(User, player.id).credit_balance == 500


def test_minor_with_canonical_consent_can_unlock_and_activate(test_db):
    player = _player(test_db, email="pc1-minor-allowed@example.test", dob=date(2012, 1, 1))
    record_guardian_consent(test_db, user=player, guardian_name="PC1 Guardian")
    test_db.commit()

    result = unlock_football_player(test_db, user=player, duration_months=1, on_date=date(2026, 9, 10))
    test_db.commit()
    selected = activate_football_player(test_db, user=player, on_date=date(2026, 9, 10))
    test_db.commit()

    assert result.assignment.season_base_category == "YOUTH"
    assert selected.license.id == result.license.id
    assert selected.assignment.id == result.assignment.id


def test_dob_change_cannot_bypass_consent_or_change_stored_season_base(test_db):
    player = _player(test_db, email="pc1-profile@example.test", dob=date(2000, 1, 1))
    result = unlock_football_player(test_db, user=player, duration_months=1, on_date=date(2026, 9, 10))
    test_db.commit()

    with pytest.raises(PlayerIdentityPolicyError) as exc:
        update_identity_profile(
            test_db,
            user=player,
            date_of_birth=date(2012, 1, 1),
            on_date=date(2026, 9, 10),
        )
    test_db.rollback()

    assert exc.value.code == "GUARDIAN_CONSENT_REQUIRED"
    persisted = test_db.get(User, player.id)
    assignment = test_db.get(FootballSeasonCategoryAssignment, result.assignment.id)
    assert persisted.date_of_birth.date() == date(2000, 1, 1)
    assert assignment.season_base_category == "AMATEUR"


def test_admin_issuance_uses_same_identity_and_assignment_authority_without_credit_write(test_db):
    player = _player(test_db, email="pc1-issued@example.test", dob=date(2000, 1, 1))

    result = issue_football_player_entitlement(
        test_db,
        user=player,
        payment_verified=False,
        on_date=date(2026, 9, 10),
    )
    test_db.commit()

    assert result.license.canonical_program_id == "LFA_FOOTBALL_PLAYER"
    assert result.license.payment_verified is False
    assert result.assignment.season_start == date(2026, 7, 1)
    assert test_db.query(CreditTransaction).filter_by(user_id=player.id).count() == 0


def test_enrollment_preparation_reuses_canonical_entitlement_and_assignment(test_db):
    player = _player(test_db, email="pc1-enrollment@example.test", dob=date(2000, 1, 1))
    unlocked = unlock_football_player(
        test_db,
        user=player,
        duration_months=1,
        on_date=date(2026, 9, 10),
    )
    test_db.commit()

    prepared = prepare_football_player_enrollment(
        test_db,
        user=player,
        user_license=unlocked.license,
        on_date=date(2026, 9, 10),
    )

    assert prepared.license.id == unlocked.license.id
    assert prepared.assignment.id == unlocked.assignment.id
    assert prepared.assignment.effective_category == "AMATEUR"


def test_enrollment_preparation_rejects_another_players_entitlement(test_db):
    owner = _player(test_db, email="pc1-owner@example.test", dob=date(2000, 1, 1))
    attacker = _player(test_db, email="pc1-attacker@example.test", dob=date(2000, 1, 1))
    unlocked = unlock_football_player(
        test_db,
        user=owner,
        duration_months=1,
        on_date=date(2026, 9, 10),
    )
    test_db.commit()

    with pytest.raises(PlayerEntitlementRequiredError) as exc:
        prepare_football_player_enrollment(
            test_db,
            user=attacker,
            user_license=unlocked.license,
            on_date=date(2026, 9, 10),
        )
    test_db.rollback()

    assert exc.value.code == "ACTIVE_FOOTBALL_ENTITLEMENT_REQUIRED"


def test_concurrent_unlock_creates_exactly_one_entitlement_assignment_and_debit(test_db):
    from app.database import SessionLocal

    seed_db = SessionLocal()
    try:
        player = _player(
            seed_db,
            email=f"pc1-race-{uuid4().hex}@example.com",
            dob=date(2000, 1, 1),
        )
        player_id = player.id
    finally:
        seed_db.close()
    gate = Barrier(2)

    def attempt():
        db = SessionLocal()
        try:
            candidate = db.get(User, player_id)
            gate.wait(timeout=5)
            try:
                unlock_football_player(
                    db,
                    user=candidate,
                    duration_months=1,
                    on_date=date(2026, 9, 10),
                )
                db.commit()
                return "created"
            except PlayerEntitlementExistsError:
                db.rollback()
                return "exists"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: attempt(), range(2)))

    test_db.expire_all()
    assert outcomes == ["created", "exists"]
    assert test_db.query(UserLicense).filter_by(user_id=player_id).count() == 1
    assert test_db.query(FootballSeasonCategoryAssignment).filter_by(player_user_id=player_id).count() == 1
    assert test_db.query(CreditTransaction).filter_by(user_id=player_id).count() == 1
    assert test_db.get(User, player_id).credit_balance == 400

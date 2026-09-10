"""WS1 entitlement identity and guardian-consent evidence on PostgreSQL."""
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models.license import UserLicense
from app.models.user import User, UserRole
from app.models.ws1_domain import UserGuardianConsent
from app.services.canonical_policy import CanonicalProgram
from app.services.program_eligibility_service import (
    is_user_eligible_for_program,
    record_guardian_consent,
)


pytestmark = pytest.mark.postgres


def _user(db, dob: date) -> User:
    user = User(
        name="WS1 Identity",
        email=f"ws1-identity-{uuid4().hex}@ws1.example.com",
        password_hash="test-only-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=dob,
    )
    db.add(user)
    db.flush()
    return user


@pytest.mark.parametrize(
    ("source", "canonical"),
    [("PLAYER", "GANCUJU_PLAYER"), ("COACH", "LFA_COACH"), ("INTERNSHIP", "INTERNSHIP")],
)
def test_proven_aliases_are_canonicalized_on_new_write(source, canonical):
    db = SessionLocal()
    user = _user(db, date(1990, 1, 1))
    row = UserLicense(
        user_id=user.id, specialization_type=source, current_level=1, max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    assert (row.specialization_type, row.canonical_program_id) == (canonical, canonical)
    db.close()


def test_ambiguous_license_write_fails_closed_and_duplicate_canonical_program_is_rejected():
    db = SessionLocal()
    user = _user(db, date(1990, 1, 1))
    db.add(UserLicense(
        user_id=user.id, specialization_type="LFA_PLAYER_PRE", current_level=1, max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(ValueError, match="four canonical"):
        db.flush()
    db.rollback()

    user = _user(db, date(1990, 1, 1))
    for raw in ("COACH", "LFA_COACH"):
        db.add(UserLicense(
            user_id=user.id, specialization_type=raw, current_level=1, max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
        ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_minor_consent_is_canonical_revocable_history_and_levels_add_no_age_gate():
    db = SessionLocal()
    user = _user(db, date(2012, 7, 1))
    eligible, reason = is_user_eligible_for_program(
        db, user, CanonicalProgram.LFA_COACH, on_date=date(2026, 7, 1)
    )
    assert (eligible, reason) == (False, "GUARDIAN_CONSENT_REQUIRED")

    first = record_guardian_consent(db, user=user, guardian_name="First Guardian")
    db.flush()
    eligible, reason = is_user_eligible_for_program(
        db, user, CanonicalProgram.LFA_COACH, on_date=date(2026, 7, 1)
    )
    assert (eligible, reason) == (True, None)

    second = record_guardian_consent(db, user=user, guardian_name="Replacement Guardian")
    db.commit()
    assert first.revoked_at is not None
    assert second.revoked_at is None
    assert db.query(UserGuardianConsent).filter_by(user_id=user.id, revoked_at=None).count() == 1
    assert is_user_eligible_for_program(
        db, user, CanonicalProgram.LFA_COACH, on_date=date(2026, 7, 1)
    ) == (True, None)
    db.close()

"""Persistence adapter for canonical profile and program eligibility policy."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.ws1_domain import UserGuardianConsent
from app.services.canonical_policy import (
    CanonicalProgram,
    ProfileAgeDecision,
    evaluate_profile_age_policy,
    is_program_age_eligible,
    resolve_program_id,
)


def has_active_guardian_consent(db: Session | None, user_id: int) -> bool:
    if db is None:
        return False
    return (
        db.query(UserGuardianConsent.id)
        .filter(
            UserGuardianConsent.user_id == user_id,
            UserGuardianConsent.revoked_at.is_(None),
        )
        .first()
        is not None
    )


def evaluate_user_profile(
    db: Session | None,
    user: User,
    *,
    on_date: date | None = None,
    biometric: bool = False,
) -> ProfileAgeDecision:
    consent = bool(user.id) and has_active_guardian_consent(db, user.id)
    return evaluate_profile_age_policy(
        user.date_of_birth,
        consent,
        on_date=on_date,
        biometric=biometric,
    )


def is_user_eligible_for_program(
    db: Session | None,
    user: User,
    program_id: object,
    *,
    on_date: date | None = None,
) -> tuple[bool, str | None]:
    resolution = resolve_program_id(program_id)
    if not resolution.usable:
        return False, "PROGRAM_ID_MANUAL_REVIEW_OR_INVALID"
    profile = evaluate_user_profile(db, user, on_date=on_date)
    if not profile.usable:
        return False, profile.reason
    if not is_program_age_eligible(resolution.canonical_program, profile.age):
        return False, "PROGRAM_MINIMUM_AGE"
    return True, None


def record_guardian_consent(
    db: Session,
    *,
    user: User,
    guardian_name: str,
    granted_by_user_id: int | None = None,
    guardian_relationship: str | None = None,
    evidence_reference: str | None = None,
) -> UserGuardianConsent:
    if not guardian_name.strip():
        raise ValueError("Guardian name is required")
    # Serialize consent replacement and preserve the previous record as revoked
    # history. The partial unique index guarantees one active row.
    db.query(User).filter(User.id == user.id).with_for_update().one()
    now = datetime.now(timezone.utc)
    active = (
        db.query(UserGuardianConsent)
        .filter(
            UserGuardianConsent.user_id == user.id,
            UserGuardianConsent.revoked_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if active is not None:
        active.revoked_at = now
        active.revoked_by_user_id = granted_by_user_id
        active.revocation_reason = "SUPERSEDED_BY_NEW_CONSENT"
    consent = UserGuardianConsent(
        user_id=user.id,
        guardian_name=guardian_name.strip(),
        guardian_relationship=guardian_relationship,
        evidence_reference=evidence_reference,
        granted_by_user_id=granted_by_user_id,
        granted_at=now,
    )
    db.add(consent)
    # Legacy fields are maintained as a compatibility projection only.
    user.parental_consent = True
    user.parental_consent_at = consent.granted_at
    user.parental_consent_by = consent.guardian_name
    return consent


def canonical_program_or_error(program_id: object) -> CanonicalProgram:
    resolution = resolve_program_id(program_id)
    if not resolution.usable:
        raise ValueError("Program identity requires manual review or is invalid")
    return resolution.canonical_program

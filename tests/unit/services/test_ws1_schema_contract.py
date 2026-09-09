from app.models.credit_transaction import CreditTransaction
from app.models.license import UserLicense
from app.models.semester_enrollment import SemesterEnrollment
from app.models.ws1_domain import (
    FootballCategoryMovementEvent,
    FootballSeasonCategoryAssignment,
    UserGuardianConsent,
)


def test_ws1_additive_columns_keep_legacy_fields():
    assert "canonical_program_id" in UserLicense.__table__.columns
    assert "credit_balance" in UserLicense.__table__.columns
    assert "football_category_assignment_id" in SemesterEnrollment.__table__.columns
    assert "age_category" in SemesterEnrollment.__table__.columns
    assert "context_user_license_id" in CreditTransaction.__table__.columns
    assert "user_license_id" in CreditTransaction.__table__.columns


def test_ws1_tables_have_required_audit_and_concurrency_fields():
    consent = UserGuardianConsent.__table__.columns
    assignment = FootballSeasonCategoryAssignment.__table__.columns
    event = FootballCategoryMovementEvent.__table__.columns
    for name in ("user_id", "guardian_name", "granted_at", "revoked_at"):
        assert name in consent
    for name in (
        "player_user_id", "season_start", "season_end", "season_base_category",
        "effective_category", "version",
    ):
        assert name in assignment
    for name in (
        "assignment_id", "player_user_id", "season_base_category",
        "previous_effective_category", "target_effective_category",
        "base_participation_retained", "actor_user_id", "reason",
        "idempotency_key", "created_at",
    ):
        assert name in event


def test_ws1_partial_uniqueness_and_history_preservation_contracts():
    license_indexes = {index.name: index for index in UserLicense.__table__.indexes}
    consent_indexes = {index.name: index for index in UserGuardianConsent.__table__.indexes}
    assert license_indexes["uq_user_license_user_canonical_program"].unique is True
    assert consent_indexes["uq_user_guardian_consents_one_active"].unique is True
    legacy_owner_fk = next(iter(CreditTransaction.__table__.c.user_license_id.foreign_keys))
    assignment_fk = next(iter(SemesterEnrollment.__table__.c.football_category_assignment_id.foreign_keys))
    assert legacy_owner_fk.ondelete == "RESTRICT"
    assert assignment_fk.ondelete == "RESTRICT"

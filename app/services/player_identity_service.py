"""Canonical Player identity, entitlement and profile command boundary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.credit_transaction import TransactionType
from app.models.license import UserLicense
from app.models.specialization import SpecializationType
from app.models.user import User
from app.models.ws1_domain import FootballSeasonCategoryAssignment
from app.services.canonical_policy import (
    CanonicalProgram,
    ProfileAgeDecision,
    evaluate_profile_age_policy,
    football_base_category,
    football_season,
    is_program_age_eligible,
    resolve_license_program,
)
from app.services.credit_service import CreditService
from app.services.football_category_movement_service import (
    FootballMovementError,
    get_or_create_current_football_assignment,
)
from app.services.licence_package import (
    calculate_expires_at,
    cost_for_duration,
    is_licence_expired,
    validate_duration_months,
)
from app.services.program_eligibility_service import (
    has_active_guardian_consent,
    is_user_eligible_for_program,
)


class PlayerIdentityError(ValueError):
    code = "PLAYER_IDENTITY_INVALID"

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PlayerIdentityPolicyError(PlayerIdentityError):
    pass


class PlayerEntitlementExistsError(PlayerIdentityError):
    def __init__(self):
        super().__init__("FOOTBALL_ENTITLEMENT_ALREADY_EXISTS")


class PlayerEntitlementRequiredError(PlayerIdentityError):
    def __init__(self):
        super().__init__("ACTIVE_FOOTBALL_ENTITLEMENT_REQUIRED")


class PlayerEntitlementConflictError(PlayerIdentityError):
    def __init__(self):
        super().__init__("PROGRAM_ID_MANUAL_REVIEW_OR_INVALID")


@dataclass(frozen=True)
class PlayerEntitlementResult:
    license: UserLicense
    assignment: FootballSeasonCategoryAssignment
    created: bool
    cost: int
    duration_months: int | None


def _lock_user(db: Session, user: User) -> User:
    return (
        db.query(User)
        .filter(User.id == user.id)
        .with_for_update()
        .one()
    )


def _resolved_licenses(db: Session, user_id: int) -> list[tuple[UserLicense, object]]:
    resolved: list[tuple[UserLicense, object]] = []
    for row in db.query(UserLicense).filter(UserLicense.user_id == user_id).all():
        resolution = resolve_license_program(row.canonical_program_id, row.specialization_type)
        if not resolution.usable:
            raise PlayerEntitlementConflictError()
        resolved.append((row, resolution))
    return resolved


def _football_license(db: Session, user_id: int) -> UserLicense | None:
    matches = [
        row
        for row, resolution in _resolved_licenses(db, user_id)
        if resolution.canonical_program is CanonicalProgram.LFA_FOOTBALL_PLAYER
    ]
    if len(matches) > 1:
        raise PlayerEntitlementConflictError()
    return matches[0] if matches else None


def _active_football_license(db: Session, user_id: int) -> UserLicense | None:
    row = _football_license(db, user_id)
    if row is None or not row.is_active or is_licence_expired(row):
        return None
    return row


def get_active_football_entitlement(
    db: Session,
    *,
    user_id: int,
) -> UserLicense | None:
    """Read the single active canonical Player entitlement, failing on ambiguity."""
    return _active_football_license(db, user_id)


def get_current_player_assignment(
    db: Session,
    *,
    user_id: int,
    on_date: date | None = None,
) -> FootballSeasonCategoryAssignment | None:
    season = football_season(on_date or date.today())
    return (
        db.query(FootballSeasonCategoryAssignment)
        .filter(
            FootballSeasonCategoryAssignment.player_user_id == user_id,
            FootballSeasonCategoryAssignment.season_start == season.start,
        )
        .first()
    )


def get_authorized_football_player_context(
    db: Session,
    *,
    user: User,
    on_date: date | None = None,
) -> PlayerEntitlementResult:
    """Read an eligible Player's active entitlement and current assignment.

    This read boundary never creates historical or current-season data. Missing
    assignment truth is therefore reported explicitly instead of reconstructed
    from DOB or a legacy enrollment projection.
    """
    eligible, denial_reason = is_user_eligible_for_program(
        db,
        user,
        CanonicalProgram.LFA_FOOTBALL_PLAYER,
        on_date=on_date,
    )
    if not eligible:
        raise PlayerIdentityPolicyError(
            denial_reason or "CANONICAL_ELIGIBILITY_DENIED"
        )
    license_row = _active_football_license(db, user.id)
    if license_row is None:
        raise PlayerEntitlementRequiredError()
    assignment = get_current_player_assignment(
        db,
        user_id=user.id,
        on_date=on_date,
    )
    if assignment is None:
        raise PlayerIdentityPolicyError("CURRENT_SEASON_ASSIGNMENT_REQUIRED")
    return PlayerEntitlementResult(license_row, assignment, False, 0, None)


def update_identity_profile(
    db: Session,
    *,
    user: User,
    date_of_birth: date | datetime | None,
    on_date: date | None = None,
) -> ProfileAgeDecision:
    """Validate and update DOB without trusting legacy consent projections."""
    locked = _lock_user(db, user)
    consent_active = has_active_guardian_consent(db, locked.id)
    decision = evaluate_profile_age_policy(
        date_of_birth,
        consent_active,
        on_date=on_date,
    )
    if not decision.usable:
        raise PlayerIdentityPolicyError(decision.reason or "PROFILE_IDENTITY_INVALID")

    for license_row, resolution in _resolved_licenses(db, locked.id):
        if license_row.is_active and not is_program_age_eligible(
            resolution.canonical_program,
            decision.age,
        ):
            raise PlayerIdentityPolicyError("PROGRAM_MINIMUM_AGE")

    current_assignment = get_current_player_assignment(
        db,
        user_id=locked.id,
        on_date=on_date,
    )
    if current_assignment is not None:
        candidate_base = football_base_category(
            date_of_birth,
            season_start=current_assignment.season_start,
        )
        if (
            candidate_base is None
            or candidate_base.value != current_assignment.season_base_category
        ):
            raise PlayerIdentityPolicyError("SEASON_BASE_CATEGORY_CONFLICT")

    locked.date_of_birth = date_of_birth
    # These columns remain compatibility projections. Evidence is authoritative.
    locked.parental_consent = consent_active
    if not consent_active:
        locked.parental_consent_at = None
        locked.parental_consent_by = None
    db.flush()
    return decision


def unlock_football_player(
    db: Session,
    *,
    user: User,
    duration_months: int,
    on_date: date | None = None,
) -> PlayerEntitlementResult:
    """Create Player entitlement, debit and season assignment in one transaction."""
    validate_duration_months(duration_months)
    locked = _lock_user(db, user)
    eligible, denial_reason = is_user_eligible_for_program(
        db,
        locked,
        CanonicalProgram.LFA_FOOTBALL_PLAYER,
        on_date=on_date,
    )
    if not eligible:
        raise PlayerIdentityPolicyError(denial_reason or "CANONICAL_ELIGIBILITY_DENIED")
    if _football_license(db, locked.id) is not None:
        raise PlayerEntitlementExistsError()

    now = datetime.now(timezone.utc)
    cost = cost_for_duration(duration_months)
    license_row = UserLicense(
        user_id=locked.id,
        specialization_type=CanonicalProgram.LFA_FOOTBALL_PLAYER.value,
        canonical_program_id=CanonicalProgram.LFA_FOOTBALL_PLAYER.value,
        current_level=1,
        max_achieved_level=1,
        started_at=now,
        payment_verified=True,
        payment_verified_at=now,
        onboarding_completed=False,
        is_active=True,
        expires_at=calculate_expires_at(now, duration_months),
    )
    db.add(license_row)
    db.flush()
    try:
        assignment = get_or_create_current_football_assignment(
            db,
            player=locked,
            user_license=license_row,
            on_date=on_date,
        )
    except FootballMovementError as exc:
        raise PlayerIdentityPolicyError(exc.code) from exc

    description = (
        "Unlocked specialization: LFA_FOOTBALL_PLAYER "
        f"({duration_months} month{'s' if duration_months > 1 else ''})"
    )
    transaction = CreditService(db).deduct(
        user=locked,
        amount=cost,
        transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
        description=description,
        idempotency_key=f"license_unlock_{license_row.id}",
    )
    transaction.context_user_license_id = license_row.id
    locked.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    db.flush()
    return PlayerEntitlementResult(license_row, assignment, True, cost, duration_months)


def issue_football_player_entitlement(
    db: Session,
    *,
    user: User,
    payment_verified: bool,
    on_date: date | None = None,
) -> PlayerEntitlementResult:
    """Issue an admin-provisioned Player entitlement through the canonical boundary.

    This command never changes credits. Its caller owns the surrounding transaction.
    """
    locked = _lock_user(db, user)
    eligible, denial_reason = is_user_eligible_for_program(
        db,
        locked,
        CanonicalProgram.LFA_FOOTBALL_PLAYER,
        on_date=on_date,
    )
    if not eligible:
        raise PlayerIdentityPolicyError(denial_reason or "CANONICAL_ELIGIBILITY_DENIED")
    if _football_license(db, locked.id) is not None:
        raise PlayerEntitlementExistsError()

    now = datetime.now(timezone.utc)
    license_row = UserLicense(
        user_id=locked.id,
        specialization_type=CanonicalProgram.LFA_FOOTBALL_PLAYER.value,
        canonical_program_id=CanonicalProgram.LFA_FOOTBALL_PLAYER.value,
        current_level=1,
        max_achieved_level=1,
        started_at=now,
        payment_verified=payment_verified,
        payment_verified_at=now if payment_verified else None,
        onboarding_completed=False,
        is_active=True,
    )
    db.add(license_row)
    db.flush()
    try:
        assignment = get_or_create_current_football_assignment(
            db,
            player=locked,
            user_license=license_row,
            on_date=on_date,
        )
    except FootballMovementError as exc:
        raise PlayerIdentityPolicyError(exc.code) from exc
    locked.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    db.flush()
    return PlayerEntitlementResult(license_row, assignment, True, 0, None)


def activate_football_player(
    db: Session,
    *,
    user: User,
    on_date: date | None = None,
) -> PlayerEntitlementResult:
    """Select an existing Player entitlement and ensure its season truth exists."""
    locked = _lock_user(db, user)
    eligible, denial_reason = is_user_eligible_for_program(
        db,
        locked,
        CanonicalProgram.LFA_FOOTBALL_PLAYER,
        on_date=on_date,
    )
    if not eligible:
        raise PlayerIdentityPolicyError(denial_reason or "CANONICAL_ELIGIBILITY_DENIED")
    license_row = _active_football_license(db, locked.id)
    if license_row is None:
        raise PlayerEntitlementRequiredError()
    try:
        assignment = get_or_create_current_football_assignment(
            db,
            player=locked,
            user_license=license_row,
            on_date=on_date,
        )
    except FootballMovementError as exc:
        raise PlayerIdentityPolicyError(exc.code) from exc
    locked.specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    db.flush()
    return PlayerEntitlementResult(license_row, assignment, False, 0, None)


def prepare_football_player_enrollment(
    db: Session,
    *,
    user: User,
    user_license: UserLicense,
    on_date: date | None = None,
) -> PlayerEntitlementResult:
    """Authorize enrollment from the canonical Player entitlement and identity."""
    locked = _lock_user(db, user)
    eligible, denial_reason = is_user_eligible_for_program(
        db,
        locked,
        CanonicalProgram.LFA_FOOTBALL_PLAYER,
        on_date=on_date,
    )
    if not eligible:
        raise PlayerIdentityPolicyError(denial_reason or "CANONICAL_ELIGIBILITY_DENIED")

    resolution = resolve_license_program(
        user_license.canonical_program_id,
        user_license.specialization_type,
    )
    if (
        user_license.user_id != locked.id
        or not resolution.usable
        or resolution.canonical_program is not CanonicalProgram.LFA_FOOTBALL_PLAYER
        or not user_license.is_active
        or is_licence_expired(user_license)
    ):
        raise PlayerEntitlementRequiredError()

    canonical_license = _active_football_license(db, locked.id)
    if canonical_license is None or canonical_license.id != user_license.id:
        raise PlayerEntitlementRequiredError()
    try:
        assignment = get_or_create_current_football_assignment(
            db,
            player=locked,
            user_license=canonical_license,
            on_date=on_date,
        )
    except FootballMovementError as exc:
        raise PlayerIdentityPolicyError(exc.code) from exc
    return PlayerEntitlementResult(canonical_license, assignment, False, 0, None)

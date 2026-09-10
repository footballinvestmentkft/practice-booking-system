"""Transactional command service for football effective-category movement."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.instructor_assignment import InstructorAssignment
from app.models.license import UserLicense
from app.models.semester_enrollment import SemesterEnrollment
from app.models.user import User, UserRole
from app.models.ws1_domain import (
    FootballCategoryMovementEvent,
    FootballSeasonCategoryAssignment,
)
from app.services.canonical_policy import (
    AgeCategory,
    CanonicalProgram,
    CoachRole,
    calculate_age,
    coach_can_teach,
    football_base_category,
    football_season,
    resolve_program_id,
    resolve_license_program,
    validate_football_movement,
)
from app.services.program_eligibility_service import is_user_eligible_for_program


class FootballMovementError(ValueError):
    code = "FOOTBALL_MOVEMENT_INVALID"


class FootballMovementAuthorizationError(FootballMovementError):
    code = "FOOTBALL_MOVEMENT_FORBIDDEN"


class FootballMovementConcurrencyError(FootballMovementError):
    code = "FOOTBALL_MOVEMENT_VERSION_CONFLICT"


class FootballMovementReplayConflict(FootballMovementError):
    code = "FOOTBALL_MOVEMENT_REPLAY_CONFLICT"


@dataclass(frozen=True)
class MovementResult:
    assignment: FootballSeasonCategoryAssignment
    event: FootballCategoryMovementEvent
    created: bool


def _lock_key(db: Session, key: str) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": key},
        )


def _coach_license(db: Session, actor_id: int) -> Optional[UserLicense]:
    candidates = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == actor_id, UserLicense.is_active.is_(True))
        .all()
    )
    for license_row in candidates:
        resolution = resolve_license_program(
            license_row.canonical_program_id,
            license_row.specialization_type,
        )
        if resolution.usable and resolution.canonical_program is CanonicalProgram.LFA_COACH:
            return license_row
    return None


def _authorize(
    db: Session,
    actor: User,
    enrollment: SemesterEnrollment,
    target: AgeCategory,
    *,
    downward: bool,
) -> tuple[str, Optional[int]]:
    if actor.id == enrollment.user_id:
        raise FootballMovementAuthorizationError("Player self-service movement is forbidden")
    if actor.role is UserRole.ADMIN:
        return "ADMIN_OVERRIDE", None
    if downward:
        raise FootballMovementAuthorizationError("Downward movement requires admin override")
    if actor.role is not UserRole.INSTRUCTOR:
        raise FootballMovementAuthorizationError("Only an authorized Head Coach or admin may move a player")

    coach_license = _coach_license(db, actor.id)
    if coach_license is None or not coach_can_teach(coach_license.current_level, target, CoachRole.HEAD):
        raise FootballMovementAuthorizationError("Target-qualified Head Coach license required")

    semester = enrollment.semester
    if semester is not None and semester.master_instructor_id == actor.id:
        return "SEMESTER_HEAD_COACH", None

    if semester is None or semester.location_id is None:
        raise FootballMovementAuthorizationError("Instructor has no assignment for this enrollment")
    assignment = (
        db.query(InstructorAssignment)
        .filter(
            InstructorAssignment.instructor_id == actor.id,
            InstructorAssignment.location_id == semester.location_id,
            InstructorAssignment.is_active.is_(True),
            InstructorAssignment.is_master.is_(True),
            InstructorAssignment.age_group == target.value,
        )
        .first()
    )
    if assignment is None:
        raise FootballMovementAuthorizationError("Instructor is outside the target enrollment scope")
    return "LOCATION_HEAD_COACH_ASSIGNMENT", assignment.id


def get_or_create_current_football_assignment(
    db: Session,
    *,
    player: User,
    user_license: UserLicense,
    on_date: Optional[date] = None,
) -> FootballSeasonCategoryAssignment:
    """Create current-season truth only during an explicit current operation."""
    if player.date_of_birth is None:
        raise FootballMovementError("Player date of birth is required")
    eligible, denial_reason = is_user_eligible_for_program(
        db, player, CanonicalProgram.LFA_FOOTBALL_PLAYER, on_date=on_date
    )
    if not eligible:
        raise FootballMovementError(denial_reason or "Player is not eligible for football")
    current_date = on_date or date.today()
    season = football_season(current_date)
    base = football_base_category(player.date_of_birth, season_start=season.start)
    if base is None:
        raise FootballMovementError("Player does not meet minimum football age")
    _lock_key(db, f"football-assignment:{player.id}:{season.start.isoformat()}")
    assignment = (
        db.query(FootballSeasonCategoryAssignment)
        .filter(
            FootballSeasonCategoryAssignment.player_user_id == player.id,
            FootballSeasonCategoryAssignment.season_start == season.start,
        )
        .with_for_update()
        .first()
    )
    if assignment is None:
        assignment = FootballSeasonCategoryAssignment(
            player_user_id=player.id,
            user_license_id=user_license.id,
            season_start=season.start,
            season_end=season.end,
            season_base_category=base.value,
            effective_category=base.value,
            base_participation_retained=False,
            version=1,
        )
        db.add(assignment)
        db.flush()
    elif assignment.season_base_category != base.value:
        raise FootballMovementError("Stored season base conflicts with canonical DOB calculation")
    return assignment


def move_football_category(
    db: Session,
    *,
    enrollment_id: int,
    actor: User,
    target_category: AgeCategory | str,
    expected_version: int,
    base_participation_retained: bool,
    reason: str,
    idempotency_key: str,
    source: str = "CANONICAL_COMMAND",
    context: Optional[dict] = None,
    on_date: Optional[date] = None,
) -> MovementResult:
    if not reason.strip():
        raise FootballMovementError("Movement reason is required")
    if not idempotency_key.strip():
        raise FootballMovementError("Idempotency key is required")
    if not source.strip():
        raise FootballMovementError("Movement source is required")
    try:
        target = AgeCategory(target_category)
    except ValueError as exc:
        raise FootballMovementError("Invalid football category") from exc

    _lock_key(db, f"football-movement:{idempotency_key}")
    replay = db.query(FootballCategoryMovementEvent).filter(
        FootballCategoryMovementEvent.idempotency_key == idempotency_key
    ).first()
    if replay is not None:
        if (
            replay.source_enrollment_id != enrollment_id
            or replay.actor_user_id != actor.id
            or replay.target_effective_category != target.value
            or replay.base_participation_retained != base_participation_retained
            or replay.reason != reason.strip()
            or replay.source != source.strip()
        ):
            raise FootballMovementReplayConflict("Idempotency key was reused for a different command")
        return MovementResult(replay.assignment, replay, False)

    enrollment = (
        db.query(SemesterEnrollment)
        .filter(SemesterEnrollment.id == enrollment_id)
        .with_for_update()
        .first()
    )
    if enrollment is None:
        raise FootballMovementError("Enrollment not found")
    license_resolution = resolve_license_program(
        enrollment.user_license.canonical_program_id,
        enrollment.user_license.specialization_type,
    )
    if (
        not license_resolution.usable
        or license_resolution.canonical_program is not CanonicalProgram.LFA_FOOTBALL_PLAYER
    ):
        raise FootballMovementError("Enrollment is not a canonical football-player enrollment")
    player = enrollment.user
    current_date = on_date or date.today()
    assignment = get_or_create_current_football_assignment(
        db, player=player, user_license=enrollment.user_license, on_date=current_date
    )
    base = AgeCategory(assignment.season_base_category)

    if assignment.version != expected_version:
        raise FootballMovementConcurrencyError(
            f"Expected version {expected_version}, current version is {assignment.version}"
        )
    previous = AgeCategory(assignment.effective_category)
    downward = list(AgeCategory).index(target) < list(AgeCategory).index(previous)
    authorization_basis, instructor_assignment_id = _authorize(
        db, actor, enrollment, target, downward=downward
    )
    decision = validate_football_movement(
        base,
        previous,
        target,
        current_age=calculate_age(player.date_of_birth, current_date),
        base_participation_retained=base_participation_retained,
    )
    if not decision.allowed:
        raise FootballMovementError(decision.reason or "Movement denied")
    if target is base:
        base_participation_retained = False

    assignment.effective_category = target.value
    assignment.base_participation_retained = base_participation_retained
    assignment.version += 1
    enrollment.football_category_assignment_id = assignment.id
    # Existing consumers read this as a projection. It is no longer the authority.
    enrollment.age_category = target.value
    enrollment.age_category_overridden = True
    enrollment.age_category_overridden_at = datetime.now(timezone.utc)
    enrollment.age_category_overridden_by = actor.id

    event = FootballCategoryMovementEvent(
        assignment_id=assignment.id,
        player_user_id=player.id,
        season_start=assignment.season_start,
        season_end=assignment.season_end,
        season_base_category=base.value,
        previous_effective_category=previous.value,
        target_effective_category=target.value,
        base_participation_retained=base_participation_retained,
        event_type="PROMOTION" if list(AgeCategory).index(target) > list(AgeCategory).index(previous) else "REVOCATION",
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        authorization_basis=authorization_basis,
        source=source.strip(),
        instructor_assignment_id=instructor_assignment_id,
        source_enrollment_id=enrollment.id,
        reason=reason.strip(),
        context=context,
        idempotency_key=idempotency_key.strip(),
        assignment_version=assignment.version,
    )
    db.add(event)
    db.flush()
    return MovementResult(assignment, event, True)

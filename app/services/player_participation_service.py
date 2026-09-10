"""Canonical Player session participation policy and transactional commands."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.attendance import (
    Attendance,
    AttendanceHistory,
    AttendanceStatus,
    ConfirmationStatus,
)
from app.models.audit_log import AuditLog
from app.models.booking import Booking, BookingStatus
from app.models.license import UserLicense
from app.models.semester_enrollment import EnrollmentStatus, SemesterEnrollment
from app.models.session import EventCategory, Session as SessionModel, SessionType
from app.models.specialization import SpecializationType
from app.models.user import User, UserRole
from app.services.canonical_policy import (
    CanonicalProgram,
    coach_can_teach,
    coach_level_scope,
    football_participation_categories,
    resolve_license_program,
)
from app.services.licence_package import is_licence_expired
from app.services.player_identity_service import (
    PlayerIdentityError,
    get_authorized_football_player_context,
)


BOOKING_LEAD_TIME = timedelta(hours=24)
CANCELLATION_LEAD_TIME = timedelta(hours=12)
ATTENDANCE_OPEN_LEAD_TIME = timedelta(minutes=15)
BUSINESS_TIMEZONE = ZoneInfo("Europe/Budapest")


class ParticipationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None


@dataclass(frozen=True)
class BookingCommandResult:
    booking: Booking
    replayed: bool = False
    promoted_booking_id: int | None = None


@dataclass(frozen=True)
class AttendanceCommandResult:
    attendance: Attendance
    replayed: bool = False


@dataclass(frozen=True)
class AttendanceResponseResult:
    attendance: Attendance
    outcome: str
    replayed: bool = False


def _business_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(BUSINESS_TIMEZONE).replace(tzinfo=None)


def _now(value: datetime | None = None) -> datetime:
    return _business_time(value or datetime.now(timezone.utc))


def _role_value(actor: Any) -> str | None:
    role = getattr(actor, "role", None)
    return getattr(role, "value", role)


def booking_window_decision(session: SessionModel, now: datetime | None = None) -> PolicyDecision:
    if getattr(session, "session_status", None) != "scheduled":
        return PolicyDecision(False, "SESSION_NOT_BOOKABLE")
    if getattr(session, "event_category", None) == EventCategory.MATCH:
        return PolicyDecision(False, "SESSION_NOT_PLAYER_TRAINING")
    if _now(now) >= _business_time(session.date_start) - BOOKING_LEAD_TIME:
        return PolicyDecision(False, "BOOKING_DEADLINE_PASSED")
    return PolicyDecision(True)


def cancellation_window_decision(session: SessionModel, now: datetime | None = None) -> PolicyDecision:
    if getattr(session, "session_status", None) != "scheduled":
        return PolicyDecision(False, "SESSION_NOT_CANCELLABLE")
    if _now(now) >= _business_time(session.date_start) - CANCELLATION_LEAD_TIME:
        return PolicyDecision(False, "CANCELLATION_DEADLINE_PASSED")
    return PolicyDecision(True)


def attendance_window_decision(session: SessionModel, now: datetime | None = None) -> PolicyDecision:
    if getattr(session, "session_status", None) == "cancelled":
        return PolicyDecision(False, "SESSION_CANCELLED")
    if _now(now) < _business_time(session.date_start) - ATTENDANCE_OPEN_LEAD_TIME:
        return PolicyDecision(False, "ATTENDANCE_WINDOW_NOT_OPEN")
    return PolicyDecision(True)


def session_target_category(session: SessionModel) -> str:
    semester = getattr(session, "semester", None)
    if semester is None:
        raise ParticipationError("SESSION_SEASON_REQUIRED")
    categories = getattr(semester, "age_groups", None)
    if categories:
        normalized = list(dict.fromkeys(str(value) for value in categories))
        if len(normalized) != 1:
            raise ParticipationError("SESSION_CATEGORY_AMBIGUOUS")
        return normalized[0]
    category = getattr(semester, "age_group", None)
    if not category:
        raise ParticipationError("SESSION_CATEGORY_REQUIRED")
    return getattr(category, "value", category)


def participation_lifecycle_status(booking: Booking, attendance: Attendance | None = None) -> str:
    if booking.status == BookingStatus.CANCELLED:
        return "cancelled"
    if attendance is not None:
        return "completed"
    if booking.status == BookingStatus.CONFIRMED:
        return "confirmed"
    if booking.status == BookingStatus.WAITLISTED:
        return "waitlisted"
    return "booking"


def player_session_availability(
    db: Session,
    *,
    player: User,
    session: SessionModel,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the canonical eligibility/capacity projection for one Player session."""
    decision = booking_window_decision(session, now)
    if not decision.allowed:
        return {"eligible": False, "reason": decision.code}
    try:
        _player_enrollment(db, player, session)
    except ParticipationError as exc:
        return {"eligible": False, "reason": exc.code}
    booking = db.query(Booking).filter(
        Booking.user_id == player.id,
        Booking.session_id == session.id,
        Booking.status != BookingStatus.CANCELLED,
    ).first()
    confirmed = db.query(func.count(Booking.id)).filter(
        Booking.session_id == session.id,
        Booking.status == BookingStatus.CONFIRMED,
    ).scalar() or 0
    waitlisted = db.query(func.count(Booking.id)).filter(
        Booking.session_id == session.id,
        Booking.status == BookingStatus.WAITLISTED,
    ).scalar() or 0
    available = None if session.capacity is None else max(0, session.capacity - confirmed)
    return {
        "eligible": True,
        "reason": None,
        "category": session_target_category(session),
        "capacity": session.capacity,
        "confirmed": confirmed,
        "available": available,
        "waitlisted": waitlisted,
        "booking_id": booking.id if booking else None,
        "participation_status": (
            participation_lifecycle_status(booking, booking.attendance) if booking else "available"
        ),
    }


def _audit(
    db: Session,
    *,
    action: str,
    actor: User,
    booking: Booking,
    source: str,
    previous: str | None,
    current: str,
    extra: dict[str, Any] | None = None,
) -> None:
    details = {
        "actor_role": _role_value(actor),
        "player_user_id": booking.user_id,
        "session_id": booking.session_id,
        "source": source,
        "previous_status": previous,
        "current_status": current,
    }
    if extra:
        details.update(extra)
    db.add(AuditLog(
        user_id=actor.id,
        action=action,
        resource_type="player_session_participation",
        resource_id=booking.id,
        details=details,
    ))


def _locked_session(db: Session, session_id: int) -> SessionModel:
    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id)
        .with_for_update()
        .first()
    )
    if session is None:
        raise ParticipationError("SESSION_NOT_FOUND")
    return session


def _player_enrollment(db: Session, player: User, session: SessionModel) -> SemesterEnrollment:
    target = getattr(session, "target_specialization", None)
    target_value = getattr(target, "value", target)
    if target_value != SpecializationType.LFA_FOOTBALL_PLAYER.value:
        raise ParticipationError("SESSION_PROGRAM_MISMATCH")
    try:
        context = get_authorized_football_player_context(
            db, user=player, on_date=session.date_start.date()
        )
    except PlayerIdentityError as exc:
        raise ParticipationError(exc.code) from exc
    enrollment = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.user_id == player.id,
            SemesterEnrollment.semester_id == session.semester_id,
            SemesterEnrollment.user_license_id == context.license.id,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            SemesterEnrollment.is_active.is_(True),
            SemesterEnrollment.payment_verified.is_(True),
        )
        .first()
    )
    if enrollment is None:
        raise ParticipationError("ACTIVE_SEASON_ENROLLMENT_REQUIRED")
    assignment = enrollment.football_category_assignment
    if assignment is None or assignment.id != context.assignment.id:
        raise ParticipationError("CANONICAL_SEASON_ASSIGNMENT_REQUIRED")
    session_date = session.date_start.date()
    semester = session.semester
    if not (semester.start_date <= session_date <= semester.end_date):
        raise ParticipationError("SESSION_OUTSIDE_SEASON")
    if not (assignment.season_start <= session_date <= assignment.season_end):
        raise ParticipationError("SESSION_SEASON_MISMATCH")
    target_category = session_target_category(session)
    permitted = football_participation_categories(
        assignment.season_base_category,
        assignment.effective_category,
        base_participation_retained=assignment.base_participation_retained,
    )
    if target_category not in {category.value for category in permitted}:
        raise ParticipationError("SESSION_CATEGORY_MISMATCH")
    return enrollment


def _active_coach_license(db: Session, instructor_id: int) -> UserLicense:
    matches: list[UserLicense] = []
    ambiguous = False
    for row in db.query(UserLicense).filter(
        UserLicense.user_id == instructor_id,
        UserLicense.is_active.is_(True),
    ).all():
        resolution = resolve_license_program(row.canonical_program_id, row.specialization_type)
        if not resolution.usable:
            ambiguous = True
        elif (
            resolution.canonical_program is CanonicalProgram.LFA_COACH
            and not is_licence_expired(row)
        ):
            matches.append(row)
    if ambiguous or len(matches) != 1:
        raise ParticipationError("CANONICAL_COACH_ENTITLEMENT_REQUIRED")
    return matches[0]


def require_session_manager(db: Session, actor: User, session: SessionModel) -> str:
    if _role_value(actor) == UserRole.ADMIN.value:
        return "ADMIN_OVERRIDE"
    if _role_value(actor) != UserRole.INSTRUCTOR.value:
        raise ParticipationError("ATTENDANCE_MANAGER_REQUIRED")
    if session.instructor_id != actor.id:
        raise ParticipationError("INSTRUCTOR_NOT_ASSIGNED")
    license_row = _active_coach_license(db, actor.id)
    try:
        _, coach_role = coach_level_scope(int(license_row.current_level))
        target = session_target_category(session)
    except (TypeError, ValueError) as exc:
        raise ParticipationError("INSTRUCTOR_SCOPE_DENIED") from exc
    if not coach_can_teach(int(license_row.current_level), target, coach_role):
        raise ParticipationError("INSTRUCTOR_SCOPE_DENIED")
    return f"WS1_COACH_MATRIX:{license_row.current_level}:{coach_role.value}"


def book_player_session(
    db: Session,
    *,
    player: User,
    session_id: int,
    notes: str | None = None,
    now: datetime | None = None,
    source: str = "API",
) -> BookingCommandResult:
    try:
        if _role_value(player) != UserRole.STUDENT.value:
            raise ParticipationError("PLAYER_ROLE_REQUIRED")
        session = _locked_session(db, session_id)
        decision = booking_window_decision(session, now)
        if not decision.allowed:
            raise ParticipationError(decision.code or "SESSION_NOT_BOOKABLE")
        enrollment = _player_enrollment(db, player, session)
        result = _stage_player_booking(
            db,
            player=player,
            session=session,
            enrollment=enrollment,
            notes=notes,
            source=source,
        )
        if result.replayed:
            db.rollback()
            return result
        db.commit()
        db.refresh(result.booking)
        return result
    except IntegrityError as exc:
        db.rollback()
        existing = db.query(Booking).filter(
            Booking.user_id == player.id,
            Booking.session_id == session_id,
            Booking.status != BookingStatus.CANCELLED,
        ).first()
        if existing is not None:
            return BookingCommandResult(existing, replayed=True)
        raise ParticipationError("BOOKING_CONCURRENCY_CONFLICT") from exc
    except Exception:
        db.rollback()
        raise


def _stage_player_booking(
    db: Session,
    *,
    player: User,
    session: SessionModel,
    enrollment: SemesterEnrollment,
    notes: str | None,
    source: str,
) -> BookingCommandResult:
    existing = db.query(Booking).filter(
        Booking.user_id == player.id,
        Booking.session_id == session.id,
        Booking.status != BookingStatus.CANCELLED,
    ).first()
    if existing is not None:
        return BookingCommandResult(existing, replayed=True)
    confirmed = db.query(func.count(Booking.id)).filter(
        Booking.session_id == session.id,
        Booking.status == BookingStatus.CONFIRMED,
    ).scalar() or 0
    if session.capacity is not None and confirmed >= session.capacity:
        status = BookingStatus.WAITLISTED
        waitlist_position = (db.query(func.count(Booking.id)).filter(
            Booking.session_id == session.id,
            Booking.status == BookingStatus.WAITLISTED,
        ).scalar() or 0) + 1
    else:
        status = BookingStatus.CONFIRMED
        waitlist_position = None
    booking = Booking(
        user_id=player.id,
        session_id=session.id,
        enrollment_id=enrollment.id,
        status=status,
        waitlist_position=waitlist_position,
        notes=notes,
    )
    db.add(booking)
    db.flush()
    _audit(
        db,
        action="PLAYER_SESSION_BOOKED",
        actor=player,
        booking=booking,
        source=source,
        previous=None,
        current=status.value,
        extra={"enrollment_id": enrollment.id, "category": session_target_category(session)},
    )
    return BookingCommandResult(booking)


def stage_player_enrollment_booking(
    db: Session,
    *,
    player: User,
    session_id: int,
    now: datetime,
    source: str = "WEB_ENROLLMENT",
) -> BookingCommandResult | None:
    """Stage auto-booking inside the caller-owned enrollment transaction."""
    if _role_value(player) != UserRole.STUDENT.value:
        raise ParticipationError("PLAYER_ROLE_REQUIRED")
    session = _locked_session(db, session_id)
    decision = booking_window_decision(session, now)
    if not decision.allowed:
        return None
    enrollment = _player_enrollment(db, player, session)
    return _stage_player_booking(
        db,
        player=player,
        session=session,
        enrollment=enrollment,
        notes=None,
        source=source,
    )


def _promote_waitlisted(db: Session, session_id: int) -> Booking | None:
    promoted = (
        db.query(Booking)
        .filter(
            Booking.session_id == session_id,
            Booking.status == BookingStatus.WAITLISTED,
        )
        .with_for_update()
        .order_by(Booking.waitlist_position.asc(), Booking.id.asc())
        .first()
    )
    if promoted is None:
        return None
    promoted.status = BookingStatus.CONFIRMED
    promoted.waitlist_position = None
    remaining = db.query(Booking).filter(
        Booking.session_id == session_id,
        Booking.status == BookingStatus.WAITLISTED,
    ).all()
    for row in remaining:
        if row.waitlist_position:
            row.waitlist_position -= 1
    return promoted


def cancel_player_booking(
    db: Session,
    *,
    actor: User,
    booking_id: int | None = None,
    session_id: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
    admin_override: bool = False,
    source: str = "API",
) -> BookingCommandResult:
    try:
        query = db.query(Booking)
        if booking_id is not None:
            query = query.filter(Booking.id == booking_id)
        elif session_id is not None:
            query = query.filter(Booking.user_id == actor.id, Booking.session_id == session_id)
        else:
            raise ParticipationError("BOOKING_REFERENCE_REQUIRED")
        booking = query.order_by(
            (Booking.status == BookingStatus.CANCELLED).asc(), Booking.id.desc()
        ).with_for_update().first()
        if booking is None:
            raise ParticipationError("BOOKING_NOT_FOUND")
        if _role_value(actor) != UserRole.ADMIN.value and booking.user_id != actor.id:
            raise ParticipationError("NOT_BOOKING_OWNER")
        if booking.status == BookingStatus.CANCELLED:
            db.rollback()
            return BookingCommandResult(booking, replayed=True)
        session = _locked_session(db, booking.session_id)
        if not (admin_override and _role_value(actor) == UserRole.ADMIN.value):
            decision = cancellation_window_decision(session, now)
            if not decision.allowed:
                raise ParticipationError(decision.code or "SESSION_NOT_CANCELLABLE")
            if booking.attendance is not None:
                raise ParticipationError("ATTENDANCE_ALREADY_RECORDED")
        result = stage_player_booking_cancellation(
            db,
            actor=actor,
            booking=booking,
            reason=reason,
            source=source,
            override_reason="ADMIN_OVERRIDE" if admin_override else None,
        )
        db.commit()
        db.refresh(booking)
        return result
    except Exception:
        db.rollback()
        raise


def stage_player_booking_cancellation(
    db: Session,
    *,
    actor: User,
    booking: Booking,
    reason: str | None,
    source: str,
    override_reason: str | None = None,
) -> BookingCommandResult:
    """Stage cancellation/audit inside a caller-owned transaction."""
    if booking.status == BookingStatus.CANCELLED:
        return BookingCommandResult(booking, replayed=True)
    if _role_value(actor) != UserRole.ADMIN.value and booking.user_id != actor.id:
        raise ParticipationError("NOT_BOOKING_OWNER")
    previous = booking.status
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = datetime.now(timezone.utc)
    if reason:
        booking.notes = reason
    promoted = (
        _promote_waitlisted(db, booking.session_id)
        if previous == BookingStatus.CONFIRMED else None
    )
    _audit(
        db,
        action="PLAYER_SESSION_CANCELLED",
        actor=actor,
        booking=booking,
        source=source,
        previous=previous.value,
        current=BookingStatus.CANCELLED.value,
        extra={"override_reason": override_reason},
    )
    if promoted is not None:
        _audit(
            db,
            action="PLAYER_SESSION_WAITLIST_PROMOTED",
            actor=actor,
            booking=promoted,
            source=source,
            previous=BookingStatus.WAITLISTED.value,
            current=BookingStatus.CONFIRMED.value,
            extra={"trigger_booking_id": booking.id},
        )
    return BookingCommandResult(
        booking, promoted_booking_id=promoted.id if promoted is not None else None
    )


def confirm_waitlisted_booking(
    db: Session,
    *,
    actor: User,
    booking_id: int,
    source: str = "API",
) -> BookingCommandResult:
    try:
        if _role_value(actor) != UserRole.ADMIN.value:
            raise ParticipationError("ADMIN_REQUIRED")
        booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
        if booking is None:
            raise ParticipationError("BOOKING_NOT_FOUND")
        if booking.status == BookingStatus.CONFIRMED:
            db.rollback()
            return BookingCommandResult(booking, replayed=True)
        if booking.status == BookingStatus.CANCELLED:
            raise ParticipationError("CANCELLED_BOOKING_CANNOT_CONFIRM")
        session = _locked_session(db, booking.session_id)
        confirmed = db.query(func.count(Booking.id)).filter(
            Booking.session_id == session.id,
            Booking.status == BookingStatus.CONFIRMED,
        ).scalar() or 0
        if session.capacity is not None and confirmed >= session.capacity:
            raise ParticipationError("SESSION_AT_CAPACITY")
        previous = booking.status
        booking.status = BookingStatus.CONFIRMED
        booking.waitlist_position = None
        _audit(
            db, action="PLAYER_SESSION_CONFIRMED", actor=actor, booking=booking,
            source=source, previous=previous.value, current=BookingStatus.CONFIRMED.value,
        )
        db.commit()
        db.refresh(booking)
        return BookingCommandResult(booking)
    except Exception:
        db.rollback()
        raise


def record_attendance(
    db: Session,
    *,
    actor: User,
    booking_id: int,
    status: AttendanceStatus | str,
    notes: str | None = None,
    now: datetime | None = None,
    source: str = "API",
) -> AttendanceCommandResult:
    try:
        attendance_status = AttendanceStatus(status)
        booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
        if booking is None:
            raise ParticipationError("BOOKING_NOT_FOUND")
        if booking.status != BookingStatus.CONFIRMED:
            raise ParticipationError("CONFIRMED_BOOKING_REQUIRED")
        session = _locked_session(db, booking.session_id)
        authorization_basis = require_session_manager(db, actor, session)
        decision = attendance_window_decision(session, now)
        admin_time_override = (
            authorization_basis == "ADMIN_OVERRIDE"
            and decision.code == "ATTENDANCE_WINDOW_NOT_OPEN"
        )
        if not decision.allowed and not admin_time_override:
            raise ParticipationError(decision.code or "ATTENDANCE_NOT_ALLOWED")
        attendance = db.query(Attendance).filter(Attendance.booking_id == booking.id).first()
        previous = attendance.status.value if attendance is not None else None
        if attendance is not None and attendance.status == attendance_status and (
            notes is None or notes == attendance.notes
        ):
            db.rollback()
            return AttendanceCommandResult(attendance, replayed=True)
        if attendance is None:
            attendance = Attendance(
                user_id=booking.user_id,
                session_id=booking.session_id,
                booking_id=booking.id,
                status=attendance_status,
                notes=notes,
                marked_by=actor.id,
                check_in_time=_now(now) if attendance_status == AttendanceStatus.present else None,
            )
            db.add(attendance)
            db.flush()
        else:
            attendance.status = attendance_status
            if notes is not None:
                attendance.notes = notes
            attendance.marked_by = actor.id
            attendance.updated_at = datetime.now(timezone.utc)
            if attendance_status == AttendanceStatus.present and attendance.check_in_time is None:
                attendance.check_in_time = _now(now)
        booking.attended_status = attendance_status.value
        db.add(AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=actor.id,
            change_type="status_change",
            old_value=previous,
            new_value=attendance_status.value,
            reason=notes,
        ))
        _audit(
            db, action="PLAYER_SESSION_ATTENDANCE_RECORDED", actor=actor,
            booking=booking, source=source, previous=previous,
            current=attendance_status.value,
            extra={"authorization_basis": authorization_basis, "attendance_id": attendance.id},
        )
        db.commit()
        db.refresh(attendance)
        return AttendanceCommandResult(attendance)
    except IntegrityError as exc:
        db.rollback()
        raise ParticipationError("ATTENDANCE_CONCURRENCY_CONFLICT") from exc
    except Exception:
        db.rollback()
        raise


def player_checkin(
    db: Session,
    *,
    player: User,
    booking_id: int,
    notes: str | None = None,
    now: datetime | None = None,
    source: str = "API",
) -> AttendanceCommandResult:
    """Idempotent Player self check-in through the attendance authority."""
    try:
        booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
        if booking is None:
            raise ParticipationError("BOOKING_NOT_FOUND")
        if booking.user_id != player.id:
            raise ParticipationError("NOT_BOOKING_OWNER")
        if booking.status != BookingStatus.CONFIRMED:
            raise ParticipationError("CONFIRMED_BOOKING_REQUIRED")
        session = _locked_session(db, booking.session_id)
        current = _now(now)
        decision = attendance_window_decision(session, current)
        if not decision.allowed:
            raise ParticipationError(decision.code or "ATTENDANCE_NOT_ALLOWED")
        if current > _business_time(session.date_end):
            raise ParticipationError("CHECKIN_WINDOW_CLOSED")
        attendance = db.query(Attendance).filter(Attendance.booking_id == booking.id).first()
        if attendance is not None and attendance.check_in_time is not None:
            db.rollback()
            return AttendanceCommandResult(attendance, replayed=True)
        previous = attendance.status.value if attendance is not None else None
        if attendance is None:
            attendance = Attendance(
                user_id=player.id,
                session_id=session.id,
                booking_id=booking.id,
                status=AttendanceStatus.present,
                check_in_time=current,
                notes=notes,
            )
            db.add(attendance)
            db.flush()
        else:
            attendance.status = AttendanceStatus.present
            attendance.check_in_time = current
            if notes is not None:
                attendance.notes = notes
        booking.attended_status = AttendanceStatus.present.value
        db.add(AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=player.id,
            change_type="self_checkin",
            old_value=previous,
            new_value=AttendanceStatus.present.value,
            reason=notes,
        ))
        _audit(
            db, action="PLAYER_SESSION_SELF_CHECKIN", actor=player, booking=booking,
            source=source, previous=previous, current=AttendanceStatus.present.value,
            extra={"attendance_id": attendance.id},
        )
        db.commit()
        db.refresh(attendance)
        return AttendanceCommandResult(attendance)
    except IntegrityError as exc:
        db.rollback()
        raise ParticipationError("ATTENDANCE_CONCURRENCY_CONFLICT") from exc
    except Exception:
        db.rollback()
        raise


def complete_virtual_session_participation(
    db: Session,
    *,
    player: User,
    session_id: int,
    notes: str | None = None,
    now: datetime | None = None,
    source: str = "API",
) -> AttendanceCommandResult:
    """Record a passed required virtual-session quiz through attendance authority."""
    try:
        booking = (
            db.query(Booking)
            .filter(
                Booking.user_id == player.id,
                Booking.session_id == session_id,
                Booking.status == BookingStatus.CONFIRMED,
            )
            .with_for_update()
            .first()
        )
        if booking is None:
            raise ParticipationError("CONFIRMED_BOOKING_REQUIRED")
        session = _locked_session(db, session_id)
        if session.session_type != SessionType.virtual:
            raise ParticipationError("VIRTUAL_SESSION_REQUIRED")
        current = _now(now)
        decision = attendance_window_decision(session, current)
        if not decision.allowed:
            raise ParticipationError(decision.code or "ATTENDANCE_NOT_ALLOWED")
        if current > _business_time(session.date_end):
            raise ParticipationError("SESSION_ENDED")
        attendance = db.query(Attendance).filter(Attendance.booking_id == booking.id).first()
        if (
            attendance is not None
            and attendance.status == AttendanceStatus.present
            and attendance.confirmation_status == ConfirmationStatus.confirmed
        ):
            db.rollback()
            return AttendanceCommandResult(attendance, replayed=True)
        previous = attendance.status.value if attendance is not None else None
        if attendance is None:
            attendance = Attendance(
                user_id=player.id,
                session_id=session.id,
                booking_id=booking.id,
                status=AttendanceStatus.present,
                check_in_time=current,
                notes=notes,
                confirmation_status=ConfirmationStatus.confirmed,
                student_confirmed_at=current,
            )
            db.add(attendance)
            db.flush()
        else:
            attendance.status = AttendanceStatus.present
            attendance.check_in_time = attendance.check_in_time or current
            attendance.confirmation_status = ConfirmationStatus.confirmed
            attendance.student_confirmed_at = current
            if notes is not None:
                attendance.notes = notes
        booking.attended_status = AttendanceStatus.present.value
        db.add(AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=player.id,
            change_type="virtual_completion",
            old_value=previous,
            new_value=AttendanceStatus.present.value,
            reason=notes,
        ))
        _audit(
            db,
            action="PLAYER_SESSION_VIRTUAL_COMPLETED",
            actor=player,
            booking=booking,
            source=source,
            previous=previous,
            current=AttendanceStatus.present.value,
            extra={"attendance_id": attendance.id},
        )
        db.commit()
        db.refresh(attendance)
        return AttendanceCommandResult(attendance)
    except IntegrityError as exc:
        db.rollback()
        raise ParticipationError("ATTENDANCE_CONCURRENCY_CONFLICT") from exc
    except Exception:
        db.rollback()
        raise


def respond_to_attendance(
    db: Session,
    *,
    player: User,
    session_id: int,
    action: str,
    dispute_reason: str | None = None,
    now: datetime | None = None,
    source: str = "WEB",
) -> AttendanceResponseResult:
    """Confirm or dispute the Player's own attendance through one authority."""
    try:
        if _role_value(player) != UserRole.STUDENT.value:
            raise ParticipationError("PLAYER_ROLE_REQUIRED")
        attendance = (
            db.query(Attendance)
            .filter(Attendance.session_id == session_id, Attendance.user_id == player.id)
            .with_for_update()
            .first()
        )
        if attendance is None:
            raise ParticipationError("ATTENDANCE_NOT_FOUND")
        if attendance.booking_id is None or attendance.booking is None:
            raise ParticipationError("BOOKING_REFERENCE_REQUIRED")
        session = _locked_session(db, session_id)
        if _now(now) > _business_time(session.date_end):
            raise ParticipationError("SESSION_ENDED")
        if action == "confirm":
            target = ConfirmationStatus.confirmed
            reason = "Player confirmed attendance"
        elif action == "dispute":
            if not dispute_reason:
                raise ParticipationError("DISPUTE_REASON_REQUIRED")
            target = ConfirmationStatus.disputed
            reason = dispute_reason
        else:
            raise ParticipationError("INVALID_ATTENDANCE_RESPONSE")
        if (
            attendance.confirmation_status == target
            and (target != ConfirmationStatus.disputed or attendance.dispute_reason == dispute_reason)
        ):
            db.rollback()
            return AttendanceResponseResult(attendance, action, replayed=True)
        previous = getattr(attendance.confirmation_status, "value", attendance.confirmation_status)
        attendance.confirmation_status = target
        attendance.student_confirmed_at = _now(now)
        attendance.dispute_reason = dispute_reason if target == ConfirmationStatus.disputed else None
        db.add(AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=player.id,
            change_type="confirmation" if action == "confirm" else "dispute",
            old_value=previous,
            new_value=target.value,
            reason=reason,
        ))
        _audit(
            db,
            action="PLAYER_SESSION_ATTENDANCE_RESPONDED",
            actor=player,
            booking=attendance.booking,
            source=source,
            previous=previous,
            current=target.value,
            extra={"attendance_id": attendance.id},
        )
        db.commit()
        db.refresh(attendance)
        return AttendanceResponseResult(attendance, action)
    except Exception:
        db.rollback()
        raise


def resolve_attendance_change_request(
    db: Session,
    *,
    player: User,
    session_id: int,
    action: str,
    source: str = "WEB",
) -> AttendanceResponseResult:
    """Apply or reject a pending attendance change for the Player's own record."""
    try:
        if _role_value(player) != UserRole.STUDENT.value:
            raise ParticipationError("PLAYER_ROLE_REQUIRED")
        attendance = (
            db.query(Attendance)
            .filter(Attendance.session_id == session_id, Attendance.user_id == player.id)
            .with_for_update()
            .first()
        )
        if attendance is None or not attendance.pending_change_to:
            raise ParticipationError("ATTENDANCE_CHANGE_REQUEST_NOT_FOUND")
        booking = attendance.booking
        if attendance.booking_id is None or booking is None:
            raise ParticipationError("BOOKING_REFERENCE_REQUIRED")
        pending = attendance.pending_change_to
        previous = attendance.status.value
        if action == "approve":
            try:
                target = AttendanceStatus(pending)
            except ValueError as exc:
                raise ParticipationError("INVALID_ATTENDANCE_STATUS") from exc
            attendance.status = target
            attendance.notes = attendance.change_request_reason
            booking.attended_status = target.value
            change_type = "change_approved"
            outcome = "change_approved"
            new_value = target.value
        elif action == "reject":
            change_type = "change_rejected"
            outcome = "change_rejected"
            new_value = pending
        else:
            raise ParticipationError("INVALID_CHANGE_REQUEST_RESPONSE")
        reason = attendance.change_request_reason
        attendance.pending_change_to = None
        attendance.change_requested_by = None
        attendance.change_requested_at = None
        attendance.change_request_reason = None
        attendance.updated_at = datetime.now(timezone.utc)
        db.add(AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=player.id,
            change_type=change_type,
            old_value=previous,
            new_value=new_value,
            reason=reason,
        ))
        _audit(
            db,
            action="PLAYER_SESSION_ATTENDANCE_CHANGE_RESOLVED",
            actor=player,
            booking=booking,
            source=source,
            previous=previous,
            current=attendance.status.value,
            extra={"attendance_id": attendance.id, "response": action},
        )
        db.commit()
        db.refresh(attendance)
        return AttendanceResponseResult(attendance, outcome)
    except Exception:
        db.rollback()
        raise

"""
Semester enrollment service operations.

Contract:
  - No db.commit() / db.rollback() calls — caller owns the transaction.
  - No assumptions about DB state outside of what is passed as arguments.
  - Query logic is isolated in private helpers (_get_*) at the bottom.
  - Returns simple primitives (tuple / int) for caller observability.
"""
import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from ..models.audit_log import AuditLog
from ..models.booking import Booking, BookingStatus
from ..models.credit_transaction import CreditTransaction
from ..models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ..models.session import Session as SessionModel
from ..models.user import User
from ..api.api_v1.endpoints.bookings.helpers import auto_promote_from_waitlist
from .player_participation_service import (
    stage_player_booking_cancellation,
    stage_player_enrollment_booking,
)


# ── Private query helpers ──────────────────────────────────────────────────────

def _get_auto_generated_sessions(db: DbSession, semester_id: int) -> list:
    return (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester_id,
            SessionModel.auto_generated == True,  # noqa: E712
        )
        .all()
    )


def _get_already_booked_ids(db: DbSession, user_id: int, session_ids: list) -> set:
    if not session_ids:
        return set()
    return {
        r[0] for r in db.query(Booking.session_id).filter(
            Booking.user_id == user_id,
            Booking.session_id.in_(session_ids),
        ).all()
    }


def _get_confirmed_session_ids(
    db: DbSession, enrollment_id: int, user_id: int
) -> list:
    return [
        row[0] for row in db.query(Booking.session_id).filter(
            Booking.enrollment_id == enrollment_id,
            Booking.user_id == user_id,
            Booking.status == BookingStatus.CONFIRMED,
        ).all()
    ]


# ── Public service functions ───────────────────────────────────────────────────

def create_enrollment_with_bookings(
    db: DbSession,
    *,
    semester_id: int,
    user_id: int,
    license_id: int,
    cost: int,
    semester_name: str,
    semester_code: str,
    user_credit_balance: int,
    now: datetime,
    football_category_assignment_id: int | None = None,
    age_category: str | None = None,
) -> tuple:
    """
    Create or reactivate a semester enrollment; attach CreditTransaction + AuditLog;
    bulk-book all auto_generated sessions with capacity enforcement.

    Returns: (enrollment_id, n_confirmed, n_waitlisted). No commit.
    """
    withdrawn = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.user_id == user_id,
            SemesterEnrollment.semester_id == semester_id,
            SemesterEnrollment.user_license_id == license_id,
            SemesterEnrollment.is_active == False,  # noqa: E712
            SemesterEnrollment.request_status == EnrollmentStatus.WITHDRAWN,
        )
        .first()
    )
    if withdrawn:
        withdrawn.request_status = EnrollmentStatus.APPROVED
        withdrawn.is_active = True
        withdrawn.approved_at = now
        withdrawn.enrolled_at = now
        withdrawn.football_category_assignment_id = football_category_assignment_id
        withdrawn.age_category = age_category
        withdrawn.payment_verified = True
        enrollment = withdrawn
        db.flush()
    else:
        enrollment = SemesterEnrollment(
            user_id=user_id,
            semester_id=semester_id,
            user_license_id=license_id,
            football_category_assignment_id=football_category_assignment_id,
            age_category=age_category,
            request_status=EnrollmentStatus.APPROVED,
            payment_verified=True,
            is_active=True,
            requested_at=now,
            approved_at=now,
            enrolled_at=now,
        )
        db.add(enrollment)
        db.flush()

    db.add(AuditLog(
        user_id=user_id,
        action="SEMESTER_ENROLLED",
        resource_type="semester_enrollment",
        resource_id=enrollment.id,
        details={"semester_id": semester_id, "cost": cost, "semester_name": semester_name},
        request_method="POST",
        request_path="/semesters/request-enrollment",
    ))

    if cost > 0:
        db.add(CreditTransaction(
            user_id=user_id,
            context_user_license_id=license_id,
            transaction_type="SEMESTER_ENROLLMENT",
            amount=-cost,
            balance_after=user_credit_balance,
            description=f"Semester enrollment: {semester_name} ({semester_code})",
            semester_id=semester_id,
            enrollment_id=enrollment.id,
            idempotency_key=str(uuid.uuid4()),
        ))

    sessions = _get_auto_generated_sessions(db, semester_id)
    already_booked = _get_already_booked_ids(db, user_id, [s.id for s in sessions])

    new_bookings = []
    legacy_new_bookings = []
    player = db.get(User, user_id) if football_category_assignment_id is not None else None
    for s in sessions:
        if s.id in already_booked:
            continue
        if player is not None:
            staged = stage_player_enrollment_booking(
                db,
                player=player,
                session_id=s.id,
                now=now,
                source="WEB_ENROLLMENT",
            )
            if staged is not None and not staged.replayed:
                new_bookings.append(staged.booking)
            continue
        s = db.query(SessionModel).filter(SessionModel.id == s.id).with_for_update().one()
        confirmed_count = (
            db.query(func.count(Booking.id))
            .filter(Booking.session_id == s.id, Booking.status == BookingStatus.CONFIRMED)
            .scalar() or 0
        )
        status = (
            BookingStatus.CONFIRMED if confirmed_count < s.capacity
            else BookingStatus.WAITLISTED
        )
        booking = Booking(
            user_id=user_id,
            session_id=s.id,
            enrollment_id=enrollment.id,
            status=status,
            created_at=now,
        )
        new_bookings.append(booking)
        legacy_new_bookings.append(booking)

    n_confirmed = sum(1 for b in new_bookings if b.status == BookingStatus.CONFIRMED)
    n_waitlisted = sum(1 for b in new_bookings if b.status == BookingStatus.WAITLISTED)
    if legacy_new_bookings:
        db.bulk_save_objects(legacy_new_bookings)

    return enrollment.id, n_confirmed, n_waitlisted


def withdraw_enrollment_bookings(
    db: DbSession,
    enrollment_id: int,
    user_id: int,
) -> int:
    """
    Cancel canonical Player bookings; preserve legacy deletion for other programs.

    Returns: number of sessions that triggered auto-promotion (promoted_count).
    No commit.
    """
    enrollment = db.get(SemesterEnrollment, enrollment_id)
    if enrollment is not None and enrollment.football_category_assignment_id is not None:
        player = db.get(User, user_id)
        bookings = (
            db.query(Booking)
            .filter(
                Booking.enrollment_id == enrollment_id,
                Booking.user_id == user_id,
                Booking.status != BookingStatus.CANCELLED,
            )
            .with_for_update()
            .all()
        )
        promoted_count = 0
        for booking in bookings:
            result = stage_player_booking_cancellation(
                db,
                actor=player,
                booking=booking,
                reason="Canonical Player enrollment withdrawn",
                source="WEB_ENROLLMENT_WITHDRAWAL",
                override_reason="ENROLLMENT_WITHDRAWAL",
            )
            promoted_count += int(result.promoted_booking_id is not None)
        return promoted_count

    confirmed_session_ids = _get_confirmed_session_ids(db, enrollment_id, user_id)
    db.query(Booking).filter(
        Booking.enrollment_id == enrollment_id,
        Booking.user_id == user_id,
    ).delete(synchronize_session=False)
    for sid in confirmed_session_ids:
        auto_promote_from_waitlist(db, sid)
    return len(confirmed_session_ids)


def cleanup_generated_session_bookings(db: DbSession, semester_id: int) -> int:
    """
    Delete all bookings for auto-generated sessions of a semester.

    Returns: number of deleted booking rows. No commit.
    """
    session_ids = [
        row[0] for row in db.query(SessionModel.id).filter(
            SessionModel.semester_id == semester_id,
            SessionModel.auto_generated == True,  # noqa: E712
        ).all()
    ]
    if not session_ids:
        return 0
    return db.query(Booking).filter(
        Booking.session_id.in_(session_ids)
    ).delete(synchronize_session=False)

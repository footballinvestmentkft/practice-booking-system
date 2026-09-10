from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Barrier
from uuid import uuid4

import pytest

from app.database import SessionLocal
from app.models.attendance import (
    Attendance,
    AttendanceHistory,
    AttendanceStatus,
    ConfirmationStatus,
)
from app.models.audit_log import AuditLog
from app.models.booking import Booking, BookingStatus
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterStatus
from app.models.semester_enrollment import EnrollmentStatus, SemesterEnrollment
from app.models.session import EventCategory, Session as SessionModel, SessionType
from app.models.specialization import SpecializationType
from app.models.user import User, UserRole
from app.services.player_identity_service import issue_football_player_entitlement
from app.services.player_participation_service import (
    ParticipationError,
    book_player_session,
    cancel_player_booking,
    complete_virtual_session_participation,
    record_attendance,
    resolve_attendance_change_request,
    respond_to_attendance,
)
from app.services.program_eligibility_service import record_guardian_consent
import app.services.player_participation_service as participation_service
from app.services.semester_service import (
    create_enrollment_with_bookings,
    withdraw_enrollment_bookings,
)


NOW = datetime(2026, 9, 10, 12, 0)


def _user(db, role, *, dob=date(2000, 1, 1), prefix="pc2"):
    user = User(
        name=f"{prefix} user",
        email=f"{prefix}-{uuid4().hex}@example.com",
        password_hash="test-hash",
        role=role,
        is_active=True,
        date_of_birth=dob,
        credit_balance=500,
    )
    db.add(user)
    db.flush()
    return user


def _player_context(db, *, category="AMATEUR", capacity=2, dob=date(2000, 1, 1)):
    player = _user(db, UserRole.STUDENT, dob=dob, prefix="player")
    if dob > date(2008, 9, 10):
        record_guardian_consent(db, user=player, guardian_name="Guardian")
        db.flush()
    entitlement = issue_football_player_entitlement(
        db, user=player, payment_verified=True, on_date=NOW.date()
    )
    semester = Semester(
        code=f"PC2-{uuid4().hex[:10]}",
        name="PC2 canonical season",
        start_date=date(2026, 7, 1),
        end_date=date(2027, 6, 30),
        status=SemesterStatus.ONGOING,
        specialization_type="LFA_FOOTBALL_PLAYER",
        age_group=category,
    )
    db.add(semester)
    db.flush()
    enrollment = SemesterEnrollment(
        user_id=player.id,
        semester_id=semester.id,
        user_license_id=entitlement.license.id,
        football_category_assignment_id=entitlement.assignment.id,
        request_status=EnrollmentStatus.APPROVED,
        payment_verified=True,
        is_active=True,
        age_category=category,
    )
    db.add(enrollment)
    session = SessionModel(
        title="PC2 training",
        date_start=datetime(2026, 10, 2, 18, 0),
        date_end=datetime(2026, 10, 2, 19, 30),
        session_type=SessionType.on_site,
        session_status="scheduled",
        capacity=capacity,
        semester_id=semester.id,
        target_specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
        event_category=EventCategory.TRAINING,
    )
    db.add(session)
    db.commit()
    return player, entitlement, semester, enrollment, session


def _coach(db, session, level):
    coach = _user(db, UserRole.INSTRUCTOR, prefix=f"coach-{level}")
    coach.specialization = SpecializationType.LFA_COACH
    license_row = UserLicense(
        user_id=coach.id,
        specialization_type="LFA_COACH",
        canonical_program_id="LFA_COACH",
        current_level=level,
        max_achieved_level=level,
        started_at=NOW,
        is_active=True,
    )
    db.add(license_row)
    session.instructor_id = coach.id
    db.commit()
    return coach


def test_eligible_player_booking_is_confirmed_linked_and_audited(test_db):
    player, _, _, enrollment, session = _player_context(test_db)
    result = book_player_session(test_db, player=player, session_id=session.id, now=NOW)

    assert result.booking.status == BookingStatus.CONFIRMED
    assert result.booking.enrollment_id == enrollment.id
    audit = test_db.query(AuditLog).filter_by(
        action="PLAYER_SESSION_BOOKED", resource_id=result.booking.id
    ).one()
    assert audit.user_id == player.id
    assert audit.details["current_status"] == "CONFIRMED"
    assert audit.details["session_id"] == session.id


def test_ineligible_minor_without_guardian_cannot_book(test_db):
    player, _, _, _, session = _player_context(test_db, category="YOUTH", dob=date(2012, 1, 1))
    # Entitlement setup requires consent; revoke the evidence before booking.
    consent = player.guardian_consents[0] if hasattr(player, "guardian_consents") else None
    if consent is None:
        from app.models.ws1_domain import UserGuardianConsent
        consent = test_db.query(UserGuardianConsent).filter_by(user_id=player.id).one()
    consent.revoked_at = datetime(2026, 9, 9)
    test_db.commit()

    with pytest.raises(ParticipationError) as exc:
        book_player_session(test_db, player=player, session_id=session.id, now=NOW)
    assert exc.value.code == "GUARDIAN_CONSENT_REQUIRED"
    assert test_db.query(Booking).filter_by(user_id=player.id).count() == 0


def test_wrong_program_and_wrong_category_fail_closed(test_db):
    player, _, _, _, session = _player_context(test_db)
    session.target_specialization = SpecializationType.GANCUJU_PLAYER
    test_db.commit()
    with pytest.raises(ParticipationError) as exc:
        book_player_session(test_db, player=player, session_id=session.id, now=NOW)
    assert exc.value.code == "SESSION_PROGRAM_MISMATCH"

    session.target_specialization = SpecializationType.LFA_FOOTBALL_PLAYER
    session.semester.age_group = "PRO"
    test_db.commit()
    with pytest.raises(ParticipationError) as exc:
        book_player_session(test_db, player=player, session_id=session.id, now=NOW)
    assert exc.value.code == "SESSION_CATEGORY_MISMATCH"


def test_duplicate_booking_and_repeated_cancellation_are_idempotent(test_db):
    player, _, _, _, session = _player_context(test_db)
    first = book_player_session(test_db, player=player, session_id=session.id, now=NOW)
    replay = book_player_session(test_db, player=player, session_id=session.id, now=NOW)
    assert replay.replayed is True
    assert replay.booking.id == first.booking.id
    assert test_db.query(AuditLog).filter_by(
        action="PLAYER_SESSION_BOOKED", resource_id=first.booking.id
    ).count() == 1

    cancelled = cancel_player_booking(
        test_db, actor=player, booking_id=first.booking.id, now=NOW
    )
    repeated = cancel_player_booking(
        test_db, actor=player, booking_id=first.booking.id, now=NOW
    )
    assert cancelled.replayed is False
    assert repeated.replayed is True
    assert test_db.query(Booking).filter_by(id=first.booking.id).one().status == BookingStatus.CANCELLED
    assert test_db.query(AuditLog).filter_by(
        action="PLAYER_SESSION_CANCELLED", resource_id=first.booking.id
    ).count() == 1


def test_player_enrollment_autobook_uses_canonical_authority(test_db):
    player = _user(test_db, UserRole.STUDENT, prefix="enrollment-player")
    entitlement = issue_football_player_entitlement(
        test_db, user=player, payment_verified=True, on_date=NOW.date()
    )
    semester = Semester(
        code=f"PC2-AUTO-{uuid4().hex[:8]}",
        name="PC2 auto booking season",
        start_date=date(2026, 7, 1),
        end_date=date(2027, 6, 30),
        status=SemesterStatus.ONGOING,
        specialization_type="LFA_FOOTBALL_PLAYER",
        age_group="AMATEUR",
    )
    test_db.add(semester)
    test_db.flush()
    session = SessionModel(
        title="PC2 auto-generated training",
        date_start=datetime(2026, 10, 5, 18, 0),
        date_end=datetime(2026, 10, 5, 19, 30),
        session_type=SessionType.on_site,
        session_status="scheduled",
        capacity=2,
        semester_id=semester.id,
        target_specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
        event_category=EventCategory.TRAINING,
        auto_generated=True,
    )
    test_db.add(session)
    test_db.flush()

    enrollment_id, confirmed, waitlisted = create_enrollment_with_bookings(
        test_db,
        semester_id=semester.id,
        user_id=player.id,
        license_id=entitlement.license.id,
        cost=0,
        semester_name=semester.name,
        semester_code=semester.code,
        user_credit_balance=player.credit_balance,
        now=NOW,
        football_category_assignment_id=entitlement.assignment.id,
        age_category="AMATEUR",
    )
    test_db.commit()

    enrollment = test_db.get(SemesterEnrollment, enrollment_id)
    booking = test_db.query(Booking).filter_by(
        enrollment_id=enrollment_id, session_id=session.id
    ).one()
    assert enrollment.payment_verified is True
    assert (confirmed, waitlisted) == (1, 0)
    assert booking.status == BookingStatus.CONFIRMED
    assert test_db.query(AuditLog).filter_by(
        action="PLAYER_SESSION_BOOKED", resource_id=booking.id
    ).count() == 1

    promoted = withdraw_enrollment_bookings(test_db, enrollment_id, player.id)
    test_db.commit()
    assert promoted == 0
    assert test_db.query(Booking).filter_by(id=booking.id).one().status == BookingStatus.CANCELLED
    assert test_db.query(Booking).filter_by(id=booking.id).count() == 1

@pytest.mark.parametrize("level", [1, 2, 5, 6])
def test_assistant_and_head_coach_matrix_controls_assigned_attendance(test_db, level):
    player, _, _, _, session = _player_context(test_db)
    booking = book_player_session(test_db, player=player, session_id=session.id, now=NOW).booking
    coach = _coach(test_db, session, level)
    if level in (5, 6):
        result = record_attendance(
            test_db,
            actor=coach,
            booking_id=booking.id,
            status=AttendanceStatus.present,
            now=datetime(2026, 10, 2, 17, 50),
        )
        assert result.attendance.marked_by == coach.id
    else:
        with pytest.raises(ParticipationError) as exc:
            record_attendance(
                test_db,
                actor=coach,
                booking_id=booking.id,
                status=AttendanceStatus.present,
                now=datetime(2026, 10, 2, 17, 50),
            )
        assert exc.value.code == "INSTRUCTOR_SCOPE_DENIED"


@pytest.mark.parametrize("level", [1, 2])
def test_assistant_and_head_coach_can_manage_their_qualified_category(test_db, level):
    player, _, _, _, session = _player_context(
        test_db, category="PRE", dob=date(2020, 8, 1)
    )
    booking = book_player_session(test_db, player=player, session_id=session.id, now=NOW).booking
    coach = _coach(test_db, session, level)
    result = record_attendance(
        test_db,
        actor=coach,
        booking_id=booking.id,
        status="present",
        now=datetime(2026, 10, 2, 17, 50),
    )
    assert result.attendance.marked_by == coach.id


def test_foreign_instructor_denied_and_admin_override_audited(test_db):
    player, _, _, _, session = _player_context(test_db)
    booking = book_player_session(test_db, player=player, session_id=session.id, now=NOW).booking
    _coach(test_db, session, 6)
    foreign = _user(test_db, UserRole.INSTRUCTOR, prefix="foreign")
    db_license = UserLicense(
        user_id=foreign.id, specialization_type="LFA_COACH",
        canonical_program_id="LFA_COACH", current_level=6,
        max_achieved_level=6, started_at=NOW, is_active=True,
    )
    test_db.add(db_license)
    admin = _user(test_db, UserRole.ADMIN, prefix="admin")
    test_db.commit()

    with pytest.raises(ParticipationError) as exc:
        record_attendance(
            test_db, actor=foreign, booking_id=booking.id,
            status="present", now=datetime(2026, 10, 2, 17, 50),
        )
    assert exc.value.code == "INSTRUCTOR_NOT_ASSIGNED"

    result = record_attendance(
        test_db, actor=admin, booking_id=booking.id,
        status="present", now=datetime(2026, 10, 2, 17, 50),
    )
    assert result.attendance.marked_by == admin.id
    audit = test_db.query(AuditLog).filter_by(action="PLAYER_SESSION_ATTENDANCE_RECORDED").one()
    assert audit.details["authorization_basis"] == "ADMIN_OVERRIDE"


def test_attendance_update_replay_and_history(test_db):
    player, _, _, _, session = _player_context(test_db)
    booking = book_player_session(test_db, player=player, session_id=session.id, now=NOW).booking
    admin = _user(test_db, UserRole.ADMIN, prefix="admin")
    test_db.commit()
    mark_time = datetime(2026, 10, 2, 17, 50)

    created = record_attendance(
        test_db, actor=admin, booking_id=booking.id, status="present", now=mark_time
    )
    replay = record_attendance(
        test_db, actor=admin, booking_id=booking.id, status="present", now=mark_time
    )
    updated = record_attendance(
        test_db, actor=admin, booking_id=booking.id, status="late", now=mark_time
    )
    assert replay.replayed is True
    assert updated.attendance.id == created.attendance.id
    assert updated.attendance.status == AttendanceStatus.late
    assert test_db.query(Attendance).filter_by(booking_id=booking.id).count() == 1
    assert test_db.query(AttendanceHistory).filter_by(attendance_id=created.attendance.id).count() == 2


def test_player_attendance_response_and_change_resolution_are_audited(test_db):
    player, _, _, _, session = _player_context(test_db)
    booking = book_player_session(test_db, player=player, session_id=session.id, now=NOW).booking
    admin = _user(test_db, UserRole.ADMIN, prefix="admin")
    test_db.commit()
    attendance = record_attendance(
        test_db,
        actor=admin,
        booking_id=booking.id,
        status="present",
        now=datetime(2026, 10, 2, 17, 50),
    ).attendance

    response = respond_to_attendance(
        test_db,
        player=player,
        session_id=session.id,
        action="dispute",
        dispute_reason="I arrived late",
        now=datetime(2026, 10, 2, 18, 30),
    )
    assert response.attendance.confirmation_status == ConfirmationStatus.disputed
    assert response.attendance.dispute_reason == "I arrived late"

    attendance.pending_change_to = "late"
    attendance.change_requested_by = admin.id
    attendance.change_requested_at = datetime(2026, 10, 2, 18, 40)
    attendance.change_request_reason = "Corrected from check-in record"
    test_db.commit()
    resolution = resolve_attendance_change_request(
        test_db, player=player, session_id=session.id, action="approve"
    )
    assert resolution.attendance.status == AttendanceStatus.late
    assert booking.attended_status == "late"
    assert resolution.attendance.pending_change_to is None
    assert test_db.query(AttendanceHistory).filter_by(
        attendance_id=attendance.id, change_type="change_approved"
    ).count() == 1
    assert test_db.query(AuditLog).filter_by(
        action="PLAYER_SESSION_ATTENDANCE_CHANGE_RESOLVED", resource_id=booking.id
    ).count() == 1


def test_virtual_completion_uses_booking_link_and_is_idempotent(test_db):
    player, _, _, _, session = _player_context(test_db)
    session.session_type = SessionType.virtual
    test_db.commit()
    booking = book_player_session(test_db, player=player, session_id=session.id, now=NOW).booking

    first = complete_virtual_session_participation(
        test_db,
        player=player,
        session_id=session.id,
        notes="Required quiz passed",
        now=datetime(2026, 10, 2, 18, 30),
    )
    replay = complete_virtual_session_participation(
        test_db,
        player=player,
        session_id=session.id,
        notes="Required quiz passed",
        now=datetime(2026, 10, 2, 18, 30),
    )

    assert first.attendance.booking_id == booking.id
    assert first.attendance.confirmation_status == ConfirmationStatus.confirmed
    assert replay.replayed is True
    assert test_db.query(AuditLog).filter_by(
        action="PLAYER_SESSION_VIRTUAL_COMPLETED", resource_id=booking.id
    ).count() == 1


def test_booking_audit_failure_rolls_back_everything(test_db, monkeypatch):
    player, _, _, _, session = _player_context(test_db)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(participation_service, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        book_player_session(test_db, player=player, session_id=session.id, now=NOW)
    assert test_db.query(Booking).filter_by(
        user_id=player.id, session_id=session.id
    ).count() == 0


def test_concurrent_capacity_boundary_confirms_one_and_waitlists_one():
    seed = SessionLocal()
    try:
        first, _, semester, _, session = _player_context(seed, capacity=1)
        second = _user(seed, UserRole.STUDENT, prefix="race-player")
        entitlement = issue_football_player_entitlement(
            seed, user=second, payment_verified=True, on_date=NOW.date()
        )
        seed.add(SemesterEnrollment(
            user_id=second.id,
            semester_id=semester.id,
            user_license_id=entitlement.license.id,
            football_category_assignment_id=entitlement.assignment.id,
            request_status=EnrollmentStatus.APPROVED,
            payment_verified=True,
            is_active=True,
            age_category="AMATEUR",
        ))
        seed.commit()
        player_ids = [first.id, second.id]
        session_id = session.id
    finally:
        seed.close()
    gate = Barrier(2)

    def attempt(player_id):
        db = SessionLocal()
        try:
            player = db.get(User, player_id)
            gate.wait(timeout=5)
            return book_player_session(
                db, player=player, session_id=session_id, now=NOW
            ).booking.status.value
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(attempt, player_ids))
    verify = SessionLocal()
    try:
        assert outcomes == ["CONFIRMED", "WAITLISTED"]
        assert verify.query(Booking).filter_by(session_id=session_id).count() == 2
        assert verify.query(Booking).filter_by(
            session_id=session_id, status=BookingStatus.CONFIRMED
        ).count() == 1
    finally:
        verify.close()


def test_concurrent_duplicate_booking_returns_one_canonical_record():
    seed = SessionLocal()
    try:
        player, _, _, _, session = _player_context(seed, capacity=2)
        player_id, session_id = player.id, session.id
    finally:
        seed.close()
    gate = Barrier(2)

    def attempt():
        db = SessionLocal()
        try:
            gate.wait(timeout=5)
            result = book_player_session(
                db, player=db.get(User, player_id), session_id=session_id, now=NOW
            )
            return result.booking.id, result.replayed
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(2)))
    verify = SessionLocal()
    try:
        assert len({booking_id for booking_id, _ in outcomes}) == 1
        assert sorted(replayed for _, replayed in outcomes) == [False, True]
        assert verify.query(Booking).filter_by(
            user_id=player_id, session_id=session_id
        ).count() == 1
    finally:
        verify.close()


def test_concurrent_repeated_cancellation_is_single_transition():
    seed = SessionLocal()
    try:
        player, _, _, _, session = _player_context(seed)
        booking = book_player_session(seed, player=player, session_id=session.id, now=NOW).booking
        player_id, booking_id = player.id, booking.id
    finally:
        seed.close()
    gate = Barrier(2)

    def attempt():
        db = SessionLocal()
        try:
            gate.wait(timeout=5)
            result = cancel_player_booking(
                db, actor=db.get(User, player_id), booking_id=booking_id, now=NOW
            )
            return result.replayed
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(lambda _: attempt(), range(2)))
    verify = SessionLocal()
    try:
        assert outcomes == [False, True]
        assert verify.query(AuditLog).filter_by(
            action="PLAYER_SESSION_CANCELLED", resource_id=booking_id
        ).count() == 1
    finally:
        verify.close()

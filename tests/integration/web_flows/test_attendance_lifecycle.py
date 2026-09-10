"""
Integration test: On-site session attendance lifecycle

Positive flows:
  1. Instructor marks student present → Attendance(present, pending_confirmation) in DB
  2. Instructor marks student absent  → Attendance(absent, check_in_time=None) in DB
  3. Student confirms attendance      → ConfirmationStatus.confirmed in DB
  4. Student disputes attendance      → ConfirmationStatus.disputed in DB

Negative flows:
  5. Instructor role tries to confirm (not STUDENT role) → 303 error=unauthorized
  6. Student confirms when no attendance record exists    → 303 error=no_attendance
  7. Mark attendance for un-booked student               → 303 error=student_not_enrolled

DB validation:
  - Negative cases leave DB state unchanged
"""

import pytest
from app.models.attendance import Attendance, AttendanceStatus, ConfirmationStatus


class TestMarkAttendance:

    def test_instructor_marks_student_present_creates_attendance(
        self,
        instructor_client,
        active_session,
        active_booking,
        student_user,
        instructor_user,
        test_db,
    ):
        """POST /sessions/{id}/attendance/mark → 303 + Attendance(present, pending) in DB."""
        resp = instructor_client.post(
            f"/sessions/{active_session.id}/attendance/mark",
            data={
                "student_id": str(student_user.id),
                "status": "present",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "attendance_marked" in resp.headers["location"]

        # Verify DB state
        attendance = (
            test_db.query(Attendance)
            .filter(
                Attendance.session_id == active_session.id,
                Attendance.user_id == student_user.id,
            )
            .first()
        )
        assert attendance is not None
        assert attendance.status == AttendanceStatus.present
        assert attendance.confirmation_status == ConfirmationStatus.pending_confirmation
        assert attendance.marked_by == instructor_user.id
        assert attendance.check_in_time is not None  # set for 'present' status

    def test_instructor_marks_student_absent(
        self,
        instructor_client,
        active_session,
        active_booking,
        student_user,
        test_db,
    ):
        """Marking absent → Attendance(absent), check_in_time is None."""
        resp = instructor_client.post(
            f"/sessions/{active_session.id}/attendance/mark",
            data={
                "student_id": str(student_user.id),
                "status": "absent",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "attendance_marked" in resp.headers["location"]

        attendance = (
            test_db.query(Attendance)
            .filter(
                Attendance.session_id == active_session.id,
                Attendance.user_id == student_user.id,
            )
            .first()
        )
        assert attendance is not None
        assert attendance.status == AttendanceStatus.absent
        assert attendance.check_in_time is None  # only set for 'present'


class TestConfirmAttendance:

    def test_student_confirms_pending_attendance(
        self,
        student_client,
        active_session,
        active_booking,
        student_user,
        instructor_user,
        test_db,
    ):
        """Student confirms pending attendance → confirmation_status=confirmed in DB."""
        # Pre-create attendance directly in DB (instructor already marked it)
        att = Attendance(
            user_id=student_user.id,
            session_id=active_session.id,
            booking_id=active_booking.id,
            status=AttendanceStatus.present,
            confirmation_status=ConfirmationStatus.pending_confirmation,
            marked_by=instructor_user.id,
        )
        test_db.add(att)
        test_db.commit()
        test_db.refresh(att)
        att_id = att.id

        resp = student_client.post(
            f"/sessions/{active_session.id}/attendance/confirm",
            data={"action": "confirm"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "attendance_confirmed" in resp.headers["location"]

        # Verify DB state
        test_db.expire_all()
        updated = test_db.query(Attendance).filter(Attendance.id == att_id).first()
        assert updated.confirmation_status == ConfirmationStatus.confirmed
        assert updated.student_confirmed_at is not None

    def test_student_disputes_attendance(
        self,
        student_client,
        active_session,
        active_booking,
        student_user,
        instructor_user,
        test_db,
    ):
        """Student disputes attendance → confirmation_status=disputed in DB."""
        att = Attendance(
            user_id=student_user.id,
            session_id=active_session.id,
            booking_id=active_booking.id,
            status=AttendanceStatus.absent,
            confirmation_status=ConfirmationStatus.pending_confirmation,
            marked_by=instructor_user.id,
        )
        test_db.add(att)
        test_db.commit()
        test_db.refresh(att)
        att_id = att.id

        resp = student_client.post(
            f"/sessions/{active_session.id}/attendance/confirm",
            data={"action": "dispute", "dispute_reason": "I was present!"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "attendance_disputed" in resp.headers["location"]

        test_db.expire_all()
        updated = test_db.query(Attendance).filter(Attendance.id == att_id).first()
        assert updated.confirmation_status == ConfirmationStatus.disputed
        assert updated.dispute_reason == "I was present!"


class TestNegativeAttendanceFlows:

    def test_instructor_role_cannot_confirm_attendance(
        self,
        instructor_client,
        active_session,
        test_db,
    ):
        """confirm_attendance requires STUDENT role → instructor gets 303 error=unauthorized."""
        resp = instructor_client.post(
            f"/sessions/{active_session.id}/attendance/confirm",
            data={"action": "confirm"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "player_role_required" in resp.headers["location"]

    def test_student_confirm_with_no_attendance_record_returns_error(
        self,
        student_client,
        active_session,
        student_user,
        test_db,
    ):
        """Student tries to confirm when no attendance row exists for them → 303 error=no_attendance."""
        # No Attendance row created → DB has nothing for this student+session
        resp = student_client.post(
            f"/sessions/{active_session.id}/attendance/confirm",
            data={"action": "confirm"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "attendance_not_found" in resp.headers["location"]

        # DB unchanged — still no Attendance row
        att = (
            test_db.query(Attendance)
            .filter(
                Attendance.session_id == active_session.id,
                Attendance.user_id == student_user.id,
            )
            .first()
        )
        assert att is None

    def test_mark_attendance_for_unbooked_student_returns_error(
        self,
        instructor_client,
        active_session,
        student_user,
        test_db,
    ):
        """Instructor marks attendance for student with no booking → 303 error=student_not_enrolled.

        No active_booking fixture → no Booking row exists for student_user.
        DB must remain clean (no Attendance row created).
        """
        resp = instructor_client.post(
            f"/sessions/{active_session.id}/attendance/mark",
            data={
                "student_id": str(student_user.id),
                "status": "present",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "student_not_enrolled" in resp.headers["location"]

        # DB unchanged — no Attendance row created
        att = (
            test_db.query(Attendance)
            .filter(
                Attendance.session_id == active_session.id,
                Attendance.user_id == student_user.id,
            )
            .first()
        )
        assert att is None

"""
Integration test: Idempotency guards and sequential race-condition simulation

These tests approximate concurrency edge-cases in a sequential manner:
one request 'wins', DB is committed, then a duplicate arrives — exactly
the state the race-winner leaves behind.

Why NOT real thread concurrency here:
    SAVEPOINT-isolated SQLAlchemy sessions cannot be safely shared across
    threads. True DB-level race conditions (two simultaneous INSERTs hitting
    a UNIQUE constraint) require separate connections and are covered by
    load/stress tests (out of scope for CI integration tests).
    The idempotency guards below are what make real races safe:
      - Booking: explicit `already_booked` check before INSERT
      - Quiz submit: `completed_at is not None` guard → 400
      - Attendance: UPDATE existing row instead of second INSERT

Note on rate limiting:
    ENABLE_RATE_LIMITING = not is_testing() → rate limiting is OFF during
    pytest (config.py line 119). Rapid repeated requests in CI are
    unthrottled; production rate limits are enforced by slowapi middleware.

Positive idempotency flows:
  1. Book same session twice → 2nd: 303 info=already_booked, 1 Booking row
  2. Submit same attempt twice → 2nd: 400, completed_at from 1st preserved
  3. Mark attendance twice → 2nd updates existing row (no duplicate)

DB validation:
  - No duplicate Booking rows after double-book
  - QuizAttempt.completed_at unchanged after failed 2nd submit
  - Exactly 1 Attendance row after double-mark
"""

from datetime import datetime, timezone

import pytest
from app.models.booking import Booking, BookingStatus
from app.models.attendance import Attendance
from app.models.credit_transaction import CreditTransaction
from app.models.license import UserLicense
from app.models.quiz import QuizAttempt
from app.models.user import User


class TestDoubleBookingIdempotency:

    def test_double_book_same_session_returns_already_booked(
        self,
        student_client,
        future_session,
        student_user,
        test_db,
    ):
        """Booking same session twice → 2nd request returns already_booked; only 1 row in DB.

        Simulates the case where two near-simultaneous booking requests race:
        the first commits → the second sees the existing row and redirects.
        """
        # First booking — must succeed
        resp1 = student_client.post(
            f"/sessions/book/{future_session.id}",
            follow_redirects=False,
        )
        assert resp1.status_code == 303
        assert "success=booked" in resp1.headers["location"]

        # Second booking — same session, same student
        resp2 = student_client.post(
            f"/sessions/book/{future_session.id}",
            follow_redirects=False,
        )
        assert resp2.status_code == 303
        assert "already_booked" in resp2.headers["location"]

        # DB: exactly one Booking row for this (student, session) pair
        bookings = (
            test_db.query(Booking)
            .filter(
                Booking.user_id == student_user.id,
                Booking.session_id == future_session.id,
            )
            .all()
        )
        assert len(bookings) == 1
        assert bookings[0].status == BookingStatus.CONFIRMED

    def test_double_cancel_same_session_returns_not_found(
        self,
        student_client,
        future_booking,
        future_session,
        student_user,
        test_db,
    ):
        """Cancel same booking twice → 1st: success=cancelled; 2nd: error=booking_not_found.

        The 2nd cancel arrives after the Booking row is already deleted.
        """
        booking_id = future_booking.id

        # First cancel — succeeds
        resp1 = student_client.post(
            f"/sessions/cancel/{future_session.id}",
            follow_redirects=False,
        )
        assert resp1.status_code == 303
        assert "success=cancelled" in resp1.headers["location"]

        # Booking row is gone
        test_db.expire_all()
        assert test_db.query(Booking).filter(Booking.id == booking_id).first() is None

        # Second cancel — booking already gone → not_found
        resp2 = student_client.post(
            f"/sessions/cancel/{future_session.id}",
            follow_redirects=False,
        )
        assert resp2.status_code == 303
        assert "booking_not_found" in resp2.headers["location"]

        # DB still clean — no ghost row
        assert test_db.query(Booking).filter(Booking.id == booking_id).first() is None


class TestDoubleQuizSubmitIdempotency:

    def test_double_submit_same_attempt_returns_400(
        self,
        student_client,
        simple_quiz,
        student_user,
        test_db,
    ):
        """Submit same QuizAttempt twice → 2nd returns 400; completed_at from 1st preserved.

        The 2nd submit arrives after completed_at is already set (attempt finalized).
        """
        attempt = QuizAttempt(
            user_id=student_user.id,
            quiz_id=simple_quiz.id,
            total_questions=0,
        )
        test_db.add(attempt)
        test_db.commit()
        test_db.refresh(attempt)
        attempt_id = attempt.id

        # First submit — succeeds (200 HTML response)
        resp1 = student_client.post(
            f"/quizzes/{simple_quiz.id}/submit",
            data={"attempt_id": str(attempt_id), "time_spent": "30.0"},
        )
        assert resp1.status_code == 200

        # Capture completed_at from first submit
        test_db.expire_all()
        after_first = (
            test_db.query(QuizAttempt).filter(QuizAttempt.id == attempt_id).first()
        )
        assert after_first.completed_at is not None
        first_completed_at = after_first.completed_at

        # Second submit — attempt already completed → 400
        resp2 = student_client.post(
            f"/quizzes/{simple_quiz.id}/submit",
            data={"attempt_id": str(attempt_id), "time_spent": "5.0"},
        )
        assert resp2.status_code == 400

        # DB: completed_at unchanged (from first submit, not overwritten)
        test_db.expire_all()
        after_second = (
            test_db.query(QuizAttempt).filter(QuizAttempt.id == attempt_id).first()
        )
        assert after_second.completed_at == first_completed_at


class TestDoubleAttendanceMarkIdempotency:

    def test_double_mark_same_student_updates_existing_row(
        self,
        instructor_client,
        active_session,
        active_booking,
        student_user,
        instructor_user,
        test_db,
    ):
        """Mark attendance for same student twice → 2nd updates existing row; no duplicate.

        The attendance route performs upsert logic:
          - 1st call: INSERT new Attendance row
          - 2nd call: UPDATE the existing row in-place
        Exactly one Attendance row must exist after both requests.
        """
        # First mark — creates Attendance row
        resp1 = instructor_client.post(
            f"/sessions/{active_session.id}/attendance/mark",
            data={"student_id": str(student_user.id), "status": "present"},
            follow_redirects=False,
        )
        assert resp1.status_code == 303
        assert "attendance_marked" in resp1.headers["location"]

        # Second mark — same student, same session (status unchanged)
        resp2 = instructor_client.post(
            f"/sessions/{active_session.id}/attendance/mark",
            data={"student_id": str(student_user.id), "status": "present"},
            follow_redirects=False,
        )
        assert resp2.status_code == 303
        assert "attendance_marked" in resp2.headers["location"]

        # DB: exactly 1 Attendance row (no duplicate insert)
        test_db.expire_all()
        rows = (
            test_db.query(Attendance)
            .filter(
                Attendance.session_id == active_session.id,
                Attendance.user_id == student_user.id,
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].marked_by == instructor_user.id

class TestSpecializationUnlockIdempotency:
    """
    SMOKE-CONC-01/02: Specialization unlock idempotency guards.

    Sequential simulation of the race-condition scenario:
      - Two requests arrive from the same user for the same specialization.
      - The 'winner' commits first: license created, 100 credits deducted.
      - The 'loser' arrives after: must see the existing license, skip deduction.

    Covered by DB-level safety:
      1. SELECT ... FOR UPDATE on the User row (serialises balance reads)
      2. UniqueConstraint uq_user_license_spec (DB rejects duplicate INSERT)
      3. Re-query for existing license AFTER acquiring the lock
    """

    def test_double_unlock_same_spec_creates_one_license_no_double_deduction(
        self,
        student_client,
        student_user,
        test_db,
    ):
        """SMOKE-CONC-01: double POST /specialization/select (same spec) → 1 license, 100 credits total."""
        # Give student enough credits for both attempts
        student_user.credit_balance = 200
        test_db.flush()

        # First unlock — must succeed
        resp1 = student_client.post(
            "/specialization/select",
            data={"specialization": "LFA_FOOTBALL_PLAYER"},
            follow_redirects=False,
        )
        assert resp1.status_code == 303

        test_db.expire_all()

        licenses = (
            test_db.query(UserLicense)
            .filter(
                UserLicense.user_id == student_user.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            )
            .all()
        )
        assert len(licenses) == 1, "Exactly 1 license after first unlock"
        license_id = licenses[0].id

        cts = (
            test_db.query(CreditTransaction)
            .filter(
                CreditTransaction.user_id == student_user.id,
                CreditTransaction.transaction_type == "SPECIALIZATION_UNLOCK",
                CreditTransaction.idempotency_key == f"license_unlock_{license_id}",
            )
            .all()
        )
        assert len(cts) == 1, "Exactly 1 CreditTransaction after first unlock"
        assert cts[0].amount == -100

        balance_after_first = (
            test_db.query(User).filter(User.id == student_user.id).first().credit_balance
        )
        assert balance_after_first == 100, "100 credits remain after first unlock"

        # Second unlock — same spec (race loser arriving after winner committed)
        resp2 = student_client.post(
            "/specialization/select",
            data={"specialization": "LFA_FOOTBALL_PLAYER"},
            follow_redirects=False,
        )
        assert resp2.status_code == 303

        test_db.expire_all()

        # Still exactly 1 license — no duplicate
        licenses_after = (
            test_db.query(UserLicense)
            .filter(
                UserLicense.user_id == student_user.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            )
            .all()
        )
        assert len(licenses_after) == 1, "Still exactly 1 license after duplicate attempt"

        # No new CreditTransaction — no double-deduction
        cts_after = (
            test_db.query(CreditTransaction)
            .filter(
                CreditTransaction.user_id == student_user.id,
                CreditTransaction.transaction_type == "SPECIALIZATION_UNLOCK",
                CreditTransaction.idempotency_key == f"license_unlock_{license_id}",
            )
            .all()
        )
        assert len(cts_after) == 1, "No new CreditTransaction on duplicate attempt"

        balance_after_second = (
            test_db.query(User).filter(User.id == student_user.id).first().credit_balance
        )
        assert balance_after_second == 100, "Credits unchanged after duplicate attempt"

    def test_insufficient_credits_unlock_rejected_no_license_created(
        self,
        student_client,
        student_user,
        test_db,
    ):
        """SMOKE-CONC-02: POST /specialization/select with < 100 credits → 303 error, no license, balance unchanged."""
        student_user.credit_balance = 50
        # Persist the precondition so the request transaction can roll back
        # independently without also undoing the fixture setup.
        test_db.commit()

        resp = student_client.post(
            "/specialization/select",
            data={"specialization": "LFA_FOOTBALL_PLAYER"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert "error" in location or "dashboard" in location

        test_db.expire_all()

        licenses = (
            test_db.query(UserLicense)
            .filter(
                UserLicense.user_id == student_user.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            )
            .all()
        )
        assert len(licenses) == 0, "No license created when credits insufficient"

        balance = (
            test_db.query(User).filter(User.id == student_user.id).first().credit_balance
        )
        assert balance == 50, "Balance unchanged when unlock rejected"

    def test_mark_present_then_absent_updates_status(
        self,
        instructor_client,
        active_session,
        active_booking,
        student_user,
        test_db,
    ):
        """Mark present then absent → status updated to absent; still 1 Attendance row.

        Verifies the update path (not re-insert) when status changes.
        """
        # Mark present
        instructor_client.post(
            f"/sessions/{active_session.id}/attendance/mark",
            data={"student_id": str(student_user.id), "status": "present"},
            follow_redirects=False,
        )

        # Mark absent (status change)
        resp = instructor_client.post(
            f"/sessions/{active_session.id}/attendance/mark",
            data={"student_id": str(student_user.id), "status": "absent"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "attendance_marked" in resp.headers["location"]

        # DB: 1 row, status=absent
        test_db.expire_all()
        from app.models.attendance import AttendanceStatus
        rows = (
            test_db.query(Attendance)
            .filter(
                Attendance.session_id == active_session.id,
                Attendance.user_id == student_user.id,
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == AttendanceStatus.absent

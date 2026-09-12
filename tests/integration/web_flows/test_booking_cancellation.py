"""
Integration test: Session booking cancellation lifecycle

Positive flows:
  1. Student POSTs /sessions/book/{id}       → 303 + Booking row in DB
  2. Student POSTs /sessions/cancel/{id}     → 303 + Booking retained as CANCELLED

Negative flows:
  3. Book already-booked session             → 303 info=already_booked
  4. Cancel nonexistent booking              → 303 error=booking_not_found
  5. Cancel after 12h deadline              → 303 error=cancellation_deadline_passed
  6. Book session within 12h of start       → 303 error=booking_deadline_passed

DB validation:
  - After book:   Booking(user_id, session_id, status=CONFIRMED) exists
  - After cancel: Booking history remains with status=CANCELLED
  - After failed cancel: Booking row still present (DB unchanged)
"""

from app.models.booking import Booking, BookingStatus


class TestBookingCancellationLifecycle:

    def test_book_session_creates_confirmed_booking(
        self,
        student_client,
        future_session,
        student_user,
        test_db,
    ):
        """POST /sessions/book/{id} → 303 redirect + Booking(CONFIRMED) in DB."""
        resp = student_client.post(
            f"/sessions/book/{future_session.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "success=confirmed" in resp.headers["location"]

        # Verify DB state
        booking = (
            test_db.query(Booking)
            .filter(
                Booking.user_id == student_user.id,
                Booking.session_id == future_session.id,
            )
            .first()
        )
        assert booking is not None
        assert booking.status == BookingStatus.CONFIRMED

    def test_cancel_booking_preserves_cancelled_history(
        self,
        student_client,
        future_booking,
        future_session,
        student_user,
        test_db,
    ):
        """POST /sessions/cancel/{id} → 303 redirect + immutable booking history."""
        booking_id = future_booking.id

        resp = student_client.post(
            f"/sessions/cancel/{future_session.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "success=cancelled" in resp.headers["location"]

        # Verify DB state — financial/participation history must remain queryable.
        test_db.expire_all()
        cancelled = test_db.query(Booking).filter(Booking.id == booking_id).one()
        assert cancelled.status == BookingStatus.CANCELLED
        assert cancelled.cancelled_at is not None

    def test_book_already_booked_returns_info_redirect(
        self,
        student_client,
        future_booking,
        future_session,
        test_db,
    ):
        """Booking a session already booked → 303 with info=already_booked."""
        resp = student_client.post(
            f"/sessions/book/{future_session.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "already_booked" in resp.headers["location"]

    def test_cancel_nonexistent_booking_returns_error_redirect(
        self,
        student_client,
        future_session,
    ):
        """Cancel without a booking → 303 with error=booking_not_found."""
        resp = student_client.post(
            f"/sessions/cancel/{future_session.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "booking_not_found" in resp.headers["location"]

    def test_cancel_after_deadline_returns_deadline_error(
        self,
        student_client,
        near_future_booking,
        near_future_session,
        student_user,
        test_db,
    ):
        """Cancel within 12h of session start → 303 error=cancellation_deadline_passed.

        near_future_session starts in 6h → deadline = NOW - 6h (already passed).
        Booking must NOT be deleted from DB.
        """
        booking_id = near_future_booking.id

        resp = student_client.post(
            f"/sessions/cancel/{near_future_session.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "cancellation_deadline_passed" in resp.headers["location"]

        # DB unchanged — booking still exists
        test_db.expire_all()
        still_there = test_db.query(Booking).filter(Booking.id == booking_id).first()
        assert still_there is not None

    def test_book_within_12h_deadline_returns_deadline_error(
        self,
        student_client,
        near_future_session,
        student_user,
        test_db,
    ):
        """Book a session starting in <12h → 303 error=booking_deadline_passed.

        near_future_session starts in 6h → booking_deadline = NOW - 6h (passed).
        No Booking row must be created in DB.
        """
        resp = student_client.post(
            f"/sessions/book/{near_future_session.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "booking_deadline_passed" in resp.headers["location"]

        # Verify DB unchanged — no booking created
        booking = (
            test_db.query(Booking)
            .filter(
                Booking.user_id == student_user.id,
                Booking.session_id == near_future_session.id,
            )
            .first()
        )
        assert booking is None

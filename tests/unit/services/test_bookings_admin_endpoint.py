"""
Unit tests for app/api/api_v1/endpoints/bookings/admin.py

Covers:
  get_all_bookings — no filters, semester_id filter (join), status filter, pagination
  confirm_booking — not found → 404, at capacity → 409, success → CONFIRMED
  admin_cancel_booking — not found → 404 (with_for_update), confirmed booking triggers
                         auto_promote, non-confirmed booking no auto_promote
  update_booking_attendance — not found → 404, invalid status → 400, existing attendance
                               → update, no attendance → create new,
                               IntegrityError uq_booking_attendance → 409

Uses with_for_update() loop pattern: fm.with_for_update.return_value = fm
"""
import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.exc import IntegrityError

from fastapi import HTTPException

from app.api.api_v1.endpoints.bookings.admin import (
    admin_cancel_booking,
    confirm_booking,
    get_all_bookings,
    update_booking_attendance,
)
from app.models.booking import BookingStatus
from app.models.user import UserRole
from app.services.player_participation_service import ParticipationError

_BASE = "app.api.api_v1.endpoints.bookings.admin"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _admin_user(uid=1):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.ADMIN
    return u


def _instructor_user(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.INSTRUCTOR
    return u


def _booking_mock(bid=1, status=BookingStatus.CONFIRMED, session_id=10, user_id=99):
    b = MagicMock()
    b.id = bid
    b.status = status
    b.session_id = session_id
    b.user_id = user_id
    b.attendance = None
    b.user = MagicMock()
    b.session = MagicMock()
    return b


def _mock_q():
    q = MagicMock()
    for m in ("filter", "join", "options", "order_by", "offset", "limit",
              "filter_by", "distinct"):
        getattr(q, m).return_value = q
    q.count.return_value = 0
    q.all.return_value = []
    q.scalar.return_value = 0
    q.first.return_value = None
    return q


def _mock_db(q=None):
    db = MagicMock()
    if q is None:
        q = _mock_q()
    db.query.return_value = q
    return db, q


def _wfu_mock_db(booking=None):
    """DB mock with with_for_update().first() chain for admin_cancel_booking/update_booking_attendance."""
    db = MagicMock()
    q = _mock_q()
    fm = MagicMock()
    fm.with_for_update.return_value = fm
    fm.first.return_value = booking
    q.filter.return_value = fm
    db.query.return_value = q
    return db


# ──────────────────────────────────────────────────────────────────────────────
# get_all_bookings
# ──────────────────────────────────────────────────────────────────────────────

class TestGetAllBookings:

    def test_no_filters_returns_empty_list(self):
        user = _admin_user()
        db, q = _mock_db()
        q.count.return_value = 0
        q.all.return_value = []
        result = get_all_bookings(db=db, current_user=user, page=1, size=50)
        assert result.total == 0
        assert result.bookings == []

    def test_semester_id_filter_applies_join(self):
        user = _admin_user()
        db, q = _mock_db()
        q.count.return_value = 0
        q.all.return_value = []
        get_all_bookings(db=db, current_user=user, semester_id=5, page=1, size=50)
        q.join.assert_called()

    def test_status_filter_applied(self):
        user = _admin_user()
        db, q = _mock_db()
        q.count.return_value = 0
        q.all.return_value = []
        get_all_bookings(db=db, current_user=user, status=BookingStatus.CONFIRMED, page=1, size=50)
        q.filter.assert_called()

    def test_pagination_page_2(self):
        user = _admin_user()
        db, q = _mock_db()
        q.count.return_value = 10
        q.all.return_value = []
        result = get_all_bookings(db=db, current_user=user, page=2, size=5)
        assert result.page == 2
        assert result.size == 5
        assert result.total == 10


# ──────────────────────────────────────────────────────────────────────────────
# confirm_booking
# ──────────────────────────────────────────────────────────────────────────────

class TestConfirmBooking:

    @pytest.mark.parametrize(("code", "status_code"), [
        ("BOOKING_NOT_FOUND", 404),
        ("SESSION_AT_CAPACITY", 409),
        ("CANCELLED_BOOKING_CANNOT_CONFIRM", 400),
    ])
    def test_canonical_confirmation_error_mapping(self, code, status_code):
        with patch(f"{_BASE}.confirm_waitlisted_booking", side_effect=ParticipationError(code)):
            with pytest.raises(HTTPException) as exc:
                confirm_booking(booking_id=3, db=MagicMock(), current_user=_admin_user())
        assert exc.value.status_code == status_code
        assert exc.value.detail == code

    def test_delegates_and_reports_replay(self):
        result = MagicMock(replayed=True)
        db=MagicMock(); admin=_admin_user()
        with patch(f"{_BASE}.confirm_waitlisted_booking", return_value=result) as command:
            response = confirm_booking(booking_id=3, db=db, current_user=admin)
        command.assert_called_once_with(db, actor=admin, booking_id=3, source="API")
        assert response == {"message": "Booking confirmed successfully", "replayed": True}


# ──────────────────────────────────────────────────────────────────────────────
# admin_cancel_booking
# ──────────────────────────────────────────────────────────────────────────────

class TestAdminCancelBooking:

    @pytest.mark.parametrize(("code", "status_code"), [
        ("BOOKING_NOT_FOUND", 404),
        ("NOT_BOOKING_OWNER", 403),
    ])
    def test_canonical_cancellation_error_mapping(self, code, status_code):
        with patch(f"{_BASE}.cancel_player_booking", side_effect=ParticipationError(code)):
            with pytest.raises(HTTPException) as exc:
                admin_cancel_booking(
                    booking_id=3, cancel_data=MagicMock(reason="reason"),
                    db=MagicMock(), current_user=_admin_user()
                )
        assert exc.value.status_code == status_code

    def test_delegates_admin_override_and_reports_promotion(self):
        result = MagicMock(
            booking=MagicMock(session_id=10), replayed=False, promoted_booking_id=44
        )
        db=MagicMock(); admin=_admin_user(); payload=MagicMock(reason="reason")
        with patch(f"{_BASE}.cancel_player_booking", return_value=result) as command:
            response = admin_cancel_booking(
                booking_id=3, cancel_data=payload, db=db, current_user=admin
            )
        command.assert_called_once_with(
            db, actor=admin, booking_id=3, reason="reason",
            admin_override=True, source="API"
        )
        assert response["promoted_booking_id"] == 44
        assert response["session_id"] == 10


# ──────────────────────────────────────────────────────────────────────────────
# update_booking_attendance
# ──────────────────────────────────────────────────────────────────────────────

class TestUpdateBookingAttendance:

    def test_invalid_attendance_status_raises_400(self):
        with patch(f"{_BASE}.record_attendance", side_effect=ValueError("invalid")):
            with pytest.raises(HTTPException) as exc:
                update_booking_attendance(
                    booking_id=1, attendance_data={"status": "flying"},
                    db=MagicMock(), current_user=_admin_user()
                )
        assert exc.value.status_code == 400
        assert exc.value.detail == "INVALID_ATTENDANCE_STATUS"

    @pytest.mark.parametrize(("code", "status_code"), [
        ("BOOKING_NOT_FOUND", 404),
        ("INSTRUCTOR_NOT_ASSIGNED", 403),
        ("ATTENDANCE_CONCURRENCY_CONFLICT", 409),
    ])
    def test_canonical_attendance_error_mapping(self, code, status_code):
        with patch(f"{_BASE}.record_attendance", side_effect=ParticipationError(code)):
            with pytest.raises(HTTPException) as exc:
                update_booking_attendance(
                    booking_id=1, attendance_data={"status": "present"},
                    db=MagicMock(), current_user=_admin_user()
                )
        assert exc.value.status_code == status_code

    def test_delegates_and_returns_updated_booking_contract(self):
        booking = _booking_mock()
        db, q = _mock_db(); q.first.return_value = booking
        actor = _admin_user()
        with patch(f"{_BASE}.record_attendance", return_value=MagicMock()) as command:
            response = update_booking_attendance(
                booking_id=1, attendance_data={"status": "late", "notes": "traffic"},
                db=db, current_user=actor
            )
        command.assert_called_once_with(
            db, actor=actor, booking_id=1, status="late", notes="traffic", source="API"
        )
        assert response is booking

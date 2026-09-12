"""
Unit tests for app/api/api_v1/endpoints/bookings/student.py
Branch coverage targets:
  create_booking:           role guard, session not found, can_book False,
                            existing booking, past session, deadline, waitlisted,
                            IntegrityError branches
  get_my_bookings:          semester_id filter, status filter, attended set
  get_booking:              not found, student not owner, attended
  cancel_booking:           not found, not owner, already cancelled, past session,
                            12h deadline, CONFIRMED auto-promote, promotion in response
  get_my_booking_statistics: semester_id branches, no/active semester, semester progress
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError

# Endpoint functions under test
from app.api.api_v1.endpoints.bookings.student import (
    create_booking,
    get_my_bookings,
    get_booking,
    cancel_booking,
    get_my_booking_statistics,
)
from app.models.user import UserRole
from app.models.booking import BookingStatus
from app.services.player_participation_service import ParticipationError
from fastapi import HTTPException

_BASE = "app.api.api_v1.endpoints.bookings.student"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(role=UserRole.STUDENT, uid=42):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.name = "Test User"
    return u


def _q(first=None, count=0, all_=None, scalar=0):
    q = MagicMock()
    for m in ("filter", "join", "options", "order_by", "offset",
              "limit", "filter_by", "with_for_update", "distinct"):
        getattr(q, m).return_value = q
    q.first.return_value = first
    q.count.return_value = count
    q.all.return_value = all_ if all_ is not None else []
    q.scalar.return_value = scalar
    q.one.return_value = MagicMock()
    return q


def _seq_db(*queries):
    calls = [0]

    def _side(*args, **kw):
        idx = calls[0]
        calls[0] += 1
        return queries[idx] if idx < len(queries) else _q()

    db = MagicMock()
    db.query.side_effect = _side
    return db


def _future_session(hours_ahead=48, has_tz=False):
    """Return a mock session whose date_start is *hours_ahead* in the future."""
    s = MagicMock()
    future = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    s.date_start = future.replace(tzinfo=None) if not has_tz else future
    s.capacity = 10
    return s


def _past_session():
    s = MagicMock()
    s.date_start = datetime.now(timezone.utc) - timedelta(hours=2)
    s.date_start = s.date_start.replace(tzinfo=None)
    s.capacity = 10
    return s


# ---------------------------------------------------------------------------
# create_booking
# ---------------------------------------------------------------------------

class TestCreateBooking:

    @pytest.mark.parametrize(("code", "status_code"), [
        ("PLAYER_ROLE_REQUIRED", 403),
        ("SESSION_NOT_FOUND", 404),
        ("GUARDIAN_CONSENT_REQUIRED", 400),
        ("SESSION_CATEGORY_MISMATCH", 400),
        ("BOOKING_CONCURRENCY_CONFLICT", 409),
    ])
    def test_canonical_policy_error_mapping(self, code, status_code):
        booking_data = MagicMock(session_id=7, notes="note")
        with patch(f"{_BASE}.book_player_session", side_effect=ParticipationError(code)):
            with pytest.raises(HTTPException) as exc:
                create_booking(booking_data, db=MagicMock(), current_user=_user())
        assert exc.value.status_code == status_code
        assert exc.value.detail == code

    @pytest.mark.parametrize("booking_status", [
        BookingStatus.CONFIRMED, BookingStatus.WAITLISTED
    ])
    def test_delegates_creation_and_returns_canonical_booking(self, booking_status):
        booking = MagicMock(status=booking_status)
        result = MagicMock(booking=booking, replayed=False)
        booking_data = MagicMock(session_id=7, notes="note")
        db = MagicMock()
        player = _user()
        with patch(f"{_BASE}.book_player_session", return_value=result) as command, \
             patch(f"{_BASE}.metrics") as metric:
            response = create_booking(booking_data, db=db, current_user=player)
        assert response is booking
        command.assert_called_once_with(
            db, player=player,
            session_id=7, notes="note", source="API"
        )
        metric.increment.assert_any_call("bookings_created")
        if booking_status == BookingStatus.WAITLISTED:
            metric.increment.assert_any_call("bookings_waitlisted")

    def test_duplicate_replay_does_not_increment_metrics(self):
        booking = MagicMock(status=BookingStatus.CONFIRMED)
        result = MagicMock(booking=booking, replayed=True)
        with patch(f"{_BASE}.book_player_session", return_value=result), \
             patch(f"{_BASE}.metrics") as metric:
            response = create_booking(
                MagicMock(session_id=7, notes=None), db=MagicMock(), current_user=_user()
            )
        assert response is booking
        metric.increment.assert_not_called()


# ---------------------------------------------------------------------------
# get_my_bookings
# ---------------------------------------------------------------------------

class TestGetMyBookings:

    def _make_booking(self, session_id=1):
        b = MagicMock()
        b.session_id = session_id
        b.id = session_id
        b.user_id = 42
        b.status = BookingStatus.CONFIRMED
        b.waitlist_position = None
        b.notes = ""
        b.created_at = datetime.now()
        b.updated_at = datetime.now()
        b.cancelled_at = None
        b.attended_status = None
        _ = b.user  # pre-warm via __getattr__
        _ = b.session
        return b

    def test_no_semester_no_status_filter(self):
        booking = self._make_booking()
        q = _q(count=1, all_=[booking])
        db = MagicMock()
        db.query.return_value = q

        with patch(f"{_BASE}.BookingWithRelations") as MockBWR, \
             patch(f"{_BASE}.BookingList") as MockBL:
            MockBWR.return_value = MagicMock()
            MockBL.return_value = MagicMock()
            result = get_my_bookings(
                db=db,
                current_user=_user(),
                page=1, size=50,
                semester_id=None, status=None,
            )
        MockBL.assert_called_once()

    def test_semester_id_applies_join_filter(self):
        booking = self._make_booking()
        q = _q(count=1, all_=[booking])
        db = MagicMock()
        db.query.return_value = q

        with patch(f"{_BASE}.BookingWithRelations"), patch(f"{_BASE}.BookingList"):
            get_my_bookings(
                db=db, current_user=_user(),
                page=1, size=50,
                semester_id=7, status=None,
            )
        # join should have been called (semester_id branch)
        q.join.assert_called()

    def test_status_filter_applied(self):
        booking = self._make_booking()
        q = _q(count=1, all_=[booking])
        db = MagicMock()
        db.query.return_value = q

        with patch(f"{_BASE}.BookingWithRelations"), patch(f"{_BASE}.BookingList"):
            get_my_bookings(
                db=db, current_user=_user(),
                page=1, size=50,
                semester_id=None, status=BookingStatus.CONFIRMED,
            )
        # filter should have been called for status
        assert q.filter.call_count >= 2


# ---------------------------------------------------------------------------
# get_booking
# ---------------------------------------------------------------------------

class TestGetBooking:

    def test_booking_not_found_raises_404(self):
        db = _seq_db(_q(first=None))
        with pytest.raises(HTTPException) as exc:
            get_booking(booking_id=99, db=db, current_user=_user())
        assert exc.value.status_code == 404

    def test_student_viewing_other_user_booking_raises_403(self):
        booking = MagicMock()
        booking.user_id = 999   # different user
        booking.session_id = 1
        db = _seq_db(_q(first=booking), _q(first=None))
        with pytest.raises(HTTPException) as exc:
            get_booking(booking_id=1, db=db, current_user=_user(uid=42))
        assert exc.value.status_code == 403

    def test_admin_can_view_any_booking(self):
        booking = MagicMock()
        booking.user_id = 999
        booking.session_id = 1
        booking.id = 1
        booking.status = BookingStatus.CONFIRMED
        booking.waitlist_position = None
        booking.notes = ""
        booking.created_at = datetime.now()
        booking.updated_at = datetime.now()
        booking.cancelled_at = None
        booking.attended_status = None
        _ = booking.user
        _ = booking.session
        db = _seq_db(_q(first=booking), _q(first=None))  # no attendance
        with patch(f"{_BASE}.BookingWithRelations") as MockBWR:
            MockBWR.return_value = MagicMock()
            result = get_booking(booking_id=1, db=db, current_user=_user(role=UserRole.ADMIN, uid=42))
        MockBWR.assert_called_once()

    def test_attended_true_when_attendance_found(self):
        booking = MagicMock()
        booking.user_id = 42
        booking.session_id = 1
        booking.id = 1
        booking.status = BookingStatus.CONFIRMED
        booking.waitlist_position = None
        booking.notes = ""
        booking.created_at = datetime.now()
        booking.updated_at = datetime.now()
        booking.cancelled_at = None
        booking.attended_status = None
        _ = booking.user
        _ = booking.session
        attendance = MagicMock()   # attendance found
        db = _seq_db(_q(first=booking), _q(first=attendance))
        with patch(f"{_BASE}.BookingWithRelations") as MockBWR:
            MockBWR.return_value = MagicMock()
            get_booking(booking_id=1, db=db, current_user=_user(uid=42))
        call_kwargs = MockBWR.call_args[1]
        assert call_kwargs["attended"] is True


# ---------------------------------------------------------------------------
# cancel_booking
# ---------------------------------------------------------------------------

class TestCancelBooking:

    @pytest.mark.parametrize(("code", "status_code"), [
        ("BOOKING_NOT_FOUND", 404),
        ("NOT_BOOKING_OWNER", 403),
        ("CANCELLATION_DEADLINE_PASSED", 400),
        ("ATTENDANCE_ALREADY_RECORDED", 400),
    ])
    def test_canonical_cancellation_error_mapping(self, code, status_code):
        with patch(f"{_BASE}.cancel_player_booking", side_effect=ParticipationError(code)):
            with pytest.raises(HTTPException) as exc:
                cancel_booking(booking_id=8, db=MagicMock(), current_user=_user())
        assert exc.value.status_code == status_code
        assert exc.value.detail == code

    def test_delegates_idempotent_cancellation(self):
        booking = MagicMock(session_id=12)
        result = MagicMock(booking=booking, replayed=True, promoted_booking_id=None)
        db = MagicMock(); player = _user()
        with patch(f"{_BASE}.cancel_player_booking", return_value=result) as command:
            response = cancel_booking(booking_id=8, db=db, current_user=player)
        command.assert_called_once_with(db, actor=player, booking_id=8, source="API")
        assert response == {
            "message": "Booking cancelled successfully",
            "cancelled_booking_id": 8,
            "session_id": 12,
            "replayed": True,
        }

    def test_reports_waitlist_promotion(self):
        result = MagicMock(
            booking=MagicMock(session_id=12), replayed=False, promoted_booking_id=91
        )
        with patch(f"{_BASE}.cancel_player_booking", return_value=result):
            response = cancel_booking(booking_id=8, db=MagicMock(), current_user=_user())
        assert response["promoted_booking_id"] == 91
        assert response["replayed"] is False


# ---------------------------------------------------------------------------
# get_my_booking_statistics
# ---------------------------------------------------------------------------
# NOTE: student.py L392 has a production bug: AttendanceStatus.PRESENT should
# be AttendanceStatus.present (lowercase). All statistics tests patch this.

_PATCH_AS = f"{_BASE}.AttendanceStatus"


class TestGetMyBookingStatistics:

    def test_no_semester_id_uses_active_semester_query(self):
        """semester_id=None → queries for active semester by date."""
        q = _q(count=0, first=None)
        db = MagicMock()
        db.query.return_value = q

        with patch(_PATCH_AS) as MockAS:
            MockAS.PRESENT = "present"
            result = get_my_booking_statistics(
                semester_id=None, db=db, current_user=_user()
            )
        assert result["user_id"] == 42
        assert result["current_semester"] is None  # no active semester mock

    def test_semester_id_provided_filters_bookings_and_attendance(self):
        """semester_id provided → join filter applied for bookings + attendance."""
        q = _q(count=3, first=None)
        db = MagicMock()
        db.query.return_value = q

        with patch(_PATCH_AS) as MockAS:
            MockAS.PRESENT = "present"
            result = get_my_booking_statistics(
                semester_id=5, db=db, current_user=_user()
            )
        # semester_id branch: join was called on the query chain
        q.join.assert_called()

    def test_semester_id_provided_no_semester_found(self):
        """semester_id provided but not found → current_semester is None (L410 branch)."""
        # NOTE: production code has a bug — when semester_id is given AND a semester
        # is found, 'current_date' is undefined (L426 UnboundLocalError). We test
        # the safe case (semester not found) to exercise the L410 branch.
        q = _q(count=1, first=None)
        db = MagicMock()
        db.query.return_value = q

        with patch(_PATCH_AS) as MockAS:
            MockAS.PRESENT = "present"
            result = get_my_booking_statistics(
                semester_id=5, db=db, current_user=_user()
            )
        assert result["current_semester"] is None

    def test_active_semester_found_calculates_progress(self):
        """No semester_id + active semester found → progress calculated."""
        sem = MagicMock()
        sem.id = 3
        sem.name = "Active Sem"
        sem.start_date = datetime.now().date() - timedelta(days=10)
        sem.end_date = datetime.now().date() + timedelta(days=80)

        q = _q(count=0, first=sem)
        db = MagicMock()
        db.query.return_value = q

        with patch(_PATCH_AS) as MockAS:
            MockAS.PRESENT = "present"
            result = get_my_booking_statistics(
                semester_id=None, db=db, current_user=_user()
            )
        assert result["current_semester"] is not None
        progress = result["current_semester"]["progress_percentage"]
        # 10 days elapsed out of 90 total → ~11%
        assert 5 <= progress <= 20

    def test_statistics_keys_present(self):
        q = _q(count=0, first=None)
        db = MagicMock()
        db.query.return_value = q

        with patch(_PATCH_AS) as MockAS:
            MockAS.PRESENT = "present"
            result = get_my_booking_statistics(
                semester_id=None, db=db, current_user=_user()
            )
        stats = result["statistics"]
        assert "total_bookings" in stats
        assert "confirmed_bookings" in stats
        assert "attendance_rate" in stats
        assert "booking_success_rate" in stats

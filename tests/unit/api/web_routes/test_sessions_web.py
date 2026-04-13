"""
Unit tests for app/api/web_routes/sessions.py

Covers:
  calendar_page — student not onboarded redirects, instructor passes, student onboarded passes
  sessions_page — student not onboarded, instructor view (template + enrolled_count),
                  student view (approved semesters only)
  book_session — session not found, deadline passed, already booked, success
  cancel_booking — booking not found, session not found, session already ended,
                   cancellation deadline passed, attendance marked, evaluation submitted,
                   success
  session_details — session not found redirects, instructor view template, student view template
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from zoneinfo import ZoneInfo
from fastapi.responses import RedirectResponse

from app.api.web_routes.sessions import (
    calendar_page,
    sessions_page,
    book_session,
    cancel_booking,
    session_details,
)
from app.models.user import UserRole
from app.models.session import SessionType
from app.models.booking import BookingStatus
from app.models.semester_enrollment import EnrollmentStatus


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_BASE = "app.api.web_routes.sessions"


def _instructor(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.INSTRUCTOR
    u.email = "instructor@test.com"
    u.name = "Coach"
    u.onboarding_completed = True
    return u


def _student(uid=99, onboarding_done=True):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.email = "student@test.com"
    u.onboarding_completed = onboarding_done
    return u


def _req():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


def _mock_db(first_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_return
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    db.query.return_value.filter.return_value.count.return_value = 0
    return db


_BUD = ZoneInfo("Europe/Budapest")


def _now_budapest():
    """Current time as naive Budapest datetime (matches DB storage convention)."""
    return datetime.now(_BUD).replace(tzinfo=None)


def _future_session(session_id=1, instructor_id=42):
    """Session starting > 13 hours from now Budapest time (safe to book and cancel)."""
    s = MagicMock()
    s.id = session_id
    s.instructor_id = instructor_id
    s.session_type = SessionType.on_site
    s.actual_end_time = None
    now = _now_budapest()
    s.date_start = now + timedelta(hours=14)
    s.date_end = now + timedelta(hours=15)
    s.title = "Test Session"
    return s


def _past_session(session_id=1, instructor_id=42):
    """Session that already ended (actual_end_time is set)."""
    s = MagicMock()
    s.id = session_id
    s.instructor_id = instructor_id
    s.session_type = SessionType.on_site
    s.actual_end_time = datetime.now(timezone.utc) - timedelta(hours=1)
    now = _now_budapest()
    s.date_start = now - timedelta(hours=3)
    s.date_end = now - timedelta(hours=2)
    s.title = "Past Session"
    return s


# ──────────────────────────────────────────────────────────────────────────────
# calendar_page
# ──────────────────────────────────────────────────────────────────────────────

class TestCalendarPage:

    def test_student_not_onboarded_redirects_to_dashboard(self):
        user = _student(onboarding_done=False)
        result = _run(calendar_page(request=_req(), db=_mock_db(), user=user))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_instructor_gets_calendar_template(self):
        user = _instructor()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(calendar_page(request=_req(), db=_mock_db(), user=user))
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "calendar.html"

    def test_student_onboarded_gets_calendar_template(self):
        user = _student(onboarding_done=True)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(calendar_page(request=_req(), db=_mock_db(), user=user))
        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "calendar.html"


# ──────────────────────────────────────────────────────────────────────────────
# sessions_page
# ──────────────────────────────────────────────────────────────────────────────

class TestSessionsPage:

    def test_student_not_onboarded_redirects(self):
        user = _student(onboarding_done=False)
        result = _run(sessions_page(request=_req(), db=_mock_db(), user=user))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_instructor_view_renders_sessions_template(self):
        user = _instructor()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "sessions.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["is_instructor"] is True

    def test_instructor_view_with_sessions_adds_enrolled_count(self):
        user = _instructor()
        s = MagicMock()
        s.id = 1

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [s]
        db.query.return_value.filter.return_value.count.return_value = 5
        db.query.return_value.filter.return_value.all.return_value = []

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        assert s.enrolled_count == 5

    def test_student_view_no_enrollments_returns_empty_sessions(self):
        user = _student()
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []  # No enrollments

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "sessions.html"
        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["is_instructor"] is False

    def test_student_view_with_approved_enrollments_shows_sessions(self):
        """Student has approved enrollments → exercises session loop body (lines 104-191)."""
        user = _student(uid=99)

        enrollment = MagicMock()
        enrollment.semester_id = 10

        session_obj = MagicMock()
        session_obj.id = 1
        session_obj.instructor_id = None   # Skip instructor name query
        # Non-virtual → skip quiz queries
        session_obj.session_type = SessionType.on_site
        now = _now_budapest()
        session_obj.date_start = now + timedelta(hours=14)  # Naive Budapest datetime

        db = MagicMock()
        # .all() calls: approved_enrollments=[enrollment], my_bookings=[]
        db.query.return_value.filter.return_value.all.side_effect = [[enrollment], []]
        # upcoming_sessions via filter().options().order_by().limit().all()
        db.query.return_value.filter.return_value.options.return_value = db.query.return_value.filter.return_value
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [session_obj]
        db.query.return_value.filter.return_value.count.return_value = 2

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["is_instructor"] is False

    def test_student_enrolled_session_attendance_and_instructor_name(self):
        """Student is enrolled → attendance + review queries run; instructor_id set → name resolved.
        Covers lines 134-139, 173-174, 187-191."""
        user = _student(uid=99)

        enrollment = MagicMock()
        enrollment.semester_id = 10

        # Booking that matches this user and session
        booking_obj = MagicMock()
        booking_obj.session_id = 1
        booking_obj.user_id = 99

        session_obj = MagicMock()
        session_obj.id = 1
        session_obj.instructor_id = 42        # truthy → instructor name query
        session_obj.session_type = SessionType.on_site  # not virtual
        session_obj.date_start = _now_budapest() + timedelta(hours=14)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        db = MagicMock()
        # .all() sequence: approved_enrollments, my_bookings
        db.query.return_value.filter.return_value.all.side_effect = [
            [enrollment], [booking_obj],
        ]
        db.query.return_value.filter.return_value.options.return_value = db.query.return_value.filter.return_value
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [session_obj]
        db.query.return_value.filter.return_value.count.return_value = 3
        # .first() sequence inside loop:
        # 1. attendance (enrolled) → None
        # 2. instructor_review → None
        # 3. instructor name → instructor_obj
        # 4. performance_review (enrolled) → None
        db.query.return_value.filter.return_value.first.side_effect = [
            None, None, instructor_obj, None,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["is_instructor"] is False
        assert session_obj.instructor_name == "Coach"

    def test_virtual_session_enrolled_empty_quizzes_loop_skipped(self):
        """Virtual session enrolled, but session_quizzes=[] → for loop skips entirely (156→168)."""
        user = _student(uid=99)

        enrollment = MagicMock()
        enrollment.semester_id = 10

        booking_obj = MagicMock()
        booking_obj.session_id = 1
        booking_obj.user_id = 99

        session_obj = MagicMock()
        session_obj.id = 1
        session_obj.instructor_id = None
        session_obj.session_type = SessionType.virtual
        session_obj.date_start = _now_budapest() + timedelta(hours=14)

        db = MagicMock()
        # .all(): approved_enrollments, my_bookings, SessionQuiz=[] (empty)
        db.query.return_value.filter.return_value.all.side_effect = [
            [enrollment], [booking_obj], [],
        ]
        db.query.return_value.filter.return_value.options.return_value = db.query.return_value.filter.return_value
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [session_obj]
        db.query.return_value.filter.return_value.count.return_value = 0
        # .first(): attendance=None, instructor_review=None, performance_review=None
        db.query.return_value.filter.return_value.first.side_effect = [None, None, None]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        # Loop skipped → quiz_completed remains False
        assert session_obj.quiz_completed is False

    def test_virtual_session_not_required_quiz_back_to_loop(self):
        """Virtual session, sq.is_required=False → skip quiz check, back to loop header (157→156)."""
        user = _student(uid=99)

        enrollment = MagicMock()
        enrollment.semester_id = 10

        booking_obj = MagicMock()
        booking_obj.session_id = 1
        booking_obj.user_id = 99

        session_obj = MagicMock()
        session_obj.id = 1
        session_obj.instructor_id = None
        session_obj.session_type = SessionType.virtual
        session_obj.date_start = _now_budapest() + timedelta(hours=14)

        sq_not_required = MagicMock()
        sq_not_required.is_required = False
        sq_not_required.quiz_id = 5

        db = MagicMock()
        # .all(): approved_enrollments, my_bookings, [sq_not_required]
        db.query.return_value.filter.return_value.all.side_effect = [
            [enrollment], [booking_obj], [sq_not_required],
        ]
        db.query.return_value.filter.return_value.options.return_value = db.query.return_value.filter.return_value
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [session_obj]
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.first.side_effect = [None, None, None]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        # Not required → skip → quiz_completed stays False
        assert session_obj.quiz_completed is False

    def test_virtual_session_required_quiz_no_passed_attempt_continues(self):
        """Virtual session, sq.is_required=True but no passed attempt → 164→156 (no break)."""
        user = _student(uid=99)

        enrollment = MagicMock()
        enrollment.semester_id = 10

        booking_obj = MagicMock()
        booking_obj.session_id = 1
        booking_obj.user_id = 99

        session_obj = MagicMock()
        session_obj.id = 1
        session_obj.instructor_id = None
        session_obj.session_type = SessionType.virtual
        session_obj.date_start = _now_budapest() + timedelta(hours=14)

        sq_required = MagicMock()
        sq_required.is_required = True
        sq_required.quiz_id = 5

        db = MagicMock()
        # .all(): approved_enrollments, my_bookings, [sq_required]
        db.query.return_value.filter.return_value.all.side_effect = [
            [enrollment], [booking_obj], [sq_required],
        ]
        db.query.return_value.filter.return_value.options.return_value = db.query.return_value.filter.return_value
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [session_obj]
        db.query.return_value.filter.return_value.count.return_value = 0
        # .first(): attendance=None, instructor_review=None,
        #           passed_attempt=None (no pass yet), performance_review=None
        db.query.return_value.filter.return_value.first.side_effect = [None, None, None, None]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        # No passed attempt → loop ends without setting quiz_completed
        assert session_obj.quiz_completed is False

    def test_student_enrolled_virtual_session_quiz_completed(self):
        """Enrolled student on a VIRTUAL session with a required quiz passed → quiz_completed=True.
        Covers lines 151-166."""
        user = _student(uid=99)

        enrollment = MagicMock()
        enrollment.semester_id = 10

        booking_obj = MagicMock()
        booking_obj.session_id = 1
        booking_obj.user_id = 99

        session_obj = MagicMock()
        session_obj.id = 1
        session_obj.instructor_id = None
        session_obj.session_type = SessionType.virtual  # triggers quiz check
        session_obj.date_start = _now_budapest() + timedelta(hours=14)

        sq = MagicMock()
        sq.is_required = True
        sq.quiz_id = 5

        passed_attempt = MagicMock()

        db = MagicMock()
        # .all(): approved_enrollments, my_bookings, SessionQuiz list
        db.query.return_value.filter.return_value.all.side_effect = [
            [enrollment], [booking_obj], [sq],
        ]
        db.query.return_value.filter.return_value.options.return_value = db.query.return_value.filter.return_value
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [session_obj]
        db.query.return_value.filter.return_value.count.return_value = 1
        # .first(): attendance=None, instructor_review=None, QuizAttempt=passed,
        #           performance_review=None (line 187)
        db.query.return_value.filter.return_value.first.side_effect = [
            None, None, passed_attempt, None,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(sessions_page(request=_req(), db=db, user=user))

        assert session_obj.quiz_completed is True


# ──────────────────────────────────────────────────────────────────────────────
# book_session
# ──────────────────────────────────────────────────────────────────────────────

class TestBookSession:

    def test_session_not_found_redirects(self):
        db = _mock_db(None)
        result = _run(book_session(request=_req(), session_id=1, db=db, user=_student()))
        assert isinstance(result, RedirectResponse)
        assert "session_not_found" in result.headers["location"]

    def test_booking_deadline_passed_redirects(self):
        user = _student()
        s = MagicMock()
        # Session starts in 1 hour Budapest time — within 12-hour deadline
        s.date_start = _now_budapest() + timedelta(hours=1)
        db = _mock_db(s)
        result = _run(book_session(request=_req(), session_id=1, db=db, user=user))
        assert "booking_deadline_passed" in result.headers["location"]

    def test_already_booked_redirects(self):
        user = _student()
        s = _future_session()
        existing_booking = MagicMock()

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [s, existing_booking]

        result = _run(book_session(request=_req(), session_id=1, db=db, user=user))
        assert "already_booked" in result.headers["location"]

    def test_book_session_success(self):
        user = _student()
        s = _future_session()

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [s, None]

        result = _run(book_session(request=_req(), session_id=1, db=db, user=user))
        assert "success=booked" in result.headers["location"]
        db.add.assert_called_once()
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# cancel_booking
# ──────────────────────────────────────────────────────────────────────────────

class TestCancelBooking:

    def test_booking_not_found_redirects(self):
        db = _mock_db(None)
        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=_student()))
        assert "booking_not_found" in result.headers["location"]

    def test_session_not_found_redirects(self):
        booking = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [booking, None]
        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=_student()))
        assert "session_not_found" in result.headers["location"]

    def test_session_already_ended_redirects(self):
        booking = MagicMock()
        s = _past_session()  # actual_end_time is set

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [booking, s]

        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=_student()))
        assert "session_already_ended" in result.headers["location"]

    def test_cancellation_deadline_passed_redirects(self):
        booking = MagicMock()
        # Session starts in 1 hour Budapest time → within 12h deadline
        s = MagicMock()
        s.actual_end_time = None
        s.date_start = _now_budapest() + timedelta(hours=1)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [booking, s]

        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=_student()))
        assert "cancellation_deadline_passed" in result.headers["location"]

    def test_attendance_already_marked_redirects(self):
        user = _student()
        booking = MagicMock()
        booking.id = 7
        s = _future_session()
        s.actual_end_time = None
        attendance = MagicMock()  # attendance exists for this booking

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [booking, s, attendance]

        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=user))
        assert "attendance_already_marked" in result.headers["location"]

    def test_evaluation_already_submitted_redirects(self):
        user = _student()
        booking = MagicMock()
        booking.id = 7
        s = _future_session()
        s.actual_end_time = None
        instructor_review = MagicMock()

        db = MagicMock()
        # booking, session, no attendance, instructor_review exists
        db.query.return_value.filter.return_value.first.side_effect = [booking, s, None, instructor_review]

        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=user))
        assert "evaluation_already_submitted" in result.headers["location"]

    def test_cancel_booking_success(self):
        user = _student()
        booking = MagicMock()
        booking.id = 7
        s = _future_session()
        s.actual_end_time = None

        db = MagicMock()
        # booking, session, no attendance, no review
        db.query.return_value.filter.return_value.first.side_effect = [booking, s, None, None]

        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=user))
        assert "success=cancelled" in result.headers["location"]
        db.delete.assert_called_once_with(booking)
        db.commit.assert_called_once()

    def test_cancel_booking_session_start_aware_datetime_skips_tz_replace(self):
        """cancel_booking: session_start already has tzinfo → False branch 288→292."""
        user = _student()
        booking = MagicMock()
        booking.id = 7

        s = MagicMock()
        s.actual_end_time = None
        # Aware datetime in future (> 12h) → no cancellation deadline error
        s.date_start = datetime.now(_BUD) + timedelta(hours=14)  # tz-aware

        db = MagicMock()
        # booking, session, attendance=None, review=None → success
        db.query.return_value.filter.return_value.first.side_effect = [booking, s, None, None]

        result = _run(cancel_booking(request=_req(), session_id=1, db=db, user=user))
        assert "success=cancelled" in result.headers["location"]


# ──────────────────────────────────────────────────────────────────────────────
# session_details
# ──────────────────────────────────────────────────────────────────────────────

class TestSessionDetails:

    def test_session_not_found_raises_404(self):
        import pytest
        from fastapi import HTTPException
        db = _mock_db(None)
        with pytest.raises(HTTPException) as exc_info:
            _run(session_details(request=_req(), session_id=1, db=db, user=_student()))
        assert exc_info.value.status_code == 404

    def _make_session_obj(self, user_id=42, session_type=SessionType.on_site):
        """Session with naive Budapest datetimes (matches DB convention)."""
        s = MagicMock()
        s.id = 1
        s.instructor_id = user_id
        s.session_type = session_type
        now = _now_budapest()
        s.date_start = now + timedelta(hours=2)   # Future session
        s.date_end = now + timedelta(hours=3)
        return s

    def test_instructor_view_renders_template(self):
        user = _instructor()
        s = self._make_session_obj(user_id=user.id)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [s, instructor_obj, None]
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "session_details.html"

    def test_student_view_renders_template(self):
        user = _student()
        s = self._make_session_obj(user_id=42)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [s, instructor_obj, None]
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "session_details.html"

    def test_student_enrolled_with_attendance_covers_review_query(self):
        """Student IS enrolled (booking in list), attendance found → exercises lines 413-424."""
        user = _student(uid=99)
        s = self._make_session_obj(user_id=42)  # instructor_id != user.id

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        # A booking belonging to this student → is_enrolled = True
        booking = MagicMock()
        booking.user_id = 99
        booking.id = 7

        student_obj = MagicMock()
        student_obj.id = 99
        student_obj.name = "Student"
        student_obj.email = "s@test.com"

        my_attendance = MagicMock()
        my_instructor_review = MagicMock()

        db = MagicMock()
        # bookings.all() → [booking]; order_by.all() → []
        db.query.return_value.filter.return_value.all.return_value = [booking]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0
        # .first() order for on_site session (no quiz block after line 427):
        #   [0] session, [1] instructor_obj,
        #   booking loop: [2] student_obj, [3] attendance=None
        #     → both `if attendance:` blocks skipped (history/.first() and student_review skip)
        #   enrolled check: [4] my_attendance (truthy!), [5] my_instructor_review (line 424)
        #   [6] parent semester query (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [
            s, instructor_obj,
            student_obj, None,
            my_attendance, my_instructor_review,
            None,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "session_details.html"

    def test_booking_loop_with_attendance_history_and_student_review(self):
        """Booking loop: student has attendance → history queried; student_review found →
        performance_review dict built. Covers lines 354-360, 372, 380."""
        user = _student(uid=99)
        s = self._make_session_obj(user_id=42)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        booking = MagicMock()
        booking.user_id = 55          # different from user.id → is_enrolled=False
        booking.id = 7

        student_obj = MagicMock()
        student_obj.id = 55
        student_obj.name = "OtherStudent"
        student_obj.email = "other@test.com"

        attendance = MagicMock()
        attendance.id = 10

        h_record = MagicMock()
        h_record.changed_by = 42
        h_record.change_type = "update"
        h_record.old_value = "absent"
        h_record.new_value = "present"
        h_record.reason = "late arrival"
        h_record.created_at = _now_budapest()

        changer_obj = MagicMock()
        changer_obj.name = "Coach"

        sr = MagicMock()
        sr.punctuality = 8
        sr.engagement = 9
        sr.focus = 7
        sr.collaboration = 8
        sr.attitude = 9
        sr.comments = "Good"
        sr.average_score = 8.2

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [booking]
        # history_records: filter().order_by().all()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [h_record]
        db.query.return_value.filter.return_value.count.return_value = 0
        # .first() sequence:
        # 1. session, 2. instructor_obj,
        # booking loop: 3. student_obj, 4. attendance(found), 5. changer_obj, 6. sr(found)
        # student NOT enrolled (booking.user_id=55 != user.id=99) → no my_attendance queries
        # 7. parent semester query (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [
            s, instructor_obj,
            student_obj, attendance, changer_obj, sr,
            None,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "session_details.html"

    def test_session_details_hybrid_session_loads_quiz_data_for_student(self):
        """Hybrid session: quiz data loaded for enrolled student. Covers lines 448-524 student path."""
        user = _student(uid=99)

        s = MagicMock()
        s.id = 1
        s.instructor_id = 42
        s.session_type = MagicMock()
        s.session_type.value = "hybrid"
        now = _now_budapest()
        s.date_start = now + timedelta(hours=2)
        s.date_end = now + timedelta(hours=3)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        booking = MagicMock()
        booking.user_id = 99          # enrolled student
        booking.id = 7

        sq = MagicMock()
        sq.quiz_id = 5

        quiz_obj = MagicMock()
        quiz_obj.id = 5
        quiz_obj.title = "Tactics Quiz"
        quiz_obj.description = "Test"
        quiz_obj.passing_score = 0.75
        quiz_obj.time_limit_minutes = 30

        attempt = MagicMock()
        attempt.score = 80.0
        attempt.passed = True
        attempt.completed_at = _now_budapest()
        attempt.correct_answers = 8
        attempt.total_questions = 10

        db = MagicMock()
        # .all(): bookings=[booking], sq_records=[sq]
        db.query.return_value.filter.return_value.all.side_effect = [[booking], [sq]]
        # QuizAttempt.order_by().all() for student attempts
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [attempt]
        db.query.return_value.filter.return_value.count.return_value = 10
        student_in_loop = MagicMock()
        student_in_loop.id = 99
        # .first(): session, instructor_obj, student(loop), attendance=None(loop),
        #           my_attendance=None(enrolled check), quiz_obj,
        #           parent semester (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [
            s, instructor_obj,
            student_in_loop, None,
            None,
            quiz_obj,
            None,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["session_quizzes"] != []

    # ── Sprint 52: BrPart fixes ───────────────────────────────────────────────

    def test_session_details_booking_student_not_found_skips_loop(self):
        """Booking loop: student=None → skip body, back to loop (344→342)."""
        user = _student(uid=99)
        s = self._make_session_obj(user_id=42)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        booking = MagicMock()
        booking.user_id = 55
        booking.id = 7

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [booking]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0
        # .first(): session, instructor_obj, student=None (skip body),
        #           parent semester (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [s, instructor_obj, None, None]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["enrolled_students"] == []   # student not found → not appended

    def test_session_details_enrolled_student_with_attendance_gets_review_query(self):
        """Enrolled student, my_attendance found → InstructorSessionReview queried (covers line 424)."""
        user = _student(uid=99)
        s = self._make_session_obj(user_id=42)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        booking = MagicMock()
        booking.user_id = 99   # enrolled
        booking.id = 7

        student_obj = MagicMock()
        student_obj.id = 99
        student_obj.name = "Student"
        student_obj.email = "s@test.com"

        my_attendance = MagicMock()
        my_instructor_review = MagicMock()

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [booking]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0
        # .first() order for on_site session (no quiz block after line 427):
        #   [0] session, [1] instructor_obj,
        #   booking loop: [2] student_obj, [3] attendance=None
        #     → `if attendance:` False → no history .first(), no student_review .first()
        #   enrolled check: [4] my_attendance (truthy → True!), [5] my_instructor_review (line 424)
        #   [6] parent semester (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [
            s, instructor_obj,
            student_obj, None,
            my_attendance, my_instructor_review,
            None,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "session_details.html"

    def test_session_details_sq_quiz_not_found_skips(self):
        """Hybrid session: sq in sq_records but quiz=None → skip (452→450)."""
        user = _student(uid=99)

        s = MagicMock()
        s.id = 1
        s.instructor_id = 42
        s.session_type = MagicMock()
        s.session_type.value = "hybrid"
        now = _now_budapest()
        s.date_start = now + timedelta(hours=2)
        s.date_end = now + timedelta(hours=3)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        sq = MagicMock()
        sq.quiz_id = 5

        db = MagicMock()
        db.query.return_value.filter.return_value.all.side_effect = [[], [sq]]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 0
        # .first(): session, instructor_obj, quiz=None (skip body),
        #           parent semester (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [s, instructor_obj, None, None]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["session_quizzes"] == []   # quiz not found → not appended

    def test_session_details_instructor_student_none_in_quiz_loop(self):
        """Instructor view: student=None in inner booking loop → skip (479→477)."""
        user = _instructor(uid=42)

        s = MagicMock()
        s.id = 1
        s.instructor_id = 42
        s.session_type = MagicMock()
        s.session_type.value = "hybrid"
        now = _now_budapest()
        s.date_start = now - timedelta(hours=1)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        booking = MagicMock()
        booking.user_id = 55
        booking.id = 7

        sq = MagicMock()
        sq.quiz_id = 5

        quiz_obj = MagicMock()
        quiz_obj.id = 5
        quiz_obj.passing_score = 0.75
        quiz_obj.time_limit_minutes = 30

        db = MagicMock()
        db.query.return_value.filter.return_value.all.side_effect = [[booking], [sq]]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.count.return_value = 5
        # .first(): session, instructor_obj,
        #   booking loop: student=None → skip (344→342)
        #   quiz fetch: quiz_obj
        #   instructor quiz loop: student=None → skip (479→477)
        #   parent semester (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [
            s, instructor_obj,
            None,      # booking loop: student not found → skip
            quiz_obj,  # quiz fetch
            None,      # instructor quiz loop: student not found → skip
            None,      # parent semester
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["session_quizzes"] != []
        assert ctx["session_quizzes"][0]["student_results"] == []

    def test_session_details_instructor_multiple_attempts_best_score_tracking(self):
        """Instructor view: 2 attempts — first is best, second is worse → 503→508 + 508→493."""
        user = _instructor(uid=42)

        s = MagicMock()
        s.id = 1
        s.instructor_id = 42
        s.session_type = MagicMock()
        s.session_type.value = "hybrid"
        now = _now_budapest()
        s.date_start = now - timedelta(hours=1)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        booking = MagicMock()
        booking.user_id = 55
        booking.id = 7

        sq = MagicMock()
        sq.quiz_id = 5

        quiz_obj = MagicMock()
        quiz_obj.id = 5
        quiz_obj.passing_score = 0.75
        quiz_obj.time_limit_minutes = 30

        student_result_user = MagicMock()
        student_result_user.id = 55
        student_result_user.name = "Student"
        student_result_user.email = "s@test.com"

        # First attempt: best
        attempt_best = MagicMock()
        attempt_best.score = 90.0
        attempt_best.passed = True
        attempt_best.completed_at = _now_budapest()
        attempt_best.correct_answers = 9
        attempt_best.total_questions = 10
        attempt_best.time_spent_minutes = 15

        # Second attempt: worse score (503 False), no completed_at (508 False → 508→493)
        attempt_worse = MagicMock()
        attempt_worse.score = 60.0
        attempt_worse.passed = False
        attempt_worse.completed_at = None
        attempt_worse.correct_answers = 6
        attempt_worse.total_questions = 10
        attempt_worse.time_spent_minutes = 20

        db = MagicMock()
        db.query.return_value.filter.return_value.all.side_effect = [[booking], [sq]]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [attempt_best, attempt_worse]
        db.query.return_value.filter.return_value.count.return_value = 10
        db.query.return_value.filter.return_value.first.side_effect = [
            s, instructor_obj,
            student_result_user, None,  # booking loop: student found, attendance=None
            student_result_user,        # instructor quiz loop: student found
            quiz_obj,
            None,                       # parent semester (semester_id truthy but not a tournament)
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        result = ctx["session_quizzes"][0]["student_results"][0]
        assert result["best_score"] == 90.0   # first attempt stays best

    def test_session_details_hybrid_session_instructor_quiz_results(self):
        """Hybrid session: instructor view → student quiz results loaded. Covers lines 475-524."""
        user = _instructor(uid=42)

        s = MagicMock()
        s.id = 1
        s.instructor_id = 42
        s.session_type = MagicMock()
        s.session_type.value = "hybrid"
        now = _now_budapest()
        s.date_start = now - timedelta(hours=1)
        s.date_end = now + timedelta(hours=1)

        instructor_obj = MagicMock()
        instructor_obj.name = "Coach"

        booking = MagicMock()
        booking.user_id = 55
        booking.id = 7

        sq = MagicMock()
        sq.quiz_id = 5

        quiz_obj = MagicMock()
        quiz_obj.id = 5
        quiz_obj.title = "Tactics Quiz"
        quiz_obj.description = "Test"
        quiz_obj.passing_score = 0.75
        quiz_obj.time_limit_minutes = 30

        student_result_user = MagicMock()
        student_result_user.id = 55
        student_result_user.name = "Student"
        student_result_user.email = "s@test.com"

        attempt = MagicMock()
        attempt.score = 90.0
        attempt.passed = True
        attempt.completed_at = _now_budapest()
        attempt.correct_answers = 9
        attempt.total_questions = 10
        attempt.time_spent_minutes = 15

        db = MagicMock()
        db.query.return_value.filter.return_value.all.side_effect = [[booking], [sq]]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [attempt]
        db.query.return_value.filter.return_value.count.return_value = 10
        student_in_loop = MagicMock()
        student_in_loop.id = 55
        # .first(): session, instructor_obj,
        #   booking loop: student_in_loop, attendance=None,
        #   instructor quiz results: student_result_user, quiz_obj,
        #   parent semester (semester_id truthy but not a tournament)
        db.query.return_value.filter.return_value.first.side_effect = [
            s, instructor_obj,
            student_in_loop, None,
            student_result_user,
            quiz_obj,
            None,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(session_details(request=_req(), session_id=1, db=db, user=user))

        ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["session_quizzes"] != []

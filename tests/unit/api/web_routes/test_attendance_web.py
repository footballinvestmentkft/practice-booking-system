"""
Unit tests for app/api/web_routes/attendance.py

Covers:
  mark_attendance — not_instructor, session_not_found, too_early, session_ended,
                    student_not_enrolled, invalid_status,
                    new_attendance_success, update_confirmed_change_request,
                    update_pending_updates_directly, present_sets_check_in_time
  confirm_attendance — not_student, no_attendance, session_ended, confirm_success,
                       dispute_success, invalid_action
  handle_change_request — not_student, no_change_request, approve_success,
                          reject_success, invalid_action

Mock strategy:
  - asyncio.run(endpoint(...)) direct call
  - db.query().filter().first() → side_effect list for multi-query routes
  - Timezone mocked via freezegun-style datetime patching or MagicMock date_start/end
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from zoneinfo import ZoneInfo
from fastapi.responses import RedirectResponse
import pytest

from app.api.web_routes.attendance import (
    mark_attendance,
    confirm_attendance,
    handle_change_request,
)
from app.models.user import UserRole
from app.models.attendance import AttendanceStatus, ConfirmationStatus
from app.services.player_participation_service import ParticipationError


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_BASE = "app.api.web_routes.attendance"


def _instructor(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.INSTRUCTOR
    return u


def _student(uid=99):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    return u


def _req():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


_BUD = ZoneInfo("Europe/Budapest")


def _now_budapest():
    """Current time as naive Budapest datetime (matches DB storage convention)."""
    return datetime.now(_BUD).replace(tzinfo=None)


def _session_in_window(session_id=1, instructor_id=42):
    """Session whose marking window is open: started 30 min ago, ends in 30 min (Budapest)."""
    s = MagicMock()
    s.id = session_id
    s.instructor_id = instructor_id
    now = _now_budapest()
    s.date_start = now - timedelta(minutes=30)
    s.date_end = now + timedelta(minutes=30)
    return s


def _session_ended(session_id=1, instructor_id=42):
    """Session that ended 1 hour ago (Budapest)."""
    s = MagicMock()
    s.id = session_id
    s.instructor_id = instructor_id
    now = _now_budapest()
    s.date_start = now - timedelta(hours=2)
    s.date_end = now - timedelta(hours=1)
    return s


def _session_future(session_id=1, instructor_id=42):
    """Session starting 2 hours from now (Budapest) — too early to mark attendance."""
    s = MagicMock()
    s.id = session_id
    s.instructor_id = instructor_id
    now = _now_budapest()
    s.date_start = now + timedelta(hours=2)
    s.date_end = now + timedelta(hours=3)
    return s


# ──────────────────────────────────────────────────────────────────────────────
# mark_attendance
# ──────────────────────────────────────────────────────────────────────────────

class TestMarkAttendance:

    def test_requires_confirmed_booking(self):
        db = MagicMock()
        with patch(f"{_BASE}.record_attendance", side_effect=ParticipationError("BOOKING_NOT_FOUND")):
            result = _run(mark_attendance(
                request=_req(), session_id=1, student_id=99, status="present",
                notes=None, db=db, user=_instructor()
            ))
        assert "student_not_enrolled" in result.headers["location"]

    def test_policy_error_redirects_with_canonical_code(self):
        db = MagicMock()
        with patch(f"{_BASE}.record_attendance", side_effect=ParticipationError("INSTRUCTOR_NOT_ASSIGNED")):
            result = _run(mark_attendance(
                request=_req(), session_id=1, student_id=99, status="present",
                notes=None, db=db, user=_instructor()
            ))
        assert "instructor_not_assigned" in result.headers["location"]

    def test_invalid_status_redirects(self):
        db = MagicMock()
        with patch(f"{_BASE}.record_attendance", side_effect=ValueError("invalid")):
            result = _run(mark_attendance(
                request=_req(), session_id=1, student_id=99, status="bad",
                notes=None, db=db, user=_instructor()
            ))
        assert "invalid_status" in result.headers["location"]

    def test_delegates_to_canonical_attendance_authority(self):
        db=MagicMock(); actor=_instructor()
        with patch(
            f"{_BASE}.record_attendance",
            return_value=MagicMock(replayed=False, change_requested=False),
        ) as command:
            result = _run(mark_attendance(
                request=_req(), session_id=1, student_id=99, status="LATE",
                notes="traffic", db=db, user=actor
            ))
        command.assert_called_once_with(
            db, actor=actor, player_id=99, session_id=1,
            status="late", notes="traffic", source="WEB"
        )
        assert "attendance_marked" in result.headers["location"]

    def test_replay_is_reported(self):
        db=MagicMock()
        with patch(
            f"{_BASE}.record_attendance",
            return_value=MagicMock(replayed=True, change_requested=False),
        ):
            result = _run(mark_attendance(
                request=_req(), session_id=1, student_id=99, status="present",
                notes=None, db=db, user=_instructor()
            ))
        assert "attendance_unchanged" in result.headers["location"]

    def test_confirmed_attendance_change_is_reported_as_request(self):
        db = MagicMock()
        command_result = MagicMock(replayed=False, change_requested=True)
        with patch(f"{_BASE}.record_attendance", return_value=command_result):
            result = _run(mark_attendance(
                request=_req(), session_id=1, student_id=99, status="late",
                notes="corrected", db=db, user=_instructor()
            ))
        assert "change_requested" in result.headers["location"]


class TestConfirmAttendance:

    @pytest.mark.parametrize("code", [
        "PLAYER_ROLE_REQUIRED", "ATTENDANCE_NOT_FOUND", "SESSION_ENDED",
        "DISPUTE_REASON_REQUIRED", "INVALID_ATTENDANCE_RESPONSE",
    ])
    def test_policy_error_redirects_with_canonical_code(self, code):
        with patch(f"{_BASE}.respond_to_attendance", side_effect=ParticipationError(code)):
            result = _run(confirm_attendance(
                request=_req(), session_id=1, action="confirm", dispute_reason=None,
                db=MagicMock(), user=_student()
            ))
        assert code.lower() in result.headers["location"]

    @pytest.mark.parametrize(("action", "outcome"), [("confirm", "confirmed"), ("dispute", "disputed")])
    def test_delegates_player_response(self, action, outcome):
        db=MagicMock(); player=_student()
        with patch(f"{_BASE}.respond_to_attendance", return_value=MagicMock(outcome=action)) as command:
            result = _run(confirm_attendance(
                request=_req(), session_id=1, action=action, dispute_reason="reason",
                db=db, user=player
            ))
        command.assert_called_once_with(
            db, player=player, session_id=1, action=action,
            dispute_reason="reason", source="WEB"
        )
        assert f"attendance_{outcome}" in result.headers["location"]


class TestHandleChangeRequest:

    @pytest.mark.parametrize("code", [
        "PLAYER_ROLE_REQUIRED", "ATTENDANCE_CHANGE_REQUEST_NOT_FOUND",
        "INVALID_ATTENDANCE_STATUS", "INVALID_CHANGE_REQUEST_RESPONSE",
    ])
    def test_policy_error_redirects_with_canonical_code(self, code):
        with patch(f"{_BASE}.resolve_attendance_change_request", side_effect=ParticipationError(code)):
            result = _run(handle_change_request(
                request=_req(), session_id=1, action="approve",
                db=MagicMock(), user=_student()
            ))
        assert code.lower() in result.headers["location"]

    @pytest.mark.parametrize("action", ["approve", "reject"])
    def test_delegates_change_resolution(self, action):
        outcome = f"change_{'approved' if action == 'approve' else 'rejected'}"
        db=MagicMock(); player=_student()
        with patch(
            f"{_BASE}.resolve_attendance_change_request",
            return_value=MagicMock(outcome=outcome),
        ) as command:
            result = _run(handle_change_request(
                request=_req(), session_id=1, action=action, db=db, user=player
            ))
        command.assert_called_once_with(
            db, player=player, session_id=1, action=action, source="WEB"
        )
        assert outcome in result.headers["location"]

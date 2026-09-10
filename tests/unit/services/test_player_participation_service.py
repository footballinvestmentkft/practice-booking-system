from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.attendance import AttendanceStatus
from app.models.booking import BookingStatus
from app.models.session import EventCategory
from app.models.specialization import SpecializationType
from app.services.player_participation_service import (
    ParticipationError,
    booking_window_decision,
    participation_lifecycle_status,
    session_target_category,
)


def _session(**overrides):
    values = {
        "date_start": datetime(2026, 10, 2, 18, 0),
        "date_end": datetime(2026, 10, 2, 19, 30),
        "session_status": "scheduled",
        "event_category": EventCategory.TRAINING,
        "target_specialization": SpecializationType.LFA_FOOTBALL_PLAYER,
        "semester": SimpleNamespace(age_group="YOUTH", age_groups=None),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_booking_window_uses_one_24_hour_policy():
    session = _session()
    assert booking_window_decision(session, datetime(2026, 10, 1, 17, 59)).allowed
    decision = booking_window_decision(session, datetime(2026, 10, 1, 18, 0))
    assert not decision.allowed
    assert decision.code == "BOOKING_DEADLINE_PASSED"


@pytest.mark.parametrize("state", ["in_progress", "completed", "cancelled"])
def test_only_scheduled_session_is_bookable(state):
    decision = booking_window_decision(_session(session_status=state), datetime(2026, 9, 1))
    assert not decision.allowed
    assert decision.code == "SESSION_NOT_BOOKABLE"


def test_player_session_requires_one_explicit_category():
    assert session_target_category(_session()) == "YOUTH"
    with pytest.raises(ParticipationError, match="SESSION_CATEGORY_REQUIRED"):
        session_target_category(_session(semester=SimpleNamespace(age_group=None, age_groups=None)))
    with pytest.raises(ParticipationError, match="SESSION_CATEGORY_AMBIGUOUS"):
        session_target_category(
            _session(semester=SimpleNamespace(age_group=None, age_groups=["PRE", "YOUTH"]))
        )


@pytest.mark.parametrize(
    ("booking_status", "attendance_status", "expected"),
    [
        (BookingStatus.CONFIRMED, None, "confirmed"),
        (BookingStatus.WAITLISTED, None, "waitlisted"),
        (BookingStatus.CANCELLED, None, "cancelled"),
        (BookingStatus.CONFIRMED, AttendanceStatus.present, "completed"),
        (BookingStatus.CONFIRMED, AttendanceStatus.absent, "completed"),
    ],
)
def test_participation_lifecycle_projection(booking_status, attendance_status, expected):
    booking = SimpleNamespace(status=booking_status)
    attendance = (
        SimpleNamespace(status=attendance_status) if attendance_status is not None else None
    )
    assert participation_lifecycle_status(booking, attendance) == expected

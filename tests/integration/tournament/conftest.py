"""
Fixtures for tournament integration tests.
"""

import uuid
import pytest
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.booking import Booking, BookingStatus
from app.models.user import User


@pytest.fixture(scope="function")
def tournament_semester_with_instructor(test_db: Session, instructor_user: User) -> Semester:
    """Create a tournament semester with a master instructor assigned."""
    semester = Semester(
        code=f"TOURN-{uuid.uuid4().hex[:8]}",
        name="Tournament Semester",
        start_date=date.today() + timedelta(days=1),
        end_date=date.today() + timedelta(days=90),
        status=SemesterStatus.ONGOING,
        master_instructor_id=instructor_user.id,
        tournament_status="IN_PROGRESS",
    )
    test_db.add(semester)
    test_db.commit()
    test_db.refresh(semester)
    return semester


@pytest.fixture(scope="function")
def tournament_session_with_bookings(
    test_db: Session,
    tournament_semester_with_instructor: Semester,
    instructor_user: User,
    student_user: User,
) -> SessionModel:
    """Create a tournament session with confirmed bookings."""
    session_start = datetime.now() + timedelta(days=7, hours=10)
    session = SessionModel(
        title="Tournament Game Session",
        date_start=session_start,
        date_end=session_start + timedelta(minutes=90),
        session_type=SessionType.on_site,
        capacity=20,
        instructor_id=instructor_user.id,
        semester_id=tournament_semester_with_instructor.id,
        event_category=EventCategory.MATCH,
        game_type="Skills Challenge",
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)

    # Create a second student so multiple bookings exist
    student2 = User(
        email=f"student2+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Student Two",
        password_hash="hashed",
        role="STUDENT",
        is_active=True,
    )
    test_db.add(student2)
    test_db.commit()
    test_db.refresh(student2)

    # Create confirmed bookings
    for user in [student_user, student2]:
        booking = Booking(
            user_id=user.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(booking)

    test_db.commit()
    test_db.refresh(session)
    return session

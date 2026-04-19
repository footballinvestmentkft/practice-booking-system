"""
API Test Fixtures

Uses PostgreSQL engine with transactional SAVEPOINT isolation.
Each test gets a fresh transactional session — all changes are
automatically rolled back at teardown.

No SQLite. No mocks. Real endpoints against real PostgreSQL.
"""

import pytest
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.database import engine, get_db
from app.main import app
from app.models.user import User, UserRole
from app.models.specialization import SpecializationType
from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.booking import Booking, BookingStatus
from app.models.instructor_assignment import InstructorAssignmentRequest, AssignmentRequestStatus
from app.models.coupon import Coupon, CouponUsage, CouponType
from app.models.invitation_code import InvitationCode
from app.models.license import UserLicense
from app.core.security import get_password_hash
from app.core.auth import create_access_token


# ============================================================================
# DATABASE FIXTURES — PostgreSQL + SAVEPOINT isolation
# ============================================================================

@pytest.fixture(scope="function")
def postgres_db():
    """
    PostgreSQL session with transactional rollback.

    Uses nested transactions (SAVEPOINT) so test code can call commit()
    while all changes are still rolled back at teardown.
    """
    connection = engine.connect()
    transaction = connection.begin()

    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSessionLocal()

    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture(scope="function")
def test_db(postgres_db: Session):
    """Alias for backward compatibility with tests expecting test_db."""
    return postgres_db


@pytest.fixture(scope="function")
def client(test_db: Session):
    """
    FastAPI TestClient bound to the test PostgreSQL session.

    get_db is overridden to yield the same transactional session,
    so all endpoint DB operations participate in the rollback.
    """
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


# ============================================================================
# USER FIXTURES
# ============================================================================

@pytest.fixture
def admin_user(test_db: Session) -> User:
    user = User(
        email=f"admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Admin User",
        password_hash=get_password_hash("admin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def instructor_user(test_db: Session) -> User:
    user = User(
        email=f"instructor+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Instructor User",
        password_hash=get_password_hash("instructor123"),
        role=UserRole.INSTRUCTOR,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def student_user(test_db: Session) -> User:
    user = User(
        email=f"student+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Student User",
        password_hash=get_password_hash("student123"),
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2005, 1, 15),
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def student_users(test_db: Session) -> list:
    students = []
    for i in range(10):
        user = User(
            email=f"student{i}+{uuid.uuid4().hex[:8]}@lfa.com",
            name=f"Student {i+1}",
            password_hash=get_password_hash("student123"),
            role=UserRole.STUDENT,
            is_active=True,
            date_of_birth=date(2005, 1, (i % 28) + 1),
        )
        test_db.add(user)
        students.append(user)

    test_db.commit()
    for student in students:
        test_db.refresh(student)

    return students


# ============================================================================
# AUTH FIXTURES
# ============================================================================

@pytest.fixture
def admin_token(admin_user: User) -> str:
    return create_access_token(data={"sub": admin_user.email})


@pytest.fixture
def instructor_token(instructor_user: User) -> str:
    return create_access_token(data={"sub": instructor_user.email})


@pytest.fixture
def student_token(student_user: User) -> str:
    return create_access_token(data={"sub": student_user.email})


# ============================================================================
# TOURNAMENT FIXTURES
# ============================================================================

@pytest.fixture
def tournament_date() -> date:
    return date.today() + timedelta(days=7)


@pytest.fixture
def tournament_semester(test_db: Session, tournament_date: date) -> Semester:
    semester = Semester(
        code=f"TOURN-{tournament_date.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}",
        name="Test Tournament",
        start_date=tournament_date,
        end_date=tournament_date,
        status=SemesterStatus.SEEKING_INSTRUCTOR,
        master_instructor_id=None,
        specialization_type=SpecializationType.LFA_PLAYER_YOUTH.value,
        age_group="YOUTH",
    )
    test_db.add(semester)
    test_db.commit()
    test_db.refresh(semester)
    return semester


@pytest.fixture
def tournament_semester_with_instructor(
    test_db: Session,
    tournament_date: date,
    instructor_user: User,
) -> Semester:
    semester = Semester(
        code=f"TOURN-{tournament_date.strftime('%Y%m%d')}-READY-{uuid.uuid4().hex[:6]}",
        name="Ready Tournament",
        start_date=tournament_date,
        end_date=tournament_date,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        master_instructor_id=instructor_user.id,
        specialization_type=SpecializationType.LFA_PLAYER_YOUTH.value,
        age_group="YOUTH",
    )
    test_db.add(semester)
    test_db.commit()
    test_db.refresh(semester)
    return semester


@pytest.fixture
def tournament_sessions(
    test_db: Session,
    tournament_semester: Semester,
    tournament_date: date,
) -> list:
    sessions = []
    times = ["09:00", "11:00", "14:00"]

    for i, time_str in enumerate(times):
        start_time = datetime.strptime(f"{tournament_date} {time_str}", "%Y-%m-%d %H:%M")
        end_time = start_time + timedelta(minutes=90)

        session = SessionModel(
            title=f"Tournament Game {i+1}",
            description=f"Game {i+1} description",
            date_start=start_time,
            date_end=end_time,
            session_type=SessionType.on_site,
            capacity=20,
            instructor_id=None,
            semester_id=tournament_semester.id,
            credit_cost=1,
            event_category=EventCategory.MATCH,
            game_type=f"Round {i+1}",
        )
        test_db.add(session)
        sessions.append(session)

    test_db.commit()
    for session in sessions:
        test_db.refresh(session)

    return sessions


@pytest.fixture
def tournament_session_with_bookings(
    test_db: Session,
    tournament_semester_with_instructor: Semester,
    tournament_date: date,
    student_users: list,
) -> SessionModel:
    start_time = datetime.strptime(f"{tournament_date} 10:00", "%Y-%m-%d %H:%M")
    end_time = start_time + timedelta(minutes=90)

    session = SessionModel(
        title="Tournament Final",
        description="Championship final game",
        date_start=start_time,
        date_end=end_time,
        session_type=SessionType.on_site,
        capacity=20,
        instructor_id=tournament_semester_with_instructor.master_instructor_id,
        semester_id=tournament_semester_with_instructor.id,
        credit_cost=1,
        is_tournament_game=True,
        game_type="Final",
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)

    for student in student_users[:5]:
        booking = Booking(
            user_id=student.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(booking)

    test_db.commit()
    return session


@pytest.fixture
def instructor_assignment_request(
    test_db: Session,
    tournament_semester: Semester,
    instructor_user: User,
    admin_user: User,
) -> InstructorAssignmentRequest:
    request = InstructorAssignmentRequest(
        semester_id=tournament_semester.id,
        instructor_id=instructor_user.id,
        requested_by=admin_user.id,
        status=AssignmentRequestStatus.PENDING,
        request_message=f"Please lead the '{tournament_semester.name}' tournament",
    )
    test_db.add(request)
    test_db.commit()
    test_db.refresh(request)
    return request


# ============================================================================
# UTILITY FIXTURES
# ============================================================================

@pytest.fixture
def today() -> date:
    return date.today()


@pytest.fixture
def future_date() -> date:
    return date.today() + timedelta(days=30)


@pytest.fixture
def past_date() -> date:
    return date.today() - timedelta(days=30)

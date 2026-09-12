"""
Shared fixtures for web_flows integration tests.

Each fixture creates minimal real DB objects via the SAVEPOINT-isolated
test_db from tests/integration/conftest.py.

Auth pattern:
    app.dependency_overrides[get_current_user_web] = lambda: user
    → bypasses cookie auth, injects the user directly into every request.

CSRF bypass:
    CSRFProtectionMiddleware skips validation for requests with an
    Authorization: Bearer ... header (Bearer auth is already CSRF-safe via CORS).
    We pass a dummy Bearer token in the TestClient default headers to bypass CSRF
    for all web_flows integration tests.

Timing design (Budapest naive datetimes, matching DB storage convention):
    future_session  : starts NOW +24h  → bookable (>12h deadline)
    active_session  : started 5min ago, ends +1h  → attendance markable
    hybrid_session  : like active_session + actual_start_time set, HYBRID type
"""

import uuid
import pytest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web
from app.models.semester import Semester, SemesterStatus
from app.models.semester_enrollment import EnrollmentStatus, SemesterEnrollment
from app.models.session import EventCategory, Session as SessionModel, SessionType
from app.models.booking import Booking, BookingStatus
from app.models.license import UserLicense
from app.models.quiz import Quiz, QuizCategory, QuizDifficulty
from app.models.specialization import SpecializationType
from app.services.player_identity_service import issue_football_player_entitlement


# ── Timing helper ─────────────────────────────────────────────────────────────

def _now_bp() -> datetime:
    """Current Budapest time as naive datetime (how sessions are stored in DB)."""
    return datetime.now(ZoneInfo("Europe/Budapest")).replace(tzinfo=None)


# ── SAVEPOINT-isolated DB fixture (web_flows local) ───────────────────────────

@pytest.fixture(scope="function")
def test_db():
    """
    PostgreSQL session with per-test SAVEPOINT isolation.
    Local copy (same pattern as tests/integration/conftest.py).
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


# ── Authenticated clients with CSRF bypass ────────────────────────────────────

@pytest.fixture
def student_client(test_db: Session, student_user):
    """
    TestClient with:
    - get_db overridden → shares test_db SAVEPOINT session
    - get_current_user_web overridden → student_user injected directly
    - Authorization: Bearer bypass header → CSRFProtectionMiddleware skips validation
    """
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_web] = lambda: student_user

    with TestClient(
        app,
        headers={"Authorization": "Bearer test-csrf-bypass"},
    ) as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def instructor_client(test_db: Session, instructor_user):
    """
    TestClient with:
    - get_db overridden → shares test_db SAVEPOINT session
    - get_current_user_web overridden → instructor_user injected directly
    - Authorization: Bearer bypass header → CSRFProtectionMiddleware skips validation
    """
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_web] = lambda: instructor_user

    with TestClient(
        app,
        headers={"Authorization": "Bearer test-csrf-bypass"},
    ) as client:
        yield client

    app.dependency_overrides.clear()


# ── Data fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def semester(test_db: Session, student_user, instructor_user) -> Semester:
    """Canonical Football semester with Player and Coach participation context."""
    sem = Semester(
        code=f"WF-{uuid.uuid4().hex[:8].upper()}",
        name="Web Flow Test Semester",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=90),
        status=SemesterStatus.ONGOING,
        specialization_type="LFA_FOOTBALL_PLAYER",
        age_group="AMATEUR",
    )
    test_db.add(sem)
    test_db.flush()
    entitlement = issue_football_player_entitlement(
        test_db,
        user=student_user,
        payment_verified=True,
        on_date=date.today(),
    )
    test_db.add(SemesterEnrollment(
        user_id=student_user.id,
        semester_id=sem.id,
        user_license_id=entitlement.license.id,
        football_category_assignment_id=entitlement.assignment.id,
        request_status=EnrollmentStatus.APPROVED,
        payment_verified=True,
        is_active=True,
        age_category=entitlement.assignment.effective_category,
    ))
    instructor_user.specialization = SpecializationType.LFA_COACH
    test_db.add(UserLicense(
        user_id=instructor_user.id,
        specialization_type="LFA_COACH",
        canonical_program_id="LFA_COACH",
        current_level=5,
        max_achieved_level=5,
        started_at=datetime.utcnow(),
        is_active=True,
    ))
    test_db.commit()
    test_db.refresh(sem)
    return sem


@pytest.fixture
def future_session(test_db: Session, semester: Semester, instructor_user) -> SessionModel:
    """On-site session starting 48h from now — safely before the 24h booking cutoff."""
    now = _now_bp()
    s = SessionModel(
        title="Future On-Site Session",
        semester_id=semester.id,
        session_type=SessionType.on_site,
        date_start=now + timedelta(hours=48),
        date_end=now + timedelta(hours=49),
        instructor_id=instructor_user.id,
        target_specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
        event_category=EventCategory.TRAINING,
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


@pytest.fixture
def active_session(test_db: Session, semester: Semester, instructor_user) -> SessionModel:
    """On-site session currently in progress (started 5min ago, ends +1h).

    Satisfies mark_attendance time window:
      (date_start - 15min) <= now <= date_end
    Satisfies confirm_attendance time window:
      now <= date_end
    """
    now = _now_bp()
    s = SessionModel(
        title="Active On-Site Session",
        semester_id=semester.id,
        session_type=SessionType.on_site,
        date_start=now - timedelta(minutes=5),
        date_end=now + timedelta(hours=1),
        instructor_id=instructor_user.id,
        target_specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
        event_category=EventCategory.TRAINING,
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


@pytest.fixture
def hybrid_session(test_db: Session, semester: Semester, instructor_user) -> SessionModel:
    """Hybrid session that was started (actual_start_time set).

    Satisfies unlock_quiz requirements:
      session_type == HYBRID
      instructor_id == instructor_user.id
      actual_start_time is not None
    """
    now = _now_bp()
    s = SessionModel(
        title="Hybrid Session",
        semester_id=semester.id,
        session_type=SessionType.hybrid,
        date_start=now - timedelta(minutes=5),
        date_end=now + timedelta(hours=1),
        instructor_id=instructor_user.id,
        actual_start_time=datetime.utcnow(),  # session was explicitly started
        target_specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
        event_category=EventCategory.TRAINING,
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


@pytest.fixture
def future_booking(test_db: Session, future_session: SessionModel, student_user) -> Booking:
    """CONFIRMED booking for student_user on future_session."""
    b = Booking(
        user_id=student_user.id,
        session_id=future_session.id,
        status=BookingStatus.CONFIRMED,
    )
    test_db.add(b)
    test_db.commit()
    test_db.refresh(b)
    return b


@pytest.fixture
def active_booking(test_db: Session, active_session: SessionModel, student_user) -> Booking:
    """CONFIRMED booking for student_user on active_session (for attendance marking)."""
    b = Booking(
        user_id=student_user.id,
        session_id=active_session.id,
        status=BookingStatus.CONFIRMED,
    )
    test_db.add(b)
    test_db.commit()
    test_db.refresh(b)
    return b


@pytest.fixture
def near_future_session(test_db: Session, semester: Semester, instructor_user) -> SessionModel:
    """On-site session starting in 6h — WITHIN 12h cancellation deadline.

    Cancellation deadline = date_start - 12h = NOW - 6h (already passed).
    Used by: test_cancel_after_deadline_returns_error.
    """
    now = _now_bp()
    s = SessionModel(
        title="Near Future Session (6h)",
        semester_id=semester.id,
        session_type=SessionType.on_site,
        date_start=now + timedelta(hours=6),
        date_end=now + timedelta(hours=7),
        instructor_id=instructor_user.id,
        target_specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
        event_category=EventCategory.TRAINING,
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


@pytest.fixture
def near_future_booking(test_db: Session, near_future_session: SessionModel, student_user) -> Booking:
    """CONFIRMED booking for student_user on near_future_session (< 12h deadline)."""
    b = Booking(
        user_id=student_user.id,
        session_id=near_future_session.id,
        status=BookingStatus.CONFIRMED,
    )
    test_db.add(b)
    test_db.commit()
    test_db.refresh(b)
    return b


@pytest.fixture
def unstarted_hybrid_session(test_db: Session, semester: Semester, instructor_user) -> SessionModel:
    """Hybrid session that was NOT yet started (actual_start_time=None).

    unlock_quiz requirement NOT met → returns error=session_not_started_unlock.
    """
    now = _now_bp()
    s = SessionModel(
        title="Unstarted Hybrid Session",
        semester_id=semester.id,
        session_type=SessionType.hybrid,
        date_start=now - timedelta(minutes=5),
        date_end=now + timedelta(hours=1),
        instructor_id=instructor_user.id,
        actual_start_time=None,  # session not yet started
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


@pytest.fixture
def simple_quiz(test_db: Session) -> Quiz:
    """Minimal Quiz with no questions (loop is empty → score=0, fails threshold)."""
    q = Quiz(
        title="Web Flow Test Quiz",
        category=QuizCategory.GENERAL,
        difficulty=QuizDifficulty.EASY,
        time_limit_minutes=10,
        xp_reward=50,
        passing_score=0.6,  # 60% required to pass
    )
    test_db.add(q)
    test_db.commit()
    test_db.refresh(q)
    return q


# ── User fixtures (UUID-suffixed, reuse from parent conftest if injected) ─────
# The instructor_user / student_user fixtures are provided by
# tests/integration/conftest.py (function-scoped, UUID-suffixed emails).
# They are inherited automatically via pytest's fixture discovery.

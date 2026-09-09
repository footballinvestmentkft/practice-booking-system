"""
Integration Test Fixtures

Two fixture tiers:

1. SAVEPOINT-isolated (function-scoped) — for non-smoke integration tests that
   use `client`, `test_db`, `db` fixture names. Each test gets its own
   transactional savepoint; all changes are rolled back at teardown.
   Pattern: identical to tests/api/conftest.py.

2. Session-scoped (postgres_db / postgres_client) — legacy fixtures kept for
   backward compatibility with tests that reference them explicitly.
   ⚠️  Data written via these fixtures persists after the test run.
"""

# Manual scripts that use the `requests` library to hit a live backend at
# localhost:8000.  They are not proper pytest tests (no fixture injection) and
# must be excluded from automated collection.
collect_ignore = [
    "test_credit_validation_fix.py",
    "test_session_list_performance.py",
    "test_semester_api.py",
]

import uuid
import pytest
from datetime import date
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from app.database import engine, SessionLocal, get_db
from app.main import app
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.core.auth import create_access_token


# ============================================================================
# TIER 1 — SAVEPOINT-isolated (function-scoped)
# Identical pattern to tests/api/conftest.py
# ============================================================================

@pytest.fixture(scope="function")
def test_db():
    """
    PostgreSQL session with per-test SAVEPOINT isolation.

    Each test gets a nested transaction (SAVEPOINT); any commit() calls inside
    the test are safe. All changes are rolled back at teardown so the database
    remains clean for the next test.
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


# Alias: tests that import `db` instead of `test_db` (e.g. test_sandbox_regression.py)
@pytest.fixture(scope="function")
def db(test_db: Session):
    return test_db


@pytest.fixture(scope="function")
def client(test_db: Session):
    """
    FastAPI TestClient bound to the SAVEPOINT-isolated test session.

    All endpoint DB operations share the same transactional session as the
    test itself, and are fully rolled back at teardown.
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


# ── User fixtures (function-scoped, UUID-suffixed to avoid collisions) ──────

@pytest.fixture(scope="function")
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


@pytest.fixture(scope="function")
def instructor_user(test_db: Session) -> User:
    user = User(
        email=f"instructor+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Instructor User",
        password_hash=get_password_hash("instructor123"),
        role=UserRole.INSTRUCTOR,
        is_active=True,
        date_of_birth=date(1990, 1, 1),
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture(scope="function")
def student_user(test_db: Session) -> User:
    user = User(
        email=f"student+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Student User",
        password_hash=get_password_hash("student123"),
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2000, 1, 1),
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture(scope="function")
def admin_token(admin_user: User) -> str:
    return create_access_token(data={"sub": admin_user.email})


@pytest.fixture(scope="function")
def instructor_token(instructor_user: User) -> str:
    return create_access_token(data={"sub": instructor_user.email})


@pytest.fixture(scope="function")
def student_token(student_user: User) -> str:
    return create_access_token(data={"sub": student_user.email})


# ============================================================================
# TIER 2 — Session-scoped (legacy, data persists)
# ============================================================================

@pytest.fixture(scope="session")
def postgres_db():
    """
    Legacy session-scoped PostgreSQL fixture.

    ⚠️  Data written here persists after the test session.
    Use test_db (SAVEPOINT) for new tests.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session")
def postgres_client(postgres_db):
    """Legacy session-scoped FastAPI client bound to postgres_db."""
    def override_get_db():
        try:
            yield postgres_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def postgres_admin_user(postgres_db: Session) -> User:
    """Get or create admin@lfa.com in the PostgreSQL database."""
    admin = postgres_db.query(User).filter(User.email == "admin@lfa.com").first()

    if admin:
        return admin

    admin = User(
        email="admin@lfa.com",
        password_hash=get_password_hash("admin123"),
        name="Admin User",
        first_name="Admin",
        last_name="User",
        nickname="Admin",
        phone="+36201234567",
        role="ADMIN",
        is_active=True,
        credit_balance=0,
    )
    postgres_db.add(admin)
    postgres_db.commit()
    postgres_db.refresh(admin)
    return admin


@pytest.fixture(scope="session")
def postgres_admin_token(postgres_client: TestClient, postgres_admin_user: User) -> str:
    """JWT token for admin@lfa.com (session-scoped)."""
    response = postgres_client.post(
        "/api/v1/auth/login",
        json={"email": "admin@lfa.com", "password": "admin123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]

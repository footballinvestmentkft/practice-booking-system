"""
Biometric test fixtures.

Uses the same transactional SAVEPOINT pattern as tests/unit/conftest.py
for full test isolation without database pollution between tests.
"""
from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.user import User, UserRole
from app.services.biometric.audit_log import BiometricAuditLogger


@pytest.fixture(scope="function")
def db():
    """
    Transactional PostgreSQL session with SAVEPOINT-based rollback.
    Every test gets a clean slate; commits only affect the SAVEPOINT.
    """
    connection  = engine.connect()
    transaction = connection.begin()
    Session     = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session     = Session()
    nested      = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def admin_user(db):
    user = User(
        name="Test Admin",
        email="admin_biometric@test.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def student_user(db):
    user = User(
        name="Test Student",
        email="student_biometric@test.com",
        password_hash="hashed",
        role=UserRole.STUDENT,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def audit_logger(db):
    return BiometricAuditLogger(db)


@pytest.fixture()
def biometric_feature_enabled(monkeypatch):
    """Override the feature flag to True for tests that need it active."""
    monkeypatch.setattr("app.config.settings.BIOMETRIC_FACE_MATCHING_ENABLED", True)
    monkeypatch.setattr(
        "app.services.biometric.feature_flag.settings.BIOMETRIC_FACE_MATCHING_ENABLED", True
    )

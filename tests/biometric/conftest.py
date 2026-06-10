"""
Biometric test fixtures.

Uses the same transactional SAVEPOINT pattern as tests/unit/conftest.py
for full test isolation without database pollution between tests.
"""
from __future__ import annotations

from unittest.mock import patch

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


# ── PR-4 fixtures ─────────────────────────────────────────────────────────────

_TEST_EMBEDDING_KEY = "ab" * 32   # 64 hex chars = 32 bytes, NOT secure, test only


@pytest.fixture()
def encryption_test_key(monkeypatch):
    """
    Sets a valid test AES-256 key for encryption service tests.
    This key is NOT secure and must never be used in production.
    """
    monkeypatch.setattr("app.config.settings.BIOMETRIC_EMBEDDING_KEY", _TEST_EMBEDDING_KEY)
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY", False)


@pytest.fixture()
def allow_test_key(monkeypatch):
    """Enables the test-key fallback (empty BIOMETRIC_EMBEDDING_KEY → zero key)."""
    monkeypatch.setattr("app.config.settings.BIOMETRIC_EMBEDDING_KEY", "")
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY", True)


@pytest.fixture()
def fake_provider_enabled(monkeypatch):
    """Ensures BIOMETRIC_EMBEDDING_PROVIDER=fake (default, but explicit for clarity)."""
    monkeypatch.setattr("app.config.settings.BIOMETRIC_EMBEDDING_PROVIDER", "fake")


@pytest.fixture(autouse=True)
def _mock_celery_task_dispatch():
    """
    Auto-mock biometric Celery task dispatch in all biometric tests.
    Prevents kombu/Redis connection errors in CI where no broker is running.
    Tests that need real eager execution use the celery_eager fixture explicitly.
    """
    with patch(
        "app.tasks.biometric_tasks.biometric_generate_embedding_task.apply_async"
    ) as _gen, patch(
        "app.tasks.biometric_tasks.biometric_delete_embedding_task.apply_async"
    ) as _del:
        yield {"generate": _gen, "delete": _del}


@pytest.fixture()
def celery_eager(monkeypatch):
    """
    Run Celery tasks synchronously (no broker needed).
    Pairs with patching SessionLocal to inject the test DB session.
    """
    from app.celery_app import celery_app
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False

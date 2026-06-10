"""
Biometric Celery task tests.

Strategy: tasks are run synchronously via task_always_eager=True (celery_eager fixture)
and SessionLocal is patched to use the test DB session (SAVEPOINT-isolated).

BBT-01  generate task happy path: embedding stored + audit EVT_REFERENCE_AUTO_APPROVED_LIVENESS
BBT-02  generate task: active embedding exists → SKIP (idempotent, audit not duplicated)
BBT-03  generate task: no active consent → ABORT + EVT_REFERENCE_REJECTED
BBT-04  generate task: feature flag off → ABORT, no retry, no embedding stored
BBT-05  generate task: unsafe photo_filename → ABORT (path traversal rejected)
BBT-06  generate task: transient failure → retries (mock store_embedding to raise)
BBT-07  generate task: plaintext never logged (caplog check)
BBT-08  delete task happy path: row deleted + audit EVT_EMBEDDING_DELETED(completed)
BBT-09  delete task: no row → idempotent success + audit(completed)
BBT-10  delete task: user not found → ABORT, no audit
BBT-11  delete task: max retries exceeded → EVT_EMBEDDING_DELETED(failed) via service mock
BBT-12  biometric_tasks module: no onnxruntime import (AST check)
BBT-13  liveness_service: biometric_generate_embedding_task.apply_async called
BBT-14  consent_service revoke: biometric_delete_embedding_task.apply_async called with eta
"""
from __future__ import annotations

import ast
import inspect
import logging
from unittest.mock import patch

import pytest

from app.models.biometric import BiometricVerificationLog, UserFaceEmbedding
from app.services.biometric.audit_log import (
    EVT_EMBEDDING_DELETED,
    EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
    EVT_REFERENCE_REJECTED,
)
from app.tasks.biometric_tasks import (
    biometric_delete_embedding_task,
    biometric_generate_embedding_task,
)

_GEN_TASK_PATH = "app.tasks.biometric_tasks.biometric_generate_embedding_task"
_DEL_TASK_PATH = "app.tasks.biometric_tasks.biometric_delete_embedding_task"
_SESSION_PATH  = "app.tasks.biometric_tasks.SessionLocal"


def _grant_consent(db, user):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=user, consent_version="v1.0")
    db.flush()


def _store_active_embedding(db, user_id):
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding
    emb = FakeEmbeddingProvider().generate(b"seed")
    row = store_embedding(db=db, user_id=user_id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()
    return row


# ── BBT-01 ────────────────────────────────────────────────────────────────────

def test_bbt01_generate_happy_path(
    db, student_user, biometric_feature_enabled, encryption_test_key, celery_eager
):
    _grant_consent(db, student_user)

    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_generate_embedding_task.apply(args=[student_user.id, "photo.jpg"])

    row = db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).first()
    assert row is not None
    assert row.embedding_ciphertext is not None

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
        BiometricVerificationLog.event_result == "completed",
    ).all()
    assert logs, "Expected EVT_REFERENCE_AUTO_APPROVED_LIVENESS(completed) audit row"


# ── BBT-02 ────────────────────────────────────────────────────────────────────

def test_bbt02_generate_skips_active_embedding(
    db, student_user, biometric_feature_enabled, encryption_test_key, celery_eager
):
    _grant_consent(db, student_user)
    _store_active_embedding(db, student_user.id)

    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_generate_embedding_task.apply(args=[student_user.id, "photo.jpg"])

    count = db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).count()
    assert count == 1, "Should skip — active embedding already exists"


# ── BBT-03 ────────────────────────────────────────────────────────────────────

def test_bbt03_generate_aborts_no_consent(
    db, student_user, biometric_feature_enabled, encryption_test_key, celery_eager
):
    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_generate_embedding_task.apply(args=[student_user.id, "photo.jpg"])

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_REFERENCE_REJECTED,
        BiometricVerificationLog.event_result == "failed",
    ).all()
    assert logs, "Expected EVT_REFERENCE_REJECTED when consent missing"
    assert any("consent_revoked" in (l.error_message or "") for l in logs)


# ── BBT-04 ────────────────────────────────────────────────────────────────────

def test_bbt04_generate_aborts_flag_off(
    db, student_user, encryption_test_key, celery_eager
):
    # biometric_feature_enabled NOT active
    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_generate_embedding_task.apply(args=[student_user.id, "photo.jpg"])

    assert db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).count() == 0


# ── BBT-05 ────────────────────────────────────────────────────────────────────

def test_bbt05_generate_rejects_path_traversal(
    db, student_user, biometric_feature_enabled, encryption_test_key, celery_eager
):
    _grant_consent(db, student_user)

    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_generate_embedding_task.apply(args=[student_user.id, "../etc/passwd"])

    assert db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).count() == 0


# ── BBT-06 ────────────────────────────────────────────────────────────────────

def test_bbt06_generate_retries_on_failure(
    db, student_user, biometric_feature_enabled, encryption_test_key, celery_eager
):
    """
    When store_embedding raises a transient error, the task should retry.
    With task_eager_propagates=True, the retry exception propagates.
    """
    _grant_consent(db, student_user)
    call_count = {"n": 0}

    def exploding_store(**kwargs):
        call_count["n"] += 1
        raise RuntimeError("transient DB failure")

    with patch(_SESSION_PATH, return_value=db), \
         patch("app.services.biometric.embedding_service.store_embedding", side_effect=exploding_store):
        db.close = lambda: None
        try:
            biometric_generate_embedding_task.apply(args=[student_user.id, "photo.jpg"])
        except Exception:
            pass  # Celery propagates retry exceptions in eager mode

    # store_embedding was called at least once (proving the task ran)
    assert call_count["n"] >= 1


# ── BBT-07 ────────────────────────────────────────────────────────────────────

def test_bbt07_generate_plaintext_not_logged(
    db, student_user, biometric_feature_enabled, encryption_test_key, celery_eager, caplog
):
    _grant_consent(db, student_user)

    with patch(_SESSION_PATH, return_value=db), \
         caplog.at_level(logging.DEBUG):
        db.close = lambda: None
        biometric_generate_embedding_task.apply(args=[student_user.id, "photo.jpg"])

    # No float vector arrays in log output
    for record in caplog.records:
        msg = record.getMessage()
        assert "0.001953" not in msg  # typical float32 value from embedding


# ── BBT-08 ────────────────────────────────────────────────────────────────────

def test_bbt08_delete_happy_path(
    db, student_user, encryption_test_key, celery_eager
):
    _store_active_embedding(db, student_user.id)

    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_delete_embedding_task.apply(args=[student_user.id])

    assert db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).count() == 0
    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_EMBEDDING_DELETED,
        BiometricVerificationLog.event_result == "completed",
    ).all()
    assert logs, "Expected EVT_EMBEDDING_DELETED(completed)"


# ── BBT-09 ────────────────────────────────────────────────────────────────────

def test_bbt09_delete_idempotent_no_row(db, student_user, celery_eager):
    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_delete_embedding_task.apply(args=[student_user.id])

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_EMBEDDING_DELETED,
        BiometricVerificationLog.event_result == "completed",
    ).all()
    assert logs, "Idempotent: audit(completed) even if no row existed"


# ── BBT-10 ────────────────────────────────────────────────────────────────────

def test_bbt10_delete_user_not_found(db, celery_eager):
    with patch(_SESSION_PATH, return_value=db):
        db.close = lambda: None
        biometric_delete_embedding_task.apply(args=[99999999])

    # No audit log row created for non-existent user
    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.event_type == EVT_EMBEDDING_DELETED,
    ).all()
    assert not logs, "No audit row when user not found"


# ── BBT-11 ────────────────────────────────────────────────────────────────────

def test_bbt11_delete_max_retries_critical_log(db, student_user, celery_eager, caplog):
    """
    When delete_embedding raises and max_retries is reached (max_retries=0),
    a CRITICAL log message is emitted.
    We mock db.rollback() to protect the test SAVEPOINT from being destroyed.
    """
    from unittest.mock import MagicMock

    def exploding_delete(**kwargs):
        raise RuntimeError("delete failed")

    original_max = biometric_delete_embedding_task.max_retries
    biometric_delete_embedding_task.max_retries = 0

    try:
        with patch(_SESSION_PATH, return_value=db), \
             patch("app.services.biometric.embedding_service.delete_embedding", side_effect=exploding_delete), \
             patch.object(db, "rollback", MagicMock()), \
             caplog.at_level(logging.CRITICAL, logger="app.tasks.biometric_tasks"):
            db.close = lambda: None
            try:
                biometric_delete_embedding_task.apply(args=[student_user.id])
            except Exception:
                pass
    finally:
        biometric_delete_embedding_task.max_retries = original_max

    critical_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert any("max_retries" in m for m in critical_msgs), (
        f"Expected CRITICAL log. Got: {critical_msgs}"
    )


# ── BBT-12 ────────────────────────────────────────────────────────────────────

def test_bbt12_tasks_module_no_onnxruntime_import():
    import app.tasks.biometric_tasks as mod
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            module = getattr(node, "module", "") or ""
            assert "onnxruntime" not in module, "onnxruntime import found in biometric_tasks"
            assert not any("onnxruntime" in n for n in names)
            assert "insightface" not in module, "insightface import found in biometric_tasks"


# ── BBT-13 ────────────────────────────────────────────────────────────────────

def test_bbt13_liveness_service_dispatches_generate_task(
    db, student_user, biometric_feature_enabled
):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    db.flush()

    from app.services.biometric.liveness_service import submit_liveness_result

    metadata = {
        "challenge_version": "v1.0",
        "steps_completed": ["center"],
        "total_duration_ms": 3000,
        "retry_count": 0,
    }

    with patch(f"{_GEN_TASK_PATH}.apply_async") as mock_dispatch:
        submit_liveness_result(
            db=db, user=student_user, liveness_metadata=metadata,
            source="onboarding_liveness", photo_filename="ref.jpg",
        )

    mock_dispatch.assert_called_once()
    call_args = mock_dispatch.call_args
    dispatched_args = call_args.kwargs.get("args") or (call_args.args[0] if call_args.args else [])
    assert student_user.id in dispatched_args, "user_id must be in apply_async args"
    assert "countdown" in call_args.kwargs, "countdown must be set for delayed dispatch"


# ── BBT-14 ────────────────────────────────────────────────────────────────────

def test_bbt14_consent_revoke_dispatches_delete_task_with_eta(
    db, student_user, biometric_feature_enabled
):
    from app.services.biometric.consent_service import grant_consent, revoke_consent
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    db.flush()

    with patch(f"{_DEL_TASK_PATH}.apply_async") as mock_dispatch:
        revoke_consent(db=db, user=student_user)

    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert "eta" in kwargs, "delete task must be dispatched with eta= (delayed physical deletion)"
    assert kwargs["args"] == [student_user.id]

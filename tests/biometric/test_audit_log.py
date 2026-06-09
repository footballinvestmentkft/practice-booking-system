"""
Audit log tests.

BMA-01  log() inserts a BiometricVerificationLog row
BMA-02  event_type stored correctly
BMA-03  liveness_metadata stored after sanitization (forbidden field stripped)
BMA-04  face_match_score stored in DB row but absent from log() return value dict
BMA-05  face_match_score stored in DB — column present on model instance
BMA-06  face_match_score NOT returned by BiometricVerificationLogOut schema
BMA-07  ALL_EVENT_TYPES set is non-empty and contains liveness events
BMA-08  log() raises RuntimeError when DB is closed (write failure)
BMA-09  actor_user_id stored when provided
BMA-10  liveness_metadata=None → liveness_metadata column is NULL in DB
BMA-11  Forbidden field in liveness_metadata never reaches DB column
BMA-12  embedding_ciphertext / embedding_iv absent from BiometricVerificationLogOut
"""
from __future__ import annotations

import pytest

from app.models.biometric import BiometricVerificationLog
from app.schemas.biometric import BiometricVerificationLogOut
from app.services.biometric.audit_log import (
    ALL_EVENT_TYPES,
    EVT_CONSENT_GRANTED,
    EVT_LIVENESS_COMPLETED,
    EVT_LIVENESS_FAILED,
    EVT_LIVENESS_STARTED,
    EVT_LIVENESS_STEP_PASSED,
    EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
    BiometricAuditLogger,
)


# ── BMA-01 / BMA-02 ──────────────────────────────────────────────────────────

def test_bma01_log_inserts_row(db, student_user):
    logger = BiometricAuditLogger(db)
    logger.log(user_id=student_user.id, event_type=EVT_CONSENT_GRANTED)
    rows = db.query(BiometricVerificationLog).filter_by(user_id=student_user.id).all()
    assert len(rows) == 1


def test_bma02_event_type_stored(db, student_user):
    logger = BiometricAuditLogger(db)
    logger.log(user_id=student_user.id, event_type=EVT_LIVENESS_COMPLETED)
    row = db.query(BiometricVerificationLog).filter_by(user_id=student_user.id).first()
    assert row.event_type == EVT_LIVENESS_COMPLETED


# ── BMA-03  liveness_metadata sanitized before storage ───────────────────────

def test_bma03_liveness_metadata_sanitized(db, student_user):
    raw_metadata = {
        "challenge_version": "v1.0",
        "steps_completed": ["centered", "head_left"],
        "total_duration_ms": 8000,
        "retry_count": 0,
        "device_model": "iPhone 15",   # FORBIDDEN — must be stripped
        "yaw": 0.35,                   # FORBIDDEN — must be stripped
    }
    logger = BiometricAuditLogger(db)
    logger.log(
        user_id=student_user.id,
        event_type=EVT_LIVENESS_COMPLETED,
        liveness_metadata=raw_metadata,
    )
    row = db.query(BiometricVerificationLog).filter_by(user_id=student_user.id).first()
    stored = row.liveness_metadata
    assert stored is not None
    assert "device_model" not in stored
    assert "yaw" not in stored
    assert stored["challenge_version"] == "v1.0"
    assert stored["steps_completed"] == ["centered", "head_left"]


# ── BMA-04 / BMA-05  face_match_score stored but not in return ───────────────

def test_bma04_face_match_score_stored_in_db(db, student_user):
    logger = BiometricAuditLogger(db)
    entry  = logger.log(
        user_id=student_user.id,
        event_type=EVT_LIVENESS_COMPLETED,
        face_match_score=0.73,
    )
    # The model instance has the value
    assert entry.face_match_score == pytest.approx(0.73)
    # Reload from DB to confirm persistence
    row = db.get(BiometricVerificationLog, entry.id)
    assert row.face_match_score == pytest.approx(0.73)


def test_bma05_log_return_is_model_instance_not_dict(db, student_user):
    logger = BiometricAuditLogger(db)
    entry  = logger.log(user_id=student_user.id, event_type=EVT_CONSENT_GRANTED)
    assert isinstance(entry, BiometricVerificationLog)


# ── BMA-06  Pydantic schema excludes face_match_score ────────────────────────

def test_bma06_schema_excludes_face_match_score(db, student_user):
    logger = BiometricAuditLogger(db)
    entry  = logger.log(
        user_id=student_user.id,
        event_type=EVT_LIVENESS_COMPLETED,
        face_match_score=0.80,
    )
    db.refresh(entry)
    out = BiometricVerificationLogOut.model_validate(entry)
    out_dict = out.model_dump()
    assert "face_match_score" not in out_dict
    # Double-check the value is not smuggled under an alias
    for v in out_dict.values():
        assert v != pytest.approx(0.80), "face_match_score value leaked into schema output"


# ── BMA-07  ALL_EVENT_TYPES ───────────────────────────────────────────────────

def test_bma07_all_event_types_non_empty():
    assert len(ALL_EVENT_TYPES) > 0


def test_bma07_liveness_events_in_all_event_types():
    for evt in (
        EVT_LIVENESS_STARTED,
        EVT_LIVENESS_STEP_PASSED,
        EVT_LIVENESS_FAILED,
        EVT_LIVENESS_COMPLETED,
        EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
    ):
        assert evt in ALL_EVENT_TYPES, f"{evt!r} missing from ALL_EVENT_TYPES"


# ── BMA-08  RuntimeError on write failure ────────────────────────────────────

def test_bma08_raises_runtime_error_on_db_failure(db, student_user):
    logger = BiometricAuditLogger(db)
    # Close the underlying connection to force a write failure
    db.close()
    with pytest.raises(RuntimeError):
        logger.log(user_id=student_user.id, event_type=EVT_CONSENT_GRANTED)


# ── BMA-09  actor_user_id ─────────────────────────────────────────────────────

def test_bma09_actor_user_id_stored(db, student_user, admin_user):
    logger = BiometricAuditLogger(db)
    entry  = logger.log(
        user_id=student_user.id,
        event_type=EVT_LIVENESS_COMPLETED,
        actor_user_id=admin_user.id,
    )
    assert entry.actor_user_id == admin_user.id


# ── BMA-10  liveness_metadata None → NULL ────────────────────────────────────

def test_bma10_none_metadata_stored_as_null(db, student_user):
    logger = BiometricAuditLogger(db)
    entry  = logger.log(
        user_id=student_user.id,
        event_type=EVT_CONSENT_GRANTED,
        liveness_metadata=None,
    )
    assert entry.liveness_metadata is None


# ── BMA-11  Forbidden field in liveness_metadata never in DB ─────────────────

def test_bma11_forbidden_field_never_reaches_db(db, student_user):
    logger = BiometricAuditLogger(db)
    logger.log(
        user_id=student_user.id,
        event_type=EVT_LIVENESS_COMPLETED,
        liveness_metadata={
            "challenge_version": "v1.0",
            "steps_completed": [],
            "total_duration_ms": 1000,
            "retry_count": 0,
            "embedding": [0.1] * 512,       # forbidden
            "face_match_score": 0.95,        # forbidden
            "ios_version": "18.2",           # forbidden
        },
    )
    row = db.query(BiometricVerificationLog).filter_by(user_id=student_user.id).first()
    stored = row.liveness_metadata or {}
    assert "embedding" not in stored
    assert "face_match_score" not in stored
    assert "ios_version" not in stored


# ── BMA-12  Pydantic schema excludes embedding fields ────────────────────────

def test_bma12_schema_excludes_embedding_fields():
    schema_fields = set(BiometricVerificationLogOut.model_fields.keys())
    assert "embedding_ciphertext" not in schema_fields
    assert "embedding_iv" not in schema_fields
    assert "face_match_score" not in schema_fields

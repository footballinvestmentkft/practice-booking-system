"""
Migration verification tests.

BMG-01  Table user_biometric_consents exists with required columns
BMG-02  Table user_face_embeddings exists with required columns
BMG-03  Table biometric_verification_logs exists with required columns
BMG-04  users table has face_match_status column
BMG-05  users table has face_match_score column
BMG-06  users table has face_reference_photo_status column
BMG-07  users table has manual_review_required column (NOT NULL, default false)
BMG-08  biometric_verification_logs.face_match_score column exists (internal)
BMG-09  biometric_verification_logs.liveness_metadata is JSONB type
BMG-10  Index ix_user_biometric_consents_user_id exists
BMG-11  Index ix_user_face_embeddings_user_id exists
BMG-12  Indexes ix_biometric_logs_user_id, _event_type, _created_at exist
"""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

from app.database import engine


@pytest.fixture(scope="module")
def inspector():
    return inspect(engine)


# ── Table existence ───────────────────────────────────────────────────────────

def test_bmg_tables_exist(inspector):
    tables = inspector.get_table_names()
    for table in (
        "user_biometric_consents",
        "user_face_embeddings",
        "biometric_verification_logs",
    ):
        assert table in tables, f"Table {table!r} not found"


# ── Column existence helpers ──────────────────────────────────────────────────

def _col_names(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def test_bmg01_user_biometric_consents_columns(inspector):
    cols = _col_names(inspector, "user_biometric_consents")
    for expected in (
        "id", "user_id", "consent_granted_at", "consent_version",
        "consent_ip_address", "consent_user_agent",
        "consent_revoked_at", "revocation_reason",
        "is_active", "created_at",
    ):
        assert expected in cols, f"Column {expected!r} missing from user_biometric_consents"


def test_bmg02_user_face_embeddings_columns(inspector):
    cols = _col_names(inspector, "user_face_embeddings")
    for expected in (
        "id", "user_id", "embedding_ciphertext", "embedding_iv",
        "model_version", "approved_by", "approved_at",
        "is_active", "created_at", "updated_at",
    ):
        assert expected in cols, f"Column {expected!r} missing from user_face_embeddings"


def test_bmg03_biometric_verification_logs_columns(inspector):
    cols = _col_names(inspector, "biometric_verification_logs")
    for expected in (
        "id", "user_id", "event_type", "event_result",
        "face_match_score",        # internal — stored, never returned via API
        "model_version", "threshold_used", "liveness_metadata",
        "actor_user_id", "actor_ip_address",
        "photo_filename", "error_message", "created_at",
    ):
        assert expected in cols, f"Column {expected!r} missing from biometric_verification_logs"


def test_bmg04_users_face_match_status(inspector):
    assert "face_match_status" in _col_names(inspector, "users")


def test_bmg05_users_face_match_score(inspector):
    assert "face_match_score" in _col_names(inspector, "users")


def test_bmg06_users_face_reference_photo_status(inspector):
    assert "face_reference_photo_status" in _col_names(inspector, "users")


def test_bmg07_users_manual_review_required(inspector):
    cols_info = {c["name"]: c for c in inspector.get_columns("users")}
    col = cols_info.get("manual_review_required")
    assert col is not None, "manual_review_required column missing"
    assert not col["nullable"], "manual_review_required should be NOT NULL"


def test_bmg08_biometric_logs_face_match_score_internal(inspector):
    cols_info = {c["name"]: c for c in inspector.get_columns("biometric_verification_logs")}
    assert "face_match_score" in cols_info


def test_bmg09_liveness_metadata_is_jsonb(inspector):
    cols_info = {c["name"]: c for c in inspector.get_columns("biometric_verification_logs")}
    col = cols_info.get("liveness_metadata")
    assert col is not None
    # SQLAlchemy inspector returns dialect-specific type; check type name string
    type_str = str(col["type"]).upper()
    assert "JSON" in type_str, f"Expected JSONB, got {type_str}"


# ── Index existence ───────────────────────────────────────────────────────────

def _index_names(inspector, table: str) -> set[str]:
    return {idx["name"] for idx in inspector.get_indexes(table)}


def test_bmg10_consent_user_index(inspector):
    assert "ix_user_biometric_consents_user_id" in _index_names(inspector, "user_biometric_consents")


def test_bmg11_embedding_user_index(inspector):
    assert "ix_user_face_embeddings_user_id" in _index_names(inspector, "user_face_embeddings")


def test_bmg12_log_indexes(inspector):
    idx = _index_names(inspector, "biometric_verification_logs")
    for expected in (
        "ix_biometric_logs_user_id",
        "ix_biometric_logs_event_type",
        "ix_biometric_logs_created_at",
    ):
        assert expected in idx, f"Index {expected!r} missing"
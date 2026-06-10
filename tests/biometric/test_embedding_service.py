"""
Biometric embedding service tests.

BES-01  FakeEmbeddingProvider.generate() returns 512-dim float list
BES-02  FakeEmbeddingProvider deterministic (same input → same output)
BES-03  FakeEmbeddingProvider result is a unit vector (L2 norm ≈ 1.0)
BES-04  FakeEmbeddingProvider — no onnxruntime import in module
BES-05  store_embedding() inserts a row into user_face_embeddings
BES-06  store_embedding() ciphertext and iv are non-None bytes
BES-07  store_embedding() is_active=False (not approved — PR-6 gate)
BES-08  store_embedding() plaintext never equals ciphertext in DB
BES-09  store_embedding() idempotent: second call overwrites, no duplicate
BES-10  delete_embedding() returns True and removes the row
BES-11  delete_embedding() returns False when no row exists (idempotent)
BES-12  get_embedding_provider() "fake" → FakeEmbeddingProvider
BES-13  get_embedding_provider() "onnx" → NotImplementedError (PR-5)
BES-14  face_match_score absent from store_embedding() return value
"""
from __future__ import annotations

import inspect

import pytest

from app.models.biometric import UserFaceEmbedding
from app.services.biometric.embedding_service import (
    FakeEmbeddingProvider,
    delete_embedding,
    get_embedding_provider,
    store_embedding,
)


# ── BES-01 ────────────────────────────────────────────────────────────────────

def test_bes01_fake_provider_returns_512_dim(fake_provider_enabled):
    provider = FakeEmbeddingProvider()
    result = provider.generate(b"test_image_bytes")
    assert isinstance(result, list)
    assert len(result) == 512
    assert all(isinstance(v, float) for v in result)


# ── BES-02 ────────────────────────────────────────────────────────────────────

def test_bes02_fake_provider_deterministic(fake_provider_enabled):
    provider = FakeEmbeddingProvider()
    seed = b"deterministic_seed"
    r1 = provider.generate(seed)
    r2 = provider.generate(seed)
    assert r1 == r2


# ── BES-03 ────────────────────────────────────────────────────────────────────

def test_bes03_fake_provider_unit_vector(fake_provider_enabled):
    provider = FakeEmbeddingProvider()
    embedding = provider.generate(b"unit_test")
    norm = sum(v * v for v in embedding) ** 0.5
    assert abs(norm - 1.0) < 1e-5, f"L2 norm should be ~1.0, got {norm}"


# ── BES-04 ────────────────────────────────────────────────────────────────────

def test_bes04_fake_provider_no_onnxruntime_import():
    import app.services.biometric.embedding_service as mod
    src = inspect.getsource(mod)
    assert "onnxruntime" not in src or "NO onnxruntime" in src
    # AST-level check: no import onnxruntime statement
    import ast
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            mod_name = getattr(node, "module", "") or ""
            assert "onnxruntime" not in mod_name
            assert not any("onnxruntime" in n for n in names)


# ── BES-05 ────────────────────────────────────────────────────────────────────

def test_bes05_store_embedding_inserts_row(db, student_user, encryption_test_key):
    provider = FakeEmbeddingProvider()
    embedding = provider.generate(b"seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    assert row is not None
    db_row = db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).first()
    assert db_row is not None


# ── BES-06 ────────────────────────────────────────────────────────────────────

def test_bes06_store_embedding_ciphertext_iv_nonnull(db, student_user, encryption_test_key):
    embedding = FakeEmbeddingProvider().generate(b"seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    assert row.embedding_ciphertext is not None
    assert isinstance(row.embedding_ciphertext, bytes)
    assert row.embedding_iv is not None
    assert isinstance(row.embedding_iv, bytes)
    assert len(row.embedding_iv) == 12


# ── BES-07 ────────────────────────────────────────────────────────────────────

def test_bes07_store_embedding_is_active_false(db, student_user, encryption_test_key):
    embedding = FakeEmbeddingProvider().generate(b"seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    assert row.is_active is False, "is_active must be False — approval gate is PR-6"


# ── BES-08 ────────────────────────────────────────────────────────────────────

def test_bes08_plaintext_not_in_db(db, student_user, encryption_test_key):
    from app.services.biometric.encryption_service import BiometricEncryptionService
    embedding = FakeEmbeddingProvider().generate(b"plaintext_test")
    row = store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    svc = BiometricEncryptionService()
    plaintext_bytes = svc.embedding_to_bytes(embedding)
    assert row.embedding_ciphertext != plaintext_bytes, "Plaintext must not equal ciphertext in DB"


# ── BES-09 ────────────────────────────────────────────────────────────────────

def test_bes09_store_embedding_idempotent(db, student_user, encryption_test_key):
    embedding = FakeEmbeddingProvider().generate(b"seed")
    store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    count = db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).count()
    assert count == 1, f"Expected 1 row (idempotent overwrite), got {count}"


# ── BES-10 ────────────────────────────────────────────────────────────────────

def test_bes10_delete_embedding_returns_true_removes_row(db, student_user, encryption_test_key):
    embedding = FakeEmbeddingProvider().generate(b"seed")
    store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    result = delete_embedding(db=db, user_id=student_user.id)
    assert result is True
    assert db.query(UserFaceEmbedding).filter_by(user_id=student_user.id).count() == 0


# ── BES-11 ────────────────────────────────────────────────────────────────────

def test_bes11_delete_embedding_returns_false_when_no_row(db, student_user):
    result = delete_embedding(db=db, user_id=student_user.id)
    assert result is False


# ── BES-12 ────────────────────────────────────────────────────────────────────

def test_bes12_get_provider_fake(fake_provider_enabled):
    provider = get_embedding_provider()
    assert isinstance(provider, FakeEmbeddingProvider)


# ── BES-13 ────────────────────────────────────────────────────────────────────

def test_bes13_get_provider_onnx_raises(monkeypatch):
    monkeypatch.setattr("app.config.settings.BIOMETRIC_EMBEDDING_PROVIDER", "onnx")
    with pytest.raises(NotImplementedError, match="PR-5"):
        get_embedding_provider()


# ── BES-14 ────────────────────────────────────────────────────────────────────

def test_bes14_store_embedding_no_face_match_score(db, student_user, encryption_test_key):
    embedding = FakeEmbeddingProvider().generate(b"seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    # UserFaceEmbedding has no face_match_score column
    assert not hasattr(row, "face_match_score"), "face_match_score must not be on embedding row"

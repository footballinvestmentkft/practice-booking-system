"""
Biometric AES-256-GCM encryption service tests.

BENC-01  encrypt() returns (ciphertext, iv) tuple
BENC-02  iv is always 12 bytes
BENC-03  ciphertext differs from plaintext
BENC-04  decrypt(encrypt(x)) == x — full round-trip
BENC-05  different plaintext → different ciphertext
BENC-06  each encrypt() call produces different iv (random per call)
BENC-07  tampered ciphertext → ValueError (GCM authentication)
BENC-08  tampered iv → ValueError
BENC-09  empty key + ALLOW_TEST_KEY=False → ValueError
BENC-10  embedding_to_bytes / bytes_to_embedding round-trip (512-dim)
"""
from __future__ import annotations

import pytest

from app.services.biometric.encryption_service import BiometricEncryptionService


# ── BENC-01 ───────────────────────────────────────────────────────────────────

def test_benc01_encrypt_returns_ciphertext_iv_tuple(encryption_test_key):
    svc = BiometricEncryptionService()
    result = svc.encrypt(b"hello biometric")
    assert isinstance(result, tuple)
    assert len(result) == 2
    ciphertext, iv = result
    assert isinstance(ciphertext, bytes)
    assert isinstance(iv, bytes)


# ── BENC-02 ───────────────────────────────────────────────────────────────────

def test_benc02_iv_is_always_12_bytes(encryption_test_key):
    svc = BiometricEncryptionService()
    for _ in range(5):
        _, iv = svc.encrypt(b"test")
        assert len(iv) == 12


# ── BENC-03 ───────────────────────────────────────────────────────────────────

def test_benc03_ciphertext_differs_from_plaintext(encryption_test_key):
    svc = BiometricEncryptionService()
    plaintext = b"plaintext embedding data"
    ciphertext, _ = svc.encrypt(plaintext)
    assert ciphertext != plaintext


# ── BENC-04 ───────────────────────────────────────────────────────────────────

def test_benc04_decrypt_encrypt_roundtrip(encryption_test_key):
    svc = BiometricEncryptionService()
    original = b"\x01\x02\x03" * 100
    ciphertext, iv = svc.encrypt(original)
    recovered = svc.decrypt(ciphertext, iv)
    assert recovered == original


# ── BENC-05 ───────────────────────────────────────────────────────────────────

def test_benc05_different_plaintext_different_ciphertext(encryption_test_key):
    svc = BiometricEncryptionService()
    ct1, iv1 = svc.encrypt(b"plaintext A")
    ct2, iv2 = svc.encrypt(b"plaintext B")
    assert ct1 != ct2


# ── BENC-06 ───────────────────────────────────────────────────────────────────

def test_benc06_each_encrypt_produces_different_iv(encryption_test_key):
    svc = BiometricEncryptionService()
    ivs = set()
    for _ in range(10):
        _, iv = svc.encrypt(b"same plaintext")
        ivs.add(iv)
    # All 10 IVs should be unique (random per call)
    assert len(ivs) == 10


# ── BENC-07 ───────────────────────────────────────────────────────────────────

def test_benc07_tampered_ciphertext_raises_value_error(encryption_test_key):
    svc = BiometricEncryptionService()
    ciphertext, iv = svc.encrypt(b"sensitive embedding")
    tampered = bytes([ciphertext[0] ^ 0xFF]) + ciphertext[1:]
    with pytest.raises(ValueError):
        svc.decrypt(tampered, iv)


# ── BENC-08 ───────────────────────────────────────────────────────────────────

def test_benc08_tampered_iv_raises_value_error(encryption_test_key):
    svc = BiometricEncryptionService()
    ciphertext, iv = svc.encrypt(b"sensitive embedding")
    tampered_iv = bytes([iv[0] ^ 0xFF]) + iv[1:]
    with pytest.raises(ValueError):
        svc.decrypt(ciphertext, tampered_iv)


# ── BENC-09 ───────────────────────────────────────────────────────────────────

def test_benc09_empty_key_raises_value_error(monkeypatch):
    monkeypatch.setattr("app.config.settings.BIOMETRIC_EMBEDDING_KEY", "")
    monkeypatch.setattr("app.config.settings.BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY", False)
    with pytest.raises(ValueError, match="BIOMETRIC_EMBEDDING_KEY"):
        BiometricEncryptionService()


# ── BENC-10 ───────────────────────────────────────────────────────────────────

def test_benc10_embedding_serialization_roundtrip(encryption_test_key):
    svc = BiometricEncryptionService()
    original = [float(i) / 512.0 for i in range(512)]
    raw = svc.embedding_to_bytes(original)
    assert len(raw) == 512 * 4   # 2048 bytes
    recovered = svc.bytes_to_embedding(raw)
    assert len(recovered) == 512
    for a, b in zip(original, recovered):
        assert abs(a - b) < 1e-6, f"float32 round-trip mismatch: {a} vs {b}"


# ── Extra: ALLOW_TEST_KEY path ────────────────────────────────────────────────

def test_benc_allow_test_key_enables_empty_key(allow_test_key):
    svc = BiometricEncryptionService()
    ct, iv = svc.encrypt(b"test data")
    assert svc.decrypt(ct, iv) == b"test data"

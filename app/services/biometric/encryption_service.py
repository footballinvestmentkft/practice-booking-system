"""
AES-256-GCM encryption service for biometric face embeddings — PR-4.

Design rules enforced here:
  1. Plaintext embedding is NEVER stored, logged, or returned.
     Only (ciphertext, iv) leaves this service.
  2. Key is loaded once at instantiation; empty key raises ValueError
     unless BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY=True (dev/test only).
  3. Each encrypt() call generates a fresh 12-byte random IV (nonce).
     IV uniqueness is critical for AES-GCM security.
  4. decrypt() raises ValueError on authentication failure (tamper detection).
  5. No face_match_score, yaw, roll, landmarks, or raw sensor data
     is ever passed through this service.

Not production-ready. DPIA/DPO approval pending.
"""
from __future__ import annotations

import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

_TEST_KEY_HEX = "00" * 32   # 32 zero bytes — deterministic, NOT secure, test only


class BiometricEncryptionService:
    """AES-256-GCM encrypt/decrypt for 512-dim face embeddings."""

    _IV_LENGTH   = 12    # bytes — NIST recommended for AES-GCM
    _KEY_BYTES   = 32    # bytes — AES-256
    _EMBED_DIM   = 512   # float32 dimensions
    _EMBED_BYTES = _EMBED_DIM * 4  # 2048 bytes

    def __init__(self) -> None:
        self._key: bytes = self._load_and_validate_key()

    # ── Key loading ────────────────────────────────────────────────────────────

    def _load_and_validate_key(self) -> bytes:
        """
        Load BIOMETRIC_EMBEDDING_KEY from settings.

        Raises ValueError if:
          - key is empty and BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY is False
          - key is not valid hex
          - decoded key is not exactly 32 bytes

        In test/dev environments with BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY=True,
        an empty key is replaced with a deterministic 32-byte zero key.
        This test key is NOT secure and MUST NOT be used in production.
        """
        raw = settings.BIOMETRIC_EMBEDDING_KEY
        if not raw:
            if settings.BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY:
                return bytes.fromhex(_TEST_KEY_HEX)
            raise ValueError(
                "BIOMETRIC_EMBEDDING_KEY must not be empty. "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        try:
            key_bytes = bytes.fromhex(raw)
        except ValueError as exc:
            raise ValueError(
                f"BIOMETRIC_EMBEDDING_KEY is not valid hex: {exc}"
            ) from exc
        if len(key_bytes) != self._KEY_BYTES:
            raise ValueError(
                f"BIOMETRIC_EMBEDDING_KEY must decode to {self._KEY_BYTES} bytes "
                f"(got {len(key_bytes)}). "
                f"Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return key_bytes

    # ── Encryption / decryption ────────────────────────────────────────────────

    def encrypt(self, plaintext: bytes) -> tuple[bytes, bytes]:
        """
        Encrypt plaintext with AES-256-GCM.

        Returns (ciphertext, iv) — both bytes, suitable for BYTEA storage.
        iv is 12 bytes, randomly generated; unique per call.
        The GCM authentication tag is appended to ciphertext by cryptography lib.
        Plaintext is NOT stored or returned.
        """
        iv = os.urandom(self._IV_LENGTH)
        aesgcm = AESGCM(self._key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)
        return ciphertext, iv

    def decrypt(self, ciphertext: bytes, iv: bytes) -> bytes:
        """
        Decrypt ciphertext with AES-256-GCM.

        Raises ValueError on authentication failure (tamper detection).
        The returned plaintext must NOT be logged or stored.
        """
        aesgcm = AESGCM(self._key)
        try:
            return aesgcm.decrypt(iv, ciphertext, None)
        except Exception as exc:
            raise ValueError(f"AES-GCM authentication failed — ciphertext may be tampered: {exc}") from exc

    # ── Embedding serialization ────────────────────────────────────────────────

    def embedding_to_bytes(self, embedding: list[float]) -> bytes:
        """
        Serialize a 512-dim float32 embedding list to 2048-byte little-endian binary.
        The resulting bytes are suitable as AES-GCM plaintext input.
        """
        return struct.pack(f"<{len(embedding)}f", *embedding)

    def bytes_to_embedding(self, raw: bytes) -> list[float]:
        """
        Deserialize 2048-byte little-endian binary to a 512-dim float32 list.
        Used for decrypt→deserialize in future face matching (PR-6).
        """
        n = len(raw) // 4
        return list(struct.unpack(f"<{n}f", raw))

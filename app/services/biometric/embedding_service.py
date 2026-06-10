"""
Embedding provider abstraction and storage/deletion service — PR-4.

Design rules enforced here:
  1. AbstractEmbeddingProvider.generate() returns list[float] only.
     Raw image bytes are consumed and NOT stored, logged, or returned.
  2. FakeEmbeddingProvider: deterministic 512-dim unit vector from SHA-256 hash.
     NO onnxruntime, NO insightface — test/dev only.
  3. OnnxEmbeddingProvider: planned for PR-5 — raises NotImplementedError here.
  4. store_embedding(): AES-256-GCM encrypt → INSERT/UPDATE user_face_embeddings.
     is_active=False (approval gate not yet implemented — PR-6).
     Plaintext embedding is deleted from memory immediately after encrypt().
  5. delete_embedding(): physical DELETE from user_face_embeddings.
     Returns True if row existed, False if idempotent no-op.
  6. face_match_score is never read, written, or returned by this module.

Not production-ready. DPIA/DPO approval pending.
"""
from __future__ import annotations

import hashlib
import logging
import struct
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.biometric import UserFaceEmbedding
from app.services.biometric.encryption_service import BiometricEncryptionService

logger = logging.getLogger(__name__)

_FAKE_MODEL_VERSION = "fake_v1"
_EMBED_DIM = 512


# ── Provider abstraction ───────────────────────────────────────────────────────

class AbstractEmbeddingProvider:
    """Base class — all embedding backends must implement generate()."""

    def generate(self, image_bytes: bytes) -> list[float]:
        raise NotImplementedError


class FakeEmbeddingProvider(AbstractEmbeddingProvider):
    """
    Deterministic fake embedding provider for tests and development.

    Returns a 512-dim unit vector seeded from SHA-256(image_bytes).
    Produces the same output for identical inputs (deterministic).

    IMPORTANT:
      - No onnxruntime import
      - No InsightFace import
      - Not suitable for real biometric matching
      - Used only when BIOMETRIC_EMBEDDING_PROVIDER=fake (default in PR-4)
    """

    def generate(self, image_bytes: bytes) -> list[float]:
        """
        Generate a deterministic 512-dim unit float32 vector.
        image_bytes is consumed for seeding only — never stored or logged.

        Note: SHA-256 output bytes can decode to IEEE 754 special values
        (NaN, Inf) when interpreted as float32. These are replaced with 0.0
        to keep the vector finite and the L2 norm well-defined.
        """
        seed = hashlib.sha256(image_bytes).digest()
        raw_floats: list[float] = []
        while len(raw_floats) < _EMBED_DIM:
            seed = hashlib.sha256(seed).digest()
            # 32 bytes → 8 float32 values (4 bytes each)
            batch = list(struct.unpack("<8f", seed))
            # Replace NaN / Inf with 0.0 (IEEE 754 special values from raw bytes)
            batch = [0.0 if (v != v or v == float("inf") or v == float("-inf")) else v for v in batch]
            raw_floats.extend(batch)
        embedding = raw_floats[:_EMBED_DIM]

        # Normalize to unit vector (L2 norm)
        magnitude = sum(v * v for v in embedding) ** 0.5
        if magnitude > 0:
            embedding = [v / magnitude for v in embedding]
        else:
            # Degenerate case: all zeros → uniform unit vector
            embedding = [1.0 / (_EMBED_DIM ** 0.5)] * _EMBED_DIM
        return embedding


# ── Provider factory ───────────────────────────────────────────────────────────

def get_embedding_provider() -> AbstractEmbeddingProvider:
    """
    Return the active embedding provider based on BIOMETRIC_EMBEDDING_PROVIDER.

    PR-4: only 'fake' is supported.
    'onnx' is reserved for PR-5 (InsightFace buffalo_sc_v1).
    """
    provider = getattr(settings, "BIOMETRIC_EMBEDDING_PROVIDER", "fake")
    if provider == "fake":
        return FakeEmbeddingProvider()
    if provider == "onnx":
        raise NotImplementedError(
            "OnnxEmbeddingProvider not implemented — planned for PR-5. "
            "Set BIOMETRIC_EMBEDDING_PROVIDER=fake for development."
        )
    raise ValueError(f"Unknown BIOMETRIC_EMBEDDING_PROVIDER: {provider!r}")


# ── Storage and deletion ───────────────────────────────────────────────────────

def store_embedding(
    *,
    db: Session,
    user_id: int,
    embedding: list[float],
    model_version: str,
) -> UserFaceEmbedding:
    """
    AES-256-GCM encrypt and INSERT/UPDATE user_face_embeddings.

    is_active is set to False — embedding is stored but not yet approved.
    Approval gate (face_match_status → verified) is implemented in PR-6.

    Idempotent: if a row already exists for user_id, it is overwritten.
    This handles the re-consent scenario (user revoked and re-enrolled).

    Plaintext embedding bytes are deleted from memory immediately after
    encryption — they are NEVER stored in DB or written to any log.
    """
    enc = BiometricEncryptionService()
    plaintext = enc.embedding_to_bytes(embedding)
    ciphertext, iv = enc.encrypt(plaintext)
    del plaintext  # plaintext protection — never persisted or logged

    row = db.query(UserFaceEmbedding).filter_by(user_id=user_id).first()
    if row is None:
        row = UserFaceEmbedding(user_id=user_id)
        db.add(row)

    row.embedding_ciphertext = ciphertext
    row.embedding_iv         = iv
    row.model_version        = model_version
    row.is_active            = False   # NOT verified — approval gate in PR-6
    row.updated_at           = datetime.now(timezone.utc)
    db.flush()

    logger.info(
        "biometric_embedding_stored user_id=%s model_version=%s is_active=False",
        user_id, model_version,
    )
    return row


def delete_embedding(
    *,
    db: Session,
    user_id: int,
) -> bool:
    """
    Physically DELETE the face embedding row for user_id.

    Returns True if a row was found and deleted.
    Returns False if no row exists (idempotent no-op).
    Audit log is written by the caller (biometric_tasks.py).
    """
    row = db.query(UserFaceEmbedding).filter_by(user_id=user_id).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True

"""
Biometric Celery tasks — PR-4.

Tasks:
  biometric_generate_embedding_task — generate and store AES-256-GCM encrypted embedding
  biometric_delete_embedding_task   — physically delete embedding after consent revocation

Queue: biometric_embeddings (dedicated, separate from tournaments / mood_photos)

Design rules enforced here:
  1. Raw image bytes are used only as a seed — never stored or logged.
  2. Plaintext embedding is deleted from memory immediately after encryption.
  3. face_match_score, yaw, roll, landmarks, frames are NEVER logged.
  4. Feature flag guard: tasks abort without retry if flag is off.
  5. Consent guard: generate task aborts without retry if consent revoked.
  6. Idempotency: generate skips if active embedding already exists.
  7. Retry: exponential backoff on transient failures; explicit audit on max retries.
  8. No onnxruntime, no InsightFace, no face matching in PR-4.

Not production-ready. DPIA/DPO approval pending.
"""
from __future__ import annotations

import logging
import os

from app.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_FAKE_MODEL_VERSION = "fake_v1"


# ── biometric_generate_embedding_task ─────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.tasks.biometric_tasks.biometric_generate_embedding_task",
    queue="biometric_embeddings",
    max_retries=3,
    acks_late=True,
    reject_on_worker_lost=True,
)
def biometric_generate_embedding_task(
    self,
    user_id: int,
    photo_filename: str | None,
) -> None:
    """
    Generate and store an AES-256-GCM encrypted face embedding.

    Steps:
      1. Feature flag guard — ABORT (no retry) if flag off
      2. Load user — ABORT if not found
      3. Consent check — ABORT (no retry) if revoked; audit EVT_REFERENCE_REJECTED
      4. Idempotency — SKIP if is_active embedding already exists
      5. Build image seed bytes from photo_filename (PR-4 fake; real file load in PR-5)
      6. FakeEmbeddingProvider.generate() — no ONNX, no InsightFace
      7. store_embedding() — encrypt + INSERT/UPDATE; is_active=False
      8. Audit EVT_REFERENCE_AUTO_APPROVED_LIVENESS (event_result=completed)
      FAILURE: EVT_REFERENCE_REJECTED + exponential retry (60s → 180s → 540s)
      MAX RETRIES: EVT_REFERENCE_REJECTED(max_retries_exceeded) + CRITICAL log
    """
    from app.config import settings
    from app.models.biometric import UserBiometricConsent, UserFaceEmbedding
    from app.models.user import User
    from app.services.biometric.audit_log import (
        BiometricAuditLogger,
        EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
        EVT_REFERENCE_REJECTED,
    )
    from app.services.biometric.embedding_service import (
        FakeEmbeddingProvider,
        store_embedding,
    )

    db = SessionLocal()
    try:
        # ── 1. Feature flag guard ─────────────────────────────────────────────
        if not settings.BIOMETRIC_FACE_MATCHING_ENABLED:
            logger.warning(
                "biometric_generate_embedding_task: feature flag off — aborting user_id=%s",
                user_id,
            )
            return

        # ── 2. Load user ──────────────────────────────────────────────────────
        user = db.query(User).filter_by(id=user_id).first()
        if user is None:
            logger.warning(
                "biometric_generate_embedding_task: user_id=%s not found — aborting",
                user_id,
            )
            return

        # ── 3. Consent check (no retry on revoked) ────────────────────────────
        active_consent = (
            db.query(UserBiometricConsent)
            .filter_by(user_id=user_id, is_active=True)
            .first()
        )
        if not active_consent:
            logger.warning(
                "biometric_generate_embedding_task: no active consent for user_id=%s — aborting",
                user_id,
            )
            BiometricAuditLogger(db).log(
                user_id=user_id,
                event_type=EVT_REFERENCE_REJECTED,
                event_result="failed",
                error_message="consent_revoked_before_embedding_generation",
            )
            db.commit()
            return

        # ── 4. Idempotency check ──────────────────────────────────────────────
        existing = (
            db.query(UserFaceEmbedding)
            .filter_by(user_id=user_id, is_active=True)
            .first()
        )
        if existing:
            logger.warning(
                "biometric_generate_embedding_task: active embedding exists for user_id=%s — skipping",
                user_id,
            )
            return

        # ── 5. Build image seed (PR-4: filename-based; PR-5: real file bytes) ─
        # photo_filename is basename only (validated upstream by liveness_service).
        # Raw bytes are used only as deterministic seed — never stored or logged.
        if photo_filename:
            if os.path.basename(photo_filename) != photo_filename:
                logger.error(
                    "biometric_generate_embedding_task: unsafe photo_filename for user_id=%s — aborting",
                    user_id,
                )
                return
            image_seed = photo_filename.encode("utf-8")
        else:
            image_seed = f"user_{user_id}".encode("utf-8")

        # ── 6. Generate embedding (FakeEmbeddingProvider — no ONNX) ──────────
        provider = FakeEmbeddingProvider()
        embedding = provider.generate(image_seed)

        # ── 7. Encrypt and store ──────────────────────────────────────────────
        store_embedding(
            db=db,
            user_id=user_id,
            embedding=embedding,
            model_version=_FAKE_MODEL_VERSION,
        )
        del embedding   # plaintext protection

        # ── 8. Audit log ──────────────────────────────────────────────────────
        BiometricAuditLogger(db).log(
            user_id=user_id,
            event_type=EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
            event_result="completed",
            model_version=_FAKE_MODEL_VERSION,
        )
        db.commit()
        logger.info(
            "biometric_generate_embedding_task: completed for user_id=%s model=%s",
            user_id, _FAKE_MODEL_VERSION,
        )

    except Exception as exc:
        db.rollback()
        retries = self.request.retries

        if retries >= self.max_retries:
            logger.critical(
                "biometric_generate_embedding_task: max_retries=%d exceeded for user_id=%s error=%s",
                self.max_retries, user_id, exc,
            )
            try:
                from app.services.biometric.audit_log import BiometricAuditLogger, EVT_REFERENCE_REJECTED
                BiometricAuditLogger(db).log(
                    user_id=user_id,
                    event_type=EVT_REFERENCE_REJECTED,
                    event_result="failed",
                    error_message="max_retries_exceeded",
                )
                db.commit()
            except Exception:
                pass
            return

        countdown = 60 * (3 ** retries)  # 60s → 180s → 540s
        logger.error(
            "biometric_generate_embedding_task: failed for user_id=%s retry=%d/%d in %ds: %s",
            user_id, retries + 1, self.max_retries, countdown, exc,
        )
        raise self.retry(exc=exc, countdown=countdown)

    finally:
        db.close()


# ── biometric_delete_embedding_task ───────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.tasks.biometric_tasks.biometric_delete_embedding_task",
    queue="biometric_embeddings",
    max_retries=5,
    acks_late=True,
    reject_on_worker_lost=True,
)
def biometric_delete_embedding_task(self, user_id: int) -> None:
    """
    Physically DELETE the face embedding after consent revocation.

    ETA-scheduled from consent_service.revoke_consent():
        biometric_delete_embedding_task.apply_async(args=[user_id], eta=delete_after)

    Steps:
      1. Load user — ABORT if not found (log WARNING)
      2. delete_embedding() — physical DELETE from user_face_embeddings
      3. Audit EVT_EMBEDDING_DELETED (event_result=completed)
      NOT FOUND: idempotent success (log WARNING, audit completed)
      FAILURE: retry (max 5, backoff 300s → 600s → ...)
      MAX RETRIES: EVT_EMBEDDING_DELETED(failed) + CRITICAL log
    """
    from app.models.user import User
    from app.services.biometric.audit_log import BiometricAuditLogger, EVT_EMBEDDING_DELETED
    from app.services.biometric.embedding_service import delete_embedding

    db = SessionLocal()
    try:
        # ── 1. Load user ──────────────────────────────────────────────────────
        user = db.query(User).filter_by(id=user_id).first()
        if user is None:
            logger.warning(
                "biometric_delete_embedding_task: user_id=%s not found — aborting",
                user_id,
            )
            return

        # ── 2. Physical delete ────────────────────────────────────────────────
        deleted = delete_embedding(db=db, user_id=user_id)
        if not deleted:
            logger.warning(
                "biometric_delete_embedding_task: no embedding row for user_id=%s — idempotent success",
                user_id,
            )

        # ── 3. Audit log ──────────────────────────────────────────────────────
        BiometricAuditLogger(db).log(
            user_id=user_id,
            event_type=EVT_EMBEDDING_DELETED,
            event_result="completed",
        )
        db.commit()
        logger.info(
            "biometric_delete_embedding_task: completed for user_id=%s deleted=%s",
            user_id, deleted,
        )

    except Exception as exc:
        db.rollback()
        retries = self.request.retries

        if retries >= self.max_retries:
            logger.critical(
                "biometric_delete_embedding_task: max_retries=%d exceeded for user_id=%s error=%s",
                self.max_retries, user_id, exc,
            )
            try:
                from app.services.biometric.audit_log import BiometricAuditLogger, EVT_EMBEDDING_DELETED
                BiometricAuditLogger(db).log(
                    user_id=user_id,
                    event_type=EVT_EMBEDDING_DELETED,
                    event_result="failed",
                    error_message="max_retries_exceeded",
                )
                db.commit()
            except Exception:
                pass
            return

        countdown = 300 * (2 ** retries)  # 300s → 600s → 1200s → ...
        logger.error(
            "biometric_delete_embedding_task: failed for user_id=%s retry=%d/%d in %ds: %s",
            user_id, retries + 1, self.max_retries, countdown, exc,
        )
        raise self.retry(exc=exc, countdown=countdown)

    finally:
        db.close()

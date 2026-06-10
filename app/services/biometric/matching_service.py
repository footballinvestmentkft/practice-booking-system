"""
Face matching service — PR-6.

Provides:
  - cosine similarity computation between two 512-dim float vectors
  - threshold-based outcome classification (verified / manual_review_required / rejected)
  - run_face_match(): full pipeline — decrypt reference, generate live, compare,
    update user state, write audit log

Design rules enforced here:
  1. face_match_score is stored in the audit log but NEVER returned to callers.
  2. Plaintext embeddings (reference and live) are deleted from memory
     immediately after the similarity computation.
  3. The return value is a MatchOutcome string — never the score or raw vector.
  4. Threshold constants are module-level; tests may override via keyword args.
  5. No onnxruntime or insightface import in this module.
  6. user.face_match_status and user.manual_review_required are updated here
     so the User row reflects the latest outcome for status queries.

Not production-ready. DPIA/DPO approval pending.
"""
from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy.orm import Session

from app.services.biometric.audit_log import (
    BiometricAuditLogger,
    EVT_MATCH_FAILED,
    EVT_MATCH_REVIEW_REQUIRED,
    EVT_MATCH_SUCCESS,
)

logger = logging.getLogger(__name__)

# Threshold constants — stored in audit log, never exposed via API
MATCH_THRESHOLD = 0.75   # score >= MATCH_THRESHOLD → verified
REVIEW_LOWER    = 0.55   # REVIEW_LOWER <= score < MATCH_THRESHOLD → manual_review_required
                          # score < REVIEW_LOWER → rejected

MatchOutcome = Literal["verified", "manual_review_required", "rejected"]

_OUTCOME_TO_EVT: dict[str, str] = {
    "verified":               EVT_MATCH_SUCCESS,
    "manual_review_required": EVT_MATCH_REVIEW_REQUIRED,
    "rejected":               EVT_MATCH_FAILED,
}


# ── Pure functions ─────────────────────────────────────────────────────────────

def compute_cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    Cosine similarity between two float vectors.

    Returns a value in [-1.0, 1.0].
    Returns 0.0 for empty, mismatched-length, or zero-magnitude inputs.
    face_match_score is NEVER returned by callers — only stored in the audit log.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def classify_match_outcome(
    score: float,
    *,
    threshold: float = MATCH_THRESHOLD,
    review_lower: float = REVIEW_LOWER,
) -> MatchOutcome:
    """
    Classify a cosine similarity score into a MatchOutcome.

    score >= threshold                      → "verified"
    review_lower <= score < threshold       → "manual_review_required"
    score < review_lower                    → "rejected"
    """
    if score >= threshold:
        return "verified"
    if score >= review_lower:
        return "manual_review_required"
    return "rejected"


# ── Full pipeline ──────────────────────────────────────────────────────────────

def run_face_match(
    *,
    db: Session,
    user,                    # app.models.user.User — typed loosely to avoid circular import
    live_image_seed: bytes,  # seed for FakeProvider; real image bytes for ONNX (PR-5+)
) -> MatchOutcome:
    """
    Full face matching pipeline.

    Steps:
      1. Load and decrypt the active reference embedding for user.id.
      2. Generate a live embedding from live_image_seed via the active provider.
      3. Compute cosine similarity.
      4. Classify outcome (verified / manual_review_required / rejected).
      5. Update user.face_match_status and user.manual_review_required.
      6. Write audit log row (face_match_score stored, NEVER returned).
      7. Delete plaintext embeddings from memory.

    Returns MatchOutcome string — never the raw score.

    Raises:
      ValueError("no_active_reference_embedding") — no active reference row.
      ValueError("embedding_decrypt_failed") — AES-GCM tag mismatch.
    """
    from app.services.biometric.embedding_service import (
        get_embedding_provider,
        load_reference_embedding,
    )

    # ── 1. Load reference embedding ───────────────────────────────────────────
    ref_row, ref_embedding = load_reference_embedding(db=db, user_id=user.id)
    if ref_row is None or ref_embedding is None:
        raise ValueError("no_active_reference_embedding")

    model_version = ref_row.model_version

    # ── 2. Generate live embedding ────────────────────────────────────────────
    provider      = get_embedding_provider()
    live_embedding = provider.generate(live_image_seed)

    # ── 3. Cosine similarity ──────────────────────────────────────────────────
    score = compute_cosine_similarity(ref_embedding, live_embedding)
    del ref_embedding, live_embedding   # plaintext protection

    # ── 4. Classify ───────────────────────────────────────────────────────────
    outcome: MatchOutcome = classify_match_outcome(score)

    # ── 5. Update user state ──────────────────────────────────────────────────
    if outcome == "verified":
        user.face_match_status     = "verified"
        user.manual_review_required = False
    elif outcome == "manual_review_required":
        user.face_match_status     = "manual_review_required"
        user.manual_review_required = True
    else:
        user.face_match_status     = "rejected"
    db.flush()

    # ── 6. Audit log (score stored internally — NEVER returned) ───────────────
    audit = BiometricAuditLogger(db)
    audit.log(
        user_id=user.id,
        event_type=_OUTCOME_TO_EVT[outcome],
        event_result=outcome,
        face_match_score=score,         # stored only — never exposed
        model_version=model_version,
        threshold_used=MATCH_THRESHOLD,
    )

    logger.info(
        "biometric_face_match_complete user_id=%s outcome=%s model=%s",
        user.id, outcome, model_version,
    )
    return outcome
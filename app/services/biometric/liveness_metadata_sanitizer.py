"""
Liveness metadata sanitizer — PR-1 foundation.

Enforces the data-minimization requirement from the audit plan:
  Allowed  : challenge_version, steps_completed, total_duration_ms,
              retry_count, failure_reason
  Forbidden: device_model, ios_version, yaw, roll, pitch, landmarks,
             face_landmarks, eye_data, frames, frame_data, pixel_data,
             bounding_box, face_rect — and any other raw sensor/biometric value

Called by BiometricAuditLogger before every DB INSERT so that tainted
input from an API caller can never reach the biometric_verification_logs
table regardless of schema-level validation.

Three layers of protection against forbidden fields reaching the DB:
  Layer 1 — iOS struct (LivenessMetadata) cannot encode forbidden fields
             (structural, compile-time enforcement in Swift).
  Layer 2 — Pydantic LivenessMetadata schema rejects unknown fields
             (validated in PR-3 endpoint).
  Layer 3 — THIS sanitizer (runtime, unconditional, before every INSERT).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Policy sets ──────────────────────────────────────────────────────────────

_ALLOWED_FIELDS: frozenset[str] = frozenset({
    "challenge_version",
    "steps_completed",
    "total_duration_ms",
    "retry_count",
    "failure_reason",
})

# Known forbidden keys — presence triggers a WARNING for audit/debugging.
# Unknown keys not in this set are also stripped (allow-list approach).
_KNOWN_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "device_model",
    "ios_version",
    "os_version",
    "app_version",
    "yaw",
    "roll",
    "pitch",
    "landmarks",
    "face_landmarks",
    "eye_data",
    "left_eye",
    "right_eye",
    "frames",
    "frame_data",
    "pixel_data",
    "bounding_box",
    "face_rect",
    "face_confidence",
    "raw_score",
    "embedding",
    "face_match_score",
})

# ── Type constraints ──────────────────────────────────────────────────────────

_FAILURE_REASON_WHITELIST: frozenset[str] = frozenset({
    "timeout",
    "face_lost",
    "multiple_faces",
    "max_retries",
    "capture_error",
})

_MAX_CHALLENGE_VERSION_LEN = 20
_MAX_STEPS_ITEMS           = 10
_MAX_STEP_NAME_LEN         = 50
_MAX_TOTAL_DURATION_MS     = 120_000   # 2 minutes
_MAX_RETRY_COUNT           = 10


# ── Public API ────────────────────────────────────────────────────────────────

def sanitize_liveness_metadata(
    raw: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Return a sanitized copy containing only allowed fields with valid types.

    - Forbidden fields are silently dropped (WARNING logged for known ones).
    - Unknown fields not in _ALLOWED_FIELDS are silently dropped.
    - Type or range failures cause the individual field to be dropped.
    - Returns None when input is None or the sanitized dict is empty.

    This function NEVER raises — callers should not catch exceptions from it.
    """
    if not raw:
        return None

    if not isinstance(raw, dict):
        logger.warning("sanitize_liveness_metadata: expected dict, got %s — discarding", type(raw).__name__)
        return None

    # Warn on known forbidden keys present in input
    for key in _KNOWN_FORBIDDEN_FIELDS:
        if key in raw:
            logger.warning(
                "sanitize_liveness_metadata: forbidden field %r present in input — discarding",
                key,
            )

    result: dict[str, Any] = {}

    # challenge_version
    if "challenge_version" in raw:
        val = raw["challenge_version"]
        if isinstance(val, str) and len(val) <= _MAX_CHALLENGE_VERSION_LEN:
            result["challenge_version"] = val
        else:
            logger.warning("sanitize_liveness_metadata: challenge_version invalid — dropped")

    # steps_completed
    if "steps_completed" in raw:
        val = raw["steps_completed"]
        if (
            isinstance(val, list)
            and len(val) <= _MAX_STEPS_ITEMS
            and all(isinstance(s, str) and len(s) <= _MAX_STEP_NAME_LEN for s in val)
        ):
            result["steps_completed"] = val
        else:
            logger.warning("sanitize_liveness_metadata: steps_completed invalid — dropped")

    # total_duration_ms
    if "total_duration_ms" in raw:
        val = raw["total_duration_ms"]
        if isinstance(val, int) and 0 <= val <= _MAX_TOTAL_DURATION_MS:
            result["total_duration_ms"] = val
        else:
            logger.warning("sanitize_liveness_metadata: total_duration_ms invalid — dropped")

    # retry_count
    if "retry_count" in raw:
        val = raw["retry_count"]
        if isinstance(val, int) and 0 <= val <= _MAX_RETRY_COUNT:
            result["retry_count"] = val
        else:
            logger.warning("sanitize_liveness_metadata: retry_count invalid — dropped")

    # failure_reason
    if "failure_reason" in raw:
        val = raw["failure_reason"]
        if isinstance(val, str) and val in _FAILURE_REASON_WHITELIST:
            result["failure_reason"] = val
        elif val is None:
            pass   # None is valid (no failure)
        else:
            logger.warning(
                "sanitize_liveness_metadata: failure_reason %r not in whitelist — dropped", val
            )

    return result if result else None

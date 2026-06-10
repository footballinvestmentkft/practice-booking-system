"""
ONNX face embedding provider — PR-5.

R&D/PROTOTYPE ONLY. NOT FOR PRODUCTION USE WITHOUT LICENSE REVIEW.

This module provides model-agnostic ONNX embedding generation.
It is designed to work with ArcFace-standard 512-dim models
(arcfaceresnet100, AuraFace, etc.) via a common interface.

Activation requires TWO separate config flags:
  1. BIOMETRIC_FACE_MATCHING_ENABLED=true  (general biometric gate)
  2. BIOMETRIC_ONNX_RND_ENABLED=true       (additional R&D-only guard)

BIOMETRIC_FACE_MATCHING_ENABLED=true alone is NOT sufficient.
Setting BIOMETRIC_ONNX_RND_ENABLED=true in production is prohibited.

The onnxruntime import is deferred to this module only.
Other modules must NOT import onnxruntime directly.

Model weight policy:
  - Model files must NOT be committed to the repository
  - .gitignore enforces *.onnx exclusion
  - Model path via BIOMETRIC_ONNX_MODEL_PATH env var only
  - Runtime CDN/URL download is prohibited
  - SHA-256 checksum enforced when BIOMETRIC_ONNX_MODEL_SHA256 is set

R&D reference model (internal evaluation only):
  ArcFace ONNX Model Zoo (arcfaceresnet100-8) — Apache 2.0 model weight,
  but trained on MS-Celeb-1M (non-commercial) → NOT for production.

Production model candidate (pending license audit):
  AuraFace v1 (fal/AuraFace-v1) — commercially-friendly intent,
  but training dataset documentation incomplete → requires legal review.

Design rules:
  - image_bytes consumed only — never stored, logged, or returned
  - plaintext embedding deleted from memory after encryption (caller's responsibility)
  - face_match_score: never generated, stored, or returned here
  - model error messages: sanitized — no image data, no embedding, no stack trace
  - No face_match_status="verified" — approval gate is PR-6
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.services.biometric.face_preprocessing import preprocess_face_image
from app.services.biometric.model_registry import (
    ModelNotAvailableError,
    assert_model_path_safe,
    verify_model_checksum,
)

logger = logging.getLogger(__name__)

_EXPECTED_EMBED_DIM = 512


class OnnxEmbeddingProvider:
    """
    R&D/PROTOTYPE ONLY. NOT FOR PRODUCTION USE WITHOUT LICENSE REVIEW.

    Model-agnostic ONNX face embedding provider.
    Loads an ArcFace-standard ONNX model and returns 512-dim float32 embeddings.

    Requires:
        BIOMETRIC_FACE_MATCHING_ENABLED=true
        BIOMETRIC_ONNX_RND_ENABLED=true
        BIOMETRIC_ONNX_MODEL_PATH=<absolute filesystem path>
    """

    def __init__(self) -> None:
        self._assert_guards()
        self._session = self._load_session()

    # ── Guard checks ──────────────────────────────────────────────────────────

    def _assert_guards(self) -> None:
        """Hard stop if either R&D guard is not satisfied."""
        if not settings.BIOMETRIC_ONNX_RND_ENABLED:
            raise ModelNotAvailableError(
                "ONNX provider is disabled. "
                "Set BIOMETRIC_ONNX_RND_ENABLED=true for R&D/prototype use only. "
                "This flag must NEVER be true in production."
            )
        if not settings.BIOMETRIC_FACE_MATCHING_ENABLED:
            raise ModelNotAvailableError(
                "ONNX provider requires BIOMETRIC_FACE_MATCHING_ENABLED=true."
            )

    # ── Session loading ───────────────────────────────────────────────────────

    def _load_session(self):
        """
        Load the ONNX InferenceSession.

        onnxruntime is imported here (and only here) so that it is not loaded
        when provider=fake or when the R&D guard is False.
        """
        import onnxruntime as ort   # deferred import — only in this module

        model_path = assert_model_path_safe(settings.BIOMETRIC_ONNX_MODEL_PATH)

        expected_checksum = settings.BIOMETRIC_ONNX_MODEL_SHA256
        if expected_checksum:
            verify_model_checksum(model_path, expected_checksum)
        else:
            logger.warning(
                "biometric_onnx_no_checksum file=%s "
                "— set BIOMETRIC_ONNX_MODEL_SHA256 for integrity verification",
                model_path.name,
            )

        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],  # CPU-only; no GPU dependency
        )
        logger.info("biometric_onnx_session_loaded file=%s", model_path.name)
        return session

    # ── Inference ─────────────────────────────────────────────────────────────

    def generate(self, image_bytes: bytes) -> list[float]:
        """
        Generate a 512-dim L2-normalized face embedding from image bytes.

        image_bytes are consumed for inference only — never stored or logged.
        The returned embedding is plaintext; callers must encrypt immediately
        and delete the reference (del embedding).

        No face_match_score is generated or returned.
        face_match_status is never set to 'verified' by this method.

        Raises:
            ValueError: if image preprocessing fails.
            ModelNotAvailableError: if session is not loaded.
        """
        input_tensor = preprocess_face_image(image_bytes)

        input_name = self._session.get_inputs()[0].name
        try:
            outputs = self._session.run(None, {input_name: input_tensor})
        except Exception as exc:
            # Sanitized error — no image data, no embedding, no stack trace
            raise ModelNotAvailableError(
                f"ONNX inference failed: model_error_code={type(exc).__name__}"
            ) from exc

        raw = outputs[0][0].tolist()

        # Validate dimension
        if len(raw) != _EXPECTED_EMBED_DIM:
            raise ModelNotAvailableError(
                f"Unexpected embedding dimension: got {len(raw)}, "
                f"expected {_EXPECTED_EMBED_DIM}"
            )

        # L2 normalize
        magnitude = sum(v * v for v in raw) ** 0.5
        if magnitude > 0:
            embedding = [v / magnitude for v in raw]
        else:
            embedding = [1.0 / (_EXPECTED_EMBED_DIM ** 0.5)] * _EXPECTED_EMBED_DIM

        return embedding
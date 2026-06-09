"""
Biometric image intake service — feat/backend-onnx-image-pipeline.

Validates incoming JPEG uploads before ONNX processing.

Rules enforced:
  - content-type must be image/jpeg or image/jpg
  - size must not exceed BIOMETRIC_IMAGE_MAX_BYTES (default 3 MB)
  - PIL decode validation (catches truncated/corrupted JPEG)
  - raw bytes are passed through ONLY — never written to disk, never logged
  - face_match_score: not handled here

FaceAlignmentError from face_alignment.py is NOT caught here —
callers must call map_alignment_error() to convert to an HTTPException.

Not KYC. R&D dev/test only. DPIA/DPO approval required for production.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, UploadFile, status

logger = logging.getLogger(__name__)

_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/jpg"})

# HTTP detail strings — mirrored in BiometricClientError on the iOS side
_DETAIL_INVALID_FORMAT = "biometric_image_invalid_format"
_DETAIL_TOO_LARGE      = "biometric_image_too_large"


class BiometricImageIntake:
    """
    Validates a FastAPI UploadFile as a JPEG for biometric processing.

    Usage:
        intake = BiometricImageIntake(max_bytes=settings.BIOMETRIC_IMAGE_MAX_BYTES)
        image_bytes = await intake.read_and_validate(upload_file)
        # image_bytes contains the raw JPEG — never log or store permanently
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes

    async def read_and_validate(self, upload: UploadFile) -> bytes:
        """
        Read and validate an image upload.

        Checks (in order):
          1. content-type is image/jpeg or image/jpg
          2. file is non-empty and ≤ max_bytes
          3. PIL can decode it as an image (basic corruption guard)

        Returns raw JPEG bytes on success.
        Raises HTTPException (413 / 415) on failure.

        Privacy:
          - raw bytes are NOT logged
          - file size IS logged at DEBUG level (no image content)
          - model path (if any) is NOT involved here
        """
        # ── 1. Content-type guard ─────────────────────────────────────────────
        ct = (upload.content_type or "").lower().split(";")[0].strip()
        if ct not in _ALLOWED_CONTENT_TYPES:
            logger.debug(
                "biometric_image_intake_rejected content_type=%r",
                ct,
            )
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=_DETAIL_INVALID_FORMAT,
            )

        # ── 2. Read + size guard ──────────────────────────────────────────────
        data = await upload.read()
        size = len(data)

        if size == 0:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=_DETAIL_INVALID_FORMAT,
            )

        if size > self.max_bytes:
            logger.debug(
                "biometric_image_intake_too_large size_bytes=%d max=%d",
                size, self.max_bytes,
            )
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=_DETAIL_TOO_LARGE,
            )

        # ── 3. PIL decode guard (basic corruption check) ─────────────────────
        try:
            import io
            from PIL import Image as _PILImage
            _PILImage.open(io.BytesIO(data)).verify()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=_DETAIL_INVALID_FORMAT,
            )

        logger.debug("biometric_image_intake_ok size_bytes=%d", size)
        return data
        # raw bytes: NEVER logged beyond size, NEVER written to disk here


def map_alignment_error_to_http(exc: "FaceAlignmentError") -> None:
    """
    Convert a FaceAlignmentError into an HTTPException with a typed detail string.

    Raises HTTPException — never returns.
    exc.detail (raw internal message) is NOT forwarded to avoid leaking info.
    """
    from app.services.biometric.face_alignment import AlignedFaceErrorCode

    _MAPPING: dict[AlignedFaceErrorCode, tuple[str, int]] = {
        AlignedFaceErrorCode.INVALID_IMAGE:          (_DETAIL_INVALID_FORMAT,              415),
        AlignedFaceErrorCode.NO_FACE_DETECTED:       ("biometric_no_face_detected",         422),
        AlignedFaceErrorCode.MULTIPLE_FACES:         ("biometric_multiple_faces_detected",  422),
        AlignedFaceErrorCode.FACE_TOO_SMALL:         ("biometric_face_too_small",           422),
        AlignedFaceErrorCode.ALIGNMENT_FAILED:       ("biometric_alignment_failed",         422),
        AlignedFaceErrorCode.LANDMARKS_MISSING:      ("biometric_alignment_failed",         422),
        AlignedFaceErrorCode.DETECTOR_NOT_AVAILABLE: ("biometric_detector_unavailable",     503),
    }
    detail, status_code = _MAPPING.get(exc.code, ("biometric_processing_error", 422))
    raise HTTPException(status_code=status_code, detail=detail)
    # exc.detail is NOT forwarded — prevents landmark coords / internal paths from leaking

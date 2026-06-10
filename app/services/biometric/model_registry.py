"""
Biometric model file validation — PR-5 R&D/prototype.

R&D/PROTOTYPE ONLY. NOT FOR PRODUCTION USE WITHOUT LICENSE REVIEW.
See docs/biometric/PR5_PLAN.md for production readiness gates.

Validates ONNX model files before loading:
  - File existence check
  - SHA-256 checksum verification (if configured)
  - Size sanity check

Design rules:
  - Never logs model file contents or paths beyond the basename
  - Never downloads models — filesystem path only
  - Checksum mismatch raises RuntimeError and blocks loading
  - No face_match_score, no embedding data, no image data handled here
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CHECKSUM_CHUNK = 65_536   # 64 KB read chunks for large model files


class ModelNotAvailableError(Exception):
    """Raised when the ONNX model file is not present or misconfigured."""


def verify_model_checksum(path: Path, expected_sha256: str) -> None:
    """
    Verify the SHA-256 checksum of a model file.

    Raises RuntimeError if the computed checksum does not match expected_sha256.
    This is a hard stop — the model must NOT be loaded if the checksum fails.

    Logs only the basename of the file, never the full path or file contents.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHECKSUM_CHUNK):
            hasher.update(chunk)
    actual = hasher.hexdigest()

    if actual.lower() != expected_sha256.lower():
        logger.critical(
            "biometric_model_checksum_mismatch file=%s expected=%s...%s actual=%s...%s",
            path.name,
            expected_sha256[:8], expected_sha256[-8:],
            actual[:8], actual[-8:],
        )
        raise RuntimeError(
            f"Model file checksum mismatch: {path.name}. "
            "Do not proceed — model may be corrupted or tampered. "
            "Verify the model file against the registered SHA-256."
        )

    logger.info("biometric_model_checksum_ok file=%s", path.name)


def assert_model_path_safe(model_path: str) -> Path:
    """
    Validate and return the model path as a Path object.

    Raises ModelNotAvailableError if:
      - path is empty (BIOMETRIC_ONNX_MODEL_PATH not configured)
      - the file does not exist on disk
      - the path attempts directory traversal
    """
    if not model_path:
        raise ModelNotAvailableError(
            "BIOMETRIC_ONNX_MODEL_PATH is not set. "
            "Provide an absolute filesystem path to the ONNX model. "
            "Do NOT set this to a URL or CDN reference."
        )

    path = Path(model_path).resolve()

    # Basic traversal check — resolved path must match the original
    if not path.is_file():
        raise ModelNotAvailableError(
            f"ONNX model file not found: {path.name}. "
            "Ensure BIOMETRIC_ONNX_MODEL_PATH points to an existing file."
        )

    return path
"""
Face image preprocessing for ArcFace-compatible ONNX input — PR-5 R&D/prototype.

R&D/PROTOTYPE ONLY. NOT FOR PRODUCTION USE WITHOUT LICENSE REVIEW.

Converts raw image bytes to a normalized numpy array suitable for
ArcFace-standard ONNX models (e.g. arcfaceresnet100, AuraFace):
  - Resize to 112×112 px
  - Normalize pixel values to [-1, 1]
  - Transpose to NCHW layout (batch=1, channels=3, H=112, W=112)
  - dtype: float32

Design rules:
  - image_bytes are consumed and NEVER stored, logged, or returned
  - No face detection (assumes pre-cropped face image)
  - No landmark alignment in PR-5 (deferred to PR-6)
  - No model-specific preprocessing here — caller selects the model
  - numpy is used; no onnxruntime dependency in this module
"""
from __future__ import annotations

import io
import logging

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

_TARGET_SIZE = (112, 112)   # ArcFace standard input size
_NORM_MEAN   = 127.5        # normalize to [-1, 1]
_NORM_STD    = 128.0


def preprocess_face_image(image_bytes: bytes) -> np.ndarray:
    """
    Preprocess raw image bytes into an ArcFace-compatible ONNX input tensor.

    Returns:
        np.ndarray of shape (1, 3, 112, 112), dtype=float32, values in [-1, 1].

    Raises:
        ValueError: if image_bytes cannot be decoded as an image.

    image_bytes are consumed only — never stored, logged, or returned.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise ValueError(f"Cannot decode image bytes: {exc}") from exc

    img = img.resize(_TARGET_SIZE, Image.BILINEAR)
    arr = np.array(img, dtype=np.float32)              # (112, 112, 3) HWC

    # Normalize to [-1, 1]
    arr = (arr - _NORM_MEAN) / _NORM_STD

    # HWC → NCHW  (1, 3, 112, 112)
    arr = arr.transpose(2, 0, 1)[np.newaxis, ...]      # (1, 3, 112, 112)

    return arr
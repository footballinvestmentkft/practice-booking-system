"""
Face detection + 5-point landmark-based alignment — feat/backend-face-detection-alignment.

R&D/PROTOTYPE ONLY. NOT FOR PRODUCTION USE WITHOUT LICENSE REVIEW.

Provides:
  FaceAlignmentPipeline  — SCRFD-500M face detector + affine crop to 112×112
  FaceAlignmentError     — typed error with AlignedFaceErrorCode

Pipeline:
  1. Validate and decode JPEG → PIL image
  2. SCRFD-500M face detection (CPUExecutionProvider)
  3. Select single best face (confidence-ranked); reject 0 or >1 if configured
  4. Validate face size (bounding box area)
  5. Validate 5-point landmarks present
  6. Compute similarity transform: detected landmarks → ARCFACE_DST_5PT
  7. Warp-crop → 112×112 RGB PIL image
  8. Normalize [-1,1], transpose to NCHW float32

Design rules:
  - No OpenCV dependency; only onnxruntime + numpy + Pillow (already installed)
  - No image bytes, pixels, or landmark coords in logs
  - Model path logged as basename only
  - face_match_score: never generated or handled here
  - CPUExecutionProvider only (no GPU dependency)

Detector: SCRFD-500M (det_500m.onnx, InsightFace v0.7 release)
  ONNX inputs:  input.1  (1, 3, 640, 640) float32, normalized [-1, 1]
  ONNX outputs: 9 tensors — scores/boxes/keypoints at strides [8, 16, 32]
  Decode:       FCOS distance → absolute pixel coords; sigmoid score threshold

Not KYC. R&D dev/test only. DPIA/DPO approval required for production.
"""
from __future__ import annotations

import logging
import math
from enum import Enum
from pathlib import Path

import numpy as np
from PIL import Image

from app.services.biometric.face_alignment_config import (
    ARCFACE_DST_5PT,
    DETECTOR_INPUT_SIZE,
    MIN_FACE_PX,
    NMS_IOU_THRESHOLD,
    SCRFD_N_ANCHORS,
    SCRFD_STRIDES,
    SCORE_THRESHOLD_LOGIT,
)
from app.services.biometric.model_registry import (
    ModelNotAvailableError,
    assert_model_path_safe,
    verify_model_checksum,
)

logger = logging.getLogger(__name__)


# ── Error types ────────────────────────────────────────────────────────────────

class AlignedFaceErrorCode(str, Enum):
    NO_FACE_DETECTED       = "no_face_detected"
    MULTIPLE_FACES         = "multiple_faces_detected"
    FACE_TOO_SMALL         = "face_too_small"
    LANDMARKS_MISSING      = "face_landmarks_missing"
    ALIGNMENT_FAILED       = "alignment_transform_failed"
    INVALID_IMAGE          = "invalid_image"
    DETECTOR_NOT_AVAILABLE = "detector_not_available"


class FaceAlignmentError(Exception):
    """Typed face alignment error. detail is sanitized — no image bytes or coords."""

    def __init__(self, code: AlignedFaceErrorCode, detail: str = "") -> None:
        self.code   = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


# ── SCRFD post-processing helpers ──────────────────────────────────────────────

def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x.astype(np.float32), -88.0, 88.0)))


def _generate_anchor_centers(fh: int, fw: int, stride: int) -> np.ndarray:
    """
    Generate anchor center coordinates for one SCRFD feature level.

    Returns (fh * fw * SCRFD_N_ANCHORS, 2) array of (x, y) centers.
    Matches InsightFace SCRFD convention: anchors at (col*stride, row*stride)
    with SCRFD_N_ANCHORS identical anchors per grid cell.
    """
    gy, gx = np.mgrid[0:fh, 0:fw]
    cx = (gx * stride).reshape(-1).astype(np.float32)
    cy = (gy * stride).reshape(-1).astype(np.float32)
    centers = np.stack([cx, cy], axis=-1)                      # (fh*fw, 2)
    return np.repeat(centers, SCRFD_N_ANCHORS, axis=0)         # (fh*fw*N, 2)


def _decode_boxes(centers: np.ndarray, box_reg: np.ndarray, stride: int) -> np.ndarray:
    """
    Decode FCOS distance regression to (x1, y1, x2, y2) boxes.

    box_reg: (N, 4) — (left, top, right, bottom) distance predictions
    Returns (N, 4) absolute pixel boxes.
    """
    dist = box_reg * stride
    x1 = centers[:, 0] - dist[:, 0]
    y1 = centers[:, 1] - dist[:, 1]
    x2 = centers[:, 0] + dist[:, 2]
    y2 = centers[:, 1] + dist[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _decode_keypoints(centers: np.ndarray, kps_reg: np.ndarray, stride: int) -> np.ndarray:
    """
    Decode FCOS keypoint regression to (N, 5, 2) absolute pixel keypoints.

    kps_reg: (N, 10) — (dx1, dy1, dx2, dy2, ...) for 5 keypoints
    Returns (N, 5, 2) absolute pixel keypoints.
    """
    dist = kps_reg * stride              # (N, 10)
    kps  = np.empty_like(dist)
    for i in range(5):
        kps[:, 2 * i]     = centers[:, 0] + dist[:, 2 * i]
        kps[:, 2 * i + 1] = centers[:, 1] + dist[:, 2 * i + 1]
    return kps.reshape(-1, 5, 2)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    """
    Greedy NMS without OpenCV.

    boxes: (N, 4) as (x1, y1, x2, y2)
    scores: (N,) float
    Returns keep indices (sorted by descending score).
    """
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        ix1 = np.maximum(x1[i], x1[order[1:]])
        iy1 = np.maximum(y1[i], y1[order[1:]])
        ix2 = np.minimum(x2[i], x2[order[1:]])
        iy2 = np.minimum(y2[i], y2[order[1:]])
        inter = (ix2 - ix1).clip(0) * (iy2 - iy1).clip(0)
        union = areas[i] + areas[order[1:]] - inter
        iou   = np.where(union > 0, inter / union, 0.0)
        order = order[np.where(iou <= iou_threshold)[0] + 1]
    return np.array(keep, dtype=np.int64)


# ── Similarity transform ───────────────────────────────────────────────────────

def _estimate_similarity_transform(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """
    Estimate a 2×3 similarity-transform matrix mapping src → dst.

    src, dst: (N, 2) float32 point arrays (N ≥ 2)
    Returns M: (2, 3) float64 affine matrix.

    Similarity transform has 4 DOF: scale, rotation, translation (tx, ty).
    Solved via least squares for robustness with 5 point pairs.
    """
    n   = src.shape[0]
    A   = np.zeros((2 * n, 4), dtype=np.float64)
    b   = np.zeros(2 * n,     dtype=np.float64)
    for i in range(n):
        x, y        = float(src[i, 0]), float(src[i, 1])
        A[2 * i]    = [x, -y, 1.0, 0.0]
        A[2 * i + 1]= [y,  x, 0.0, 1.0]
        b[2 * i]    = float(dst[i, 0])
        b[2 * i + 1]= float(dst[i, 1])
    p, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    sx, sy, tx, ty = p
    return np.array([[sx, -sy, tx],
                     [sy,  sx, ty]], dtype=np.float64)


def _warp_aligned_crop(img: Image.Image, M: np.ndarray, size: int = 112) -> Image.Image:
    """
    Apply similarity transform M (2×3, src→dst) and crop to size×size.

    PIL.Image.transform expects the INVERSE mapping (dst pixel → src pixel),
    so we invert M before passing to PIL.
    """
    M33     = np.vstack([M, [0.0, 0.0, 1.0]])   # (3,3)
    M33_inv = np.linalg.inv(M33)
    Minv    = M33_inv[:2]                        # (2,3) inverse
    flat    = (
        float(Minv[0, 0]), float(Minv[0, 1]), float(Minv[0, 2]),
        float(Minv[1, 0]), float(Minv[1, 1]), float(Minv[1, 2]),
    )
    return img.transform(
        (size, size),
        Image.Transform.AFFINE,
        flat,
        resample=Image.BILINEAR,
    )


# ── Main pipeline class ────────────────────────────────────────────────────────

class FaceAlignmentPipeline:
    """
    R&D/PROTOTYPE ONLY. NOT FOR PRODUCTION USE WITHOUT LICENSE REVIEW.

    SCRFD-500M face detection + ArcFace 5-point landmark alignment.

    Activation requires BIOMETRIC_FACE_DETECTOR_PATH to be set.
    SHA-256 checksum enforced when BIOMETRIC_FACE_DETECTOR_SHA256 is set.
    """

    def __init__(self) -> None:
        self._session = self._load_session()

    # ── Session loading ───────────────────────────────────────────────────────

    def _load_session(self):
        from app.config import settings
        import onnxruntime as ort   # deferred — same pattern as OnnxEmbeddingProvider

        detector_path = getattr(settings, "BIOMETRIC_FACE_DETECTOR_PATH", "")
        if not detector_path:
            raise ModelNotAvailableError(
                "BIOMETRIC_FACE_DETECTOR_PATH is not set. "
                "Provide the absolute path to det_500m.onnx."
            )

        path = assert_model_path_safe(detector_path)

        checksum = getattr(settings, "BIOMETRIC_FACE_DETECTOR_SHA256", "")
        if checksum:
            verify_model_checksum(path, checksum)
        else:
            logger.warning(
                "biometric_face_detector_no_checksum file=%s "
                "— set BIOMETRIC_FACE_DETECTOR_SHA256 for integrity verification",
                path.name,
            )

        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        logger.info("biometric_face_detector_loaded file=%s", path.name)
        return session

    # ── Public interface ──────────────────────────────────────────────────────

    def preprocess(self, image_bytes: bytes) -> np.ndarray:
        """
        Full pipeline: JPEG bytes → aligned 112×112 → NCHW float32 [-1,1].

        Raises FaceAlignmentError on all failure modes.
        No image bytes, pixel values, or landmark coords are logged.
        """
        img_pil = self._decode_image(image_bytes)
        boxes, scores, kps_all = self._detect_faces(img_pil)
        box, kps = self._select_face(boxes, scores, kps_all)
        aligned  = self._align(img_pil, kps)
        return self._to_tensor(aligned)

    # ── Step 1: Image decode ──────────────────────────────────────────────────

    def _decode_image(self, image_bytes: bytes) -> Image.Image:
        import io
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        except Exception:
            raise FaceAlignmentError(
                AlignedFaceErrorCode.INVALID_IMAGE,
                "Cannot decode image bytes",
            )
        return img

    # ── Step 2: Face detection ────────────────────────────────────────────────

    def _detect_faces(
        self,
        img_pil: Image.Image,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Run SCRFD-500M on the image.

        Returns:
          boxes:  (K, 4)  float32 — (x1, y1, x2, y2) in original pixel space
          scores: (K,)    float32 — sigmoid confidence
          kps:    (K, 5, 2) float32 — 5-point landmarks in original pixel space

        K is the number of detections after NMS (may be 0).
        """
        orig_w, orig_h = img_pil.size
        inp_size       = DETECTOR_INPUT_SIZE

        # Resize while preserving aspect ratio; pad to square
        scale    = inp_size / max(orig_w, orig_h)
        new_w    = int(round(orig_w * scale))
        new_h    = int(round(orig_h * scale))
        img_rs   = img_pil.resize((new_w, new_h), Image.BILINEAR)

        canvas   = Image.new("RGB", (inp_size, inp_size), (127, 127, 127))
        canvas.paste(img_rs, (0, 0))

        # Normalize [-1, 1] and convert to NCHW float32
        x = np.array(canvas, dtype=np.float32)
        x = (x - 127.5) / 128.0
        x = x.transpose(2, 0, 1)[np.newaxis, ...]   # (1, 3, H, W)

        inp_name = self._session.get_inputs()[0].name
        outs     = self._session.run(None, {inp_name: x})

        # Post-process all stride levels
        all_boxes:  list[np.ndarray] = []
        all_scores: list[np.ndarray] = []
        all_kps:    list[np.ndarray] = []

        for level_idx, stride in enumerate(SCRFD_STRIDES):
            fsize    = inp_size // stride
            raw_sc   = outs[level_idx]                    # (fsize², 1)
            raw_bx   = outs[level_idx + 3]                # (fsize², 4)
            raw_kp   = outs[level_idx + 6]                # (fsize², 10)

            scores_level = _sigmoid(raw_sc).flatten()     # (fsize²,)

            # Filter by threshold
            mask = scores_level >= _sigmoid(
                np.array([SCORE_THRESHOLD_LOGIT], dtype=np.float32)
            ).item()
            if not mask.any():
                continue

            centers = _generate_anchor_centers(fsize, fsize, stride)
            boxes_level = _decode_boxes(centers, raw_bx, stride)  # (N, 4)
            kps_level   = _decode_keypoints(centers, raw_kp, stride)  # (N, 5, 2)

            # Scale back to original image coordinates
            inv_scale = 1.0 / scale
            boxes_level = boxes_level[mask] * inv_scale
            kps_level   = kps_level[mask]   * inv_scale
            scores_level = scores_level[mask]

            all_boxes.append(boxes_level)
            all_scores.append(scores_level)
            all_kps.append(kps_level)

        if not all_boxes:
            return (
                np.zeros((0, 4),    dtype=np.float32),
                np.zeros((0,),      dtype=np.float32),
                np.zeros((0, 5, 2), dtype=np.float32),
            )

        boxes  = np.concatenate(all_boxes,  axis=0)
        scores = np.concatenate(all_scores, axis=0)
        kps    = np.concatenate(all_kps,    axis=0)

        # NMS
        keep   = _nms(boxes, scores, NMS_IOU_THRESHOLD)
        return boxes[keep], scores[keep], kps[keep]

    # ── Step 3: Face selection and validation ─────────────────────────────────

    def _select_face(
        self,
        boxes:  np.ndarray,
        scores: np.ndarray,
        kps:    np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Select the single best (highest-confidence) face.

        Raises FaceAlignmentError for 0 faces or face too small.
        Returns (box, kps) where kps shape is (5, 2).
        """
        if boxes.shape[0] == 0:
            raise FaceAlignmentError(AlignedFaceErrorCode.NO_FACE_DETECTED)

        # Pick highest-confidence detection
        best = int(scores.argmax())
        box  = boxes[best]   # (4,) x1,y1,x2,y2
        kp   = kps[best]     # (5, 2)

        # Validate face size
        face_w = float(box[2] - box[0])
        face_h = float(box[3] - box[1])
        if face_w < MIN_FACE_PX or face_h < MIN_FACE_PX:
            raise FaceAlignmentError(
                AlignedFaceErrorCode.FACE_TOO_SMALL,
                f"face_w={face_w:.0f} face_h={face_h:.0f} min={MIN_FACE_PX}",
            )

        # Validate keypoints are finite
        if not np.isfinite(kp).all():
            raise FaceAlignmentError(AlignedFaceErrorCode.LANDMARKS_MISSING)

        return box, kp

    # ── Step 4: Alignment ────────────────────────────────────────────────────

    def _align(self, img_pil: Image.Image, kps: np.ndarray) -> Image.Image:
        """
        Compute similarity transform from detected landmarks → ARCFACE_DST_5PT,
        then warp-crop the image to 112×112.
        """
        try:
            M = _estimate_similarity_transform(
                src=kps.astype(np.float32),
                dst=ARCFACE_DST_5PT,
            )
        except Exception:
            raise FaceAlignmentError(
                AlignedFaceErrorCode.ALIGNMENT_FAILED,
                "Similarity transform computation failed",
            )

        # Sanity check: transformation scale must be finite and non-degenerate
        scale = math.sqrt(float(M[0, 0]) ** 2 + float(M[1, 0]) ** 2)
        if not (0.01 < scale < 100.0):
            raise FaceAlignmentError(
                AlignedFaceErrorCode.ALIGNMENT_FAILED,
                f"Degenerate transform scale={scale:.4f}",
            )

        try:
            aligned = _warp_aligned_crop(img_pil, M, size=112)
        except Exception:
            raise FaceAlignmentError(
                AlignedFaceErrorCode.ALIGNMENT_FAILED,
                "Warp crop failed",
            )

        return aligned

    # ── Step 5: Tensor conversion ────────────────────────────────────────────

    @staticmethod
    def _to_tensor(img: Image.Image) -> np.ndarray:
        """112×112 RGB PIL → NCHW float32 [-1, 1]."""
        arr = np.array(img, dtype=np.float32)       # (112, 112, 3)
        arr = (arr - 127.5) / 128.0                 # normalize to [-1, 1]
        arr = arr.transpose(2, 0, 1)[np.newaxis, :]  # NCHW (1, 3, 112, 112)
        return arr


# ── Factory ────────────────────────────────────────────────────────────────────

def get_face_alignment_pipeline() -> FaceAlignmentPipeline | None:
    """
    Return a FaceAlignmentPipeline if BIOMETRIC_FACE_DETECTOR_PATH is set,
    otherwise return None (falls back to naive preprocessing).
    """
    from app.config import settings
    path = getattr(settings, "BIOMETRIC_FACE_DETECTOR_PATH", "")
    if not path:
        return None
    try:
        return FaceAlignmentPipeline()
    except ModelNotAvailableError as exc:
        logger.warning("biometric_face_alignment_unavailable: %s", exc)
        return None

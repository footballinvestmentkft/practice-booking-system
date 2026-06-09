"""
Face alignment constants — ArcFace canonical landmarks and detection thresholds.

ArcFace standard: 5-point landmarks projected onto a 112×112 image.
Source: InsightFace ArcFace training convention (widely published).
These coordinates are the destination for the similarity transform.

Not model-specific — applies to all ArcFace-compatible embeddings.
"""
from __future__ import annotations

import numpy as np

# ── ArcFace standard 5-point destination (112×112) ───────────────────────────
# Order: left_eye, right_eye, nose_tip, mouth_left, mouth_right
# "left" / "right" from the subject's perspective (not camera perspective).
ARCFACE_DST_5PT: np.ndarray = np.array(
    [
        [38.2946, 51.6963],   # left eye
        [73.5318, 51.5014],   # right eye
        [56.0252, 71.7366],   # nose tip
        [41.5493, 92.3655],   # left mouth corner
        [70.7299, 92.2041],   # right mouth corner
    ],
    dtype=np.float32,
)

# ── SCRFD detector constants ──────────────────────────────────────────────────

# Expected input spatial dimensions for det_500m.onnx (must be multiple of 32)
DETECTOR_INPUT_SIZE: int = 640

# SCRFD-500M strides and corresponding output indices in model output list:
#   output[0]: stride8  scores  (12800, 1)
#   output[1]: stride16 scores  (3200, 1)
#   output[2]: stride32 scores  (800, 1)
#   output[3]: stride8  boxes   (12800, 4)
#   output[4]: stride16 boxes   (3200, 4)
#   output[5]: stride32 boxes   (800, 4)
#   output[6]: stride8  kps     (12800, 10)
#   output[7]: stride16 kps     (3200, 10)
#   output[8]: stride32 kps     (800, 10)
SCRFD_STRIDES: list[int] = [8, 16, 32]
SCRFD_N_ANCHORS: int = 2   # 2 anchors per grid cell for SCRFD-500M

# Score threshold (raw logit — before sigmoid)
# logit=0.598 ≈ sigmoid=0.645; conservative to avoid missed detections
SCORE_THRESHOLD_LOGIT: float = 0.0   # sigmoid(0.0)=0.5

# NMS IoU threshold
NMS_IOU_THRESHOLD: float = 0.4

# Minimum face dimension (pixels in original image) below which we reject
MIN_FACE_PX: int = 20

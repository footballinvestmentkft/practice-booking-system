"""
ONNX Ball Detector — SSD MobileNet v1 inference via onnxruntime (MIT).

Model: ssd_mobilenet_v1_12.onnx (Apache-2.0, ONNX Model Zoo).
Input: uint8 NHWC frame. Output: best sports_ball detection or None.
No skill pipeline interaction — measurement utility only.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)


class OnnxBallDetector:
    """Lazy-loaded ONNX session for ball detection."""

    def __init__(self, model_path: str):
        if not Path(model_path).is_file():
            raise FileNotFoundError(
                f"ONNX model not found: {model_path}. "
                "Run scripts/download_ml_models.py to download."
            )
        self._session = ort.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
        )
        logger.info("onnx_ball_detector: loaded %s", model_path)

    def detect(
        self,
        frame_rgb: np.ndarray,
        target_class_id: int = 37,
        confidence_threshold: float = 0.3,
    ) -> tuple[float, float, float] | None:
        """
        Run detection on a single RGB frame.

        Returns (center_x_norm, center_y_norm, confidence) for the highest-
        confidence sports_ball detection, or None if nothing found above
        the threshold.  Coordinates are normalized [0, 1], origin top-left.
        """
        input_tensor = np.expand_dims(frame_rgb.astype(np.uint8), axis=0)
        outputs = self._session.run(None, {"image_tensor:0": input_tensor})

        num_det = int(outputs[0][0])
        boxes   = outputs[1][0]
        scores  = outputs[2][0]
        classes = outputs[3][0]

        best_score = 0.0
        best_box = None

        for i in range(num_det):
            if int(classes[i]) == target_class_id and scores[i] >= confidence_threshold:
                if scores[i] > best_score:
                    best_score = float(scores[i])
                    best_box = boxes[i]

        if best_box is None:
            return None

        ymin, xmin, ymax, xmax = best_box
        cx = float((xmin + xmax) / 2.0)
        cy = float((ymin + ymax) / 2.0)
        return (cx, cy, best_score)


_detector_cache: dict[str, OnnxBallDetector] = {}


def get_detector(model_path: str) -> OnnxBallDetector:
    if model_path not in _detector_cache:
        _detector_cache[model_path] = OnnxBallDetector(model_path)
    return _detector_cache[model_path]

"""
Video frame extraction via OpenCV (opencv-python-headless, Apache-2.0).

Extracts a single RGB frame at a given timestamp for ball detection.
No skill pipeline interaction — measurement utility only.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def extract_frame_at_ms(
    video_path: str,
    timestamp_ms: int,
) -> tuple[np.ndarray, int, int]:
    """
    Extract a single RGB frame from a video at the given millisecond offset.

    Returns (frame_rgb, width, height).
    Raises ValueError if the file cannot be opened or the frame cannot be read.
    """
    cap = cv2.VideoCapture(video_path)
    try:
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_ms)
        ret, frame_bgr = cap.read()
        if not ret or frame_bgr is None:
            raise ValueError(
                f"Frame extraction failed at {timestamp_ms}ms from {video_path}"
            )
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = frame_rgb.shape[:2]
        return frame_rgb, w, h
    finally:
        cap.release()

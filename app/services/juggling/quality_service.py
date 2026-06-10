"""
Juggling video quality analysis.

Inputs: server_detected_metadata (from ffprobe), raw file bytes for frame sampling.

Scores computed in this branch:
  blur_score         — higher = sharper  (0.0–1.0)
  dark_frame_ratio   — fraction of frames that are too dark (0.0–1.0, lower = better)
  fps_score          — derived from detected fps

NOT computed in this branch (P2/P3 scope):
  subject_size_score — requires MediaPipe pose inference
  ball_visible_score — requires FootAndBall / YOLO detector

overall_quality_score weighting (only scored dimensions):
  blur_score       × 0.40
  dark_brightness  × 0.40  (= 1.0 - dark_frame_ratio)
  fps_score        × 0.20

Reject thresholds (config-independent, hard-coded for POC):
  dark_frame_ratio > 0.40  → too_dark
  blur_score       < 0.30  → too_blurry
  fps              < 24    → fps_too_low
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


# ── Reject thresholds ────────────────────────────────────────────────────────
_MAX_DARK_FRAME_RATIO: float = 0.40
_MIN_BLUR_SCORE:       float = 0.30
_MIN_FPS:              float = 24.0


def _fps_score(fps: Optional[float]) -> float:
    """Map detected FPS to a score in [0.0, 1.0]."""
    if fps is None:
        return 0.5  # unknown; neutral
    if fps >= 60:
        return 1.0
    if fps >= 30:
        return 0.7
    if fps >= _MIN_FPS:
        return 0.4
    return 0.1


def _estimate_blur_score(file_bytes: bytes) -> float:
    """
    Estimate sharpness from file size / duration as a POC proxy.

    Real blur detection requires frame decoding (OpenCV Laplacian variance).
    That is P2 scope.  For POC we use bitrate as a proxy: higher bitrate
    generally means more detail, less compression artefacts.

    Returns 0.0–1.0. This is a placeholder; replace with Laplacian in P2.
    """
    size_mb = len(file_bytes) / (1024 * 1024)
    # Normalise: assume 5 MB minimum for reasonable quality, 80 MB = excellent
    score = min(1.0, max(0.0, (size_mb - 2) / 78))
    # Floor at 0.40 so the placeholder doesn't reject valid videos
    return round(max(0.40, score), 3)


def _estimate_dark_frame_ratio(file_bytes: bytes) -> float:
    """
    POC placeholder — returns a heuristic dark_frame_ratio.

    Real dark frame detection requires frame decoding.  That is P2 scope.
    We conservatively return 0.05 (5 % dark frames) as a safe default.
    """
    return 0.05


def analyze(
    file_bytes: bytes,
    server_metadata: Optional[Dict[str, Any]],
) -> Tuple[float, str, Dict[str, Any], Optional[str]]:
    """
    Run quality analysis and return:
      (quality_score, quality_status, quality_detail, rejection_reason)

    quality_status: "acceptable" | "needs_review" | "rejected"
    rejection_reason: None or a machine-readable code string
    """
    meta = server_metadata or {}

    fps_detected: Optional[float] = meta.get("fps")
    duration_ok = True  # duration gate is enforced in metadata_service / task
    rotation: int = meta.get("rotation", 0)

    blur_score = _estimate_blur_score(file_bytes)
    dark_frame_ratio = _estimate_dark_frame_ratio(file_bytes)
    fps_sc = _fps_score(fps_detected)

    fps_acceptable = fps_detected is None or fps_detected >= _MIN_FPS
    has_audio: bool = meta.get("has_audio", False)

    # ── Overall score (null dimensions excluded) ─────────────────────────────
    dark_brightness = 1.0 - dark_frame_ratio
    overall = round(
        blur_score * 0.40
        + dark_brightness * 0.40
        + fps_sc * 0.20,
        4,
    )

    # ── Rejection checks ─────────────────────────────────────────────────────
    rejection_reason: Optional[str] = None
    if dark_frame_ratio > _MAX_DARK_FRAME_RATIO:
        rejection_reason = "too_dark"
    elif blur_score < _MIN_BLUR_SCORE:
        rejection_reason = "too_blurry"
    elif fps_detected is not None and fps_detected < _MIN_FPS:
        rejection_reason = "fps_too_low"

    # ── Quality status ────────────────────────────────────────────────────────
    if rejection_reason:
        quality_status = "rejected"
    elif overall >= 0.70:
        quality_status = "acceptable"
    else:
        quality_status = "needs_review"

    quality_detail: Dict[str, Any] = {
        "blur_score":          blur_score,
        "dark_frame_ratio":    dark_frame_ratio,
        "fps_detected":        fps_detected,
        "fps_acceptable":      fps_acceptable,
        "duration_acceptable": duration_ok,
        "rotation":            rotation,
        # P2/P3 scope — always null in this branch
        "subject_size_score":  None,
        "ball_visible_score":  None,
    }
    if has_audio:
        quality_detail["audio_present"] = True

    return overall, quality_status, quality_detail, rejection_reason
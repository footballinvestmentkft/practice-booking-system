"""
Canonical crop-box geometry and coordinate transforms — AN-3B2F PR-1B.

canonical_crop_box():
  Computes a square crop centred on (ball_x * img_w, ball_y * img_h) with
  half-side = margin_ratio * min(img_w, img_h) / 2 pixels, clamped to the
  image bounds on all four sides.

tap_to_full_frame():
  Converts a normalised tap coordinate within the cropped image to a
  normalised full-frame coordinate.  Input tap values outside [0, 1] are
  clamped first (treats edge-overflow as a boundary contact).

clamp_unit(val):
  Clamp a float to [0.0, 1.0].
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CropBox:
    """Pixel-coordinate crop box, clamped to image bounds (all values >= 0)."""

    left:   float
    top:    float
    right:  float
    bottom: float


def clamp_unit(val: float) -> float:
    """Clamp *val* to the closed interval [0.0, 1.0]."""
    return max(0.0, min(1.0, float(val)))


def canonical_crop_box(
    ball_x: float,
    ball_y: float,
    img_w: int,
    img_h: int,
    margin_ratio: float = 0.70,
) -> CropBox:
    """Return a square CropBox centred on the ball position.

    half_side = margin_ratio * min(img_w, img_h) / 2  (pixels)

    All four edges are clamped so the box stays within [0, img_w] × [0, img_h].
    The box remains a square only when the ball is sufficiently far from all
    edges; edge-clamping may make it rectangular near the image border.
    """
    cx = ball_x * img_w
    cy = ball_y * img_h
    half = margin_ratio * min(img_w, img_h) / 2.0
    return CropBox(
        left=max(0.0, cx - half),
        top=max(0.0, cy - half),
        right=min(float(img_w), cx + half),
        bottom=min(float(img_h), cy + half),
    )


def tap_to_full_frame(
    tap_x: float,
    tap_y: float,
    crop_box: CropBox,
    img_w: int,
    img_h: int,
) -> tuple[float, float]:
    """Convert a normalised crop-image tap to normalised full-frame coordinates.

    *tap_x*, *tap_y* are in [0, 1] relative to the crop box.  Values outside
    [0, 1] are clamped before back-projection.

    Returns (full_x, full_y) in [0, 1], clamped to image bounds.
    """
    tx = clamp_unit(tap_x)
    ty = clamp_unit(tap_y)
    crop_w = crop_box.right - crop_box.left
    crop_h = crop_box.bottom - crop_box.top
    px = crop_box.left + tx * crop_w
    py = crop_box.top + ty * crop_h
    return clamp_unit(px / img_w), clamp_unit(py / img_h)

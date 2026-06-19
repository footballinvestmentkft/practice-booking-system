"""
Privacy-safe frame serving for the Global Ball Training Hub — AN-3B2F PR-1B.

serve_assignment_frame():
  1. Locks + validates the assignment (existence, ownership, expiry, consumed state).
  2. Loads the trajectory for display-mode determination (read-only).
  3. Runs a live consent check (revoke propagates immediately).
  4. Determines display_mode: "context_crop" | "full_frame".
     - "full_frame"   when tracking_state == 'lost' OR confidence < threshold
                      OR no trajectory row exists.
     - "context_crop" otherwise: ball is centred in a square crop.
  5. Persists display_mode on BallTrainingAssignment (idempotent — flush only,
     no separate commit; the outer commit in the HTTP handler finalises it).
  6. Extracts the JPEG frame via OpenCV and crops if context_crop.
  7. Returns (jpeg_bytes, display_mode).

No video_id, frame_ms, storage_path or owner identity appear in any response
header or body — the caller (HTTP handler) must not add them either.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.juggling import (
    BallTrainingAssignment,
    JugglingBallTrajectory,
    JugglingConsent,
    JugglingVideo,
)
from app.services.juggling.coordinate_transform import canonical_crop_box
from app.services.juggling.frame_extractor import extract_frame_at_ms


def extract_video_frame(storage_path: str, frame_ms: int) -> bytes:
    """Extract one frame at *frame_ms* from *storage_path* and return JPEG bytes."""
    frame_rgb, _w, _h = extract_frame_at_ms(storage_path, frame_ms)
    img = Image.fromarray(frame_rgb)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def apply_crop_to_jpeg(
    jpeg_bytes: bytes,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> bytes:
    """Crop a JPEG image to (left, top, right, bottom) pixel coords and re-encode."""
    img = Image.open(io.BytesIO(jpeg_bytes))
    cropped = img.crop((int(left), int(top), int(right), int(bottom)))
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def serve_assignment_frame(
    db: Session,
    assignment_id: object,
    user_id: int,
) -> tuple[bytes, str]:
    """Validate an assignment and return (jpeg_bytes, display_mode).

    display_mode is either "context_crop" (ball-centred square crop) or
    "full_frame" (entire video frame as-is).

    Error mapping:
      404 — not found or cross-user (identical response; no info-leak)
      410 — assignment expired (consumed_at IS NULL, expires_at ≤ now)
      409 — assignment already submitted (consumed_at IS NOT NULL)
      403 — video deleted or owner consent revoked
    """
    now = datetime.now(timezone.utc)

    # 1. Lock and validate assignment.
    assignment: BallTrainingAssignment | None = db.execute(
        select(BallTrainingAssignment)
        .where(BallTrainingAssignment.id == assignment_id)
        .with_for_update()
    ).scalar_one_or_none()

    if assignment is None or assignment.user_id != user_id:
        raise HTTPException(404, "Assignment not found")
    if assignment.expires_at < now:
        raise HTTPException(410, "Assignment expired")
    if assignment.consumed_at is not None:
        raise HTTPException(409, "Assignment already submitted")

    # 2. Load trajectory (read-only; display-mode determination only).
    trajectory: JugglingBallTrajectory | None = db.execute(
        select(JugglingBallTrajectory).where(
            JugglingBallTrajectory.video_id == assignment.video_id,
            JugglingBallTrajectory.frame_ms == assignment.frame_ms,
        )
    ).scalar_one_or_none()

    # 3. Live consent check.
    video: JugglingVideo | None = db.execute(
        select(JugglingVideo).where(JugglingVideo.id == assignment.video_id)
    ).scalar_one_or_none()
    if video is None or video.status == "gdpr_deleted":
        raise HTTPException(403, "Training content no longer available")

    consent = db.execute(
        select(JugglingConsent).where(
            JugglingConsent.user_id == video.user_id,
            JugglingConsent.training_consent.is_(True),
        )
    ).scalar_one_or_none()
    if consent is None:
        raise HTTPException(403, "Training consent revoked for this content")

    # 4. Determine display mode.
    threshold = settings.BALL_TRAINING_FULL_FRAME_CONFIDENCE_THRESHOLD
    is_full_frame = (
        trajectory is None
        or trajectory.tracking_state == "lost"
        or (trajectory.confidence is not None and trajectory.confidence < threshold)
    )
    display_mode = "full_frame" if is_full_frame else "context_crop"

    # 5. Persist display_mode so the feedback endpoint can back-calculate coords.
    assignment.display_mode = display_mode
    db.flush()

    # 6. Extract raw JPEG frame.
    jpeg_bytes = extract_video_frame(video.storage_path, assignment.frame_ms)

    # 7. Crop to canonical context box when applicable.
    if display_mode == "context_crop" and trajectory is not None:
        img_w = trajectory.image_width_px or 1920
        img_h = trajectory.image_height_px or 1080
        box = canonical_crop_box(
            trajectory.ball_x,
            trajectory.ball_y,
            img_w,
            img_h,
            margin_ratio=settings.BALL_TRAINING_FRAME_MARGIN_RATIO,
        )
        jpeg_bytes = apply_crop_to_jpeg(
            jpeg_bytes, box.left, box.top, box.right, box.bottom
        )

    db.commit()
    return jpeg_bytes, display_mode

"""
Legacy v1 → v2 adapter for PoseKeypointsDTO → Skeleton3DFrame.

Deterministic: same input + same parameters = bitwise-identical output.
UUID fields are injected by the caller (not generated internally).
"""
from __future__ import annotations

import uuid as _uuid
from typing import Any, Dict, Optional

from app.schemas.skeleton_3d import Skeleton3DFrame
from app.services.skeleton.joint_mapping import map_apple_vision_to_canonical


def adapt_v1_to_v2(
    keypoints: Dict[str, Any],
    *,
    session_id: _uuid.UUID,
    capture_id: _uuid.UUID,
    frame_id: _uuid.UUID,
    source_timestamp_ns: int = 0,
    camera_id: str = "iphone_primary",
    source_model: str = "apple_vision_body_pose_v1",
    processing_version: str = "1.0.0",
) -> Skeleton3DFrame:
    schema_version = keypoints.get("schema_version", "1")
    body = keypoints.get("body", [])

    joints = map_apple_vision_to_canonical(body, source_model=source_model)

    return Skeleton3DFrame(
        schema_version="2",
        session_id=session_id,
        capture_id=capture_id,
        camera_id=camera_id,
        calibration_id=None,
        frame_id=frame_id,
        source_timestamp_ns=source_timestamp_ns,
        synchronized_timestamp_ns=None,
        person_id=0,
        joints=joints,
        coordinate_system="camera_a_origin_rh_meters",
        triangulation_method=None,
        processing_version=processing_version,
    )

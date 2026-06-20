"""
Canonical joint mapping — Apple Vision + MediaPipe BlazePose → 19 canonical joints.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.schemas.skeleton_3d import (
    CanonicalJoint,
    PoseModelConfig,
    Skeleton3DJoint,
    TriangulationStatus,
)

APPLE_VISION_MAP: Dict[str, Tuple[CanonicalJoint, str]] = {
    "nose":           (CanonicalJoint.NOSE,            "nose"),
    "left_eye":       (CanonicalJoint.LEFT_EYE,        "leftEye"),
    "right_eye":      (CanonicalJoint.RIGHT_EYE,       "rightEye"),
    "left_ear":       (CanonicalJoint.LEFT_EAR,        "leftEar"),
    "right_ear":      (CanonicalJoint.RIGHT_EAR,       "rightEar"),
    "neck":           (CanonicalJoint.NECK,            "neck1"),
    "left_shoulder":  (CanonicalJoint.LEFT_SHOULDER,   "leftShoulder1"),
    "right_shoulder": (CanonicalJoint.RIGHT_SHOULDER,  "rightShoulder1"),
    "left_elbow":     (CanonicalJoint.LEFT_ELBOW,      "leftElbow1"),
    "right_elbow":    (CanonicalJoint.RIGHT_ELBOW,     "rightElbow1"),
    "left_wrist":     (CanonicalJoint.LEFT_WRIST,      "leftWrist1"),
    "right_wrist":    (CanonicalJoint.RIGHT_WRIST,     "rightWrist1"),
    "root":           (CanonicalJoint.ROOT,            "root"),
    "left_hip":       (CanonicalJoint.LEFT_HIP,        "leftHip1"),
    "right_hip":      (CanonicalJoint.RIGHT_HIP,       "rightHip1"),
    "left_knee":      (CanonicalJoint.LEFT_KNEE,       "leftKnee1"),
    "right_knee":     (CanonicalJoint.RIGHT_KNEE,      "rightKnee1"),
    "left_ankle":     (CanonicalJoint.LEFT_ANKLE,      "leftAnkle1"),
    "right_ankle":    (CanonicalJoint.RIGHT_ANKLE,     "rightAnkle1"),
}

MEDIAPIPE_BLAZEPOSE_MAP: Dict[int, Tuple[CanonicalJoint, str]] = {
    0:  (CanonicalJoint.NOSE,            "NOSE"),
    2:  (CanonicalJoint.LEFT_EYE,        "LEFT_EYE"),
    5:  (CanonicalJoint.RIGHT_EYE,       "RIGHT_EYE"),
    7:  (CanonicalJoint.LEFT_EAR,        "LEFT_EAR"),
    8:  (CanonicalJoint.RIGHT_EAR,       "RIGHT_EAR"),
    11: (CanonicalJoint.LEFT_SHOULDER,    "LEFT_SHOULDER"),
    12: (CanonicalJoint.RIGHT_SHOULDER,   "RIGHT_SHOULDER"),
    13: (CanonicalJoint.LEFT_ELBOW,       "LEFT_ELBOW"),
    14: (CanonicalJoint.RIGHT_ELBOW,      "RIGHT_ELBOW"),
    15: (CanonicalJoint.LEFT_WRIST,       "LEFT_WRIST"),
    16: (CanonicalJoint.RIGHT_WRIST,      "RIGHT_WRIST"),
    23: (CanonicalJoint.LEFT_HIP,         "LEFT_HIP"),
    24: (CanonicalJoint.RIGHT_HIP,        "RIGHT_HIP"),
    25: (CanonicalJoint.LEFT_KNEE,        "LEFT_KNEE"),
    26: (CanonicalJoint.RIGHT_KNEE,       "RIGHT_KNEE"),
    27: (CanonicalJoint.LEFT_ANKLE,       "LEFT_ANKLE"),
    28: (CanonicalJoint.RIGHT_ANKLE,      "RIGHT_ANKLE"),
}

MODEL_CONFIGS: Dict[str, PoseModelConfig] = {
    "apple_vision_body_pose_v1": PoseModelConfig(
        model_id="apple_vision_body_pose_v1",
        confidence_field_name="confidence",
        default_threshold=0.3,
        threshold_calibrated=False,
    ),
    "mediapipe_blazepose_lite_v1": PoseModelConfig(
        model_id="mediapipe_blazepose_lite_v1",
        confidence_field_name="visibility",
        default_threshold=0.5,
        threshold_calibrated=False,
    ),
}

SUPPORTED_MODELS = set(MODEL_CONFIGS.keys())


def synthesize_midpoint(
    joint_a: Optional[Dict],
    joint_b: Optional[Dict],
    canonical_name: str,
    source_model: str,
    synthetic_source_name: str,
) -> Optional[Skeleton3DJoint]:
    if joint_a is None or joint_b is None:
        return None
    conf = min(joint_a["confidence"], joint_b["confidence"]) * 0.9
    return Skeleton3DJoint(
        canonical_joint_name=canonical_name,
        source_joint_name=synthetic_source_name,
        source_model=source_model,
        source_confidence=round(conf, 4),
        image_x=round((joint_a["x"] + joint_b["x"]) / 2, 6),
        image_y=round((joint_a["y"] + joint_b["y"]) / 2, 6),
        image_confidence=round(conf, 4),
        is_synthetic=True,
        triangulation_status=TriangulationStatus.SINGLE_VIEW_ONLY.value,
    )


def map_apple_vision_to_canonical(
    body: List[Dict],
    source_model: str = "apple_vision_body_pose_v1",
) -> List[Skeleton3DJoint]:
    if source_model not in SUPPORTED_MODELS:
        raise ValueError(f"Unknown source model: {source_model}")
    by_name = {j["name"]: j for j in body}
    result: List[Skeleton3DJoint] = []
    for v1_name, (canonical, raw_source_name) in APPLE_VISION_MAP.items():
        j = by_name.get(v1_name)
        if j is None:
            continue
        result.append(Skeleton3DJoint(
            canonical_joint_name=canonical.value,
            source_joint_name=raw_source_name,
            source_model=source_model,
            source_confidence=j["confidence"],
            image_x=j["x"],
            image_y=j["y"],
            image_confidence=j["confidence"],
            is_synthetic=False,
            triangulation_status=TriangulationStatus.SINGLE_VIEW_ONLY.value,
        ))
    return result


def map_mediapipe_to_canonical(
    landmarks: List[Dict],
    source_model: str = "mediapipe_blazepose_lite_v1",
) -> List[Skeleton3DJoint]:
    if source_model not in SUPPORTED_MODELS:
        raise ValueError(f"Unknown source model: {source_model}")
    by_idx = {lm["index"]: lm for lm in landmarks}
    result: List[Skeleton3DJoint] = []
    for idx, (canonical, source_name) in MEDIAPIPE_BLAZEPOSE_MAP.items():
        lm = by_idx.get(idx)
        if lm is None:
            continue
        result.append(Skeleton3DJoint(
            canonical_joint_name=canonical.value,
            source_joint_name=source_name,
            source_model=source_model,
            source_confidence=lm["visibility"],
            image_x=lm["x"],
            image_y=lm["y"],
            image_confidence=lm["visibility"],
            is_synthetic=False,
            triangulation_status=TriangulationStatus.SINGLE_VIEW_ONLY.value,
        ))
    ls = by_idx.get(11)
    rs = by_idx.get(12)
    neck = synthesize_midpoint(
        {"x": ls["x"], "y": ls["y"], "confidence": ls["visibility"]} if ls else None,
        {"x": rs["x"], "y": rs["y"], "confidence": rs["visibility"]} if rs else None,
        CanonicalJoint.NECK.value, source_model, "SYNTHETIC_NECK",
    )
    if neck:
        result.append(neck)
    lh = by_idx.get(23)
    rh = by_idx.get(24)
    root = synthesize_midpoint(
        {"x": lh["x"], "y": lh["y"], "confidence": lh["visibility"]} if lh else None,
        {"x": rh["x"], "y": rh["y"], "confidence": rh["visibility"]} if rh else None,
        CanonicalJoint.ROOT.value, source_model, "SYNTHETIC_ROOT",
    )
    if root:
        result.append(root)
    return result

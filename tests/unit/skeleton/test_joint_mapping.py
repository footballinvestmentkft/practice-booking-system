"""Joint mapping tests — JM-01..JM-12."""
import pytest

from app.schemas.skeleton_3d import CanonicalJoint, TriangulationStatus
from app.services.skeleton.joint_mapping import (
    APPLE_VISION_MAP,
    MEDIAPIPE_BLAZEPOSE_MAP,
    MODEL_CONFIGS,
    map_apple_vision_to_canonical,
    map_mediapipe_to_canonical,
    synthesize_midpoint,
)


def _full_vision_body():
    names = [
        "nose", "left_eye", "right_eye", "left_ear", "right_ear",
        "neck", "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
        "left_wrist", "right_wrist", "root", "left_hip", "right_hip",
        "left_knee", "right_knee", "left_ankle", "right_ankle",
    ]
    return [{"name": n, "x": 0.5, "y": 0.5, "confidence": 0.9} for n in names]


def _full_mediapipe_landmarks():
    indices = [0, 2, 5, 7, 8, 11, 12, 13, 14, 15, 16, 23, 24, 25, 26, 27, 28]
    extras = [1, 3, 4, 6, 9, 10, 17, 18, 19, 20, 21, 22, 29, 30, 31, 32]
    all_idx = indices + extras
    return [{"index": i, "x": 0.5, "y": 0.5, "visibility": 0.85} for i in all_idx]


def test_jm_01_apple_vision_19_to_19_canonical():
    body = _full_vision_body()
    result = map_apple_vision_to_canonical(body)
    assert len(result) == 19
    names = {j.canonical_joint_name for j in result}
    assert names == {c.value for c in CanonicalJoint}


def test_jm_02_mediapipe_33_to_19_canonical():
    lms = _full_mediapipe_landmarks()
    result = map_mediapipe_to_canonical(lms)
    assert len(result) == 19
    names = {j.canonical_joint_name for j in result}
    assert names == {c.value for c in CanonicalJoint}


def test_jm_03_synthetic_neck_shoulder_midpoint():
    lms = _full_mediapipe_landmarks()
    result = map_mediapipe_to_canonical(lms)
    neck = next(j for j in result if j.canonical_joint_name == "neck")
    assert neck.is_synthetic is True
    assert neck.source_joint_name == "SYNTHETIC_NECK"


def test_jm_04_synthetic_root_hip_midpoint():
    lms = _full_mediapipe_landmarks()
    result = map_mediapipe_to_canonical(lms)
    root = next(j for j in result if j.canonical_joint_name == "root")
    assert root.is_synthetic is True
    assert root.source_joint_name == "SYNTHETIC_ROOT"


def test_jm_05_synthetic_confidence():
    lms = [
        {"index": 11, "x": 0.3, "y": 0.4, "visibility": 0.80},
        {"index": 12, "x": 0.7, "y": 0.4, "visibility": 0.60},
    ]
    result = map_mediapipe_to_canonical(lms)
    neck = next((j for j in result if j.canonical_joint_name == "neck"), None)
    assert neck is not None
    assert neck.source_confidence == pytest.approx(min(0.80, 0.60) * 0.9, abs=0.001)


def test_jm_06_missing_source_joint_no_synthetic():
    lms = [{"index": 11, "x": 0.3, "y": 0.4, "visibility": 0.80}]
    result = map_mediapipe_to_canonical(lms)
    neck = next((j for j in result if j.canonical_joint_name == "neck"), None)
    assert neck is None


def test_jm_07_source_model_and_joint_name_preserved():
    body = [{"name": "left_shoulder", "x": 0.4, "y": 0.3, "confidence": 0.95}]
    result = map_apple_vision_to_canonical(body)
    assert result[0].source_model == "apple_vision_body_pose_v1"
    assert result[0].source_joint_name == "leftShoulder1"


def test_jm_08_source_confidence_preserved():
    body = [{"name": "nose", "x": 0.5, "y": 0.2, "confidence": 0.77}]
    result = map_apple_vision_to_canonical(body)
    assert result[0].source_confidence == 0.77
    assert result[0].image_confidence == 0.77


def test_jm_09_model_config_exists_not_applied():
    assert "apple_vision_body_pose_v1" in MODEL_CONFIGS
    assert "mediapipe_blazepose_lite_v1" in MODEL_CONFIGS
    cfg = MODEL_CONFIGS["apple_vision_body_pose_v1"]
    assert cfg.default_threshold == 0.3
    assert cfg.threshold_calibrated is False


def test_jm_10_unknown_model_raises():
    body = [{"name": "nose", "x": 0.5, "y": 0.5, "confidence": 0.9}]
    with pytest.raises(ValueError, match="Unknown source model"):
        map_apple_vision_to_canonical(body, source_model="unknown_model_v99")


def test_jm_11_empty_body_no_crash():
    result = map_apple_vision_to_canonical([])
    assert result == []


def test_jm_12_canonical_joint_enum_19_members():
    assert len(CanonicalJoint) == 19

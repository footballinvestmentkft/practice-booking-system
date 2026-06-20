"""Skeleton 3D contract tests — S3D-01..S3D-08, CS-01..CS-08, SN-01..SN-04, LA-01..LA-08."""
import json
import uuid
from pathlib import Path

import pytest

from app.schemas.skeleton_3d import (
    CanonicalJoint,
    CapturePresetDTO,
    IntrinsicCalibrationDTO,
    Skeleton3DFrame,
    Skeleton3DJoint,
    StereoCalibrationDTO,
    SyncMetadata,
    TriangulationStatus,
    validate_preset_compatibility,
)
from app.services.skeleton.legacy_adapter import adapt_v1_to_v2

FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "skeleton_3d"


# ── S3D: Skeleton3DFrame contract ─────────────────────────────────────────────

def test_s3d_01_full_v2_roundtrip():
    data = json.loads((FIXTURES / "frame_v2_full.json").read_text())
    frame = Skeleton3DFrame(**data)
    rt = json.loads(frame.model_dump_json())
    assert rt["schema_version"] == "2"
    assert len(rt["joints"]) == 5
    assert rt["joints"][0]["triangulation_status"] == "triangulated"


def test_s3d_02_v2_null_world():
    data = json.loads((FIXTURES / "frame_v2_2d_only.json").read_text())
    frame = Skeleton3DFrame(**data)
    j = frame.joints[0]
    assert j.world_x is None
    assert j.world_y is None
    assert j.world_z is None
    assert j.triangulation_status == "single_view_only"


def test_s3d_03_triangulation_status_coverage():
    values = {s.value for s in TriangulationStatus}
    assert values == {"triangulated", "single_view_only", "below_confidence", "joint_missing"}


def test_s3d_04_coordinate_system_validation():
    data = json.loads((FIXTURES / "frame_v2_full.json").read_text())
    data["coordinate_system"] = "invalid_system"
    with pytest.raises(Exception):
        Skeleton3DFrame(**data)


def test_s3d_05_session_id_required():
    data = json.loads((FIXTURES / "frame_v2_full.json").read_text())
    del data["session_id"]
    with pytest.raises(Exception):
        Skeleton3DFrame(**data)


def test_s3d_06_calibration_id_nullable():
    data = json.loads((FIXTURES / "frame_v2_2d_only.json").read_text())
    frame = Skeleton3DFrame(**data)
    assert frame.calibration_id is None


def test_s3d_07_person_id_defaults_to_0():
    data = json.loads((FIXTURES / "frame_v2_full.json").read_text())
    del data["person_id"]
    frame = Skeleton3DFrame(**data)
    assert frame.person_id == 0


def test_s3d_08_source_view_ids_empty_single_view():
    data = json.loads((FIXTURES / "frame_v2_2d_only.json").read_text())
    frame = Skeleton3DFrame(**data)
    assert frame.joints[0].source_view_ids == []


# ── CS: Calibration contract ─────────────────────────────────────────────────

def test_cs_01_valid_intrinsic():
    data = json.loads((FIXTURES / "calibration_intrinsic.json").read_text())
    cal = IntrinsicCalibrationDTO(**data)
    assert cal.image_width_px == 1920


def test_cs_02_invalid_intrinsic_shape():
    data = json.loads((FIXTURES / "calibration_intrinsic.json").read_text())
    data["intrinsic_matrix"] = [[1, 0], [0, 1]]
    with pytest.raises(Exception):
        IntrinsicCalibrationDTO(**data)


def test_cs_03_valid_distortion():
    data = json.loads((FIXTURES / "calibration_intrinsic.json").read_text())
    cal = IntrinsicCalibrationDTO(**data)
    assert len(cal.distortion_coeffs) == 5


def test_cs_04_invalid_distortion_count():
    data = json.loads((FIXTURES / "calibration_intrinsic.json").read_text())
    data["distortion_coeffs"] = [0.1, 0.2, 0.3]
    with pytest.raises(Exception):
        IntrinsicCalibrationDTO(**data)


def test_cs_05_reproj_within_gate():
    data = json.loads((FIXTURES / "calibration_intrinsic.json").read_text())
    cal = IntrinsicCalibrationDTO(**data)
    assert cal.reprojection_error < 0.5


def test_cs_06_reproj_above_gate():
    data = json.loads((FIXTURES / "calibration_intrinsic.json").read_text())
    data["reprojection_error"] = 1.5
    cal = IntrinsicCalibrationDTO(**data)
    assert cal.reprojection_error >= 0.5


def test_cs_07_stereo_shapes():
    data = json.loads((FIXTURES / "calibration_stereo.json").read_text())
    stereo = StereoCalibrationDTO(**data)
    assert len(stereo.rotation_matrix) == 3
    assert len(stereo.translation_vector) == 3
    assert len(stereo.fundamental_matrix) == 3
    assert len(stereo.essential_matrix) == 3


def test_cs_08_preset_compatibility():
    cal_preset = CapturePresetDTO(resolution="1920x1080", fps=30, lens_mode="linear", stabilization="off")
    session_preset = CapturePresetDTO(resolution="1920x1080", fps=30, lens_mode="linear", stabilization="off")
    errors = validate_preset_compatibility(cal_preset, session_preset)
    assert errors == []

    bad_preset = CapturePresetDTO(resolution="3840x2160", fps=60, lens_mode="wide", stabilization="on")
    errors = validate_preset_compatibility(cal_preset, bad_preset)
    assert len(errors) == 4
    assert any("resolution" in e for e in errors)
    assert any("fps" in e for e in errors)
    assert any("lens_mode" in e for e in errors)
    assert any("stabilization" in e for e in errors)


# ── SN: Sync metadata ─────────────────────────────────────────────────────────

def test_sn_01_full_construction():
    data = json.loads((FIXTURES / "sync_metadata.json").read_text())
    meta = SyncMetadata(**data)
    assert meta.sync_quality == "high"
    assert meta.matched_frame_count == 8950


def test_sn_02_sync_quality_values():
    data = json.loads((FIXTURES / "sync_metadata.json").read_text())
    for q in ["high", "acceptable", "degraded", "failed"]:
        data["sync_quality"] = q
        SyncMetadata(**data)
    data["sync_quality"] = "unknown"
    with pytest.raises(Exception):
        SyncMetadata(**data)


def test_sn_03_optional_fields():
    data = json.loads((FIXTURES / "sync_metadata.json").read_text())
    data["sync_reference_end_ns"] = None
    data["p95_alignment_ms"] = None
    meta = SyncMetadata(**data)
    assert meta.sync_reference_end_ns is None


def test_sn_04_fixture_roundtrip():
    data = json.loads((FIXTURES / "sync_metadata.json").read_text())
    meta = SyncMetadata(**data)
    rt = json.loads(meta.model_dump_json())
    assert rt["session_id"] == data["session_id"]
    assert rt["initial_offset_ms"] == data["initial_offset_ms"]


# ── LA: Legacy adapter ────────────────────────────────────────────────────────

_FIX_SESSION = uuid.UUID("00000000-0000-4000-8000-000000000001")
_FIX_CAPTURE = uuid.UUID("00000000-0000-4000-8000-000000000002")
_FIX_FRAME   = uuid.UUID("00000000-0000-4000-8000-000000000003")


def test_la_01_v1_full_body_to_v2():
    v1 = json.loads((FIXTURES / "frame_v1_source.json").read_text())
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    assert len(frame.joints) == 19


def test_la_02_camera_id_default():
    v1 = json.loads((FIXTURES / "frame_v1_source.json").read_text())
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    assert frame.camera_id == "iphone_primary"


def test_la_03_source_model():
    v1 = json.loads((FIXTURES / "frame_v1_source.json").read_text())
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    assert all(j.source_model == "apple_vision_body_pose_v1" for j in frame.joints)


def test_la_04_all_world_null():
    v1 = json.loads((FIXTURES / "frame_v1_source.json").read_text())
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    for j in frame.joints:
        assert j.world_x is None
        assert j.world_y is None
        assert j.world_z is None


def test_la_05_triangulation_status():
    v1 = json.loads((FIXTURES / "frame_v1_source.json").read_text())
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    assert all(j.triangulation_status == "single_view_only" for j in frame.joints)


def test_la_06_missing_schema_version():
    v1 = {"body": [{"name": "nose", "x": 0.5, "y": 0.5, "confidence": 0.9}], "left_hand": [], "right_hand": []}
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    assert frame.schema_version == "2"


def test_la_07_empty_body():
    v1 = {"schema_version": "1", "body": [], "left_hand": [], "right_hand": []}
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    assert frame.joints == []


def test_la_08_deterministic_output_matches_fixture():
    v1 = json.loads((FIXTURES / "frame_v1_source.json").read_text())
    expected = json.loads((FIXTURES / "frame_v1_adapted.json").read_text())
    frame = adapt_v1_to_v2(v1, session_id=_FIX_SESSION, capture_id=_FIX_CAPTURE, frame_id=_FIX_FRAME)
    actual = json.loads(frame.model_dump_json())
    assert actual["session_id"] == expected["session_id"]
    assert actual["camera_id"] == expected["camera_id"]
    assert len(actual["joints"]) == len(expected["joints"])
    for a, e in zip(actual["joints"], expected["joints"]):
        assert a["canonical_joint_name"] == e["canonical_joint_name"]
        assert a["source_joint_name"] == e["source_joint_name"]
        assert a["source_confidence"] == e["source_confidence"]
        assert a["image_x"] == e["image_x"]
        assert a["image_y"] == e["image_y"]
        assert a["is_synthetic"] == e["is_synthetic"]
        assert a["triangulation_status"] == e["triangulation_status"]

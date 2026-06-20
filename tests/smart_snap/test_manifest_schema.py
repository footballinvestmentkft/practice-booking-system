"""SS-MAN: manifest schema v2 validation tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REQUIRED_TOP_KEYS = {
    "generated_at", "poc", "schema_version", "summary", "frames",
}
REQUIRED_FRAME_KEYS = {
    "frame_id", "type", "video_id", "frame_ms",
    "storage_path", "image_width_px", "image_height_px",
    "tracking_state", "model_x", "model_y", "model_confidence",
    "db_corrected_x", "db_corrected_y", "is_no_ball",
    "category", "category_source", "gt_status", "split",
}


def _make_frame(frame_type: str = "A", is_no_ball: bool = False) -> dict:
    return {
        "frame_id": "aaaabbbb_0001000ms",
        "type": frame_type,
        "video_id": "aaaabbbb-0000-0000-0000-000000000001",
        "frame_ms": 1000,
        "storage_path": "app/uploads/juggling/test.mp4",
        "image_width_px": 640,
        "image_height_px": 480,
        "tracking_state": "predicted" if not is_no_ball else "lost",
        "model_x": None if is_no_ball else 0.5,
        "model_y": None if is_no_ball else 0.5,
        "model_confidence": None if is_no_ball else 0.55,
        "db_corrected_x": 0.51 if frame_type == "A" else None,
        "db_corrected_y": 0.49 if frame_type == "A" else None,
        "correction_method": "tap_in_crop" if frame_type == "A" else None,
        "is_no_ball": is_no_ball,
        "category": "no_ball" if is_no_ball else "clear_ball",
        "category_source": "db_lost_state" if is_no_ball else "provisional_auto",
        "gt_status": "VALIDATED_NO_BALL" if is_no_ball else "PENDING_ANNOTATION",
        "split": "tuning",
    }


def _make_minimal_manifest() -> dict:
    return {
        "generated_at": "2026-06-20T00:00:00+00:00",
        "poc": "smart_snap_poc1",
        "schema_version": "2.0",
        "diversity_warnings": [],
        "summary": {
            "type_a_count": 1,
            "type_b_count": 1,
            "type_c_no_ball_count": 1,
            "total_count": 3,
            "positive_count": 2,
            "no_ball_count": 1,
            "videos_positive": 2,
            "videos_no_ball": 1,
            "per_video_positive": {"aaaabbbb": 1, "ccccdddd": 1},
            "max_video_fraction": 0.5,
            "category_distribution": {"clear_ball": 2, "no_ball": 1},
            "categories_missing": ["motion_blur", "partial_occlusion"],
        },
        "frames": [
            _make_frame("A", is_no_ball=False),
            {**_make_frame("B", is_no_ball=False),
             "frame_id": "ccccdddd_0002000ms",
             "video_id": "ccccdddd-0000-0000-0000-000000000002",
             "frame_ms": 2000},
            {**_make_frame("C", is_no_ball=True),
             "frame_id": "aaaabbbb_0003000ms",
             "frame_ms": 3000},
        ],
    }


class TestManifestSchemaV2:
    def test_SS_MAN_01_top_level_keys(self):
        """SS-MAN-01: All required top-level keys present."""
        m = _make_minimal_manifest()
        for key in REQUIRED_TOP_KEYS:
            assert key in m, f"Missing top-level key: {key}"

    def test_SS_MAN_02_frame_keys(self):
        """SS-MAN-02: Each frame has all required v2 keys."""
        m = _make_minimal_manifest()
        for frame in m["frames"]:
            for key in REQUIRED_FRAME_KEYS:
                assert key in frame, f"Frame {frame['frame_id']} missing key: {key}"

    def test_SS_MAN_03_frame_id_format(self):
        """SS-MAN-03: frame_id is non-empty string."""
        m = _make_minimal_manifest()
        for frame in m["frames"]:
            assert isinstance(frame["frame_id"], str) and len(frame["frame_id"]) > 0

    def test_SS_MAN_04_type_values(self):
        """SS-MAN-04: type must be 'A', 'B', or 'C' (v2 adds Type C no-ball)."""
        m = _make_minimal_manifest()
        for frame in m["frames"]:
            assert frame["type"] in ("A", "B", "C"), f"Invalid type: {frame['type']}"

    def test_SS_MAN_05_frame_ms_non_negative(self):
        """SS-MAN-05: frame_ms must be non-negative integer."""
        m = _make_minimal_manifest()
        for frame in m["frames"]:
            assert isinstance(frame["frame_ms"], int) and frame["frame_ms"] >= 0

    def test_SS_MAN_06_coord_range(self):
        """SS-MAN-06: model_x/y and db_corrected_x/y in [0,1] if not None."""
        m = _make_minimal_manifest()
        for frame in m["frames"]:
            for key in ("model_x", "model_y", "db_corrected_x", "db_corrected_y"):
                v = frame.get(key)
                if v is not None:
                    assert 0.0 <= v <= 1.0, f"{key}={v} out of [0,1]"

    def test_SS_MAN_07_type_c_is_no_ball(self):
        """SS-MAN-07: Type C frames must have is_no_ball=True."""
        m = _make_minimal_manifest()
        for frame in m["frames"]:
            if frame["type"] == "C":
                assert frame["is_no_ball"] is True

    def test_SS_MAN_08_type_a_has_db_corrected(self):
        """SS-MAN-08: Type A frames must have db_corrected_x/y (DB reference)."""
        m = _make_minimal_manifest()
        for frame in m["frames"]:
            if frame["type"] == "A":
                assert frame.get("db_corrected_x") is not None
                assert frame.get("db_corrected_y") is not None

    def test_SS_MAN_09_json_serialisable(self):
        """SS-MAN-09: manifest must be JSON-serialisable."""
        m = _make_minimal_manifest()
        serialised = json.dumps(m)
        recovered = json.loads(serialised)
        assert recovered["poc"] == "smart_snap_poc1"
        assert recovered["schema_version"] == "2.0"

    def test_SS_MAN_10_summary_count_matches_frames(self):
        """SS-MAN-10: summary.total_count == len(frames)."""
        m = _make_minimal_manifest()
        assert m["summary"]["total_count"] == len(m["frames"])

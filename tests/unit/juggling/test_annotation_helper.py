"""
HELP-01 through HELP-18 — Local Annotation Helper tests.

Tests for scripts/juggling_annotation_helper.py.
No DB, no production app, no external services. Fast and isolated.
All filesystem operations use tmp_path; module-level paths are monkeypatched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# Import the standalone helper script (not part of the production app package).
import sys as _sys
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))

import juggling_annotation_helper as helper  # noqa: E402


# ── Shared test data ──────────────────────────────────────────────────────────

_VIDEO_ID = "jug_test_001"
_FILENAME = "test_clip_001.mp4"

_SAMPLE_MANIFEST = {
    "manifest_version": "1.0",
    "schema_version": "annotation_schema_v1.json",
    "summary": {
        "metadata_ready": 1,
        "annotated_videos": 0,
        "reviewed_videos": 0,
        "privacy_approved_videos": 0,
    },
    "videos": [
        {
            "video_id": _VIDEO_ID,
            "filename": _FILENAME,
            "annotation_file": f"annotations/{_VIDEO_ID}.json",
            "annotation_status": "metadata_ready",
            "privacy_review_status": "pending",
            "dataset_eligibility": "pending_human_review",
            "duration_seconds": 10.0,
            "original_resolution": "1080x1920",
            "processed_resolution": "406x720",
            "display_orientation": "portrait",
            "fps": 30,
        }
    ],
    "storage_policy": {},
}

_SAMPLE_ANNOTATION = {
    "annotation_schema_version": "1.0",
    "video_id": _VIDEO_ID,
    "filename": _FILENAME,
    "upload_source": "app_gallery_upload",
    "capture_device": "unknown",
    "duration_seconds": 10.0,
    "original_resolution": "1080x1920",
    "processed_resolution": "406x720",
    "fps": 30.0,
    "original_rotation_metadata": 90,
    "display_orientation": "portrait",
    "checksum_sha256": "abc123test",
    "annotation_status": "metadata_ready",
    "annotation_version": "v1.0",
    "privacy_review_status": "pending",
    "ownership_confirmed": None,
    "consent_confirmed": None,
    "recognizable_third_party_present": None,
    "minor_present": None,
    "external_source": None,
    "difficulty": None,
    "total_juggling_count": None,
    "count_confidence": None,
    "dominant_body_part": None,
    "body_parts_used": None,
    "ball_visible_quality": None,
    "lighting_quality": None,
    "camera_stability": None,
    "multi_person_present": None,
    "multiple_balls_present": None,
    "expected_validity": None,
    "invalidity_reason": None,
    "notes": None,
    "annotator": None,
    "second_annotator": None,
    "second_annotator_count": None,
    "inter_annotator_agreement": None,
    "annotation_date": None,
    "contact_events": None,
}

# All required human fields filled + privacy conditions met → eligible
_FULL_HUMAN_PAYLOAD: dict = {
    "ownership_confirmed": True,
    "consent_confirmed": True,
    "recognizable_third_party_present": False,
    "minor_present": False,
    "external_source": False,
    "privacy_review_status": "approved",
    "total_juggling_count": 15,
    "count_confidence": "high",
    "dominant_body_part": "foot",
    "body_parts_used": {
        "foot": True, "knee": False, "thigh": False,
        "shoulder": False, "head": False, "chest": False,
    },
    "difficulty": "medium",
    "ball_visible_quality": "good",
    "lighting_quality": "good",
    "camera_stability": "handheld_stable",
    "multi_person_present": False,
    "multiple_balls_present": False,
    "expected_validity": "valid",
    "invalidity_reason": None,
    "notes": "Test annotation — HELP suite",
    "annotator": "ZL",
    "annotation_date": "2026-06-13",
    "contact_events": None,
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_fs(tmp_path, monkeypatch):
    """
    Create isolated tmp directories for uploads, annotations, and manifest.
    Monkeypatches the module-level path constants so every helper function
    reads/writes to tmp_path instead of the real datasets directory.
    """
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    annotations_dir = tmp_path / "annotations"
    annotations_dir.mkdir()
    manifest_path = tmp_path / "dataset_manifest.json"

    manifest_path.write_text(json.dumps(_SAMPLE_MANIFEST), encoding="utf-8")
    (annotations_dir / f"{_VIDEO_ID}.json").write_text(
        json.dumps(_SAMPLE_ANNOTATION), encoding="utf-8"
    )
    # Minimal valid MP4 header bytes (sufficient for FileResponse serving)
    video_file = uploads_dir / _FILENAME
    video_file.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isom" + b"\x00" * 200
    )

    monkeypatch.setattr(helper, "UPLOADS_DIR", uploads_dir.resolve())
    monkeypatch.setattr(helper, "ANNOTATIONS_DIR", annotations_dir.resolve())
    monkeypatch.setattr(helper, "MANIFEST_PATH", manifest_path.resolve())

    return {
        "uploads_dir": uploads_dir,
        "annotations_dir": annotations_dir,
        "manifest_path": manifest_path,
        "video_file": video_file,
    }


@pytest.fixture()
def client(tmp_fs):
    return TestClient(helper.app, raise_server_exceptions=True)


# ── HELP-01: localhost bind config ────────────────────────────────────────────

class TestHELP01_LocalhostBind:
    def test_host_is_127_0_0_1(self):
        """HELP-01: HOST constant must be 127.0.0.1 — never 0.0.0.0."""
        assert helper.HOST == "127.0.0.1"

    def test_host_is_not_wildcard(self):
        """HELP-01b: HOST must not be the wildcard 0.0.0.0."""
        assert helper.HOST != "0.0.0.0"

    def test_startup_assertion_passes(self):
        """HELP-01c: The __main__ guard assertion passes for the current HOST value."""
        # Reproduces the check from the if __name__ == '__main__' block.
        assert helper.HOST == "127.0.0.1", "SECURITY: annotation helper must only bind to localhost"


# ── HELP-02: manifest video accessible ───────────────────────────────────────

class TestHELP02_ManifestVideoAccessible:
    def test_manifest_video_returns_200(self, client):
        """HELP-02: A video file listed in the manifest is served via /media/."""
        resp = client.get(f"/media/{_FILENAME}")
        assert resp.status_code == 200

    def test_manifest_endpoint_lists_video(self, client):
        """HELP-02b: GET /api/manifest includes the test video_id."""
        resp = client.get("/api/manifest")
        assert resp.status_code == 200
        ids = [v["video_id"] for v in resp.json()["videos"]]
        assert _VIDEO_ID in ids


# ── HELP-03: non-manifest video → 404 ────────────────────────────────────────

class TestHELP03_UnlistedVideo404:
    def test_unlisted_file_on_disk_returns_404(self, client, tmp_fs):
        """HELP-03: A file that exists on disk but is NOT in the manifest → 404."""
        extra = tmp_fs["uploads_dir"] / "unlisted.mp4"
        extra.write_bytes(b"\x00" * 10)
        resp = client.get("/media/unlisted.mp4")
        assert resp.status_code == 404

    def test_unknown_filename_returns_404(self, client):
        """HELP-03b: Completely unknown filename → 404."""
        resp = client.get("/media/totally_unknown_file.mp4")
        assert resp.status_code == 404


# ── HELP-04: path traversal blocked ──────────────────────────────────────────

class TestHELP04_PathTraversalBlocked:
    def test_dotdot_traversal_rejected_by_name_guard(self, tmp_fs):
        """HELP-04: _safe_video_path rejects '../secret.mp4' via name-component check."""
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("../secret.mp4")
        assert exc_info.value.status_code in (400, 404)

    def test_absolute_path_rejected(self, tmp_fs):
        """HELP-04b: _safe_video_path rejects absolute paths."""
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("/etc/passwd")
        assert exc_info.value.status_code in (400, 404)

    def test_dotdot_with_subdir_rejected(self, tmp_fs):
        """HELP-04c: 'subdir/../secret.mp4' is rejected by name guard."""
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("subdir/../secret.mp4")
        assert exc_info.value.status_code in (400, 404)

    def test_empty_filename_rejected(self, tmp_fs):
        """HELP-04d: Empty filename is rejected with 400."""
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("")
        assert exc_info.value.status_code == 400


# ── HELP-05: annotation GET ───────────────────────────────────────────────────

class TestHELP05_AnnotationGET:
    def test_get_annotation_returns_data(self, client):
        """HELP-05: GET /api/annotation/{video_id} returns the annotation JSON."""
        resp = client.get(f"/api/annotation/{_VIDEO_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["video_id"] == _VIDEO_ID

    def test_get_annotation_contains_required_fields(self, client):
        """HELP-05b: Returned annotation contains expected schema fields."""
        resp = client.get(f"/api/annotation/{_VIDEO_ID}")
        data = resp.json()
        for field in ("annotation_status", "privacy_review_status", "total_juggling_count"):
            assert field in data

    def test_unknown_video_id_returns_404(self, client):
        """HELP-05c: Unknown video_id → 404."""
        resp = client.get("/api/annotation/jug_nonexistent_999")
        assert resp.status_code == 404


# ── HELP-06: only human fields writable ──────────────────────────────────────

class TestHELP06_OnlyHumanFieldsWritable:
    def test_human_fields_accepted_and_saved(self, client, tmp_fs):
        """HELP-06: A payload containing only human fields is accepted (200)."""
        payload = {
            "annotator": "ZL",
            "annotation_date": "2026-06-13",
            "notes": "unit test",
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_human_fields_persisted_to_disk(self, client, tmp_fs):
        """HELP-06b: Human-field values are actually written to the JSON file."""
        client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"annotator": "ZL", "annotation_date": "2026-06-13"},
        )
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        assert saved["annotator"] == "ZL"
        assert saved["annotation_date"] == "2026-06-13"


# ── HELP-07: objective metadata write attempt → 422 ──────────────────────────

class TestHELP07_ObjectiveFieldsRejected:
    def test_video_id_write_rejected(self, client):
        """HELP-07: Attempting to overwrite 'video_id' (objective) → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"video_id": "tampered", "annotator": "ZL"},
        )
        assert resp.status_code == 422

    def test_checksum_write_rejected(self, client):
        """HELP-07b: Attempting to overwrite 'checksum_sha256' → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"checksum_sha256": "aaaaaaa", "annotator": "ZL"},
        )
        assert resp.status_code == 422

    def test_duration_write_rejected(self, client):
        """HELP-07c: Attempting to overwrite 'duration_seconds' → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"duration_seconds": 999.0},
        )
        assert resp.status_code == 422

    def test_original_resolution_write_rejected(self, client):
        """HELP-07d: Attempting to overwrite 'original_resolution' → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"original_resolution": "9999x9999"},
        )
        assert resp.status_code == 422

    def test_objective_field_not_modified_in_saved_file(self, client, tmp_fs):
        """HELP-07e: Even if somehow merged, objective fields are not overwritten."""
        # Post a valid human-only payload, then verify original checksum unchanged.
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        assert saved["checksum_sha256"] == "abc123test"


# ── HELP-08: invalid enum → 422 ──────────────────────────────────────────────

class TestHELP08_InvalidEnum422:
    def test_invalid_difficulty_enum(self, client):
        """HELP-08: Invalid 'difficulty' enum value → 422."""
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"difficulty": "extreme"})
        assert resp.status_code == 422

    def test_invalid_camera_stability_enum(self, client):
        """HELP-08b: Invalid 'camera_stability' value → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"camera_stability": "drone"}
        )
        assert resp.status_code == 422

    def test_invalid_ball_visible_quality_enum(self, client):
        """HELP-08c: Invalid 'ball_visible_quality' value → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"ball_visible_quality": "blurry"}
        )
        assert resp.status_code == 422

    def test_invalid_privacy_review_status_enum(self, client):
        """HELP-08d: Invalid 'privacy_review_status' value → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"privacy_review_status": "maybe"}
        )
        assert resp.status_code == 422

    def test_invalid_expected_validity_enum(self, client):
        """HELP-08e: Invalid 'expected_validity' value → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"expected_validity": "unknown"}
        )
        assert resp.status_code == 422


# ── HELP-09: negative count → 422 ────────────────────────────────────────────

class TestHELP09_NegativeCount422:
    def test_negative_count_rejected(self, client):
        """HELP-09: Negative total_juggling_count → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": -1}
        )
        assert resp.status_code == 422

    def test_string_count_rejected(self, client):
        """HELP-09b: String value for total_juggling_count → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": "many"}
        )
        assert resp.status_code == 422

    def test_float_count_rejected(self, client):
        """HELP-09c: Float value for total_juggling_count → 422."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": 3.7}
        )
        assert resp.status_code == 422

    def test_zero_count_accepted(self, client):
        """HELP-09d: Zero total_juggling_count (edge case) is valid."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": 0}
        )
        assert resp.status_code == 200


# ── HELP-10: atomic save successful ──────────────────────────────────────────

class TestHELP10_AtomicSave:
    def test_annotation_file_written_after_save(self, client, tmp_fs):
        """HELP-10: After successful POST, annotation JSON on disk reflects new values."""
        client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"annotator": "ZL", "notes": "atomic test"},
        )
        ann_path = tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json"
        saved = json.loads(ann_path.read_text(encoding="utf-8"))
        assert saved["annotator"] == "ZL"
        assert saved["notes"] == "atomic test"

    def test_tmp_file_not_left_behind(self, client, tmp_fs):
        """HELP-10b: No stray .tmp file remains after a successful save."""
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        tmp_file = tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json.tmp"
        assert not tmp_file.exists()

    def test_atomic_write_json_direct(self, tmp_path):
        """HELP-10c: _atomic_write_json writes correct content to target path."""
        target = tmp_path / "test.json"
        target.write_text('{"original": true}', encoding="utf-8")
        helper._atomic_write_json(target, {"updated": True})
        result = json.loads(target.read_text(encoding="utf-8"))
        assert result == {"updated": True}


# ── HELP-11: backup file created ─────────────────────────────────────────────

class TestHELP11_BackupCreated:
    def test_annotation_bak_file_created(self, client, tmp_fs):
        """HELP-11: A .bak backup is created next to the annotation file after save."""
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        bak = tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json.bak"
        assert bak.exists()

    def test_bak_contains_pre_save_content(self, client, tmp_fs):
        """HELP-11b: The .bak file contains the original annotation (before overwrite)."""
        ann_path = tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json"
        original = json.loads(ann_path.read_text(encoding="utf-8"))
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        bak = tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json.bak"
        backed_up = json.loads(bak.read_text(encoding="utf-8"))
        assert backed_up["annotator"] == original["annotator"]  # null in original


# ── HELP-12: save failure preserves original ──────────────────────────────────

class TestHELP12_SaveFailurePreservesOriginal:
    def test_original_file_intact_when_replace_fails(self, tmp_path, monkeypatch):
        """HELP-12: If os.replace fails, the original JSON file is left intact."""
        target = tmp_path / "annotation.json"
        original_content = '{"video_id": "safe", "annotator": null}'
        target.write_text(original_content, encoding="utf-8")

        def _boom(*args, **kwargs):
            raise OSError("simulated disk full")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(OSError):
            helper._atomic_write_json(target, {"video_id": "safe", "annotator": "injected"})

        assert target.read_text(encoding="utf-8") == original_content

    def test_tmp_file_cleaned_up_on_failure(self, tmp_path, monkeypatch):
        """HELP-12b: The .tmp file is removed even when os.replace fails."""
        target = tmp_path / "annotation.json"
        target.write_text('{"x": 1}', encoding="utf-8")

        def _boom(*args, **kwargs):
            raise OSError("simulated error")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(OSError):
            helper._atomic_write_json(target, {"x": 2})

        tmp_file = target.with_suffix(".json.tmp")
        assert not tmp_file.exists()


# ── HELP-13: privacy pending → not eligible ───────────────────────────────────

class TestHELP13_PrivacyPendingNotEligible:
    def test_privacy_pending_blocks_eligible(self, client):
        """HELP-13: When privacy conditions not met, dataset_eligibility != 'eligible'."""
        payload = {
            **_FULL_HUMAN_PAYLOAD,
            "ownership_confirmed": None,  # not confirmed → privacy stays pending
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 200
        assert resp.json()["dataset_eligibility"] != "eligible"

    def test_compute_eligibility_privacy_pending(self):
        """HELP-13b: Logic: annotated + pending privacy → privacy_pending."""
        result = helper._compute_dataset_eligibility("annotated", "pending")
        assert result == "privacy_pending"

    def test_compute_eligibility_rejected_privacy(self):
        """HELP-13c: Logic: annotated + rejected privacy → privacy_pending."""
        result = helper._compute_dataset_eligibility("annotated", "rejected")
        assert result == "privacy_pending"

    def test_compute_privacy_status_pending_when_field_missing(self):
        """HELP-13d: _compute_privacy_status returns 'pending' when a field is None."""
        data = {k: v for k, v in _FULL_HUMAN_PAYLOAD.items()}
        data["ownership_confirmed"] = None
        assert helper._compute_privacy_status(data) == "pending"


# ── HELP-14: privacy approved + full annotation → eligible ───────────────────

class TestHELP14_FullyAnnotatedEligible:
    def test_full_annotation_and_privacy_approved_is_eligible(self, client):
        """HELP-14: All required fields + privacy approved → dataset_eligibility = eligible."""
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        assert resp.status_code == 200
        result = resp.json()
        assert result["annotation_status"] == "annotated"
        assert result["privacy_review_status"] == "approved"
        assert result["dataset_eligibility"] == "eligible"

    def test_compute_annotation_status_all_filled(self):
        """HELP-14b: _compute_annotation_status returns 'annotated' when all required fields present."""
        data = {**_SAMPLE_ANNOTATION, **_FULL_HUMAN_PAYLOAD}
        assert helper._compute_annotation_status(data) == "annotated"

    def test_compute_annotation_status_missing_field(self):
        """HELP-14c: _compute_annotation_status returns 'human_review_pending' when a field is missing."""
        data = {**_SAMPLE_ANNOTATION}  # all null
        assert helper._compute_annotation_status(data) == "human_review_pending"


# ── HELP-15: reviewed status never auto-set ──────────────────────────────────

class TestHELP15_ReviewedNotAutoSet:
    def test_annotation_status_never_reviewed_after_full_save(self, client):
        """HELP-15: annotation_status is never set to 'reviewed' by the helper."""
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        assert resp.status_code == 200
        assert resp.json()["annotation_status"] != "reviewed"

    def test_saved_file_never_reviewed(self, client, tmp_fs):
        """HELP-15b: The saved JSON file on disk never has annotation_status = 'reviewed'."""
        client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        assert saved.get("annotation_status") != "reviewed"

    def test_compute_annotation_status_cannot_return_reviewed(self):
        """HELP-15c: _compute_annotation_status never returns 'reviewed'."""
        data = {**_SAMPLE_ANNOTATION, **_FULL_HUMAN_PAYLOAD, "annotation_status": "reviewed"}
        result = helper._compute_annotation_status(data)
        assert result != "reviewed"


# ── HELP-16: manifest atomic update ──────────────────────────────────────────

class TestHELP16_ManifestAtomicUpdate:
    def test_manifest_updated_after_full_save(self, client, tmp_fs):
        """HELP-16: Manifest entry reflects annotation_status + dataset_eligibility after save."""
        client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        manifest = json.loads(tmp_fs["manifest_path"].read_text(encoding="utf-8"))
        entry = next(v for v in manifest["videos"] if v["video_id"] == _VIDEO_ID)
        assert entry["annotation_status"] == "annotated"
        assert entry["dataset_eligibility"] == "eligible"

    def test_manifest_privacy_status_updated(self, client, tmp_fs):
        """HELP-16b: Manifest privacy_review_status is updated to 'approved' after full save."""
        client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        manifest = json.loads(tmp_fs["manifest_path"].read_text(encoding="utf-8"))
        entry = next(v for v in manifest["videos"] if v["video_id"] == _VIDEO_ID)
        assert entry["privacy_review_status"] == "approved"

    def test_manifest_bak_created(self, client, tmp_fs):
        """HELP-16c: Manifest .bak file is created atomically alongside the manifest update."""
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        bak = tmp_fs["manifest_path"].with_suffix(".json.bak")
        assert bak.exists()

    def test_manifest_tmp_not_left_behind(self, client, tmp_fs):
        """HELP-16d: No stray .tmp file remains in the manifest directory after save."""
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        tmp_file = tmp_fs["manifest_path"].with_suffix(".json.tmp")
        assert not tmp_file.exists()


# ── HELP-17: contact event timestamp saved ────────────────────────────────────

class TestHELP17_ContactEventSaved:
    def test_contact_event_timestamp_persisted(self, client, tmp_fs):
        """HELP-17: A contact event with timestamp_ms is saved to the annotation file."""
        payload = {
            "annotator": "ZL",
            "annotation_date": "2026-06-13",
            "contact_events": [
                {
                    "event_id": 1,
                    "timestamp_ms": 2500,
                    "body_part": "foot",
                    "annotation_confidence": "certain",
                    "side": "right",
                    "note": None,
                }
            ],
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 200
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        assert saved["contact_events"] is not None
        assert saved["contact_events"][0]["timestamp_ms"] == 2500

    def test_contact_event_body_part_saved(self, client, tmp_fs):
        """HELP-17b: Contact event body_part and side are persisted correctly."""
        payload = {
            "annotator": "ZL",
            "annotation_date": "2026-06-13",
            "contact_events": [
                {
                    "event_id": 1,
                    "timestamp_ms": 5000,
                    "body_part": "knee",
                    "annotation_confidence": "probable",
                    "side": "left",
                    "note": None,
                }
            ],
        }
        client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        evt = saved["contact_events"][0]
        assert evt["body_part"] == "knee"
        assert evt["side"] == "left"

    def test_invalid_contact_event_timestamp_rejected(self, client):
        """HELP-17c: Negative timestamp_ms in contact event → 422."""
        payload = {
            "contact_events": [
                {
                    "event_id": 1,
                    "timestamp_ms": -100,
                    "body_part": "foot",
                    "annotation_confidence": "certain",
                }
            ]
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 422

    def test_invalid_contact_body_part_rejected(self, client):
        """HELP-17d: Unknown body_part in contact event → 422."""
        payload = {
            "contact_events": [
                {
                    "event_id": 1,
                    "timestamp_ms": 1000,
                    "body_part": "elbow",
                    "annotation_confidence": "certain",
                }
            ]
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 422


# ── HELP-18: Range request ────────────────────────────────────────────────────

class TestHELP18_RangeRequest:
    def test_range_request_accepted(self, client):
        """HELP-18: GET /media/{filename} with Range header returns 200 or 206."""
        resp = client.get(f"/media/{_FILENAME}", headers={"Range": "bytes=0-9"})
        assert resp.status_code in (200, 206)

    def test_range_response_has_content_range_header_on_206(self, client):
        """HELP-18b: If server returns 206, the Content-Range header must be present."""
        resp = client.get(f"/media/{_FILENAME}", headers={"Range": "bytes=0-9"})
        if resp.status_code == 206:
            assert "content-range" in resp.headers or "Content-Range" in resp.headers

    def test_video_served_without_range(self, client):
        """HELP-18c: Without Range header, the full file is served (200)."""
        resp = client.get(f"/media/{_FILENAME}")
        assert resp.status_code == 200
        assert len(resp.content) > 0

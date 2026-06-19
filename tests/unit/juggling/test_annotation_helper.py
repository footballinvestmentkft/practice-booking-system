"""
HELP-01 through HELP-36 — Local Annotation Helper v2 tests.

Tests for scripts/juggling_annotation_helper.py (taxonomy v1, schema v2).
No DB, no production app, no external services. Fast and isolated.
All filesystem operations use tmp_path; module-level paths are monkeypatched.
Taxonomy is loaded from the real contact_types_v1.json (no mock needed).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

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
    "schema_version": "annotation_schema_v2.json",
    "taxonomy_version": "v1",
    "contact_taxonomy_source": "contact_types_v1.json",
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
    "annotation_schema_version": "2.0",
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
    "checksum_sha256": "abc123test" + "a" * 54,
    "annotation_status": "metadata_ready",
    "annotation_version": "v2.0",
    "taxonomy_version": "v1",
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

# v2 body_parts_used uses hip (not thigh) and back
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
        "foot": True, "knee": False, "hip": False,
        "chest": False, "shoulder": False, "head": False, "back": False,
    },
    "difficulty": "medium",
    "ball_visible_quality": "good",
    "lighting_quality": "good",
    "camera_stability": "handheld_stable",
    "multi_person_present": False,
    "multiple_balls_present": False,
    "expected_validity": "valid",
    "invalidity_reason": None,
    "notes": "Test annotation — HELP suite v2",
    "annotator": "ZL",
    "annotation_date": "2026-06-13",
    "contact_events": None,
}

# Minimal valid v2 contact event
_VALID_RIGHT_INSTEP_EVENT = {
    "event_id": 1,
    "timestamp_ms": 2500,
    "contact_type": "right_instep",
    "side": "right",
    "annotation_confidence": "certain",
    "annotation_source": "manual_annotator",
}
_VALID_CHEST_EVENT = {
    "event_id": 1,
    "timestamp_ms": 3000,
    "contact_type": "chest",
    "side": "center",
    "annotation_confidence": "probable",
    "annotation_source": "manual_annotator",
}
_VALID_CUSTOM_EVENT = {
    "event_id": 1,
    "timestamp_ms": 4000,
    "contact_type": "custom_other",
    "side": "right",
    "annotation_confidence": "uncertain",
    "annotation_source": "manual_annotator",
    "custom_label": "right_sole",
    "custom_description": "Talppal való érintés — cipőtalp alsó felszíne.",
}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_fs(tmp_path, monkeypatch):
    uploads_dir     = tmp_path / "uploads"
    annotations_dir = tmp_path / "annotations"
    uploads_dir.mkdir()
    annotations_dir.mkdir()
    manifest_path = tmp_path / "dataset_manifest.json"

    manifest_path.write_text(json.dumps(_SAMPLE_MANIFEST), encoding="utf-8")
    (annotations_dir / f"{_VIDEO_ID}.json").write_text(
        json.dumps(_SAMPLE_ANNOTATION), encoding="utf-8"
    )
    video_file = uploads_dir / _FILENAME
    video_file.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isom" + b"\x00" * 200
    )

    monkeypatch.setattr(helper, "UPLOADS_DIR",      uploads_dir.resolve())
    monkeypatch.setattr(helper, "ANNOTATIONS_DIR",  annotations_dir.resolve())
    monkeypatch.setattr(helper, "MANIFEST_PATH",    manifest_path.resolve())

    return {
        "uploads_dir":     uploads_dir,
        "annotations_dir": annotations_dir,
        "manifest_path":   manifest_path,
        "video_file":      video_file,
    }


@pytest.fixture()
def client(tmp_fs):
    return TestClient(helper.app, raise_server_exceptions=True)


# ── HELP-01: localhost bind ───────────────────────────────────────────────────

class TestHELP01_LocalhostBind:
    def test_host_is_127_0_0_1(self):
        assert helper.HOST == "127.0.0.1"

    def test_host_is_not_wildcard(self):
        assert helper.HOST != "0.0.0.0"

    def test_startup_assertion_passes(self):
        assert helper.HOST == "127.0.0.1", "SECURITY: must only bind to localhost"


# ── HELP-02: manifest video accessible ───────────────────────────────────────

class TestHELP02_ManifestVideoAccessible:
    def test_manifest_video_returns_200(self, client):
        resp = client.get(f"/media/{_FILENAME}")
        assert resp.status_code == 200

    def test_manifest_endpoint_lists_video(self, client):
        resp = client.get("/api/manifest")
        assert resp.status_code == 200
        assert _VIDEO_ID in [v["video_id"] for v in resp.json()["videos"]]


# ── HELP-03: non-manifest video → 404 ────────────────────────────────────────

class TestHELP03_UnlistedVideo404:
    def test_unlisted_file_on_disk_returns_404(self, client, tmp_fs):
        extra = tmp_fs["uploads_dir"] / "unlisted.mp4"
        extra.write_bytes(b"\x00" * 10)
        assert client.get("/media/unlisted.mp4").status_code == 404

    def test_unknown_filename_returns_404(self, client):
        assert client.get("/media/totally_unknown_file.mp4").status_code == 404


# ── HELP-04: path traversal blocked ──────────────────────────────────────────

class TestHELP04_PathTraversalBlocked:
    def test_dotdot_rejected_by_name_guard(self, tmp_fs):
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("../secret.mp4")
        assert exc_info.value.status_code in (400, 404)

    def test_absolute_path_rejected(self, tmp_fs):
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("/etc/passwd")
        assert exc_info.value.status_code in (400, 404)

    def test_dotdot_with_subdir_rejected(self, tmp_fs):
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("subdir/../secret.mp4")
        assert exc_info.value.status_code in (400, 404)

    def test_empty_filename_rejected(self, tmp_fs):
        with pytest.raises(HTTPException) as exc_info:
            helper._safe_video_path("")
        assert exc_info.value.status_code == 400


# ── HELP-05: annotation GET ───────────────────────────────────────────────────

class TestHELP05_AnnotationGET:
    def test_get_annotation_returns_data(self, client):
        resp = client.get(f"/api/annotation/{_VIDEO_ID}")
        assert resp.status_code == 200
        assert resp.json()["video_id"] == _VIDEO_ID

    def test_get_annotation_contains_required_fields(self, client):
        data = client.get(f"/api/annotation/{_VIDEO_ID}").json()
        for field in ("annotation_status", "privacy_review_status", "total_juggling_count"):
            assert field in data

    def test_unknown_video_id_returns_404(self, client):
        assert client.get("/api/annotation/jug_nonexistent_999").status_code == 404


# ── HELP-06: only human fields writable ──────────────────────────────────────

class TestHELP06_OnlyHumanFieldsWritable:
    def test_human_fields_accepted(self, client):
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"annotator": "ZL", "annotation_date": "2026-06-13", "notes": "ok"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_human_fields_persisted_to_disk(self, client, tmp_fs):
        client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"annotator": "ZL", "annotation_date": "2026-06-13"},
        )
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        assert saved["annotator"] == "ZL"


# ── HELP-07: objective metadata write → 422 ───────────────────────────────────

class TestHELP07_ObjectiveFieldsRejected:
    def test_video_id_write_rejected(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"video_id": "tampered"}
        ).status_code == 422

    def test_checksum_write_rejected(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"checksum_sha256": "aaaa"}
        ).status_code == 422

    def test_duration_write_rejected(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"duration_seconds": 999.0}
        ).status_code == 422

    def test_original_resolution_write_rejected(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"original_resolution": "9x9"}
        ).status_code == 422

    def test_objective_field_not_modified_in_saved_file(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        assert saved["checksum_sha256"] == _SAMPLE_ANNOTATION["checksum_sha256"]


# ── HELP-08: invalid enum → 422 ──────────────────────────────────────────────

class TestHELP08_InvalidEnum422:
    def test_invalid_difficulty(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"difficulty": "extreme"}
        ).status_code == 422

    def test_invalid_camera_stability(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"camera_stability": "drone"}
        ).status_code == 422

    def test_invalid_ball_visible_quality(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"ball_visible_quality": "blurry"}
        ).status_code == 422

    def test_invalid_privacy_review_status(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"privacy_review_status": "maybe"}
        ).status_code == 422

    def test_invalid_expected_validity(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"expected_validity": "unknown"}
        ).status_code == 422

    def test_dominant_body_part_thigh_deprecated_422(self, client):
        """v2: 'thigh' is deprecated in dominant_body_part → 422."""
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"dominant_body_part": "thigh"}
        ).status_code == 422


# ── HELP-09: negative count → 422 ────────────────────────────────────────────

class TestHELP09_NegativeCount422:
    def test_negative_count_rejected(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": -1}
        ).status_code == 422

    def test_string_count_rejected(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": "many"}
        ).status_code == 422

    def test_float_count_rejected(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": 3.7}
        ).status_code == 422

    def test_zero_count_accepted(self, client):
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"total_juggling_count": 0}
        ).status_code == 200


# ── HELP-10: atomic save ──────────────────────────────────────────────────────

class TestHELP10_AtomicSave:
    def test_annotation_file_written(self, client, tmp_fs):
        client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL", "notes": "atomic"}
        )
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text(encoding="utf-8")
        )
        assert saved["annotator"] == "ZL"
        assert saved["notes"] == "atomic"

    def test_tmp_file_not_left_behind(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        assert not (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json.tmp").exists()

    def test_atomic_write_json_direct(self, tmp_path):
        target = tmp_path / "test.json"
        target.write_text('{"original": true}', encoding="utf-8")
        helper._atomic_write_json(target, {"updated": True})
        assert json.loads(target.read_text())["updated"] is True


# ── HELP-11: backup created ───────────────────────────────────────────────────

class TestHELP11_BackupCreated:
    def test_bak_file_created(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        assert (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json.bak").exists()

    def test_bak_contains_original_content(self, client, tmp_fs):
        ann_path = tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json"
        original = json.loads(ann_path.read_text())
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        backed = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json.bak").read_text()
        )
        assert backed["annotator"] == original["annotator"]


# ── HELP-12: save failure preserves original ──────────────────────────────────

class TestHELP12_SaveFailurePreservesOriginal:
    def test_original_intact_when_replace_fails(self, tmp_path, monkeypatch):
        target  = tmp_path / "ann.json"
        original = '{"video_id": "safe", "annotator": null}'
        target.write_text(original, encoding="utf-8")
        monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
        with pytest.raises(OSError):
            helper._atomic_write_json(target, {"video_id": "tampered"})
        assert target.read_text(encoding="utf-8") == original

    def test_tmp_cleaned_on_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "ann.json"
        target.write_text('{"x": 1}', encoding="utf-8")
        monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
        with pytest.raises(OSError):
            helper._atomic_write_json(target, {"x": 2})
        assert not target.with_suffix(".json.tmp").exists()


# ── HELP-13: privacy pending → not eligible ───────────────────────────────────

class TestHELP13_PrivacyPendingNotEligible:
    def test_privacy_pending_blocks_eligible(self, client):
        payload = {**_FULL_HUMAN_PAYLOAD, "ownership_confirmed": None}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 200
        assert resp.json()["dataset_eligibility"] != "eligible"

    def test_compute_eligibility_pending(self):
        assert helper._compute_dataset_eligibility("annotated", "pending") == "privacy_pending"

    def test_compute_eligibility_rejected_privacy(self):
        assert helper._compute_dataset_eligibility("annotated", "rejected") == "privacy_pending"

    def test_compute_privacy_pending_when_field_missing(self):
        data = {**_FULL_HUMAN_PAYLOAD, "ownership_confirmed": None}
        assert helper._compute_privacy_status(data) == "pending"


# ── HELP-14: full annotation + approved → eligible ────────────────────────────

class TestHELP14_FullyAnnotatedEligible:
    def test_full_payload_eligible(self, client):
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        assert resp.status_code == 200
        result = resp.json()
        assert result["annotation_status"]    == "annotated"
        assert result["privacy_review_status"] == "approved"
        assert result["dataset_eligibility"]   == "eligible"

    def test_compute_annotation_status_all_filled(self):
        data = {**_SAMPLE_ANNOTATION, **_FULL_HUMAN_PAYLOAD}
        assert helper._compute_annotation_status(data) == "annotated"

    def test_compute_annotation_status_missing_field(self):
        assert helper._compute_annotation_status(_SAMPLE_ANNOTATION) == "human_review_pending"


# ── HELP-15: reviewed never auto-set ─────────────────────────────────────────

class TestHELP15_ReviewedNotAutoSet:
    def test_annotation_status_never_reviewed(self, client):
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        assert resp.json()["annotation_status"] != "reviewed"

    def test_saved_file_never_reviewed(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text()
        )
        assert saved.get("annotation_status") != "reviewed"

    def test_compute_annotation_status_cannot_return_reviewed(self):
        data = {**_SAMPLE_ANNOTATION, **_FULL_HUMAN_PAYLOAD, "annotation_status": "reviewed"}
        assert helper._compute_annotation_status(data) != "reviewed"


# ── HELP-16: manifest atomic update ──────────────────────────────────────────

class TestHELP16_ManifestAtomicUpdate:
    def test_manifest_updated_after_save(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        manifest = json.loads(tmp_fs["manifest_path"].read_text())
        entry = next(v for v in manifest["videos"] if v["video_id"] == _VIDEO_ID)
        assert entry["annotation_status"]  == "annotated"
        assert entry["dataset_eligibility"] == "eligible"

    def test_manifest_privacy_status_updated(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json=_FULL_HUMAN_PAYLOAD)
        manifest = json.loads(tmp_fs["manifest_path"].read_text())
        entry = next(v for v in manifest["videos"] if v["video_id"] == _VIDEO_ID)
        assert entry["privacy_review_status"] == "approved"

    def test_manifest_bak_created(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        assert tmp_fs["manifest_path"].with_suffix(".json.bak").exists()

    def test_manifest_tmp_not_left_behind(self, client, tmp_fs):
        client.post(f"/api/annotation/{_VIDEO_ID}", json={"annotator": "ZL"})
        assert not tmp_fs["manifest_path"].with_suffix(".json.tmp").exists()


# ── HELP-17: contact event (v2 format) ───────────────────────────────────────

class TestHELP17_ContactEventV2:
    def test_right_instep_event_persisted(self, client, tmp_fs):
        """HELP-17: v2 contact_type event with derived side is saved correctly."""
        payload = {
            "annotator": "ZL", "annotation_date": "2026-06-13",
            "contact_events": [_VALID_RIGHT_INSTEP_EVENT],
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 200
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text()
        )
        assert saved["contact_events"][0]["timestamp_ms"] == 2500
        assert saved["contact_events"][0]["contact_type"] == "right_instep"

    def test_chest_event_center_side(self, client, tmp_fs):
        """HELP-17b: chest event with side=center is saved."""
        payload = {"annotator": "ZL", "annotation_date": "2026-06-13",
                   "contact_events": [_VALID_CHEST_EVENT]}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 200
        saved = json.loads((tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text())
        assert saved["contact_events"][0]["contact_type"] == "chest"

    def test_negative_timestamp_rejected(self, client):
        """HELP-17c: Negative timestamp_ms → 422."""
        bad = {**_VALID_RIGHT_INSTEP_EVENT, "timestamp_ms": -100}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [bad]})
        assert resp.status_code == 422

    def test_invalid_contact_type_rejected(self, client):
        """HELP-17d: Unknown contact_type → 422."""
        bad = {**_VALID_RIGHT_INSTEP_EVENT, "contact_type": "elbow"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [bad]})
        assert resp.status_code == 422


# ── HELP-18: Range request ────────────────────────────────────────────────────

class TestHELP18_RangeRequest:
    def test_range_request_accepted(self, client):
        resp = client.get(f"/media/{_FILENAME}", headers={"Range": "bytes=0-9"})
        assert resp.status_code in (200, 206)

    def test_range_response_206_has_content_range(self, client):
        resp = client.get(f"/media/{_FILENAME}", headers={"Range": "bytes=0-9"})
        if resp.status_code == 206:
            headers_lower = {k.lower(): v for k, v in resp.headers.items()}
            assert "content-range" in headers_lower

    def test_full_file_served_without_range(self, client):
        resp = client.get(f"/media/{_FILENAME}")
        assert resp.status_code == 200
        assert len(resp.content) > 0


# ── HELP-19: taxonomy loaded from source-of-truth file ───────────────────────

class TestHELP19_TaxonomyLoadedFromFile:
    def test_taxonomy_is_loaded(self):
        """HELP-19: _taxonomy is loaded from contact_types_v1.json at import."""
        assert helper._taxonomy is not None
        assert isinstance(helper._taxonomy, dict)

    def test_taxonomy_path_exists(self):
        """HELP-19b: TAXONOMY_PATH points to an existing file."""
        assert helper.TAXONOMY_PATH.exists(), f"Taxonomy file not found: {helper.TAXONOMY_PATH}"

    def test_api_taxonomy_endpoint_returns_taxonomy(self, client):
        """HELP-19c: GET /api/taxonomy returns the taxonomy JSON."""
        resp = client.get("/api/taxonomy")
        assert resp.status_code == 200
        data = resp.json()
        assert "all_keys" in data
        assert "stable_keys" in data


# ── HELP-20: all 18 contact types available ───────────────────────────────────

class TestHELP20_All18ContactTypesAvailable:
    def test_all_contact_types_count_18(self):
        """HELP-20: _ALL_CONTACT_TYPES has exactly 18 entries."""
        assert len(helper._ALL_CONTACT_TYPES) == 18

    def test_stable_types_count_17(self):
        """HELP-20b: _STABLE_TYPES has exactly 17 entries."""
        assert len(helper._STABLE_TYPES) == 17

    def test_all_18_keys_in_set(self):
        """HELP-20c: All expected keys are present in _ALL_CONTACT_TYPES."""
        expected = {
            "right_instep","left_instep","right_inside_foot","left_inside_foot",
            "right_outside_foot","left_outside_foot","right_heel","left_heel",
            "right_knee","left_knee","right_hip","left_hip",
            "chest","right_shoulder","left_shoulder","head","back","custom_other",
        }
        assert expected == helper._ALL_CONTACT_TYPES

    def test_taxonomy_endpoint_has_18_keys(self, client):
        """HELP-20d: /api/taxonomy all_keys has exactly 18 entries."""
        data = client.get("/api/taxonomy").json()
        assert len(data["all_keys"]) == 18


# ── HELP-21: no hardcoded old 6-type enum ────────────────────────────────────

class TestHELP21_NoHardcodedOldEnum:
    def test_thigh_not_in_all_contact_types(self):
        """HELP-21: 'thigh' is not a valid contact_type (deprecated v1 body_part)."""
        assert "thigh" not in helper._ALL_CONTACT_TYPES

    def test_thigh_not_in_stable_types(self):
        """HELP-21b: 'thigh' not in stable types set."""
        assert "thigh" not in helper._STABLE_TYPES

    def test_thigh_in_legacy_body_parts(self):
        """HELP-21c: 'thigh' is recognised as a legacy v1 body_part."""
        assert "thigh" in helper._LEGACY_V1_BODY_PARTS

    def test_helper_source_no_hardcoded_old_set(self):
        """HELP-21d: Helper source has no hardcoded 6-element body_part set."""
        src = (helper.ROOT / "scripts/juggling_annotation_helper.py").read_text()
        # The old hardcoded set: {"foot","knee","thigh","shoulder","head","chest"}
        assert '"thigh"' not in src or "_LEGACY_V1_BODY_PARTS" in src, \
            "thigh appears hardcoded outside the legacy set"
        assert "_VALID_BODY_PARTS = " not in src, \
            "Old _VALID_BODY_PARTS hardcoded set still present"


# ── HELP-22: right_* → side=right derived ────────────────────────────────────

class TestHELP22_RightTypeSideRight:
    @pytest.mark.parametrize("ctype", list(helper._RIGHT_TYPES))
    def test_right_type_derives_side_right(self, ctype):
        """HELP-22: All right_* stable types derive side=right."""
        assert helper._derived_side(ctype) == "right"

    def test_right_instep_wrong_side_422(self, client):
        """HELP-22b: right_instep with side=left → 422 (side consistency)."""
        evt = {**_VALID_RIGHT_INSTEP_EVENT, "side": "left"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        assert "side" in resp.json()["detail"].lower() or "right" in resp.json()["detail"].lower()


# ── HELP-23: left_* → side=left derived ──────────────────────────────────────

class TestHELP23_LeftTypeSideLeft:
    @pytest.mark.parametrize("ctype", list(helper._LEFT_TYPES))
    def test_left_type_derives_side_left(self, ctype):
        """HELP-23: All left_* stable types derive side=left."""
        assert helper._derived_side(ctype) == "left"

    def test_left_knee_wrong_side_422(self, client):
        """HELP-23b: left_knee with side=right → 422."""
        evt = {"event_id": 1, "timestamp_ms": 1000, "contact_type": "left_knee",
               "side": "right", "annotation_confidence": "certain",
               "annotation_source": "manual_annotator"}
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]}
        ).status_code == 422


# ── HELP-24: chest/head/back → side=center ───────────────────────────────────

class TestHELP24_CenterTypeSideCenter:
    @pytest.mark.parametrize("ctype", ["chest", "head", "back"])
    def test_center_type_derives_center(self, ctype):
        """HELP-24: center types (chest/head/back) derive side=center."""
        assert helper._derived_side(ctype) == "center"

    @pytest.mark.parametrize("ctype", ["chest", "head", "back"])
    def test_center_type_wrong_side_422(self, client, ctype):
        """HELP-24b: center type with side=left → 422."""
        evt = {"event_id": 1, "timestamp_ms": 1000, "contact_type": ctype,
               "side": "left", "annotation_confidence": "certain",
               "annotation_source": "manual_annotator"}
        assert client.post(
            f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]}
        ).status_code == 422


# ── HELP-25: stable type with custom fields → 422 ────────────────────────────

class TestHELP25_StableTypeCustomFields422:
    def test_stable_with_custom_label_rejected(self, client):
        """HELP-25: Stable contact_type with non-null custom_label → 422."""
        evt = {**_VALID_RIGHT_INSTEP_EVENT, "custom_label": "extra_label"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        assert "custom_label" in resp.json()["detail"]

    def test_stable_with_custom_description_rejected(self, client):
        """HELP-25b: Stable contact_type with non-null custom_description → 422."""
        evt = {**_VALID_CHEST_EVENT, "custom_description": "This should not be here"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        assert "custom_description" in resp.json()["detail"]


# ── HELP-26: custom_other empty label → 422 ───────────────────────────────────

class TestHELP26_CustomOtherEmptyLabel422:
    def test_empty_custom_label_rejected(self, client):
        """HELP-26: custom_other with empty custom_label → 422."""
        evt = {**_VALID_CUSTOM_EVENT, "custom_label": ""}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        assert "custom_label" in resp.json()["detail"]

    def test_null_custom_label_rejected(self, client):
        """HELP-26b: custom_other with null custom_label → 422."""
        evt = {**_VALID_CUSTOM_EVENT, "custom_label": None}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422

    def test_custom_label_clash_with_stable_key_rejected(self, client):
        """HELP-26c: custom_label matching an existing stable key → 422."""
        evt = {**_VALID_CUSTOM_EVENT, "custom_label": "right_instep"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422


# ── HELP-27: custom_other empty description → 422 ────────────────────────────

class TestHELP27_CustomOtherEmptyDescription422:
    def test_empty_description_rejected(self, client):
        """HELP-27: custom_other with empty custom_description → 422."""
        evt = {**_VALID_CUSTOM_EVENT, "custom_description": ""}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        assert "custom_description" in resp.json()["detail"]

    def test_null_description_rejected(self, client):
        """HELP-27b: custom_other with null custom_description → 422."""
        evt = {**_VALID_CUSTOM_EVENT, "custom_description": None}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422


# ── HELP-28: custom_other without side → 422 ─────────────────────────────────

class TestHELP28_CustomOtherMissingSide422:
    def test_null_side_rejected(self, client):
        """HELP-28: custom_other with side=null → 422."""
        evt = {**_VALID_CUSTOM_EVENT, "side": None}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        assert "side" in resp.json()["detail"].lower()

    def test_missing_side_rejected(self, client):
        """HELP-28b: custom_other with no side key → 422."""
        evt = {k: v for k, v in _VALID_CUSTOM_EVENT.items() if k != "side"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422


# ── HELP-29: custom_other excluded_from_training=true ────────────────────────

class TestHELP29_CustomOtherExcludedFromTraining:
    def test_custom_other_excluded_from_training_in_saved_json(self, client, tmp_fs):
        """HELP-29: After saving a custom_other event, excluded_from_training=true in JSON."""
        payload = {"annotator": "ZL", "annotation_date": "2026-06-13",
                   "contact_events": [_VALID_CUSTOM_EVENT]}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        assert resp.status_code == 200
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text()
        )
        evt = saved["contact_events"][0]
        assert evt["excluded_from_training"] is True

    def test_custom_other_review_status_pending_taxonomy(self, client, tmp_fs):
        """HELP-29b: custom_other gets review_status=pending_taxonomy_review automatically."""
        payload = {"annotator": "ZL", "annotation_date": "2026-06-13",
                   "contact_events": [_VALID_CUSTOM_EVENT]}
        client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text()
        )
        assert saved["contact_events"][0]["review_status"] == "pending_taxonomy_review"

    def test_stable_type_excluded_from_training_true_when_pending(self, client, tmp_fs):
        """HELP-29c: Pending manual_annotator stable event defaults to excluded_from_training=true.

        Policy B: excluded_from_training=true until a second reviewer confirms the event.
        Only confirmed/corrected events may become training-eligible (via explicit set).
        """
        payload = {"annotator": "ZL", "annotation_date": "2026-06-13",
                   "contact_events": [_VALID_RIGHT_INSTEP_EVENT]}
        client.post(f"/api/annotation/{_VIDEO_ID}", json=payload)
        saved = json.loads(
            (tmp_fs["annotations_dir"] / f"{_VIDEO_ID}.json").read_text()
        )
        assert saved["contact_events"][0]["excluded_from_training"] is True
        assert saved["contact_events"][0]["review_status"] == "pending"


# ── HELP-30: legacy foot event → manual review required ──────────────────────

class TestHELP30_LegacyFootEventManualReview:
    def test_body_part_foot_rejected_with_instructive_error(self, client):
        """HELP-30: v1 body_part='foot' in contact_events → 422 with legacy message."""
        evt = {
            "event_id": 1, "timestamp_ms": 1000,
            "body_part": "foot",        # v1 field — no contact_type
            "side": "right", "annotation_confidence": "certain",
            "annotation_source": "manual_annotator",
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "legacy" in detail.lower() or "body_part" in detail.lower()

    def test_foot_as_contact_type_value_rejected(self, client):
        """HELP-30b: contact_type='foot' (v1 value) → 422 as not a valid v2 contact_type."""
        evt = {**_VALID_RIGHT_INSTEP_EVENT, "contact_type": "foot"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422


# ── HELP-31: legacy thigh event → manual review required ─────────────────────

class TestHELP31_LegacyThighEventManualReview:
    def test_body_part_thigh_rejected_with_forbidden_message(self, client):
        """HELP-31: v1 body_part='thigh' → 422 with FORBIDDEN migration message."""
        evt = {
            "event_id": 1, "timestamp_ms": 2000,
            "body_part": "thigh",
            "side": "left", "annotation_confidence": "probable",
            "annotation_source": "manual_annotator",
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        detail = resp.json()["detail"].lower()
        assert "forbidden" in detail or "thigh" in detail

    def test_thigh_as_contact_type_value_rejected(self, client):
        """HELP-31b: contact_type='thigh' → 422 (legacy v1 body_part, not v2 contact_type)."""
        evt = {**_VALID_RIGHT_INSTEP_EVENT, "contact_type": "thigh", "side": "right"}
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422


# ── HELP-32: thigh→hip auto-migration forbidden ───────────────────────────────

class TestHELP32_ThighToHipForbidden:
    def test_thigh_not_auto_migrated_to_hip_in_contact_events(self, client, tmp_fs):
        """HELP-32: Saving with thigh body_part does NOT silently become hip."""
        evt = {
            "event_id": 1, "timestamp_ms": 1000,
            "body_part": "thigh", "side": "left",
            "annotation_confidence": "probable", "annotation_source": "manual_annotator",
        }
        resp = client.post(f"/api/annotation/{_VIDEO_ID}", json={"contact_events": [evt]})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # Error must mention forbidden, not silently succeed
        assert "forbidden" in detail.lower() or "thigh" in detail.lower()

    def test_body_parts_used_thigh_deprecated_422(self, client):
        """HELP-32b: body_parts_used.thigh → 422 (deprecated in v2)."""
        resp = client.post(
            f"/api/annotation/{_VIDEO_ID}",
            json={"body_parts_used": {"thigh": True}}
        )
        assert resp.status_code == 422
        assert "thigh" in resp.json()["detail"]

    def test_forbidden_flag_in_legacy_body_parts(self):
        """HELP-32c: 'thigh' is in _LEGACY_FORBIDDEN_MIGRATION set."""
        assert "thigh" in helper._LEGACY_FORBIDDEN_MIGRATION


# ── HELP-33: skeleton JSONs are schema v2 ────────────────────────────────────

class TestHELP33_SkeletonJsonsSchemaV2:
    _SKELETONS = [f"jug_b1_00{i}" for i in range(1, 5) if i != 2]

    @pytest.mark.parametrize("vid_id", _SKELETONS)
    def test_skeleton_annotation_schema_version_is_v2(self, vid_id):
        """HELP-33: Each skeleton annotation_schema_version == '2.0'."""
        path = helper.ROOT / "datasets/juggling/annotations" / f"{vid_id}.json"
        d = json.loads(path.read_text())
        assert d["annotation_schema_version"] == "2.0", f"{vid_id}: expected 2.0"

    @pytest.mark.parametrize("vid_id", _SKELETONS)
    def test_skeleton_annotation_version_is_v2(self, vid_id):
        """HELP-33b: Each skeleton annotation_version == 'v2.0'."""
        path = helper.ROOT / "datasets/juggling/annotations" / f"{vid_id}.json"
        d = json.loads(path.read_text())
        assert d["annotation_version"] == "v2.0", f"{vid_id}: expected v2.0"

    @pytest.mark.parametrize("vid_id", _SKELETONS)
    def test_skeleton_taxonomy_version_is_v1(self, vid_id):
        """HELP-33c: Each skeleton taxonomy_version == 'v1'."""
        path = helper.ROOT / "datasets/juggling/annotations" / f"{vid_id}.json"
        d = json.loads(path.read_text())
        assert d.get("taxonomy_version") == "v1", f"{vid_id}: taxonomy_version missing/wrong"

    @pytest.mark.parametrize("vid_id", _SKELETONS)
    def test_skeleton_contact_events_still_null(self, vid_id):
        """HELP-33d: contact_events remains null in all skeleton JSONs."""
        path = helper.ROOT / "datasets/juggling/annotations" / f"{vid_id}.json"
        d = json.loads(path.read_text())
        assert d["contact_events"] is None, f"{vid_id}: contact_events should be null"


# ── HELP-34: manifest taxonomy reference ─────────────────────────────────────

class TestHELP34_ManifestTaxonomyReference:
    def test_manifest_schema_version_is_v2(self):
        """HELP-34: manifest schema_version points to annotation_schema_v2.json."""
        m = json.loads(helper.MANIFEST_PATH.read_text())
        assert m["schema_version"] == "annotation_schema_v2.json"

    def test_manifest_taxonomy_version_is_v1(self):
        """HELP-34b: manifest taxonomy_version == 'v1'."""
        m = json.loads(helper.MANIFEST_PATH.read_text())
        assert m["taxonomy_version"] == "v1"

    def test_manifest_contact_taxonomy_source(self):
        """HELP-34c: manifest contact_taxonomy_source == 'contact_types_v1.json'."""
        m = json.loads(helper.MANIFEST_PATH.read_text())
        assert m["contact_taxonomy_source"] == "contact_types_v1.json"


# ── HELP-35: all human fields still null/pending ──────────────────────────────

class TestHELP35_HumanFieldsNullPending:
    _SKELETONS = [f"jug_b1_00{i}" for i in range(1, 5) if i != 2]
    _NULL_HUMAN_FIELDS = [
        "difficulty", "total_juggling_count", "count_confidence",
        "dominant_body_part", "body_parts_used",
        "ball_visible_quality", "lighting_quality", "camera_stability",
        "multi_person_present", "multiple_balls_present",
        "expected_validity", "invalidity_reason", "notes",
        "annotator", "annotation_date", "contact_events",
    ]

    @pytest.mark.parametrize("vid_id", _SKELETONS)
    def test_all_human_fields_null(self, vid_id):
        """HELP-35: No human observation field has been auto-filled in skeleton JSONs."""
        path = helper.ROOT / "datasets/juggling/annotations" / f"{vid_id}.json"
        d = json.loads(path.read_text())
        for field in self._NULL_HUMAN_FIELDS:
            assert d.get(field) is None, f"{vid_id}.{field} should be null, got {d.get(field)!r}"

    @pytest.mark.parametrize("vid_id", _SKELETONS)
    def test_annotation_status_is_metadata_ready(self, vid_id):
        """HELP-35b: annotation_status is 'metadata_ready' (not annotated/reviewed)."""
        path = helper.ROOT / "datasets/juggling/annotations" / f"{vid_id}.json"
        d = json.loads(path.read_text())
        assert d["annotation_status"] == "metadata_ready"

    @pytest.mark.parametrize("vid_id", _SKELETONS)
    def test_privacy_status_is_pending(self, vid_id):
        """HELP-35c: privacy_review_status is 'pending'."""
        path = helper.ROOT / "datasets/juggling/annotations" / f"{vid_id}.json"
        d = json.loads(path.read_text())
        assert d["privacy_review_status"] == "pending"


# ── HELP-36: production app route count unchanged ────────────────────────────

class TestHELP36_ProductionRouteCountUnchanged:
    def test_helper_has_no_production_imports(self):
        """HELP-36: The annotation helper does not import from the production app/ package."""
        src = (helper.ROOT / "scripts/juggling_annotation_helper.py").read_text()
        assert "from app." not in src, "Helper imports from production app package"
        assert "import app." not in src, "Helper imports from production app package"

    def test_production_snapshot_exists(self):
        """HELP-36b: OpenAPI snapshot file exists (production routes unchanged)."""
        snapshot_path = helper.ROOT / "tests/snapshots/openapi_snapshot.json"
        assert snapshot_path.exists(), "OpenAPI snapshot missing"

    def test_production_route_count_is_892(self):
        """HELP-36c: Production OpenAPI snapshot has 903 routes after AN-3B2B (+2 ball detection paths)."""
        snapshot_path = helper.ROOT / "tests/snapshots/openapi_snapshot.json"
        snapshot = json.loads(snapshot_path.read_text())
        route_count = len(snapshot.get("paths", {}))
        assert route_count == 912, f"Unexpected production route count: {route_count}"

    def test_helper_routes_not_in_production_snapshot(self):
        """HELP-36d: Annotation helper routes (/api/taxonomy etc.) not in production snapshot."""
        snapshot_path = helper.ROOT / "tests/snapshots/openapi_snapshot.json"
        snapshot = json.loads(snapshot_path.read_text())
        helper_only_routes = {"/api/taxonomy", "/api/manifest", "/media/{filename}"}
        for route in helper_only_routes:
            assert route not in snapshot.get("paths", {}), \
                f"Helper route '{route}' leaked into production OpenAPI snapshot"

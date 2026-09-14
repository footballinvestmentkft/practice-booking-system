from datetime import date
from types import SimpleNamespace
import struct

from app.models.ws1_domain import UserGuardianConsent
from app.services.juggling.golden_dataset import build_reviewed_sample, evaluate, validate_manifest
from app.services.juggling import feature_flag as ff_module


import pytest


@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _consent(client, token):
    return client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True, "training_consent": True, "admin_review_consent": True},
        headers=_auth(token),
    )


def _mp4():
    return struct.pack(">I", 20) + b"ftypisom" + b"\0\0\0\0isom" + b"\0" * 200


def test_direct_capture_requires_canonical_dob(client, student_token):
    _consent(client, student_token)
    response = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "in_app_capture", "upload_source": "camera", "upload_idempotency_key": "capture-attempt-0001"},
        headers=_auth(student_token),
    )
    assert response.status_code == 403
    assert "DATE_OF_BIRTH_REQUIRED" in response.text


def test_minor_direct_capture_requires_canonical_guardian_consent(
    client, student_token, student_user, db_session,
):
    student_user.date_of_birth = date(2012, 1, 1)
    db_session.flush()
    _consent(client, student_token)
    body = {"source_type": "in_app_capture", "upload_source": "camera", "upload_idempotency_key": "capture-attempt-0002"}
    denied = client.post("/api/v1/users/me/juggling/videos/upload-init", json=body, headers=_auth(student_token))
    assert denied.status_code == 403
    assert "GUARDIAN_CONSENT_REQUIRED" in denied.text

    db_session.add(UserGuardianConsent(user_id=student_user.id, guardian_name="Test Guardian"))
    db_session.flush()
    allowed = client.post("/api/v1/users/me/juggling/videos/upload-init", json=body, headers=_auth(student_token))
    assert allowed.status_code == 201


def test_upload_init_and_file_replay_are_idempotent(
    client, student_token, student_user, db_session, tmp_path, monkeypatch,
):
    student_user.date_of_birth = date(2000, 1, 1)
    db_session.flush()
    _consent(client, student_token)
    body = {
        "source_type": "in_app_capture", "upload_source": "camera",
        "upload_idempotency_key": "capture-attempt-0003",
        "client_reported_metadata": {"duration_seconds": 4.2, "device_model": "iPhone", "local_path": "/private/raw.mov"},
    }
    first = client.post("/api/v1/users/me/juggling/videos/upload-init", json=body, headers=_auth(student_token))
    replay = client.post("/api/v1/users/me/juggling/videos/upload-init", json=body, headers=_auth(student_token))
    assert first.status_code == replay.status_code == 201
    assert first.json()["video_id"] == replay.json()["video_id"]
    conflict_body = {
        **body,
        "client_reported_metadata": {
            **body["client_reported_metadata"], "device_model": "Different device",
        },
    }
    conflict = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json=conflict_body,
        headers=_auth(student_token),
    )
    assert conflict.status_code == 409
    assert "UPLOAD_IDEMPOTENCY_CONFLICT" in conflict.text

    target = tmp_path / "capture.mp4"
    monkeypatch.setattr("app.services.juggling.video_service.save_file", lambda data, name: target)
    video_id = first.json()["video_id"]
    upload1 = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/upload",
        files={"file": ("capture.mp4", _mp4(), "video/mp4")}, headers=_auth(student_token),
    )
    upload2 = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/upload",
        files={"file": ("capture.mp4", _mp4(), "video/mp4")}, headers=_auth(student_token),
    )
    assert upload1.status_code == upload2.status_code == 200
    assert upload1.json()["checksum_sha256"] == upload2.json()["checksum_sha256"]
    changed = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/upload",
        files={"file": ("capture.mp4", _mp4() + b"changed", "video/mp4")},
        headers=_auth(student_token),
    )
    assert changed.status_code == 409
    assert "UPLOAD_REPLAY_CONTENT_MISMATCH" in changed.text


def test_reviewed_sample_is_privacy_safe_and_evaluation_reports_all_metrics():
    verification = SimpleNamespace(
        id="verification-1", player_user_id=42, provenance_hash="f" * 64,
        video_checksum_sha256="a" * 64, contact_event_revision_hash="b" * 64,
        touch_count=1, status="VALID", sequence_start_ms=100, sequence_end_ms=100,
        termination_reason="VIDEO_ENDED",
        contact_event_snapshot=[{
            "timestamp_ms": 100, "contact_type": "right_instep", "side": "right",
            "excluded_from_count": False, "deleted_at": None,
        }],
    )
    video = SimpleNamespace(
        server_detected_metadata={"duration_seconds": 2.5},
        client_reported_metadata={
            "device_model": "iPhone", "local_path": "/private/raw.mov",
            "os_version": "/private/device-owner/secret",
        },
    )
    consent = SimpleNamespace(service_consent=True, training_consent=True, admin_review_consent=True)
    sample = build_reviewed_sample(verification=verification, video=video, consent=consent)
    manifest = {"schema_version": "PC6B_GOLDEN_V1", "samples": [sample]}
    validate_manifest(manifest)
    encoded = str(sample)
    assert "player_user_id" not in encoded and "local_path" not in encoded and "/private" not in encoded

    result = evaluate(manifest, {"verification-1": {
        "touch_count": 1, "sequence_status": "VALID",
        "contact_events": [{"timestamp_ms": 120, "contact_type": "right_instep", "side": "right"}],
    }})
    assert result.touch_count_exact_accuracy == 1
    assert result.contact_precision == result.contact_recall == 1
    assert result.mean_timestamp_error_ms == 20
    assert result.left_right_accuracy == result.body_part_accuracy == 1
    assert result.sequence_validity_accuracy == 1

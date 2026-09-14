from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import struct

from app.models.ws1_domain import UserGuardianConsent
from app.services.player_identity_service import issue_football_player_entitlement
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


def _issue_attempt(client, token, key="capture-request-0001"):
    return client.post(
        "/api/v1/users/me/juggling/capture-attempts",
        json={"request_idempotency_key": key},
        headers=_auth(token),
    )


def _authorize_football_player(db_session, student_user):
    issue_football_player_entitlement(
        db_session, user=student_user, payment_verified=True,
    )
    db_session.flush()


def test_server_issues_front_only_solo_attempt_idempotently(
    client, student_token, student_user, db_session,
):
    student_user.date_of_birth = date(2000, 1, 1)
    db_session.flush()
    _authorize_football_player(db_session, student_user)

    first = _issue_attempt(client, student_token)
    replay = _issue_attempt(client, student_token)

    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert first.json()["capture_mode"] == "SOLO_VERIFIED"
    assert first.json()["camera_position"] == "front"
    assert first.json()["status"] == "ISSUED"


def test_solo_upload_requires_matching_server_attempt_and_front_metadata(
    client, student_token, student_user, db_session,
):
    student_user.date_of_birth = date(2000, 1, 1)
    db_session.flush()
    _authorize_football_player(db_session, student_user)
    _consent(client, student_token)

    missing = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={
            "source_type": "in_app_capture",
            "upload_source": "camera",
            "upload_idempotency_key": "capture-upload-0001",
            "client_reported_metadata": {"camera_position": "front"},
        },
        headers=_auth(student_token),
    )
    assert missing.status_code == 422

    attempt = _issue_attempt(client, student_token, "capture-request-0002").json()
    wrong_source = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={
            "source_type": "in_app_capture",
            "upload_source": "gallery",
            "capture_attempt_id": attempt["capture_attempt_id"],
            "upload_idempotency_key": "capture-upload-0002",
            "client_reported_metadata": {"camera_position": "front"},
        },
        headers=_auth(student_token),
    )
    assert wrong_source.status_code == 422
    assert "SOLO_CAPTURE_SOURCE_MISMATCH" in wrong_source.text

    spoofed = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={
            "source_type": "in_app_capture",
            "upload_source": "camera",
            "capture_attempt_id": attempt["capture_attempt_id"],
            "upload_idempotency_key": "capture-upload-0002",
            "client_reported_metadata": {"camera_position": "back"},
        },
        headers=_auth(student_token),
    )
    assert spoofed.status_code == 422
    assert "CAPTURE_CAMERA_POSITION_MISMATCH" in spoofed.text

    valid = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={
            "source_type": "in_app_capture",
            "upload_source": "camera",
            "capture_attempt_id": attempt["capture_attempt_id"],
            "upload_idempotency_key": "capture-upload-0002",
            "client_reported_metadata": {"camera_position": "front"},
            "capture_mode": "INSTRUCTOR_VERIFIED",
            "player_user_id": 999999,
            "camera_position": "back",
        },
        headers=_auth(student_token),
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["capture_attempt_id"] == attempt["capture_attempt_id"]
    assert valid.json()["capture_mode"] == "SOLO_VERIFIED"
    assert valid.json()["camera_position"] == "front"
    from app.models.juggling import JugglingVideo
    persisted = db_session.get(JugglingVideo, UUID(valid.json()["video_id"]))
    assert persisted.user_id == student_user.id


def test_gallery_upload_never_becomes_canonical_solo_candidate(
    client, student_token,
):
    _consent(client, student_token)
    response = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video", "upload_source": "gallery"},
        headers=_auth(student_token),
    )
    assert response.status_code == 201
    assert response.json()["capture_mode"] is None
    assert response.json()["capture_attempt_id"] is None

    spoofed = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={
            "source_type": "uploaded_video", "upload_source": "gallery",
            "capture_attempt_id": "11111111-1111-4111-8111-111111111111",
        },
        headers=_auth(student_token),
    )
    assert spoofed.status_code == 422
    assert "CAPTURE_ATTEMPT_NOT_ALLOWED_FOR_IMPORT" in spoofed.text


def test_direct_capture_requires_canonical_dob(client, student_token):
    response = _issue_attempt(client, student_token, "capture-request-dob")
    assert response.status_code == 403
    assert "DATE_OF_BIRTH_REQUIRED" in response.text


def test_direct_capture_requires_active_canonical_football_entitlement(
    client, student_token, student_user, db_session,
):
    student_user.date_of_birth = date(2000, 1, 1)
    db_session.flush()
    response = _issue_attempt(client, student_token, "capture-request-entitlement")
    assert response.status_code == 403
    assert "ACTIVE_FOOTBALL_ENTITLEMENT_REQUIRED" in response.text


def test_minor_direct_capture_requires_canonical_guardian_consent(
    client, student_token, student_user, db_session,
):
    student_user.date_of_birth = date(2012, 1, 1)
    db_session.flush()
    _consent(client, student_token)
    denied = _issue_attempt(client, student_token, "capture-request-minor")
    assert denied.status_code == 403
    assert "GUARDIAN_CONSENT_REQUIRED" in denied.text

    db_session.add(UserGuardianConsent(user_id=student_user.id, guardian_name="Test Guardian"))
    db_session.flush()
    _authorize_football_player(db_session, student_user)
    allowed = _issue_attempt(client, student_token, "capture-request-minor")
    assert allowed.status_code == 201


def test_upload_init_and_file_replay_are_idempotent(
    client, student_token, student_user, db_session, tmp_path, monkeypatch,
):
    student_user.date_of_birth = date(2000, 1, 1)
    db_session.flush()
    _authorize_football_player(db_session, student_user)
    _consent(client, student_token)
    attempt = _issue_attempt(client, student_token, "capture-request-replay").json()
    body = {
        "source_type": "in_app_capture", "upload_source": "camera",
        "capture_attempt_id": attempt["capture_attempt_id"],
        "upload_idempotency_key": "capture-upload-replay",
        "client_reported_metadata": {
            "duration_seconds": 4.2, "device_model": "iPhone",
            "camera_position": "front", "local_path": "/private/raw.mov",
        },
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
    assert "CAPTURE_ATTEMPT_ALREADY_BOUND" in conflict.text

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


def test_openapi_and_native_share_canonical_solo_capture_contract(client):
    from app.main import app

    schema = app.openapi()
    assert "/api/v1/users/me/juggling/capture-attempts" in schema["paths"]
    upload_ref = schema["paths"][
        "/api/v1/users/me/juggling/videos/upload-init"
    ]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    upload_schema = schema["components"]["schemas"][upload_ref.rsplit("/", 1)[-1]]
    assert "capture_attempt_id" in upload_schema["properties"]

    native_view = Path(
        "ios/LFAEducationCenter/Juggling/Upload/JugglingVideoUploadView.swift"
    ).read_text()
    native_client = Path(
        "ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationAPIClient.swift"
    ).read_text()
    public_player_route = Path(
        "app/api/web_routes/public_player.py"
    ).read_text()
    public_player_template = Path(
        "app/templates/public/player_profile.html"
    ).read_text()
    assert "position: .front" in native_view
    assert "JugglingVideoPHPicker(" not in native_view
    assert "viewModel.startPicker()" not in native_view
    assert '"/api/v1/users/me/juggling/capture-attempts"' in native_client
    assert 'case captureAttemptId = "capture_attempt_id"' in native_client
    assert "user.name or user.email" not in public_player_route
    assert '"name": public_identity' in public_player_route
    assert "profile_user.name" not in public_player_template
    assert "profile_user.email" not in public_player_template

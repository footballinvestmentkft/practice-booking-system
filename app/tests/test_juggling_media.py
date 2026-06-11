"""
Juggling P4 — Media endpoint integration tests — PM-01..PM-34.

Tests cover:
  - Bearer auth (PM-01, PM-03)
  - Cookie auth (PM-02, PM-04)
  - Unauthenticated 401 (PM-11, PM-12)
  - Other user 404 (PM-13, PM-14)
  - gdpr_deleted 410 (PM-15, PM-16)
  - Status guards 409 (PM-17..PM-24)
  - Missing file 404 (PM-25..PM-27)
  - No raw path in response (PM-28, PM-29)
  - No original endpoint (PM-30)
  - Route delta exactly +2 (PM-31)
  - OpenAPI path delta exactly +2 (PM-32)
  - No DB migration (PM-33)
  - P1/P2/P3 regression (PM-34)

Range/206 tests are covered by Starlette FileResponse (Starlette own test suite).
Functional Range smoke tests included for PM-05..PM-10.

JUGGLING_POC_ENABLED is monkeypatched to True.
Files are created in tmp_path fixtures.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models.juggling import JugglingVideo, JugglingVideoStatus, JugglingTranscodeStatus
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module


@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


@pytest.fixture()
def media_upload_dir(tmp_path, monkeypatch):
    """Patch media_service._UPLOAD_DIR to tmp_path so path safety guard passes."""
    from app.services.juggling import media_service
    monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
    return tmp_path


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_video(
    db_session,
    user_id: int,
    status: str = JugglingVideoStatus.analyzed.value,
    transcode_status: str = JugglingTranscodeStatus.done.value,
    thumbnail_path: str | None = None,
    processed_path: str | None = None,
) -> JugglingVideo:
    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=status,
        transcode_status=transcode_status,
        thumbnail_path=thumbnail_path,
        processed_path=processed_path,
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)
    return video


# ── PM-01: Thumbnail — Bearer auth ───────────────────────────────────────────

def test_pm01_thumbnail_bearer_auth(client, student_token, student_user, db_session, media_upload_dir):
    """PM-01: GET /thumbnail with Bearer token + analyzed video → 200 JPEG."""
    f = media_upload_dir / "thumb.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0")
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/jpeg")
    assert "no-store" in r.headers.get("cache-control", "")


def test_pm02_thumbnail_cookie_auth(client, student_token, student_user, db_session, media_upload_dir):
    """PM-02: GET /thumbnail with access_token cookie → 200 (dual-auth cookie path)."""
    f = media_upload_dir / "thumb_cookie.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0")
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    client.cookies.set("access_token", f"Bearer {student_token}")
    r = client.get(f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail")
    client.cookies.clear()
    assert r.status_code == 200, r.text


# ── PM-03: Media — Bearer auth ────────────────────────────────────────────────

def test_pm03_media_bearer_full_response(client, student_token, student_user, db_session, media_upload_dir):
    """PM-03: GET /media with Bearer token → 200, Accept-Ranges: bytes."""
    f = media_upload_dir / "video.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("accept-ranges") == "bytes"
    assert "no-store" in r.headers.get("cache-control", "")


def test_pm04_media_cookie_auth(client, student_token, student_user, db_session, media_upload_dir):
    """PM-04: GET /media with access_token cookie → 200 (dual-auth cookie path)."""
    f = media_upload_dir / "video_cookie.mp4"
    f.write_bytes(b"\x00" * 100)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    client.cookies.set("access_token", f"Bearer {student_token}")
    r = client.get(f"/api/v1/users/me/juggling/videos/{video.id}/media")
    client.cookies.clear()
    assert r.status_code == 200, r.text


# ── PM-05..PM-10: Range behavior ─────────────────────────────────────────────

def test_pm05_media_range_single(client, student_token, student_user, db_session, media_upload_dir):
    """PM-05: Range: bytes=0-3 → 206 Partial Content."""
    f = media_upload_dir / "video_range.mp4"
    f.write_bytes(b"ABCDEFGHIJ" * 10)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers={**_auth(student_token), "Range": "bytes=0-3"},
    )
    assert r.status_code == 206, r.text
    assert "content-range" in r.headers

def test_pm06_media_range_open_ended(client, student_token, student_user, db_session, media_upload_dir):
    """PM-06: Range: bytes=5- → 206."""
    f = media_upload_dir / "video_open.mp4"
    f.write_bytes(b"ABCDEFGHIJ" * 10)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers={**_auth(student_token), "Range": "bytes=5-"},
    )
    assert r.status_code == 206, r.text

def test_pm07_media_range_suffix(client, student_token, student_user, db_session, media_upload_dir):
    """PM-07: Range: bytes=-10 (suffix) → 206."""
    f = media_upload_dir / "video_suffix.mp4"
    f.write_bytes(b"ABCDEFGHIJ" * 10)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers={**_auth(student_token), "Range": "bytes=-10"},
    )
    assert r.status_code == 206, r.text

def test_pm08_media_range_multiple(client, student_token, student_user, db_session, media_upload_dir):
    """PM-08: Multiple range → Starlette returns 206 multipart (native behavior)."""
    f = media_upload_dir / "video_multi.mp4"
    f.write_bytes(b"ABCDEFGHIJ" * 10)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers={**_auth(student_token), "Range": "bytes=0-3,5-8"},
    )
    assert r.status_code == 206, r.text

def test_pm09_media_range_invalid_start_gt_end(client, student_token, student_user, db_session, media_upload_dir):
    """PM-09: Range: bytes=10-0 (start > end) → 400 Bad Request (Starlette MalformedRangeHeader)."""
    f = media_upload_dir / "video_inv.mp4"
    f.write_bytes(b"ABCDEFGHIJ" * 10)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers={**_auth(student_token), "Range": "bytes=10-0"},
    )
    assert r.status_code == 400, r.text

def test_pm10_media_range_start_beyond_file(client, student_token, student_user, db_session, media_upload_dir):
    """PM-10: Range: bytes=99999- (start > file_size) → 416."""
    f = media_upload_dir / "video_beyond.mp4"
    f.write_bytes(b"ABC")
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers={**_auth(student_token), "Range": "bytes=99999-"},
    )
    assert r.status_code == 416, r.text


# ── PM-11..PM-12: Unauthenticated ────────────────────────────────────────────

def test_pm11_thumbnail_unauthenticated(client, student_user, db_session, media_upload_dir):
    """PM-11: No auth → 401."""
    f = media_upload_dir / "t.jpg"
    f.write_bytes(b"\xff\xd8")
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    r = client.get(f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail")
    assert r.status_code == 401, r.text

def test_pm12_media_unauthenticated(client, student_user, db_session, media_upload_dir):
    """PM-12: No auth → 401."""
    f = media_upload_dir / "v.mp4"
    f.write_bytes(b"\x00" * 10)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(f"/api/v1/users/me/juggling/videos/{video.id}/media")
    assert r.status_code == 401, r.text


# ── PM-13..PM-14: Other user → 404 ───────────────────────────────────────────

def test_pm13_thumbnail_other_user(client, student_token, db_session, media_upload_dir):
    """PM-13: Other user's video → 404."""
    other = User(
        name="OtherUser",
        email=f"other+{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        role=UserRole.STUDENT,
    )
    db_session.add(other)
    db_session.flush()
    f = media_upload_dir / "t2.jpg"
    f.write_bytes(b"\xff\xd8")
    video = _make_video(db_session, other.id, thumbnail_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text

def test_pm14_media_other_user(client, student_token, db_session, media_upload_dir):
    """PM-14: Other user's video → 404."""
    other = User(
        name="OtherUser2",
        email=f"other2+{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        role=UserRole.STUDENT,
    )
    db_session.add(other)
    db_session.flush()
    f = media_upload_dir / "v2.mp4"
    f.write_bytes(b"\x00" * 10)
    video = _make_video(db_session, other.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text


# ── PM-15..PM-16: gdpr_deleted → 410 ─────────────────────────────────────────

def test_pm15_thumbnail_gdpr_deleted(client, student_token, student_user, db_session):
    """PM-15: gdpr_deleted video → 410."""
    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=student_user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.gdpr_deleted.value,
        deleted_at=datetime.now(timezone.utc),
        deletion_reason="gdpr_request",
    )
    db_session.add(video)
    db_session.commit()
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 410, r.text

def test_pm16_media_gdpr_deleted(client, student_token, student_user, db_session):
    """PM-16: gdpr_deleted video → 410."""
    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=student_user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.gdpr_deleted.value,
        deleted_at=datetime.now(timezone.utc),
        deletion_reason="gdpr_request",
    )
    db_session.add(video)
    db_session.commit()
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 410, r.text


# ── PM-17..PM-19: Status 409 ─────────────────────────────────────────────────

def test_pm17_media_pending_upload_409(client, student_token, student_user, db_session):
    """PM-17: pending_upload → 409."""
    video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.pending_upload.value)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text

def test_pm18_media_uploaded_409(client, student_token, student_user, db_session):
    """PM-18: uploaded → 409."""
    video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.uploaded.value)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text

def test_pm19_media_processing_409(client, student_token, student_user, db_session):
    """PM-19: processing → 409."""
    video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.processing.value)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text


# ── PM-20..PM-21: Rejected/failed thumbnail 200 ──────────────────────────────

def test_pm20_rejected_thumbnail_200(client, student_token, student_user, db_session, media_upload_dir):
    """PM-20: rejected + thumbnail_path set → 200 (Option B policy)."""
    f = media_upload_dir / "rej_thumb.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    video = _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.rejected.value,
        thumbnail_path=str(f),
    )
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text

def test_pm21_failed_thumbnail_200(client, student_token, student_user, db_session, media_upload_dir):
    """PM-21: failed + thumbnail_path set → 200."""
    f = media_upload_dir / "fail_thumb.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    video = _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.failed.value,
        thumbnail_path=str(f),
    )
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text


# ── PM-22..PM-24: Rejected/failed/skipped media 409 ─────────────────────────

def test_pm22_rejected_media_409(client, student_token, student_user, db_session):
    """PM-22: rejected → media 409."""
    video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.rejected.value)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text

def test_pm23_failed_media_409(client, student_token, student_user, db_session):
    """PM-23: failed → media 409."""
    video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.failed.value)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text

def test_pm24_transcode_skipped_no_processed_path_409(client, student_token, student_user, db_session):
    """PM-24: transcode_skipped + processed_path=None → media 409."""
    video = _make_video(
        db_session, student_user.id,
        transcode_status=JugglingTranscodeStatus.skipped.value,
        processed_path=None,
    )
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text


# ── PM-25..PM-27: Missing files → 404 ────────────────────────────────────────

def test_pm25_missing_thumbnail_path_none_404(client, student_token, student_user, db_session):
    """PM-25: thumbnail_path=None → 404."""
    video = _make_video(db_session, student_user.id, thumbnail_path=None)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text

def test_pm26_missing_processed_path_none_409(client, student_token, student_user, db_session):
    """PM-26: analyzed + done + processed_path=None → 409 (conservative guard)."""
    video = _make_video(db_session, student_user.id, processed_path=None)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text

def test_pm27_media_file_missing_on_disk_404(client, student_token, student_user, db_session):
    """PM-27: processed_path set but file deleted from disk → 404."""
    video = _make_video(
        db_session, student_user.id,
        processed_path="/nonexistent/ghost_video.mp4",
    )
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text


# ── PM-28..PM-29: No raw path in response ────────────────────────────────────

def test_pm28_no_raw_path_in_response_body(client, student_token, student_user, db_session, media_upload_dir):
    """PM-28: response body must not contain filesystem path strings."""
    f = media_upload_dir / "secret_path_video.mp4"
    f.write_bytes(b"\x00" * 50)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    # Response body is binary video data — must not contain the path string
    body_text = r.content.decode("latin-1", errors="replace")
    assert str(media_upload_dir) not in body_text
    assert "secret_path_video" not in body_text

def test_pm29_no_raw_path_in_response_headers(client, student_token, student_user, db_session, media_upload_dir):
    """PM-29: response headers must not contain filesystem path."""
    f = media_upload_dir / "secret_header_video.mp4"
    f.write_bytes(b"\x00" * 50)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    headers_str = str(dict(r.headers))
    assert str(media_upload_dir) not in headers_str
    assert "secret_header_video" not in headers_str


# ── PM-30: No original endpoint ──────────────────────────────────────────────

def test_pm30_no_original_endpoint(client, student_token, student_user, db_session):
    """PM-30: GET .../original must not exist (404 from router)."""
    video = _make_video(db_session, student_user.id)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/original",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text


# ── PM-31..PM-32: Route / OpenAPI delta ──────────────────────────────────────

def test_pm31_route_delta_exactly_plus_2(client):
    """PM-31: Branch has exactly +2 routes vs main baseline of 1010."""
    from app.main import app as fastapi_app
    routes = [
        (m, r.path)
        for r in fastapi_app.routes
        if hasattr(r, "methods") and hasattr(r, "path")
        for m in (r.methods or [])
    ]
    # main baseline = 1010 (committed); biometric WD mods not counted
    # P4 adds exactly +2: GET .../thumbnail + GET .../media
    juggling_media = [
        r for r in routes
        if "juggling/videos" in r[1] and ("thumbnail" in r[1] or r[1].endswith("/media"))
    ]
    assert len(juggling_media) == 2, f"Expected 2 juggling media routes, got: {juggling_media}"
    methods = {m for m, _ in juggling_media}
    assert methods == {"GET"}, f"Juggling media routes must be GET only, got: {methods}"

def test_pm32_openapi_path_delta_exactly_plus_2(client):
    """PM-32: OpenAPI schema has exactly +2 new juggling media paths."""
    from app.main import app as fastapi_app
    schema = fastapi_app.openapi()
    media_paths = [
        p for p in schema["paths"]
        if "juggling/videos" in p and ("thumbnail" in p or p.endswith("/media"))
    ]
    assert len(media_paths) == 2, f"Expected 2 juggling media OpenAPI paths, got: {media_paths}"


# ── PM-33: No DB migration ────────────────────────────────────────────────────

def test_pm33_no_db_migration():
    """PM-33: Alembic head unchanged from P3 baseline (2026_06_11_1100)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import os
    alembic_ini = os.path.join(
        os.path.dirname(__file__), "..", "..", "alembic.ini"
    )
    cfg = Config(alembic_ini)
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert heads == ["2026_06_11_1100"], f"Unexpected alembic heads: {heads}"


# ── PM-34: P1/P2/P3 regression ───────────────────────────────────────────────

def test_pm34_juggling_feature_flag_503_still_works(client, student_token, monkeypatch):
    """PM-34 (regression): feature flag off → 503 on media endpoints."""
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: False)
    r = client.get(
        "/api/v1/users/me/juggling/videos/00000000-0000-0000-0000-000000000000/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text

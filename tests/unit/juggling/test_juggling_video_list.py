"""
Juggling P5 — Video list endpoint unit tests (JVL-01..JVL-30).

GET /api/v1/users/me/juggling/videos

Privacy invariants verified:
  - No raw path in response (JVL-18)
  - No public URL in response (JVL-19)
  - No quality_detail / client_reported_metadata / server_detected_metadata (JVL-22..24)
  - gdpr_deleted excluded (JVL-05)
  - Other user videos excluded (JVL-04)

Run: pytest tests/unit/juggling/test_juggling_video_list.py -v
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.core.auth import create_access_token
from app.database import engine, get_db
from app.main import app
from app.models.juggling import JugglingVideo, JugglingVideoStatus, JugglingTranscodeStatus
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def student_user(db_session):
    user = User(
        email=f"jvl+{uuid.uuid4().hex[:8]}@test.com",
        name="JVL Test Student",
        password_hash="hashed",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def other_user(db_session):
    user = User(
        email=f"other+{uuid.uuid4().hex[:8]}@test.com",
        name="Other User",
        password_hash="hashed",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def student_token(student_user):
    return create_access_token(
        data={"sub": student_user.email},
        expires_delta=timedelta(hours=1),
    )


@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


_LIST_URL = "/api/v1/users/me/juggling/videos"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_video(
    db_session,
    user_id: int,
    status: str = JugglingVideoStatus.analyzed.value,
    transcode_status: str | None = JugglingTranscodeStatus.done.value,
    thumbnail_path: str | None = "/tmp/uploads/thumb.jpg",
    processed_path: str | None = "/tmp/uploads/proc.mp4",
    quality_status: str | None = "pass",
    quality_score: float | None = 87.5,
    quality_detail: dict | None = None,
    created_at: datetime | None = None,
    source_type: str = "uploaded_video",
    upload_source: str = "gallery",
    processed_resolution: str | None = "1920x1080",
    processed_fps: float | None = 30.0,
    processed_file_size_bytes: int | None = 14_800_000,
) -> JugglingVideo:
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type=source_type,
        upload_source=upload_source,
        status=status,
        transcode_status=transcode_status,
        thumbnail_path=thumbnail_path,
        processed_path=processed_path,
        quality_status=quality_status,
        quality_score=quality_score,
        quality_detail=quality_detail,
        processed_resolution=processed_resolution,
        processed_fps=processed_fps,
        processed_file_size_bytes=processed_file_size_bytes,
    )
    if created_at is not None:
        v.created_at = created_at
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


# ── JVL-01: Authenticated user gets own videos ────────────────────────────────

def test_jvl01_authenticated_user_gets_own_videos(client, student_token, student_user, db_session):
    """JVL-01: Authenticated user with 3 videos → 200, total=3, len(videos)=3."""
    for _ in range(3):
        _make_video(db_session, student_user.id)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 3
    assert len(data["videos"]) == 3


# ── JVL-02: Unauthenticated 401 ───────────────────────────────────────────────

def test_jvl02_unauthenticated_401(client):
    """JVL-02: No token → 401."""
    r = client.get(_LIST_URL)
    assert r.status_code == 401, r.text


# ── JVL-03: Feature flag off → 503 ───────────────────────────────────────────

def test_jvl03_feature_flag_off_503(client, student_token, monkeypatch):
    """JVL-03: JUGGLING_POC_ENABLED=false → 503."""
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: False)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 503, r.text


# ── JVL-04: Only own videos returned ─────────────────────────────────────────

def test_jvl04_only_own_videos(client, student_token, student_user, other_user, db_session):
    """JVL-04: Other user's video is not returned."""
    own = _make_video(db_session, student_user.id)
    other = _make_video(db_session, other_user.id)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200
    ids = [v["video_id"] for v in r.json()["videos"]]
    assert str(own.id) in ids
    assert str(other.id) not in ids


# ── JVL-05: gdpr_deleted excluded ────────────────────────────────────────────

def test_jvl05_gdpr_deleted_excluded(client, student_token, student_user, db_session):
    """JVL-05: gdpr_deleted video does not appear in list."""
    good = _make_video(db_session, student_user.id)
    deleted = _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.gdpr_deleted.value,
        transcode_status=None, thumbnail_path=None, processed_path=None,
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200
    ids = [v["video_id"] for v in r.json()["videos"]]
    assert str(good.id) in ids
    assert str(deleted.id) not in ids


# ── JVL-06: Ordering created_at DESC ─────────────────────────────────────────

def test_jvl06_ordering_created_at_desc(client, student_token, student_user, db_session):
    """JVL-06: Videos ordered by created_at descending — newest first."""
    now = datetime.now(timezone.utc)
    old = _make_video(db_session, student_user.id, created_at=now - timedelta(hours=2))
    mid = _make_video(db_session, student_user.id, created_at=now - timedelta(hours=1))
    new = _make_video(db_session, student_user.id, created_at=now)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200
    ids = [v["video_id"] for v in r.json()["videos"]]
    assert ids[0] == str(new.id)
    assert ids[1] == str(mid.id)
    assert ids[2] == str(old.id)


# ── JVL-07: Pagination first page ────────────────────────────────────────────

def test_jvl07_pagination_first_page(client, student_token, student_user, db_session):
    """JVL-07: limit=2&offset=0 → 2 items, total=4."""
    for _ in range(4):
        _make_video(db_session, student_user.id)
    r = client.get(_LIST_URL + "?limit=2&offset=0", headers=_auth(student_token))
    assert r.status_code == 200
    data = r.json()
    assert len(data["videos"]) == 2
    assert data["total"] == 4
    assert data["limit"] == 2
    assert data["offset"] == 0


# ── JVL-08: Pagination second page ───────────────────────────────────────────

def test_jvl08_pagination_second_page(client, student_token, student_user, db_session):
    """JVL-08: limit=2&offset=2 returns next 2 items."""
    now = datetime.now(timezone.utc)
    videos = [
        _make_video(db_session, student_user.id, created_at=now - timedelta(seconds=i))
        for i in range(4)
    ]
    r1 = client.get(_LIST_URL + "?limit=2&offset=0", headers=_auth(student_token))
    r2 = client.get(_LIST_URL + "?limit=2&offset=2", headers=_auth(student_token))
    ids1 = {v["video_id"] for v in r1.json()["videos"]}
    ids2 = {v["video_id"] for v in r2.json()["videos"]}
    assert len(ids1) == 2
    assert len(ids2) == 2
    assert ids1.isdisjoint(ids2)  # no overlap between pages


# ── JVL-09: Max limit enforced ───────────────────────────────────────────────

def test_jvl09_max_limit_enforced(client, student_token):
    """JVL-09: limit=101 → 422 Unprocessable Entity."""
    r = client.get(_LIST_URL + "?limit=101", headers=_auth(student_token))
    assert r.status_code == 422, r.text


# ── JVL-10: Negative limit → 422 ─────────────────────────────────────────────

def test_jvl10_negative_limit_422(client, student_token):
    """JVL-10: limit=-1 → 422."""
    r = client.get(_LIST_URL + "?limit=-1", headers=_auth(student_token))
    assert r.status_code == 422, r.text


# ── JVL-11: Negative offset → 422 ────────────────────────────────────────────

def test_jvl11_negative_offset_422(client, student_token):
    """JVL-11: offset=-1 → 422."""
    r = client.get(_LIST_URL + "?offset=-1", headers=_auth(student_token))
    assert r.status_code == 422, r.text


# ── JVL-12: has_thumbnail = true (analyzed + thumbnail_path set) ──────────────

def test_jvl12_has_thumbnail_true(client, student_token, student_user, db_session):
    """JVL-12: analyzed + thumbnail_path set → has_thumbnail=True."""
    _make_video(db_session, student_user.id, thumbnail_path="/tmp/uploads/t.jpg")
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.json()["videos"][0]["has_thumbnail"] is True


# ── JVL-13: has_thumbnail = false (processing status) ────────────────────────

def test_jvl13_has_thumbnail_false_processing(client, student_token, student_user, db_session):
    """JVL-13: processing status → has_thumbnail=False regardless of path."""
    _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.processing.value,
        transcode_status=None,
        thumbnail_path="/tmp/uploads/t.jpg",
        processed_path=None,
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.json()["videos"][0]["has_thumbnail"] is False


# ── JVL-14: has_thumbnail = false (thumbnail_path is null) ───────────────────

def test_jvl14_has_thumbnail_false_no_path(client, student_token, student_user, db_session):
    """JVL-14: analyzed + thumbnail_path=None → has_thumbnail=False."""
    _make_video(db_session, student_user.id, thumbnail_path=None)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.json()["videos"][0]["has_thumbnail"] is False


# ── JVL-15: has_media = true ──────────────────────────────────────────────────

def test_jvl15_has_media_true(client, student_token, student_user, db_session):
    """JVL-15: analyzed + transcode_done + processed_path set → has_media=True."""
    _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.analyzed.value,
        transcode_status=JugglingTranscodeStatus.done.value,
        processed_path="/tmp/uploads/p.mp4",
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.json()["videos"][0]["has_media"] is True


# ── JVL-16: has_media = false (processing) ───────────────────────────────────

def test_jvl16_has_media_false_processing(client, student_token, student_user, db_session):
    """JVL-16: processing status → has_media=False."""
    _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.processing.value,
        transcode_status=JugglingTranscodeStatus.processing.value,
        thumbnail_path=None, processed_path=None,
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.json()["videos"][0]["has_media"] is False


# ── JVL-17: has_media = false (transcode_failed) ─────────────────────────────

def test_jvl17_has_media_false_transcode_failed(client, student_token, student_user, db_session):
    """JVL-17: analyzed + transcode_failed → has_media=False."""
    _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.analyzed.value,
        transcode_status=JugglingTranscodeStatus.failed.value,
        processed_path=None,
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.json()["videos"][0]["has_media"] is False


# ── JVL-18: No raw path in response ──────────────────────────────────────────

def test_jvl18_no_raw_path_in_response(client, student_token, student_user, db_session):
    """JVL-18: Response must not contain any filesystem path strings."""
    _make_video(
        db_session, student_user.id,
        thumbnail_path="/tmp/juggling_uploads/secret_thumb.jpg",
        processed_path="/tmp/juggling_uploads/secret_video.mp4",
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200
    raw = r.text
    forbidden = [
        "storage_path", "original_path", "processed_path", "thumbnail_path",
        "filename_stored", "checksum_sha256", "checksum_processed",
        "transcode_error", "deletion_reason", "deleted_at",
        "retention_expires_at",
        "/tmp/juggling_uploads", "secret_thumb", "secret_video",
    ]
    for field in forbidden:
        assert field not in raw, f"Forbidden field/value found in response: {field!r}"


# ── JVL-19: No public URL in response ────────────────────────────────────────

def test_jvl19_no_public_url_in_response(client, student_token, student_user, db_session):
    """JVL-19: Response must not contain public URLs."""
    _make_video(db_session, student_user.id)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    raw = r.text
    for pattern in ["http://", "https://", "/uploads/", "/media/", "signed", "public_url"]:
        assert pattern not in raw, f"Forbidden URL pattern in response: {pattern!r}"


# ── JVL-20: Empty list ────────────────────────────────────────────────────────

def test_jvl20_empty_list(client, student_token):
    """JVL-20: No videos → 200, videos=[], total=0."""
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200
    data = r.json()
    assert data["videos"] == []
    assert data["total"] == 0


# ── JVL-21: duration_seconds extracted from quality_detail ───────────────────

def test_jvl21_duration_seconds_from_quality_detail(client, student_token, student_user, db_session):
    """JVL-21: duration_seconds extracted from quality_detail JSONB."""
    _make_video(
        db_session, student_user.id,
        quality_detail={"duration_seconds": 15.3, "fps_detected": 30.0},
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.json()["videos"][0]["duration_seconds"] == pytest.approx(15.3)


# ── JVL-22: quality_detail raw JSONB not in response ─────────────────────────

def test_jvl22_quality_detail_not_in_response(client, student_token, student_user, db_session):
    """JVL-22: Raw quality_detail JSONB must not appear in response."""
    _make_video(
        db_session, student_user.id,
        quality_detail={"duration_seconds": 15.3, "fps_detected": 30.0},
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert "quality_detail" not in r.text
    assert "fps_detected" not in r.text


# ── JVL-23: client_reported_metadata not in response ─────────────────────────

def test_jvl23_client_reported_metadata_not_in_response(client, student_token, student_user, db_session):
    """JVL-23: client_reported_metadata must not appear in response."""
    _make_video(db_session, student_user.id)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert "client_reported_metadata" not in r.text


# ── JVL-24: server_detected_metadata not in response ─────────────────────────

def test_jvl24_server_detected_metadata_not_in_response(client, student_token, student_user, db_session):
    """JVL-24: server_detected_metadata must not appear in response."""
    _make_video(db_session, student_user.id)
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert "server_detected_metadata" not in r.text


# ── JVL-25: OpenAPI path delta +1, route delta +1 ────────────────────────────

def test_jvl25_openapi_path_and_route_delta(client):
    """JVL-25: GET /api/v1/users/me/juggling/videos in OpenAPI; route count = 1013."""
    from app.main import app as fastapi_app
    schema = fastapi_app.openapi()
    assert "/api/v1/users/me/juggling/videos" in schema["paths"], "List path missing from OpenAPI"
    routes = [
        (m, r.path)
        for r in fastapi_app.routes
        if hasattr(r, "methods") and hasattr(r, "path")
        for m in (r.methods or [])
    ]
    assert len(routes) == 1039, f"Expected 1038 routes (AN-3B2F PR-1B: +1 frame endpoint), got {len(routes)}"
    get_list = [r for r in routes if r[0] == "GET" and r[1] == "/api/v1/users/me/juggling/videos"]
    assert len(get_list) == 1


# ── JVL-26: Alembic head unchanged ───────────────────────────────────────────

def test_jvl26_alembic_head_unchanged():
    """JVL-26: Alembic head is 2026_06_22_1000 (AN-3B PR-4B2 multicamera session contract)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import os
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "..", "..", "alembic.ini"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert heads == ["2026_06_22_1000"], f"Unexpected Alembic heads: {heads}"


# ── JVL-27: P4 thumbnail/media regression ────────────────────────────────────

def test_jvl27_p4_thumbnail_endpoint_unchanged(client, student_token):
    """JVL-27a: P4 thumbnail endpoint still returns 404 for unknown video (not 500)."""
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{uuid.uuid4()}/thumbnail",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text


def test_jvl27_p4_media_endpoint_unchanged(client, student_token):
    """JVL-27b: P4 media endpoint still returns 404 for unknown video (not 500)."""
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{uuid.uuid4()}/media",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text


# ── JVL-28: rejected status in list ──────────────────────────────────────────

def test_jvl28_rejected_in_list(client, student_token, student_user, db_session):
    """JVL-28: rejected video IS included in list (gdpr_deleted and media_deleted are excluded)."""
    v = _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.rejected.value,
        transcode_status=None, processed_path=None,
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    ids = [x["video_id"] for x in r.json()["videos"]]
    assert str(v.id) in ids


# ── JVL-29: failed status in list ────────────────────────────────────────────

def test_jvl29_failed_in_list(client, student_token, student_user, db_session):
    """JVL-29: failed video IS included in list."""
    v = _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.failed.value,
        transcode_status=JugglingTranscodeStatus.failed.value,
        thumbnail_path=None, processed_path=None,
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    ids = [x["video_id"] for x in r.json()["videos"]]
    assert str(v.id) in ids


# ── JVL-30: Default limit = 50 ───────────────────────────────────────────────

def test_jvl30_default_limit_50(client, student_token):
    """JVL-30: No ?limit param → limit=50 in response envelope."""
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200
    assert r.json()["limit"] == 50


# ── JVL-31: media_deleted excluded from list ──────────────────────────────────

def test_jvl31_media_deleted_excluded(client, student_token, student_user, db_session):
    """JVL-31: media_deleted video does not appear in list (user-facing delete visibility fix)."""
    visible = _make_video(db_session, student_user.id)
    deleted = _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.media_deleted.value,
        transcode_status=None, thumbnail_path=None, processed_path=None,
    )
    r = client.get(_LIST_URL, headers=_auth(student_token))
    assert r.status_code == 200
    ids = [v["video_id"] for v in r.json()["videos"]]
    assert str(visible.id) in ids
    assert str(deleted.id) not in ids


# ── JVL-32: media_deleted absent even after reload (no cache re-appearance) ───

def test_jvl32_media_deleted_absent_on_second_request(client, student_token, student_user, db_session):
    """JVL-32: media_deleted video stays absent across two consecutive list requests."""
    deleted = _make_video(
        db_session, student_user.id,
        status=JugglingVideoStatus.media_deleted.value,
        transcode_status=None, thumbnail_path=None, processed_path=None,
    )
    for _ in range(2):
        r = client.get(_LIST_URL, headers=_auth(student_token))
        assert r.status_code == 200
        ids = [v["video_id"] for v in r.json()["videos"]]
        assert str(deleted.id) not in ids, "media_deleted video must not appear on repeated list requests"
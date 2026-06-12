"""
Juggling P4 — HTTP route branch coverage for CI path.

Covers get_thumbnail + get_media endpoint branches in juggling_videos.py
and get_current_user_media dependency branches in dependencies.py.

All branches exercised:
  - 404 (video not found)
  - 410 (gdpr_deleted)
  - 409 (not-ready status)
  - 404 (missing file)
  - 200 (success thumbnail)
  - 200 (success media)
  - Bearer auth (get_current_user_media)
  - Cookie auth (get_current_user_media)
  - 401 (no auth)

Run: pytest tests/unit/juggling/test_juggling_p4_routes.py -v
"""
from __future__ import annotations

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


# ── DB fixture ────────────────────────────────────────────────────────────────

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
        email=f"p4test+{uuid.uuid4().hex[:8]}@test.com",
        name="P4 Test Student",
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


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    from app.services.juggling import media_service
    monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
    return tmp_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_video(
    db_session,
    user_id: int,
    status: str = JugglingVideoStatus.analyzed.value,
    transcode_status: str | None = JugglingTranscodeStatus.done.value,
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


def _thumbnail_url(video_id) -> str:
    return f"/api/v1/users/me/juggling/videos/{video_id}/thumbnail"


def _media_url(video_id) -> str:
    return f"/api/v1/users/me/juggling/videos/{video_id}/media"


# ── get_current_user_media branches ──────────────────────────────────────────

def test_p4r01_bearer_auth_thumbnail(client, student_token, student_user, db_session, upload_dir):
    """P4R-01: Bearer token → authenticated → 200 thumbnail."""
    f = upload_dir / "thumb.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0")
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    r = client.get(_thumbnail_url(video.id), headers=_auth(student_token))
    assert r.status_code == 200

def test_p4r02_cookie_auth_thumbnail(client, student_token, student_user, db_session, upload_dir):
    """P4R-02: Cookie auth (no Bearer) → authenticated via cookie path → 200."""
    f = upload_dir / "thumb_cookie.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    client.cookies.set("access_token", f"Bearer {student_token}")
    r = client.get(_thumbnail_url(video.id))
    client.cookies.clear()
    assert r.status_code == 200

def test_p4r03_no_auth_thumbnail_401(client, student_user, db_session, upload_dir):
    """P4R-03: No auth at all → 401 (get_current_user_media raises)."""
    f = upload_dir / "t.jpg"
    f.write_bytes(b"\xff\xd8")
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    r = client.get(_thumbnail_url(video.id))
    assert r.status_code == 401

def test_p4r04_invalid_bearer_no_cookie_401(client, student_user, db_session, upload_dir):
    """P4R-04: Invalid Bearer + no cookie → credentials present but username=None → falls through to cookie → 401."""
    f = upload_dir / "t2.jpg"
    f.write_bytes(b"\xff\xd8")
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    r = client.get(_thumbnail_url(video.id), headers={"Authorization": "Bearer bad.token.here"})
    assert r.status_code == 401


# ── _get_video_or_404 branches ────────────────────────────────────────────────

def test_p4r05_thumbnail_video_not_found_404(client, student_token):
    """P4R-05: Unknown video_id → 404."""
    r = client.get(_thumbnail_url(uuid.uuid4()), headers=_auth(student_token))
    assert r.status_code == 404

def test_p4r06_thumbnail_gdpr_deleted_410(client, student_token, student_user, db_session):
    """P4R-06: gdpr_deleted video → 410."""
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
    r = client.get(_thumbnail_url(video.id), headers=_auth(student_token))
    assert r.status_code == 410

def test_p4r07_media_video_not_found_404(client, student_token):
    """P4R-07: Unknown video_id → 404."""
    r = client.get(_media_url(uuid.uuid4()), headers=_auth(student_token))
    assert r.status_code == 404

def test_p4r08_media_gdpr_deleted_410(client, student_token, student_user, db_session):
    """P4R-08: gdpr_deleted video → 410."""
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
    r = client.get(_media_url(video.id), headers=_auth(student_token))
    assert r.status_code == 410


# ── get_thumbnail — error branches ────────────────────────────────────────────

def test_p4r09_thumbnail_not_ready_409(client, student_token, student_user, db_session):
    """P4R-09: pending_upload status → ThumbnailNotReadyError → 409."""
    video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.pending_upload.value)
    r = client.get(_thumbnail_url(video.id), headers=_auth(student_token))
    assert r.status_code == 409

def test_p4r10_thumbnail_missing_path_none_404(client, student_token, student_user, db_session):
    """P4R-10: analyzed + thumbnail_path=None → ThumbnailMissingError → 404."""
    video = _make_video(db_session, student_user.id, thumbnail_path=None)
    r = client.get(_thumbnail_url(video.id), headers=_auth(student_token))
    assert r.status_code == 404

def test_p4r11_thumbnail_file_missing_on_disk_404(client, student_token, student_user, db_session, upload_dir):
    """P4R-11: analyzed + thumbnail_path set but file absent → ThumbnailMissingError → 404."""
    ghost = upload_dir / "ghost_thumb.jpg"
    video = _make_video(db_session, student_user.id, thumbnail_path=str(ghost))
    r = client.get(_thumbnail_url(video.id), headers=_auth(student_token))
    assert r.status_code == 404

def test_p4r12_thumbnail_path_safety_violation_404(client, student_token, student_user, db_session, upload_dir, tmp_path):
    """P4R-12: thumbnail_path outside JUGGLING_UPLOAD_DIR → PathSafetyError → 404."""
    safe_dir = upload_dir / "subdir"
    safe_dir.mkdir()
    from app.services.juggling import media_service
    import pytest as _pytest
    # Patch to a strict subdirectory so tmp_path root is "outside"
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(media_service, "_UPLOAD_DIR", safe_dir)
        outside = tmp_path / "outside_thumb.jpg"
        outside.write_bytes(b"\xff\xd8")
        video = _make_video(db_session, student_user.id, thumbnail_path=str(outside))
        r = client.get(_thumbnail_url(video.id), headers=_auth(student_token))
    assert r.status_code == 404


# ── get_media — error branches ────────────────────────────────────────────────

def test_p4r13_media_not_ready_409(client, student_token, student_user, db_session):
    """P4R-13: processing status → MediaNotReadyError → 409."""
    video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.processing.value)
    r = client.get(_media_url(video.id), headers=_auth(student_token))
    assert r.status_code == 409

def test_p4r14_media_file_missing_on_disk_404(client, student_token, student_user, db_session, upload_dir):
    """P4R-14: analyzed + processed_path set but file absent → MediaMissingError → 404."""
    ghost = upload_dir / "ghost_video.mp4"
    video = _make_video(db_session, student_user.id, processed_path=str(ghost))
    r = client.get(_media_url(video.id), headers=_auth(student_token))
    assert r.status_code == 404

def test_p4r15_media_path_safety_violation_404(client, student_token, student_user, db_session, upload_dir, tmp_path):
    """P4R-15: processed_path outside JUGGLING_UPLOAD_DIR → PathSafetyError → 404."""
    safe_dir = upload_dir / "subdir2"
    safe_dir.mkdir()
    from app.services.juggling import media_service
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(media_service, "_UPLOAD_DIR", safe_dir)
        outside = tmp_path / "outside_video.mp4"
        outside.write_bytes(b"\x00" * 10)
        video = _make_video(db_session, student_user.id, processed_path=str(outside))
        r = client.get(_media_url(video.id), headers=_auth(student_token))
    assert r.status_code == 404


# ── Success paths ─────────────────────────────────────────────────────────────

def test_p4r16_thumbnail_success_200(client, student_token, student_user, db_session, upload_dir):
    """P4R-16: analyzed + thumbnail_path exists → 200 image/jpeg."""
    f = upload_dir / "ok_thumb.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    video = _make_video(db_session, student_user.id, thumbnail_path=str(f))
    r = client.get(_thumbnail_url(video.id), headers=_auth(student_token))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")

def test_p4r17_media_success_200(client, student_token, student_user, db_session, upload_dir):
    """P4R-17: analyzed + done + processed_path exists → 200 video/mp4."""
    f = upload_dir / "ok_video.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(_media_url(video.id), headers=_auth(student_token))
    assert r.status_code == 200

def test_p4r18_bearer_auth_media(client, student_token, student_user, db_session, upload_dir):
    """P4R-18: Bearer auth for media → 200."""
    f = upload_dir / "bearer_media.mp4"
    f.write_bytes(b"\x00" * 50)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    r = client.get(_media_url(video.id), headers=_auth(student_token))
    assert r.status_code == 200

def test_p4r19_cookie_auth_media(client, student_token, student_user, db_session, upload_dir):
    """P4R-19: Cookie auth for media → 200."""
    f = upload_dir / "cookie_media.mp4"
    f.write_bytes(b"\x00" * 50)
    video = _make_video(db_session, student_user.id, processed_path=str(f))
    client.cookies.set("access_token", f"Bearer {student_token}")
    r = client.get(_media_url(video.id))
    client.cookies.clear()
    assert r.status_code == 200

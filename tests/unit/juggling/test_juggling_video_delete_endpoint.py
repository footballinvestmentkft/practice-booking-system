"""
Juggling media delete endpoint tests — VDE-01..20.

DELETE /api/v1/users/me/juggling/videos/{video_id}
  + related GET endpoint behaviour after media delete.

Isolation: TestClient with db_session override (SAVEPOINT rollback).
File paths set to non-existent values — _try_delete_file() treats
a missing file as already deleted (success), so no real filesystem writes.

Run: pytest tests/unit/juggling/test_juggling_video_delete_endpoint.py -v
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_access_token
from app.database import engine, get_db
from app.main import app
from app.models.juggling import (
    JugglingContactEvent,
    JugglingConsent,
    JugglingVideo,
    JugglingVideoStatus,
    JugglingTranscodeStatus,
)
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

    @sa_event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, txn):
        if txn.nested and not txn._parent.nested:
            sess.begin_nested()

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
        email=f"vde+{uuid.uuid4().hex[:8]}@test.com",
        name="VDE Test Student",
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
        email=f"vde-other+{uuid.uuid4().hex[:8]}@test.com",
        name="VDE Other User",
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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _delete_url(video_id: str) -> str:
    return f"/api/v1/users/me/juggling/videos/{video_id}"


def _make_video(
    db_session,
    user_id: int,
    status: str = JugglingVideoStatus.analyzed.value,
    transcode_status: str | None = JugglingTranscodeStatus.done.value,
    original_path: str | None = "/nonexistent/orig.mp4",
    processed_path: str | None = "/nonexistent/proc.mp4",
    thumbnail_path: str | None = "/nonexistent/thumb.jpg",
    quality_score: str | None = "0.87",
    quality_status: str | None = "acceptable",
    quality_detail: dict | None = None,
    server_detected_metadata: dict | None = None,
    annotation_status: str | None = "annotated",
    total_juggling_count: int | None = 12,
) -> JugglingVideo:
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type="in_app_capture",
        upload_source="camera",
        status=status,
        transcode_status=transcode_status,
        original_path=original_path,
        processed_path=processed_path,
        thumbnail_path=thumbnail_path,
        quality_score=quality_score,
        quality_status=quality_status,
        quality_detail=quality_detail or {"fps_detected": 30},
        server_detected_metadata=server_detected_metadata or {"fps": 30},
        annotation_status=annotation_status,
        total_juggling_count=total_juggling_count,
    )
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


def _make_consent(db_session, user_id: int) -> JugglingConsent:
    c = JugglingConsent(
        user_id=user_id,
        service_consent=True,
        training_consent=True,
        admin_review_consent=True,
    )
    db_session.add(c)
    db_session.commit()
    return c


def _make_contact_event(db_session, video: JugglingVideo, user_id: int) -> JugglingContactEvent:
    ev = JugglingContactEvent(
        id=uuid.uuid4(),
        video_id=video.id,
        created_by_user_id=user_id,
        device_event_id=uuid.uuid4(),
        timestamp_ms=1500,
        contact_type="right_foot_top",
        annotation_confidence="certain",
        annotation_source="manual_user",
    )
    db_session.add(ev)
    db_session.commit()
    return ev


# ── VDE-01..05: 204 from all active statuses ─────────────────────────────────

class TestDeleteVideoSuccess:
    def test_vde01_analyzed_video_delete_204(self, client, student_token, student_user, db_session):
        """VDE-01: analyzed video DELETE → 204."""
        video = _make_video(db_session, student_user.id, status=JugglingVideoStatus.analyzed.value)
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 204, r.text

    def test_vde02_uploaded_video_delete_204(self, client, student_token, student_user, db_session):
        """VDE-02: uploaded video DELETE → 204."""
        video = _make_video(
            db_session, student_user.id,
            status=JugglingVideoStatus.uploaded.value,
            transcode_status=None, processed_path=None, thumbnail_path=None,
            quality_score=None, quality_status=None, quality_detail=None,
            server_detected_metadata=None, annotation_status=None, total_juggling_count=None,
        )
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 204, r.text

    def test_vde03_rejected_video_delete_204(self, client, student_token, student_user, db_session):
        """VDE-03: rejected video DELETE → 204."""
        video = _make_video(
            db_session, student_user.id,
            status=JugglingVideoStatus.rejected.value,
            transcode_status=None, processed_path=None, thumbnail_path=None,
        )
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 204, r.text

    def test_vde04_failed_video_delete_204(self, client, student_token, student_user, db_session):
        """VDE-04: failed video DELETE → 204."""
        video = _make_video(
            db_session, student_user.id,
            status=JugglingVideoStatus.failed.value,
            transcode_status=None, processed_path=None, thumbnail_path=None,
            quality_score=None, quality_status=None, quality_detail=None,
            server_detected_metadata=None, annotation_status=None, total_juggling_count=None,
        )
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 204, r.text

    def test_vde05_processing_video_delete_204(self, client, student_token, student_user, db_session):
        """VDE-05: processing video DELETE → 204 (race guard handles in-flight task)."""
        video = _make_video(
            db_session, student_user.id,
            status=JugglingVideoStatus.processing.value,
            transcode_status=JugglingTranscodeStatus.processing.value,
            processed_path=None, thumbnail_path=None,
            quality_score=None, quality_status=None, quality_detail=None,
            server_detected_metadata=None, annotation_status=None, total_juggling_count=None,
        )
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 204, r.text


# ── VDE-06..07: Idempotency and terminal guard ────────────────────────────────

class TestDeleteVideoGuards:
    def test_vde06_media_deleted_idempotent_204(self, client, student_token, student_user, db_session):
        """VDE-06: DELETE on already media_deleted video → 204 (idempotent)."""
        video = _make_video(
            db_session, student_user.id,
            status=JugglingVideoStatus.media_deleted.value,
            original_path=None, processed_path=None, thumbnail_path=None,
        )
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 204, r.text

    def test_vde07_gdpr_deleted_410(self, client, student_token, student_user, db_session):
        """VDE-07: gdpr_deleted video → 410 Gone."""
        video = _make_video(
            db_session, student_user.id,
            status=JugglingVideoStatus.gdpr_deleted.value,
            original_path=None, processed_path=None, thumbnail_path=None,
        )
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 410, r.text


# ── VDE-08..10: 404 and body ──────────────────────────────────────────────────

class TestDeleteVideoNotFound:
    def test_vde08_other_user_video_404(self, client, student_token, other_user, db_session):
        """VDE-08: Another user's video → 404."""
        video = _make_video(db_session, other_user.id)
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 404, r.text

    def test_vde09_nonexistent_video_404(self, client, student_token):
        """VDE-09: Non-existent video_id → 404."""
        r = client.delete(_delete_url(str(uuid.uuid4())), headers=_auth(student_token))
        assert r.status_code == 404, r.text

    def test_vde10_response_body_empty(self, client, student_token, student_user, db_session):
        """VDE-10: Successful DELETE response body is empty (204 No Content)."""
        video = _make_video(db_session, student_user.id)
        r = client.delete(_delete_url(str(video.id)), headers=_auth(student_token))
        assert r.status_code == 204, r.text
        assert r.content == b"", f"Expected empty body, got: {r.content!r}"

    def test_vde20_unauthenticated_401(self, client, student_user, db_session):
        """VDE-20: No Bearer token → 401."""
        video = _make_video(db_session, student_user.id)
        r = client.delete(_delete_url(str(video.id)))
        assert r.status_code == 401, r.text


# ── VDE-11..12: Media and thumbnail → 410 after delete ───────────────────────

class TestMediaEndpointsAfterDelete:
    def test_vde11_get_media_410_after_delete(self, client, student_token, student_user, db_session):
        """VDE-11: GET /media → 410 after media delete."""
        video = _make_video(db_session, student_user.id)
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        r = client.get(
            f"/api/v1/users/me/juggling/videos/{video.id}/media",
            headers=_auth(student_token),
        )
        assert r.status_code == 410, r.text

    def test_vde12_get_thumbnail_410_after_delete(self, client, student_token, student_user, db_session):
        """VDE-12: GET /thumbnail → 410 after media delete."""
        video = _make_video(db_session, student_user.id)
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        r = client.get(
            f"/api/v1/users/me/juggling/videos/{video.id}/thumbnail",
            headers=_auth(student_token),
        )
        assert r.status_code == 410, r.text


# ── VDE-13..14: Quality and contacts still work after delete ──────────────────

class TestAnalysisEndpointsAfterDelete:
    def test_vde13_get_quality_200_after_delete(self, client, student_token, student_user, db_session):
        """VDE-13: GET /quality → 200 with preserved quality data after media delete."""
        video = _make_video(
            db_session, student_user.id,
            quality_score="0.91",
            quality_status="acceptable",
        )
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        r = client.get(
            f"/api/v1/users/me/juggling/videos/{video.id}/quality",
            headers=_auth(student_token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == JugglingVideoStatus.media_deleted.value
        assert body["quality_score"] == 0.91
        assert body["quality_status"] == "acceptable"

    def test_vde14_get_contacts_200_after_delete(self, client, student_token, student_user, db_session):
        """VDE-14: GET /contacts → 200 with contact events intact after media delete."""
        video = _make_video(db_session, student_user.id)
        _make_contact_event(db_session, video, student_user.id)
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        r = client.get(
            f"/api/v1/users/me/juggling/videos/{video.id}/contacts",
            headers=_auth(student_token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["events"]) == 1


# ── VDE-15..17: List endpoint archive behaviour ───────────────────────────────

class TestListAfterDelete:
    def test_vde15_list_excludes_media_deleted(self, client, student_token, student_user, db_session):
        """VDE-15: List endpoint does NOT return media_deleted video (user-facing delete = gone)."""
        video = _make_video(db_session, student_user.id)
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        r = client.get("/api/v1/users/me/juggling/videos", headers=_auth(student_token))
        assert r.status_code == 200, r.text
        ids = [v["video_id"] for v in r.json()["videos"]]
        assert str(video.id) not in ids

    def test_vde16_list_total_decrements_after_delete(self, client, student_token, student_user, db_session):
        """VDE-16: total count decreases by 1 after DELETE (media_deleted excluded from count)."""
        v1 = _make_video(db_session, student_user.id)
        v2 = _make_video(db_session, student_user.id)
        r_before = client.get("/api/v1/users/me/juggling/videos", headers=_auth(student_token))
        total_before = r_before.json()["total"]

        client.delete(_delete_url(str(v1.id)), headers=_auth(student_token))

        r_after = client.get("/api/v1/users/me/juggling/videos", headers=_auth(student_token))
        assert r_after.json()["total"] == total_before - 1

    def test_vde17_list_empty_after_deleting_only_video(self, client, student_token, student_user, db_session):
        """VDE-17: After deleting the only video, list returns videos=[] and total=0."""
        video = _make_video(db_session, student_user.id)
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        r = client.get("/api/v1/users/me/juggling/videos", headers=_auth(student_token))
        data = r.json()
        assert data["videos"] == []
        assert data["total"] == 0


# ── VDE-18..19: Data preservation ────────────────────────────────────────────

class TestDataPreservationAfterDelete:
    def test_vde18_analysis_data_unchanged_after_delete(self, client, student_token, student_user, db_session):
        """VDE-18: quality_score, quality_status, annotation_status, total_juggling_count
        are unchanged in the DB record after DELETE."""
        video = _make_video(
            db_session, student_user.id,
            quality_score="0.88",
            quality_status="acceptable",
            annotation_status="annotated",
            total_juggling_count=33,
        )
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        db_session.refresh(video)
        assert video.status == JugglingVideoStatus.media_deleted.value
        assert video.quality_score == "0.88"
        assert video.quality_status == "acceptable"
        assert video.annotation_status == "annotated"
        assert video.total_juggling_count == 33
        assert video.original_path is None
        assert video.processed_path is None
        assert video.thumbnail_path is None

    def test_vde19_contact_events_unchanged_after_delete(self, client, student_token, student_user, db_session):
        """VDE-19: Contact events remain active (deleted_at=None) after media delete."""
        video = _make_video(db_session, student_user.id)
        ev = _make_contact_event(db_session, video, student_user.id)
        client.delete(_delete_url(str(video.id)), headers=_auth(student_token))

        db_session.refresh(ev)
        assert ev.deleted_at is None
        assert ev.video_id == video.id

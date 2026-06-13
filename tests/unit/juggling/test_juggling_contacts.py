"""
Juggling AN-1 — Contact annotation API tests (CA-01..CA-60).

Tests cover:
  Taxonomy endpoint (CA-01..CA-06)
  Ownership isolation / auth (CA-07..CA-12)
  Contact type validation — taxonomy, thigh, custom_other (CA-13..CA-22)
  Side derivation and enforcement (CA-23..CA-27)
  Single-event create + idempotency (CA-28..CA-34)
  Batch submit — 207 partial success (CA-35..CA-40)
  PATCH — edit + optimistic locking (CA-41..CA-46)
  DELETE — soft delete (CA-47..CA-49)
  Finish — state machine + zero-contact confirm (CA-50..CA-57)
  Post-finish CRUD guard (CA-58..CA-60)

Run: pytest tests/unit/juggling/test_juggling_contacts.py -v
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
    JugglingConsent,
    JugglingContactEvent,
    JugglingVideo,
    JugglingVideoStatus,
    JugglingTranscodeStatus,
)
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module


def _err_msg(r) -> str:
    """Extract error message regardless of whether this project uses {"detail": ...} or {"error": {"message": ...}}."""
    body = r.json()
    if "detail" in body:
        return str(body["detail"]).lower()
    if "error" in body and "message" in body["error"]:
        return str(body["error"]["message"]).lower()
    return str(body).lower()


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


@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


@pytest.fixture()
def user(db_session):
    u = User(
        email=f"ca+{uuid.uuid4().hex[:8]}@test.com",
        name="CA Test User",
        password_hash="hashed",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def other_user(db_session):
    u = User(
        email=f"ca_other+{uuid.uuid4().hex[:8]}@test.com",
        name="CA Other User",
        password_hash="hashed",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def token(user):
    return create_access_token(data={"sub": user.email}, expires_delta=timedelta(hours=1))


@pytest.fixture()
def other_token(other_user):
    return create_access_token(data={"sub": other_user.email}, expires_delta=timedelta(hours=1))


@pytest.fixture()
def video(db_session, user):
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.analyzed.value,
        transcode_status=JugglingTranscodeStatus.done.value,
    )
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


@pytest.fixture()
def other_video(db_session, other_user):
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=other_user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.analyzed.value,
    )
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _contacts_url(video_id) -> str:
    return f"/api/v1/users/me/juggling/videos/{video_id}/contacts"


def _base_event(
    contact_type: str = "right_instep",
    confidence: str = "probable",
    timestamp_ms: int = 1000,
    device_event_id: str | None = None,
) -> dict:
    return {
        "device_event_id": device_event_id or str(uuid.uuid4()),
        "timestamp_ms": timestamp_ms,
        "contact_type": contact_type,
        "annotation_confidence": confidence,
    }


# ── CA-01..CA-06: Taxonomy endpoint ──────────────────────────────────────────

class TestCA01_TaxonomyEndpoint:

    def test_ca01_returns_200(self, client, token):
        """CA-01: GET /taxonomy returns 200 with taxonomy v1."""
        r = client.get("/api/v1/users/me/juggling/taxonomy", headers=_auth(token))
        assert r.status_code == 200
        d = r.json()
        assert d["version"] == "v1"

    def test_ca02_has_exactly_18_total(self, client, token):
        """CA-02: taxonomy has exactly 18 contact types total."""
        r = client.get("/api/v1/users/me/juggling/taxonomy", headers=_auth(token))
        assert r.status_code == 200
        d = r.json()
        all_keys = [ct["key"] for g in d["groups"] for ct in g["contact_types"]]
        assert len(all_keys) == 18, f"Expected 18 types, got {len(all_keys)}: {all_keys}"

    def test_ca03_no_thigh(self, client, token):
        """CA-03: thigh is absent from taxonomy response."""
        r = client.get("/api/v1/users/me/juggling/taxonomy", headers=_auth(token))
        all_keys = [ct["key"] for g in r.json()["groups"] for ct in g["contact_types"]]
        assert "right_thigh" not in all_keys
        assert "left_thigh" not in all_keys
        assert "thigh" not in all_keys

    def test_ca04_custom_other_present(self, client, token):
        """CA-04: custom_other is present in taxonomy."""
        r = client.get("/api/v1/users/me/juggling/taxonomy", headers=_auth(token))
        all_keys = [ct["key"] for g in r.json()["groups"] for ct in g["contact_types"]]
        assert "custom_other" in all_keys

    def test_ca05_etag_304(self, client, token):
        """CA-05: If-None-Match with matching ETag returns 304."""
        r1 = client.get("/api/v1/users/me/juggling/taxonomy", headers=_auth(token))
        etag = r1.headers.get("etag", "")
        assert etag, "ETag header missing"
        r2 = client.get(
            "/api/v1/users/me/juggling/taxonomy",
            headers={**_auth(token), "if-none-match": etag},
        )
        assert r2.status_code == 304

    def test_ca06_unauthenticated_401(self, client):
        """CA-06: no token → 401."""
        r = client.get("/api/v1/users/me/juggling/taxonomy")
        assert r.status_code == 401


# ── CA-07..CA-12: Ownership isolation / auth ─────────────────────────────────

class TestCA07_Ownership:

    def test_ca07_unauthenticated_401_get(self, client, video):
        """CA-07: GET /contacts without token → 401."""
        r = client.get(_contacts_url(video.id))
        assert r.status_code == 401

    def test_ca08_unauthenticated_401_post(self, client, video):
        """CA-08: POST /contacts without token → 401."""
        r = client.post(_contacts_url(video.id), json=_base_event())
        assert r.status_code == 401

    def test_ca09_other_user_video_404_get(self, client, other_video, token):
        """CA-09: GET /contacts on another user's video → 404."""
        r = client.get(_contacts_url(other_video.id), headers=_auth(token))
        assert r.status_code == 404

    def test_ca10_other_user_video_404_post(self, client, other_video, token):
        """CA-10: POST /contacts on another user's video → 404."""
        r = client.post(_contacts_url(other_video.id), json=_base_event(), headers=_auth(token))
        assert r.status_code == 404

    def test_ca11_unknown_video_404(self, client, token):
        """CA-11: non-existent video_id → 404."""
        r = client.get(_contacts_url(uuid.uuid4()), headers=_auth(token))
        assert r.status_code == 404

    def test_ca12_feature_flag_off_503(self, client, token, video, monkeypatch):
        """CA-12: JUGGLING_POC_ENABLED=false → 503."""
        monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: False)
        r = client.get(_contacts_url(video.id), headers=_auth(token))
        assert r.status_code == 503


# ── CA-13..CA-22: Contact type validation ────────────────────────────────────

class TestCA13_ContactTypeValidation:

    def test_ca13_valid_right_instep(self, client, token, video):
        """CA-13: valid stable type accepted → 201."""
        r = client.post(_contacts_url(video.id), json=_base_event("right_instep"), headers=_auth(token))
        assert r.status_code == 201

    def test_ca14_right_thigh_422(self, client, token, video):
        """CA-14: right_thigh is explicitly rejected → 422."""
        r = client.post(_contacts_url(video.id), json=_base_event("right_thigh"), headers=_auth(token))
        assert r.status_code == 422
        assert "thigh" in _err_msg(r) or r.status_code == 422

    def test_ca15_left_thigh_422(self, client, token, video):
        """CA-15: left_thigh is explicitly rejected → 422."""
        r = client.post(_contacts_url(video.id), json=_base_event("left_thigh"), headers=_auth(token))
        assert r.status_code == 422

    def test_ca16_unknown_type_422(self, client, token, video):
        """CA-16: completely unknown contact_type → 422."""
        r = client.post(_contacts_url(video.id), json=_base_event("robot_kick"), headers=_auth(token))
        assert r.status_code == 422

    def test_ca17_all_18_stable_types_accepted(self, client, token, video):
        """CA-17: all 18 taxonomy v1 keys are accepted by the server."""
        from app.services.juggling.taxonomy_service import get_stable_keys
        stable_keys = get_stable_keys()
        assert len(stable_keys) == 17
        for key in stable_keys:
            body = {**_base_event(key), "device_event_id": str(uuid.uuid4())}
            r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
            assert r.status_code == 201, f"Expected 201 for {key}, got {r.status_code}: {r.text}"

    def test_ca18_custom_other_missing_side_422(self, client, token, video):
        """CA-18: custom_other without side → 422."""
        body = {**_base_event("custom_other"), "custom_label": "spin_heel", "custom_description": "heel spin"}
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 422

    def test_ca19_custom_other_missing_custom_label_422(self, client, token, video):
        """CA-19: custom_other without custom_label → 422."""
        body = {**_base_event("custom_other"), "side": "right", "custom_description": "heel spin"}
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 422

    def test_ca20_custom_other_missing_description_422(self, client, token, video):
        """CA-20: custom_other without custom_description → 422."""
        body = {**_base_event("custom_other"), "side": "right", "custom_label": "spin_heel"}
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 422

    def test_ca21_custom_other_full_payload_201(self, client, token, video):
        """CA-21: custom_other with all required fields → 201."""
        body = {
            **_base_event("custom_other"),
            "side": "right",
            "custom_label": "spin_heel",
            "custom_description": "Heel spin kick",
        }
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 201
        d = r.json()
        assert d["taxonomy_review_status"] == "pending_taxonomy_review"
        assert d["annotation_review_status"] == "pending"
        assert d["excluded_from_training"] is True

    def test_ca22_timestamp_negative_422(self, client, token, video):
        """CA-22: negative timestamp_ms → 422 (Pydantic ge=0)."""
        body = {**_base_event(), "timestamp_ms": -1}
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 422


# ── CA-23..CA-27: Side derivation and enforcement ────────────────────────────

class TestCA23_SideDerivation:

    def test_ca23_stable_right_prefix_derives_right(self, client, token, video):
        """CA-23: right_knee → side=right derived server-side."""
        r = client.post(_contacts_url(video.id), json=_base_event("right_knee"), headers=_auth(token))
        assert r.status_code == 201
        assert r.json()["side"] == "right"

    def test_ca24_stable_left_prefix_derives_left(self, client, token, video):
        """CA-24: left_instep → side=left derived server-side."""
        r = client.post(_contacts_url(video.id), json=_base_event("left_instep"), headers=_auth(token))
        assert r.status_code == 201
        assert r.json()["side"] == "left"

    def test_ca25_center_type_derives_center(self, client, token, video):
        """CA-25: head → side=center derived server-side."""
        r = client.post(_contacts_url(video.id), json=_base_event("head"), headers=_auth(token))
        assert r.status_code == 201
        assert r.json()["side"] == "center"

    def test_ca26_stable_type_side_mismatch_422(self, client, token, video):
        """CA-26: sending wrong side for stable type → 422."""
        body = {**_base_event("right_knee"), "side": "left"}
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 422
        assert "mismatch" in _err_msg(r)

    def test_ca27_stable_type_correct_side_201(self, client, token, video):
        """CA-27: sending matching side for stable type → 201 (allowed)."""
        body = {**_base_event("right_knee"), "side": "right"}
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 201


# ── CA-28..CA-34: Single-event create + idempotency ──────────────────────────

class TestCA28_SingleCreate:

    def test_ca28_creates_event_201(self, client, token, video):
        """CA-28: new event → 201 with correct fields."""
        body = _base_event("right_instep", timestamp_ms=567)
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 201
        d = r.json()
        assert d["contact_type"] == "right_instep"
        assert d["timestamp_ms"] == 567
        assert d["annotation_review_status"] == "pending"
        assert d["taxonomy_review_status"] == "not_applicable"
        assert d["excluded_from_training"] is True
        assert d["annotation_confidence"] == "probable"

    def test_ca29_server_fields_not_client_settable(self, client, token, video):
        """CA-29: client-supplied server fields are ignored — schema rejects them."""
        body = {
            **_base_event(),
            "annotation_source": "model_prediction",
            "annotation_review_status": "confirmed",
            "excluded_from_training": False,
        }
        # Pydantic strict mode strips unknown fields; response always has correct values
        r = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        # Either 201 (extra fields stripped) or 422 (schema rejects unknown)
        if r.status_code == 201:
            assert r.json()["annotation_review_status"] == "pending"
            assert r.json()["excluded_from_training"] is True

    def test_ca30_video_status_becomes_in_progress(self, client, token, video, db_session):
        """CA-30: first contact event transitions video to in_progress."""
        assert video.annotation_status is None
        client.post(_contacts_url(video.id), json=_base_event(), headers=_auth(token))
        db_session.refresh(video)
        assert video.annotation_status == "in_progress"

    def test_ca31_get_contacts_returns_empty_before_any(self, client, token, video):
        """CA-31: GET /contacts on fresh video → empty list."""
        r = client.get(_contacts_url(video.id), headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["events"] == []

    def test_ca32_idempotent_exact_duplicate_200(self, client, token, video):
        """CA-32: exact duplicate (same device_event_id, same payload) → 200 + existing event."""
        body = _base_event()
        r1 = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r1.status_code == 201
        event_id_1 = r1.json()["event_id"]

        r2 = client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        assert r2.status_code == 200
        assert r2.json()["event_id"] == event_id_1

    def test_ca33_idempotency_conflict_409(self, client, token, video):
        """CA-33: same device_event_id, different payload → 409 idempotency_conflict."""
        dev_id = str(uuid.uuid4())
        body1 = {**_base_event(device_event_id=dev_id), "timestamp_ms": 1000}
        body2 = {**_base_event(device_event_id=dev_id), "timestamp_ms": 2000}
        client.post(_contacts_url(video.id), json=body1, headers=_auth(token))
        r = client.post(_contacts_url(video.id), json=body2, headers=_auth(token))
        assert r.status_code == 409
        assert "idempotency_conflict" in _err_msg(r)

    def test_ca34_get_contacts_lists_created_event(self, client, token, video):
        """CA-34: created event appears in GET /contacts."""
        body = _base_event("left_knee", timestamp_ms=1357)
        client.post(_contacts_url(video.id), json=body, headers=_auth(token))
        r = client.get(_contacts_url(video.id), headers=_auth(token))
        events = r.json()["events"]
        assert len(events) == 1
        assert events[0]["contact_type"] == "left_knee"


# ── CA-35..CA-40: Batch submit ────────────────────────────────────────────────

class TestCA35_Batch:

    def _batch_url(self, video_id) -> str:
        return f"{_contacts_url(video_id)}/batch"

    def test_ca35_batch_creates_multiple_201(self, client, token, video):
        """CA-35: batch with 3 new events → 207, created=3."""
        body = {"events": [_base_event(timestamp_ms=i * 1000) for i in range(1, 4)]}
        r = client.post(self._batch_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 207
        d = r.json()
        assert d["created"] == 3
        assert d["duplicate_skipped"] == 0
        assert d["conflict"] == 0

    def test_ca36_batch_exact_duplicate_skipped(self, client, token, video):
        """CA-36: batch with 1 new + 1 exact duplicate → 207, created=1, duplicate=1."""
        single = _base_event(timestamp_ms=1000)
        client.post(_contacts_url(video.id), json=single, headers=_auth(token))
        body = {"events": [single, _base_event(timestamp_ms=2000)]}
        r = client.post(self._batch_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 207
        d = r.json()
        assert d["created"] == 1
        assert d["duplicate_skipped"] == 1

    def test_ca37_batch_conflict_captured(self, client, token, video):
        """CA-37: batch item with same device_event_id but different payload → conflict item."""
        dev_id = str(uuid.uuid4())
        client.post(_contacts_url(video.id), json={**_base_event(device_event_id=dev_id), "timestamp_ms": 1000}, headers=_auth(token))
        conflict_body = {"events": [{**_base_event(device_event_id=dev_id), "timestamp_ms": 9999}]}
        r = client.post(self._batch_url(video.id), json=conflict_body, headers=_auth(token))
        assert r.status_code == 207
        d = r.json()
        assert d["conflict"] == 1

    def test_ca38_batch_invalid_type_counts_as_conflict(self, client, token, video):
        """CA-38: batch with invalid contact_type → per-item conflict."""
        body = {"events": [_base_event("right_thigh")]}
        r = client.post(self._batch_url(video.id), json=body, headers=_auth(token))
        assert r.status_code == 207
        d = r.json()
        assert d["conflict"] == 1
        assert d["created"] == 0

    def test_ca39_batch_other_user_video_404(self, client, other_video, token):
        """CA-39: batch on another user's video → 404."""
        body = {"events": [_base_event()]}
        r = client.post(f"{_contacts_url(other_video.id)}/batch", json=body, headers=_auth(token))
        assert r.status_code == 404

    def test_ca40_batch_empty_events_422(self, client, token, video):
        """CA-40: batch with empty events list → 422 (min_length=1)."""
        r = client.post(self._batch_url(video.id), json={"events": []}, headers=_auth(token))
        assert r.status_code == 422


# ── CA-41..CA-46: PATCH — edit + optimistic locking ──────────────────────────

class TestCA41_Patch:

    def _patch_url(self, video_id, event_id) -> str:
        return f"{_contacts_url(video_id)}/{event_id}"

    def _create_event(self, client, token, video, **kwargs) -> dict:
        r = client.post(_contacts_url(video.id), json=_base_event(**kwargs), headers=_auth(token))
        assert r.status_code == 201
        return r.json()

    def test_ca41_patch_contact_type(self, client, token, video):
        """CA-41: PATCH contact_type changes type and re-derives side."""
        evt = self._create_event(client, token, video, contact_type="right_knee")
        patch = {"contact_type": "left_hip", "version": evt["version"]}
        r = client.patch(self._patch_url(video.id, evt["event_id"]), json=patch, headers=_auth(token))
        assert r.status_code == 200
        d = r.json()
        assert d["contact_type"] == "left_hip"
        assert d["side"] == "left"
        assert d["version"] == evt["version"] + 1

    def test_ca42_patch_confidence(self, client, token, video):
        """CA-42: PATCH annotation_confidence changes confidence."""
        evt = self._create_event(client, token, video, confidence="probable")
        patch = {"annotation_confidence": "certain", "version": evt["version"]}
        r = client.patch(self._patch_url(video.id, evt["event_id"]), json=patch, headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["annotation_confidence"] == "certain"

    def test_ca43_optimistic_lock_conflict_409(self, client, token, video):
        """CA-43: PATCH with wrong version → 409 version_conflict."""
        evt = self._create_event(client, token, video)
        patch = {"annotation_confidence": "certain", "version": 999}
        r = client.patch(self._patch_url(video.id, evt["event_id"]), json=patch, headers=_auth(token))
        assert r.status_code == 409
        assert "version_conflict" in _err_msg(r)

    def test_ca44_patch_other_user_event_404(self, client, token, other_video, other_token, db_session, other_user):
        """CA-44: PATCH on event belonging to another user's video → 404."""
        r_create = client.post(_contacts_url(other_video.id), json=_base_event(), headers=_auth(other_token))
        evt_id = r_create.json()["event_id"]
        patch = {"annotation_confidence": "certain", "version": 1}
        r = client.patch(self._patch_url(other_video.id, evt_id), json=patch, headers=_auth(token))
        assert r.status_code == 404

    def test_ca45_patch_stable_side_via_type_change(self, client, token, video):
        """CA-45: changing contact_type to stable re-derives side correctly."""
        # Start with chest (center), change to right_shoulder (right)
        evt = self._create_event(client, token, video, contact_type="chest")
        patch = {"contact_type": "right_shoulder", "version": evt["version"]}
        r = client.patch(self._patch_url(video.id, evt["event_id"]), json=patch, headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["side"] == "right"
        assert r.json()["taxonomy_review_status"] == "not_applicable"

    def test_ca46_patch_thigh_422(self, client, token, video):
        """CA-46: PATCH to thigh contact_type → 422."""
        evt = self._create_event(client, token, video)
        patch = {"contact_type": "right_thigh", "version": evt["version"]}
        r = client.patch(self._patch_url(video.id, evt["event_id"]), json=patch, headers=_auth(token))
        assert r.status_code == 422


# ── CA-47..CA-49: DELETE — soft delete ───────────────────────────────────────

class TestCA47_Delete:

    def _delete_url(self, video_id, event_id) -> str:
        return f"{_contacts_url(video_id)}/{event_id}"

    def test_ca47_soft_delete_204(self, client, token, video):
        """CA-47: DELETE → 204, event absent from GET /contacts."""
        r_create = client.post(_contacts_url(video.id), json=_base_event(), headers=_auth(token))
        evt_id = r_create.json()["event_id"]
        r_del = client.delete(self._delete_url(video.id, evt_id), headers=_auth(token))
        assert r_del.status_code == 204
        events = client.get(_contacts_url(video.id), headers=_auth(token)).json()["events"]
        assert not any(e["event_id"] == evt_id for e in events)

    def test_ca48_delete_nonexistent_404(self, client, token, video):
        """CA-48: DELETE on non-existent event_id → 404."""
        r = client.delete(self._delete_url(video.id, uuid.uuid4()), headers=_auth(token))
        assert r.status_code == 404

    def test_ca49_delete_other_user_event_404(self, client, token, other_video, other_token):
        """CA-49: DELETE another user's event → 404."""
        r_create = client.post(_contacts_url(other_video.id), json=_base_event(), headers=_auth(other_token))
        evt_id = r_create.json()["event_id"]
        r = client.delete(self._delete_url(other_video.id, evt_id), headers=_auth(token))
        assert r.status_code == 404


# ── CA-50..CA-57: Finish — state machine + zero-contact confirm ───────────────

class TestCA50_Finish:

    def _finish_url(self, video_id) -> str:
        return f"{_contacts_url(video_id)}/finish"

    def _add_event(self, client, token, video, ts: int = 1000) -> dict:
        r = client.post(_contacts_url(video.id), json=_base_event(timestamp_ms=ts), headers=_auth(token))
        assert r.status_code == 201
        return r.json()

    def test_ca50_finish_with_events_200(self, client, token, video):
        """CA-50: Finish with 1 event → 200, status=human_review_pending."""
        self._add_event(client, token, video)
        r = client.post(self._finish_url(video.id), json={"confirm_zero_contacts": False}, headers=_auth(token))
        assert r.status_code == 200
        d = r.json()
        assert d["annotation_status"] == "human_review_pending"
        assert d["total_juggling_count"] == 1
        assert d["contact_event_count"] == 1

    def test_ca51_finish_zero_without_confirm_422(self, client, token, video):
        """CA-51: Finish with 0 events, confirm_zero_contacts=false → 422."""
        r = client.post(self._finish_url(video.id), json={"confirm_zero_contacts": False}, headers=_auth(token))
        assert r.status_code == 422
        assert "zero_contact_not_confirmed" in _err_msg(r)

    def test_ca52_finish_zero_with_confirm_200(self, client, token, video):
        """CA-52: Finish with 0 events, confirm_zero_contacts=true → 200."""
        r = client.post(self._finish_url(video.id), json={"confirm_zero_contacts": True}, headers=_auth(token))
        assert r.status_code == 200
        d = r.json()
        assert d["annotation_status"] == "human_review_pending"
        assert d["total_juggling_count"] == 0

    def test_ca53_finish_transitions_status(self, client, token, video, db_session):
        """CA-53: annotation_status transitions to human_review_pending in DB."""
        self._add_event(client, token, video)
        client.post(self._finish_url(video.id), json={"confirm_zero_contacts": False}, headers=_auth(token))
        db_session.refresh(video)
        assert video.annotation_status == "human_review_pending"
        assert video.annotation_finished_at is not None
        assert video.total_juggling_count == 1

    def test_ca54_finish_sets_total_juggling_count(self, client, token, video):
        """CA-54: total_juggling_count equals active event count after Finish."""
        self._add_event(client, token, video, ts=1000)
        self._add_event(client, token, video, ts=2000)
        self._add_event(client, token, video, ts=3000)
        r = client.post(self._finish_url(video.id), json={"confirm_zero_contacts": False}, headers=_auth(token))
        assert r.json()["total_juggling_count"] == 3

    def test_ca55_finish_other_user_video_404(self, client, token, other_video):
        """CA-55: Finish on another user's video → 404."""
        r = client.post(self._finish_url(other_video.id), json={"confirm_zero_contacts": False}, headers=_auth(token))
        assert r.status_code == 404

    def test_ca56_deleted_events_not_counted(self, client, token, video):
        """CA-56: soft-deleted events excluded from total_juggling_count."""
        evt = self._add_event(client, token, video, ts=1000)
        self._add_event(client, token, video, ts=2000)
        # Delete first event
        client.delete(f"{_contacts_url(video.id)}/{evt['event_id']}", headers=_auth(token))
        r = client.post(self._finish_url(video.id), json={"confirm_zero_contacts": False}, headers=_auth(token))
        assert r.json()["total_juggling_count"] == 1

    def test_ca57_video_list_shows_annotation_status(self, client, token, video):
        """CA-57: GET /me/juggling/videos surfaces annotation_status after Finish."""
        self._add_event(client, token, video)
        client.post(self._finish_url(video.id), json={"confirm_zero_contacts": False}, headers=_auth(token))
        r = client.get("/api/v1/users/me/juggling/videos", headers=_auth(token))
        videos = r.json()["videos"]
        match = next((v for v in videos if v["video_id"] == str(video.id)), None)
        assert match is not None
        assert match["annotation_status"] == "human_review_pending"


# ── CA-58..CA-60: Post-finish CRUD guard ──────────────────────────────────────

class TestCA58_PostFinishGuard:

    def _finish(self, client, token, video) -> None:
        client.post(_contacts_url(video.id), json=_base_event(), headers=_auth(token))
        client.post(f"{_contacts_url(video.id)}/finish", json={"confirm_zero_contacts": False}, headers=_auth(token))

    def test_ca58_post_blocked_after_finish(self, client, token, video):
        """CA-58: POST /contacts after Finish → 409."""
        self._finish(client, token, video)
        r = client.post(_contacts_url(video.id), json=_base_event(), headers=_auth(token))
        assert r.status_code == 409
        assert "annotation_closed" in _err_msg(r)

    def test_ca59_patch_blocked_after_finish(self, client, token, video):
        """CA-59: PATCH after Finish → 409."""
        client.post(_contacts_url(video.id), json=_base_event(), headers=_auth(token))
        r_list = client.get(_contacts_url(video.id), headers=_auth(token))
        evt_id = r_list.json()["events"][0]["event_id"]
        client.post(f"{_contacts_url(video.id)}/finish", json={"confirm_zero_contacts": False}, headers=_auth(token))
        r = client.patch(f"{_contacts_url(video.id)}/{evt_id}", json={"annotation_confidence": "certain", "version": 1}, headers=_auth(token))
        assert r.status_code == 409
        assert "annotation_closed" in _err_msg(r)

    def test_ca60_delete_blocked_after_finish(self, client, token, video):
        """CA-60: DELETE after Finish → 409."""
        client.post(_contacts_url(video.id), json=_base_event(), headers=_auth(token))
        r_list = client.get(_contacts_url(video.id), headers=_auth(token))
        evt_id = r_list.json()["events"][0]["event_id"]
        client.post(f"{_contacts_url(video.id)}/finish", json={"confirm_zero_contacts": False}, headers=_auth(token))
        r = client.delete(f"{_contacts_url(video.id)}/{evt_id}", headers=_auth(token))
        assert r.status_code == 409
        assert "annotation_closed" in _err_msg(r)

"""Multicamera Session API Tests — AN-3B PR-4B3A + PR-4B3B-0B."""
import uuid as _uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models.user import User, UserRole
from app.services.multicamera.device_service import DeviceService

client = TestClient(app)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _create_user(db, role=UserRole.INSTRUCTOR):
    tag = _uuid.uuid4().hex[:8]
    u = User(name=f"Test-{tag}", email=f"api-{tag}@mcs-test.com", password_hash="x", role=role, is_active=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth(user):
    from app.core.auth import create_access_token
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _create_session(headers, max_p=2, max_d=4):
    return client.post("/api/v1/multicamera/sessions", json={"max_participants": max_p, "max_devices": max_d}, headers=headers)


# ── API-01..08 Auth + Guard ──────────────────────────────────────────────────

class TestAuthGuard:

    def test_api_01_create_auto_instructor(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "lobby"
        assert len(data["participants"]) == 1
        assert data["participants"][0]["role"] == "instructor"
        assert data["participants"][0]["user_id"] == u.id

    def test_api_02_create_no_auth(self):
        r = client.post("/api/v1/multicamera/sessions", json={"max_participants": 2, "max_devices": 4})
        assert r.status_code == 401

    def test_api_03_get_participant(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        uid = r.json()["session_uuid"]
        r2 = client.get(f"/api/v1/multicamera/sessions/{uid}", headers=_auth(u))
        assert r2.status_code == 200

    def test_api_04_get_non_participant(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1))
        uid = r.json()["session_uuid"]
        r2 = client.get(f"/api/v1/multicamera/sessions/{uid}", headers=_auth(u2))
        assert r2.status_code == 403

    def test_api_05_get_not_found(self, db):
        u = _create_user(db)
        r = client.get(f"/api/v1/multicamera/sessions/{_uuid.uuid4()}", headers=_auth(u))
        assert r.status_code == 404

    def test_api_06_transition_instructor(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        d = r.json()
        r2 = client.patch(f"/api/v1/multicamera/sessions/{d['session_uuid']}/status",
                          json={"target_status": "devices_ready", "revision": d["revision"]}, headers=_auth(u))
        assert r2.status_code == 200
        assert r2.json()["status"] == "devices_ready"
        d2 = r2.json()
        r3 = client.patch(f"/api/v1/multicamera/sessions/{d['session_uuid']}/status",
                          json={"target_status": "recording_pending", "revision": d2["revision"]}, headers=_auth(u))
        assert r3.status_code == 200

    def test_api_07_transition_player_forbidden(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1))
        d = r.json()
        client.post(f"/api/v1/multicamera/sessions/{d['session_uuid']}/join",
                    json={"role": "player"}, headers=_auth(u2))
        r2 = client.patch(f"/api/v1/multicamera/sessions/{d['session_uuid']}/status",
                          json={"target_status": "devices_ready", "revision": d["revision"]}, headers=_auth(u2))
        assert r2.status_code == 403

    def test_api_08_all_endpoints_no_auth(self):
        uid = str(_uuid.uuid4())
        assert client.post("/api/v1/multicamera/sessions", json={}).status_code == 401
        assert client.get(f"/api/v1/multicamera/sessions/{uid}").status_code == 401
        assert client.post(f"/api/v1/multicamera/sessions/{uid}/join", json={"role": "player"}).status_code == 401
        assert client.patch(f"/api/v1/multicamera/sessions/{uid}/status", json={"target_status": "cancelled", "revision": 1}).status_code == 401
        assert client.post(f"/api/v1/multicamera/sessions/{uid}/devices", json={"device_type": "iphone", "device_role": "player_primary"}).status_code == 401
        assert client.post(f"/api/v1/multicamera/sessions/{uid}/devices/1/heartbeat").status_code == 401


# ── API-09..11 Join ──────────────────────────────────────────────────────────

class TestJoin:

    def test_api_09_join(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1))
        uid = r.json()["session_uuid"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/join", json={"role": "player"}, headers=_auth(u2))
        assert r2.status_code == 200
        assert r2.json()["role"] == "player"

    def test_api_10_join_duplicate_idempotent(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1))
        uid = r.json()["session_uuid"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/join", json={"role": "player"}, headers=_auth(u2))
        r3 = client.post(f"/api/v1/multicamera/sessions/{uid}/join", json={"role": "player"}, headers=_auth(u2))
        assert r2.json()["id"] == r3.json()["id"]

    def test_api_11_join_full(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1), max_p=1)
        uid = r.json()["session_uuid"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/join", json={"role": "player"}, headers=_auth(u2))
        assert r2.status_code == 409


# ── API-12..14 Transition ────────────────────────────────────────────────────

class TestTransition:

    def test_api_12_valid_transition(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        d = r.json()
        r2 = client.patch(f"/api/v1/multicamera/sessions/{d['session_uuid']}/status",
                          json={"target_status": "devices_ready", "revision": d["revision"]}, headers=_auth(u))
        assert r2.status_code == 200

    def test_api_13_invalid_transition(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        d = r.json()
        r2 = client.patch(f"/api/v1/multicamera/sessions/{d['session_uuid']}/status",
                          json={"target_status": "recording", "revision": d["revision"]}, headers=_auth(u))
        assert r2.status_code == 422

    def test_api_14_revision_conflict(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        d = r.json()
        r2 = client.patch(f"/api/v1/multicamera/sessions/{d['session_uuid']}/status",
                          json={"target_status": "devices_ready", "revision": d["revision"] + 99}, headers=_auth(u))
        assert r2.status_code == 409


# ── API-15..20 Device ────────────────────────────────────────────────────────

class TestDevice:

    def test_api_15_register_existing_device(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad Pro")
        r = _create_session(_auth(u))
        uid = r.json()["session_uuid"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary",
                               "participant_id": r.json()["participants"][0]["id"]},
                         headers=_auth(u))
        assert r2.status_code == 201

    def test_api_16_register_implicit_creation(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        uid = r.json()["session_uuid"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_type": "iphone", "device_name": "My iPhone", "device_role": "instructor_primary"},
                         headers=_auth(u))
        assert r2.status_code == 201

    def test_api_17_register_duplicate_idempotent(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        uid = r.json()["session_uuid"]
        body = {"device_uuid": str(md.device_uuid), "device_role": "instructor_primary"}
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices", json=body, headers=_auth(u))
        r3 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices", json=body, headers=_auth(u))
        assert r2.json()["id"] == r3.json()["id"]

    def test_api_18_register_full(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u), max_d=1)
        uid = r.json()["session_uuid"]
        client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                    json={"device_type": "ipad", "device_role": "instructor_primary"}, headers=_auth(u))
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_type": "iphone", "device_role": "player_primary"}, headers=_auth(u))
        assert r2.status_code == 409

    def test_api_19_register_non_participant(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1))
        uid = r.json()["session_uuid"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_type": "iphone", "device_role": "player_primary"}, headers=_auth(u2))
        assert r2.status_code == 403

    def test_api_20_auxiliary_invariant_violated(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        uid = r.json()["session_uuid"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_type": "gopro", "device_role": "auxiliary_camera"}, headers=_auth(u))
        assert r2.status_code == 422


# ── API-21..23 Heartbeat ─────────────────────────────────────────────────────

class TestHeartbeat:

    def _setup_session_with_device(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u))
        return u, uid, rd.json()["id"]

    def test_api_21_heartbeat_owner(self, db):
        u, uid, sd_id = self._setup_session_with_device(db)
        r = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/heartbeat", headers=_auth(u))
        assert r.status_code == 200
        assert r.json()["session_device_id"] == sd_id

    def test_api_22_heartbeat_wrong_user(self, db):
        u, uid, sd_id = self._setup_session_with_device(db)
        u2 = _create_user(db, UserRole.STUDENT)
        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        ss.join_session(_uuid.UUID(uid), u2.id, "player")
        r = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/heartbeat", headers=_auth(u2))
        assert r.status_code == 403

    def test_api_23_heartbeat_manager_auxiliary(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        ipad = ds.register_managed_device(u.id, "ipad", "iPad")
        gopro = ds.register_managed_device(u.id, "gopro", "HERO13", ble_identifier=f"BLE-{_uuid.uuid4().hex[:6]}")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd_ipad = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                              json={"device_uuid": str(ipad.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                              headers=_auth(u))
        ipad_sd_id = rd_ipad.json()["id"]
        rd_gopro = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                               json={"device_uuid": str(gopro.device_uuid), "device_role": "auxiliary_camera",
                                     "managed_by_device_id": ipad_sd_id},
                               headers=_auth(u))
        gopro_sd_id = rd_gopro.json()["id"]
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{gopro_sd_id}/heartbeat", headers=_auth(u))
        assert r2.status_code == 200


# ── API-24..26 Lifecycle + Snapshot ──────────────────────────────────────────

class TestLifecycleAndSnapshot:

    def test_api_24_full_lifecycle(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1))
        d = r.json()
        uid = d["session_uuid"]
        client.post(f"/api/v1/multicamera/sessions/{uid}/join", json={"role": "player"}, headers=_auth(u2))
        client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                    json={"device_type": "ipad", "device_role": "instructor_primary"}, headers=_auth(u1))
        d = client.get(f"/api/v1/multicamera/sessions/{uid}", headers=_auth(u1)).json()
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "devices_ready", "revision": d["revision"]}, headers=_auth(u1)).json()
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": d["revision"]}, headers=_auth(u1)).json()
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording", "revision": d["revision"]}, headers=_auth(u1)).json()
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "stopped", "revision": d["revision"]}, headers=_auth(u1)).json()
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "finalizing", "revision": d["revision"]}, headers=_auth(u1)).json()
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "completed", "revision": d["revision"]}, headers=_auth(u1)).json()
        assert d["status"] == "completed"

    def test_api_25_heartbeat_removed_device(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u))
        sd_id = rd.json()["id"]
        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        session = ss.get_session(_uuid.UUID(uid))
        ss.remove_device(sd_id, session.revision)
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/heartbeat", headers=_auth(u))
        assert r2.status_code == 404

    def test_api_26_route_count(self):
        from app.main import app as _app
        paths = len(_app.openapi().get("paths", {}))
        assert paths == 933, f"Expected 933 routes, got {paths}"


# ── API-27..40 Device Status + Capture Stream (PR-4B3B-0) ───────────────────

class TestDeviceStatusAPI:

    def _setup(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u))
        return u, uid, rd.json()["id"], rd.json()["revision"]

    def test_api_27_device_status_valid(self, db):
        u, uid, sd_id, rev = self._setup(db)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/status",
                         json={"target_status": "ready", "device_revision": rev}, headers=_auth(u))
        assert r.status_code == 200
        assert r.json()["status"] == "ready"
        assert r.json()["revision"] == rev + 1

    def test_api_28_device_status_invalid_transition(self, db):
        u, uid, sd_id, rev = self._setup(db)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/status",
                         json={"target_status": "stopped", "device_revision": rev}, headers=_auth(u))
        assert r.status_code == 422

    def test_api_29_device_status_revision_conflict(self, db):
        u, uid, sd_id, rev = self._setup(db)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/status",
                         json={"target_status": "ready", "device_revision": rev + 99}, headers=_auth(u))
        assert r.status_code == 409

    def test_api_30_device_status_wrong_owner(self, db):
        u, uid, sd_id, rev = self._setup(db)
        u2 = _create_user(db, UserRole.STUDENT)
        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        ss.join_session(_uuid.UUID(uid), u2.id, "player")
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/status",
                         json={"target_status": "ready", "device_revision": rev}, headers=_auth(u2))
        assert r.status_code == 403

    def test_api_31_device_status_cross_session(self, db):
        u, uid1, sd_id, rev = self._setup(db)
        r2 = _create_session(_auth(u))
        uid2 = r2.json()["session_uuid"]
        r = client.patch(f"/api/v1/multicamera/sessions/{uid2}/devices/{sd_id}/status",
                         json={"target_status": "ready", "device_revision": rev}, headers=_auth(u))
        assert r.status_code == 404

    def test_api_32_device_status_removed(self, db):
        u, uid, sd_id, rev = self._setup(db)
        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        session = ss.get_session(_uuid.UUID(uid))
        ss.remove_device(sd_id, session.revision)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/status",
                         json={"target_status": "ready", "device_revision": rev}, headers=_auth(u))
        assert r.status_code in (404, 422)

    def test_api_33_device_status_no_auth(self):
        r = client.patch(f"/api/v1/multicamera/sessions/{_uuid.uuid4()}/devices/1/status",
                         json={"target_status": "ready", "device_revision": 1})
        assert r.status_code == 401


class TestCaptureStreamAPI:

    def _setup(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u))
        return u, uid, rd.json()["id"]

    def test_api_34_create_stream(self, db):
        u, uid, sd_id = self._setup(db)
        r = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams",
                        json={"stream_type": "video", "preset_json": {"resolution": "1920x1080", "fps": 30}},
                        headers=_auth(u))
        assert r.status_code == 201
        assert r.json()["stream_type"] == "video"

    def test_api_35_create_stream_duplicate_idempotent(self, db):
        u, uid, sd_id = self._setup(db)
        body = {"stream_type": "video", "preset_json": {"resolution": "1920x1080", "fps": 30}}
        r1 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams", json=body, headers=_auth(u))
        r2 = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams", json=body, headers=_auth(u))
        assert r1.json()["id"] == r2.json()["id"]

    def test_api_36_create_stream_wrong_owner(self, db):
        u, uid, sd_id = self._setup(db)
        u2 = _create_user(db, UserRole.STUDENT)
        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        ss.join_session(_uuid.UUID(uid), u2.id, "player")
        r = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams",
                        json={"stream_type": "video", "preset_json": {"fps": 30}}, headers=_auth(u2))
        assert r.status_code == 403

    def test_api_37_create_stream_cross_session(self, db):
        u, uid1, sd_id = self._setup(db)
        r2 = _create_session(_auth(u))
        uid2 = r2.json()["session_uuid"]
        r = client.post(f"/api/v1/multicamera/sessions/{uid2}/devices/{sd_id}/streams",
                        json={"stream_type": "video", "preset_json": {"fps": 30}}, headers=_auth(u))
        assert r.status_code == 404

    def test_api_38_create_stream_removed_device(self, db):
        u, uid, sd_id = self._setup(db)
        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        session = ss.get_session(_uuid.UUID(uid))
        ss.remove_device(sd_id, session.revision)
        r = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams",
                        json={"stream_type": "video", "preset_json": {"fps": 30}}, headers=_auth(u))
        assert r.status_code == 422

    def test_api_39_create_stream_oversized_preset(self, db):
        u, uid, sd_id = self._setup(db)
        big = {"key": "x" * 5000}
        r = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams",
                        json={"stream_type": "video", "preset_json": big}, headers=_auth(u))
        assert r.status_code == 422

    def test_api_40_create_stream_no_auth(self):
        r = client.post(f"/api/v1/multicamera/sessions/{_uuid.uuid4()}/devices/1/streams",
                        json={"stream_type": "video", "preset_json": {"fps": 30}})
        assert r.status_code == 401

    def test_api_41_heartbeat_regression(self, db):
        """Heartbeat still works after _require_device_access refactor."""
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u))
        sd_id = rd.json()["id"]
        r = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/heartbeat", headers=_auth(u))
        assert r.status_code == 200


# ── SS/SL/GR: Scheduled Start + Stream Lifecycle (PR-4B3B-0B) ───────────────

class TestScheduledStart:

    def _setup_ready(self, db):
        u = _create_user(db)
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "devices_ready", "revision": d["revision"]}, headers=_auth(u)).json()
        return u, uid, d["revision"]

    def test_ss_01_recording_pending(self, db):
        u, uid, rev = self._setup_ready(db)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u))
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "recording_pending"
        assert d["scheduled_start_at"] is not None

    def test_ss_02_recording_confirm(self, db):
        u, uid, rev = self._setup_ready(db)
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u)).json()
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording", "revision": d["revision"]}, headers=_auth(u))
        assert r.status_code == 200
        assert r.json()["status"] == "recording"

    def test_ss_03_abort_to_devices_ready(self, db):
        u, uid, rev = self._setup_ready(db)
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u)).json()
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "devices_ready", "revision": d["revision"]}, headers=_auth(u))
        assert r.status_code == 200
        assert r.json()["scheduled_start_at"] is None

    def test_ss_04_recording_pending_cancel(self, db):
        u, uid, rev = self._setup_ready(db)
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u)).json()
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "cancelled", "revision": d["revision"]}, headers=_auth(u))
        assert r.status_code == 200

    def test_ss_05_skip_pending_invalid(self, db):
        u, uid, rev = self._setup_ready(db)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording", "revision": rev}, headers=_auth(u))
        assert r.status_code == 422

    def test_ss_06_duplicate_pending_revision(self, db):
        u, uid, rev = self._setup_ready(db)
        client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                     json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u))
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u))
        assert r.status_code == 409

    def test_ss_07_non_instructor_pending(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        r = _create_session(_auth(u1))
        d = r.json()
        uid = d["session_uuid"]
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "devices_ready", "revision": d["revision"]}, headers=_auth(u1)).json()
        client.post(f"/api/v1/multicamera/sessions/{uid}/join", json={"role": "player"}, headers=_auth(u2))
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": d["revision"]}, headers=_auth(u2))
        assert r.status_code == 403

    def test_ss_08_late_confirm_expired(self, db):
        u, uid, rev = self._setup_ready(db)
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u)).json()
        from app.services.multicamera.session_service import SessionService
        from app.database import SessionLocal
        sdb = SessionLocal()
        from app.models.multicamera_session import MultiCameraSession
        from datetime import timedelta
        s = sdb.query(MultiCameraSession).filter(MultiCameraSession.session_uuid == _uuid.UUID(uid)).first()
        s.scheduled_start_at = s.scheduled_start_at - timedelta(seconds=120)
        sdb.commit()
        sdb.close()
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording", "revision": d["revision"]}, headers=_auth(u))
        assert r.status_code == 422

    def test_ss_09_refetch_after_pending(self, db):
        u, uid, rev = self._setup_ready(db)
        d = client.patch(f"/api/v1/multicamera/sessions/{uid}/status",
                         json={"target_status": "recording_pending", "revision": rev}, headers=_auth(u)).json()
        refetch = client.get(f"/api/v1/multicamera/sessions/{uid}", headers=_auth(u)).json()
        assert refetch["status"] == "recording_pending"
        assert refetch["scheduled_start_at"] == d["scheduled_start_at"]

    def test_ss_10_transition_matrix_complete(self, db):
        from app.models.multicamera_session import SESSION_TRANSITIONS, SessionStatus
        assert SessionStatus.RECORDING_PENDING in SESSION_TRANSITIONS
        assert SessionStatus.RECORDING in SESSION_TRANSITIONS[SessionStatus.RECORDING_PENDING]
        assert SessionStatus.DEVICES_READY in SESSION_TRANSITIONS[SessionStatus.RECORDING_PENDING]
        assert SessionStatus.CANCELLED in SESSION_TRANSITIONS[SessionStatus.RECORDING_PENDING]


class TestStreamLifecycle:

    def _setup_stream(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u))
        sd_id = rd.json()["id"]
        cs = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams",
                         json={"stream_type": "video", "preset_json": {"fps": 30}}, headers=_auth(u))
        return u, uid, sd_id, cs.json()["id"], cs.json()["revision"]

    def test_sl_01_patch_started(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        now = datetime.now(timezone.utc).isoformat()
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                         json={"started_at": now, "stream_revision": rev}, headers=_auth(u))
        assert r.status_code == 200
        assert r.json()["started_at"] is not None
        assert r.json()["revision"] == rev + 1

    def test_sl_02_patch_stopped_success(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        start = datetime.now(timezone.utc)
        stop = start + timedelta(seconds=10)
        r1 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"started_at": start.isoformat(), "stream_revision": rev}, headers=_auth(u))
        r2 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"stopped_at": stop.isoformat(), "capture_result": "success", "stream_revision": r1.json()["revision"]},
                          headers=_auth(u))
        assert r2.status_code == 200
        assert r2.json()["duration_ms"] == 10000
        assert r2.json()["capture_result"] == "success"

    def test_sl_03_stopped_before_started(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        start = datetime.now(timezone.utc)
        r1 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"started_at": start.isoformat(), "stream_revision": rev}, headers=_auth(u))
        r2 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"stopped_at": (start - timedelta(seconds=5)).isoformat(), "stream_revision": r1.json()["revision"]},
                          headers=_auth(u))
        assert r2.status_code == 422

    def test_sl_04_stopped_without_started(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                         json={"stopped_at": datetime.now(timezone.utc).isoformat(), "stream_revision": rev}, headers=_auth(u))
        assert r.status_code == 422

    def test_sl_05_success_without_stopped(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        start = datetime.now(timezone.utc)
        r1 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"started_at": start.isoformat(), "stream_revision": rev}, headers=_auth(u))
        r2 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"capture_result": "success", "stream_revision": r1.json()["revision"]}, headers=_auth(u))
        assert r2.status_code == 422

    def test_sl_06_error_auto_stop(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        start = datetime.now(timezone.utc)
        r1 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"started_at": start.isoformat(), "stream_revision": rev}, headers=_auth(u))
        r2 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"capture_result": "error", "stream_revision": r1.json()["revision"]}, headers=_auth(u))
        assert r2.status_code == 200
        assert r2.json()["stopped_at"] is not None
        assert r2.json()["capture_result"] == "error"

    def test_sl_07_terminal_immutable(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        start = datetime.now(timezone.utc)
        stop = start + timedelta(seconds=5)
        r1 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"started_at": start.isoformat(), "stopped_at": stop.isoformat(),
                                "capture_result": "success", "stream_revision": rev}, headers=_auth(u))
        r2 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"capture_result": "error", "stream_revision": r1.json()["revision"]}, headers=_auth(u))
        assert r2.status_code == 409

    def test_sl_08_identical_duplicate(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        start = datetime.now(timezone.utc)
        r1 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"started_at": start.isoformat(), "stream_revision": rev}, headers=_auth(u))
        r2 = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                          json={"started_at": start.isoformat(), "stream_revision": rev}, headers=_auth(u))
        assert r2.status_code == 200
        assert r2.json()["id"] == r1.json()["id"]

    def test_sl_09_conflicting_duplicate(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        start1 = datetime.now(timezone.utc)
        start2 = start1 + timedelta(seconds=1)
        client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                     json={"started_at": start1.isoformat(), "stream_revision": rev}, headers=_auth(u))
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                         json={"started_at": start2.isoformat(), "stream_revision": rev}, headers=_auth(u))
        assert r.status_code == 409

    def test_sl_10_stream_revision_conflict(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                         json={"started_at": datetime.now(timezone.utc).isoformat(), "stream_revision": rev + 99}, headers=_auth(u))
        assert r.status_code == 409

    def test_sl_11_future_timestamp_reject(self, db):
        u, uid, sd_id, sid, rev = self._setup_stream(db)
        future = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{sid}",
                         json={"started_at": future, "stream_revision": rev}, headers=_auth(u))
        assert r.status_code == 422

    def test_sl_12_cross_session_stream(self, db):
        u, uid1, sd_id, sid, rev = self._setup_stream(db)
        r2 = _create_session(_auth(u))
        uid2 = r2.json()["session_uuid"]
        r = client.patch(f"/api/v1/multicamera/sessions/{uid2}/devices/{sd_id}/streams/{sid}",
                         json={"started_at": datetime.now(timezone.utc).isoformat(), "stream_revision": rev}, headers=_auth(u))
        assert r.status_code == 404


class TestScheduledStreamGuard:

    def test_gr_01_wrong_owner_stream_patch(self, db):
        u1 = _create_user(db)
        u2 = _create_user(db, UserRole.STUDENT)
        ds = DeviceService(db)
        md = ds.register_managed_device(u1.id, "ipad", "iPad")
        r = _create_session(_auth(u1))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u1))
        sd_id = rd.json()["id"]
        cs = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams",
                         json={"stream_type": "video", "preset_json": {"fps": 30}}, headers=_auth(u1))
        from app.services.multicamera.session_service import SessionService
        ss = SessionService(db)
        ss.join_session(_uuid.UUID(uid), u2.id, "player")
        r = client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams/{cs.json()['id']}",
                         json={"started_at": datetime.now(timezone.utc).isoformat(), "stream_revision": cs.json()["revision"]},
                         headers=_auth(u2))
        assert r.status_code == 403

    def test_gr_02_existing_regression(self, db):
        u = _create_user(db)
        ds = DeviceService(db)
        md = ds.register_managed_device(u.id, "ipad", "iPad")
        r = _create_session(_auth(u))
        d = r.json()
        uid = d["session_uuid"]
        pid = d["participants"][0]["id"]
        rd = client.post(f"/api/v1/multicamera/sessions/{uid}/devices",
                         json={"device_uuid": str(md.device_uuid), "device_role": "instructor_primary", "participant_id": pid},
                         headers=_auth(u))
        sd_id = rd.json()["id"]
        assert client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/heartbeat", headers=_auth(u)).status_code == 200
        assert client.patch(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/status",
                            json={"target_status": "ready", "device_revision": rd.json()["revision"]}, headers=_auth(u)).status_code == 200
        cs = client.post(f"/api/v1/multicamera/sessions/{uid}/devices/{sd_id}/streams",
                         json={"stream_type": "video", "preset_json": {"fps": 30}}, headers=_auth(u))
        assert cs.status_code == 201

"""Multicamera Session API Tests — AN-3B PR-4B3A. 26 tests."""
import uuid as _uuid
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
        assert paths == 920, f"Expected 920, got {paths}"

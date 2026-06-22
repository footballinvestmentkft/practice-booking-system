"""
Multicamera Session Contract Tests — AN-3B PR-4B2.

MCS-01..16  Session lifecycle
MCD-01..16  Device lifecycle + invariants
MCC-01..03  Capture stream
MCM-01..03  Migration
"""
import pytest
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.user import User, UserRole
from app.models.managed_device import ManagedDevice
from app.models.multicamera_session import (
    MultiCameraSession, SessionParticipant, SessionDevice, CaptureStream,
    SessionStatus, DeviceRole, DeviceStatus,
)
from app.services.multicamera.session_service import SessionService
from app.services.multicamera.device_service import DeviceService
from app.services.multicamera.exceptions import (
    SessionNotFoundError, InvalidTransitionError, RevisionConflictError,
    SessionFullError, CrossSessionReferenceError, DeviceNotFoundError,
    DeviceRoleViolationError, ParticipantNotFoundError,
)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def users(db):
    import uuid as _uuid
    tag = _uuid.uuid4().hex[:8]
    u1 = User(name="Instructor", email=f"mcs-inst-{tag}@mcs-test.com", password_hash="x", role=UserRole.INSTRUCTOR, is_active=True)
    u2 = User(name="Player", email=f"mcs-play-{tag}@mcs-test.com", password_hash="x", role=UserRole.STUDENT, is_active=True)
    db.add_all([u1, u2])
    db.flush()
    return u1, u2


@pytest.fixture()
def devices(db, users):
    u1, u2 = users
    ds = DeviceService(db)
    ipad = ds.register_managed_device(u1.id, "ipad", "iPad Pro")
    iphone = ds.register_managed_device(u2.id, "iphone", "iPhone 15")
    gopro = ds.register_managed_device(u1.id, "gopro", "HERO13", ble_identifier="GP24653383")
    return ipad, iphone, gopro


# ── MCS: Session lifecycle ───────────────────────────────────────────────────

class TestSessionLifecycle:

    def test_mcs_01_create_lobby(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        assert s.status == "lobby"
        assert s.revision == 1

    def test_mcs_02_join(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        p = ss.join_session(s.session_uuid, users[1].id, "player")
        assert p.user_id == users[1].id
        assert p.role == "player"

    def test_mcs_03_duplicate_join_idempotent(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        p1 = ss.join_session(s.session_uuid, users[1].id, "player")
        p2 = ss.join_session(s.session_uuid, users[1].id, "player")
        assert p1.id == p2.id

    def test_mcs_04_join_full(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id, max_participants=1)
        ss.join_session(s.session_uuid, users[0].id, "instructor")
        with pytest.raises(SessionFullError, match="participants"):
            ss.join_session(s.session_uuid, users[1].id, "player")

    def test_mcs_05_leave(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ss.join_session(s.session_uuid, users[1].id, "player")
        rev_before = ss.get_session(s.session_uuid).revision
        p = ss.leave_session(s.session_uuid, users[1].id)
        assert p.left_at is not None
        assert ss.get_session(s.session_uuid).revision == rev_before + 1

    def test_mcs_06_transition_lobby_to_devices_ready(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        s = ss.transition_session(s.session_uuid, "devices_ready", s.revision)
        assert s.status == "devices_ready"

    def test_mcs_07_invalid_transition_lobby_to_recording(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        with pytest.raises(InvalidTransitionError):
            ss.transition_session(s.session_uuid, "recording", s.revision)

    def test_mcs_08_transition_devices_ready_to_recording(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        s = ss.transition_session(s.session_uuid, "devices_ready", s.revision)
        s = ss.transition_session(s.session_uuid, "recording", s.revision)
        assert s.status == "recording"
        assert s.started_at is not None

    def test_mcs_09_revision_conflict(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        with pytest.raises(RevisionConflictError):
            ss.transition_session(s.session_uuid, "devices_ready", s.revision + 99)

    def test_mcs_10_recording_to_stopped(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        s = ss.transition_session(s.session_uuid, "devices_ready", s.revision)
        s = ss.transition_session(s.session_uuid, "recording", s.revision)
        s = ss.transition_session(s.session_uuid, "stopped", s.revision)
        assert s.status == "stopped"
        assert s.stopped_at is not None

    def test_mcs_11_full_lifecycle(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        s = ss.transition_session(s.session_uuid, "devices_ready", s.revision)
        s = ss.transition_session(s.session_uuid, "recording", s.revision)
        s = ss.transition_session(s.session_uuid, "stopped", s.revision)
        s = ss.transition_session(s.session_uuid, "finalizing", s.revision)
        s = ss.transition_session(s.session_uuid, "completed", s.revision)
        assert s.status == "completed"
        assert s.finalized_at is not None

    def test_mcs_12_cancel_from_lobby(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        s = ss.transition_session(s.session_uuid, "cancelled", s.revision)
        assert s.cancelled_at is not None

    def test_mcs_13_cancel_from_devices_ready(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        s = ss.transition_session(s.session_uuid, "devices_ready", s.revision)
        s = ss.transition_session(s.session_uuid, "cancelled", s.revision)
        assert s.status == "cancelled"

    def test_mcs_14_completed_is_terminal(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        s = ss.transition_session(s.session_uuid, "devices_ready", s.revision)
        s = ss.transition_session(s.session_uuid, "recording", s.revision)
        s = ss.transition_session(s.session_uuid, "stopped", s.revision)
        s = ss.transition_session(s.session_uuid, "finalizing", s.revision)
        s = ss.transition_session(s.session_uuid, "completed", s.revision)
        with pytest.raises(InvalidTransitionError):
            ss.transition_session(s.session_uuid, "lobby", s.revision)

    def test_mcs_15_get_session_nested(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ss.join_session(s.session_uuid, users[0].id, "instructor")
        ipad, _, _ = devices
        ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary",
                          participant_id=db.query(SessionParticipant).filter_by(session_id=s.id).first().id)
        full = ss.get_session(s.session_uuid)
        assert len(full.participants) >= 1
        assert len(full.devices) >= 1

    def test_mcs_16_calibration_json_nullable(self, db, users):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        assert s.calibration_json is None
        from app.schemas.multicamera_session import CalibrationPlaceholder
        cal = CalibrationPlaceholder()
        assert cal.schema_version == 1
        assert cal.world_origin_camera_id is None


# ── MCD: Device lifecycle + invariants ───────────────────────────────────────

class TestDeviceLifecycle:

    def test_mcd_01_register_device(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ss.join_session(s.session_uuid, users[0].id, "instructor")
        ipad, _, _ = devices
        p = db.query(SessionParticipant).filter_by(session_id=s.id).first()
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary", participant_id=p.id)
        assert sd.device_role == "instructor_primary"

    def test_mcd_02_duplicate_register_idempotent(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd1 = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        sd2 = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        assert sd1.id == sd2.id

    def test_mcd_03_max_devices(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id, max_devices=1)
        ipad, iphone, _ = devices
        ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        with pytest.raises(SessionFullError, match="devices"):
            ss.register_device(s.session_uuid, iphone.device_uuid, "player_primary")

    def test_mcd_04_device_status_transition(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        rev_before = sd.revision
        sd = ss.update_device_status(sd.id, "ready", sd.revision)
        assert sd.status == "ready"
        assert sd.revision == rev_before + 1

    def test_mcd_05_device_revision_conflict(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        with pytest.raises(RevisionConflictError):
            ss.update_device_status(sd.id, "ready", sd.revision + 99)

    def test_mcd_06_heartbeat_no_revision(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        rev_before = sd.revision
        ts = ss.heartbeat(sd.id)
        db.refresh(sd)
        assert sd.last_heartbeat is not None
        assert sd.revision == rev_before

    def test_mcd_07_remove_device(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        s = ss.get_session(s.session_uuid)
        sd = ss.remove_device(sd.id, s.revision)
        assert sd.removed_at is not None

    def test_mcd_08_gopro_auxiliary_valid(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, gopro = devices
        manager = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        aux = ss.register_device(s.session_uuid, gopro.device_uuid, "auxiliary_camera",
                                managed_by_device_id=manager.id)
        assert aux.participant_id is None
        assert aux.managed_by_device_id == manager.id

    def test_mcd_09_cross_session_participant(self, db, users, devices):
        ss = SessionService(db)
        s1 = ss.create_session(users[0].id)
        s2 = ss.create_session(users[0].id)
        p_in_s2 = ss.join_session(s2.session_uuid, users[0].id, "instructor")
        ipad, _, _ = devices
        with pytest.raises(CrossSessionReferenceError):
            ss.register_device(s1.session_uuid, ipad.device_uuid, "instructor_primary",
                             participant_id=p_in_s2.id)

    def test_mcd_10_managed_device_idempotent_ble(self, db, users):
        ds = DeviceService(db)
        d1 = ds.register_managed_device(users[0].id, "gopro", "HERO13", ble_identifier="BLE123")
        d2 = ds.register_managed_device(users[0].id, "gopro", "HERO13", ble_identifier="BLE123")
        assert d1.id == d2.id

    def test_mcd_11_managed_by_cross_session(self, db, users, devices):
        ss = SessionService(db)
        s1 = ss.create_session(users[0].id)
        s2 = ss.create_session(users[0].id)
        ipad, _, gopro = devices
        manager_in_s2 = ss.register_device(s2.session_uuid, ipad.device_uuid, "instructor_primary")
        with pytest.raises(CrossSessionReferenceError):
            ss.register_device(s1.session_uuid, gopro.device_uuid, "auxiliary_camera",
                             managed_by_device_id=manager_in_s2.id)

    def test_mcd_12_managed_by_chain_depth(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id, max_devices=4)
        ipad, iphone, gopro = devices
        manager = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        aux1 = ss.register_device(s.session_uuid, gopro.device_uuid, "auxiliary_camera",
                                 managed_by_device_id=manager.id)
        ds = DeviceService(db)
        gopro2 = ds.register_managed_device(users[0].id, "gopro", "HERO13-2", ble_identifier="BLE999")
        with pytest.raises(CrossSessionReferenceError, match="chain depth"):
            ss.register_device(s.session_uuid, gopro2.device_uuid, "auxiliary_camera",
                             managed_by_device_id=aux1.id)

    def test_mcd_13_auxiliary_with_participant_rejected(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, gopro = devices
        manager = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        p = ss.join_session(s.session_uuid, users[0].id, "instructor")
        with pytest.raises(DeviceRoleViolationError, match="auxiliary_camera must not have participant_id"):
            ss.register_device(s.session_uuid, gopro.device_uuid, "auxiliary_camera",
                             participant_id=p.id, managed_by_device_id=manager.id)

    def test_mcd_14_non_auxiliary_with_managed_by_rejected(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, iphone, _ = devices
        manager = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        with pytest.raises(DeviceRoleViolationError, match="must not have managed_by_device_id"):
            ss.register_device(s.session_uuid, iphone.device_uuid, "player_primary",
                             managed_by_device_id=manager.id)

    def test_mcd_15_heartbeat_removed_device(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        s = ss.get_session(s.session_uuid)
        ss.remove_device(sd.id, s.revision)
        with pytest.raises(DeviceNotFoundError):
            ss.heartbeat(sd.id)

    def test_mcd_16_removed_device_no_recording(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        sd = ss.update_device_status(sd.id, "ready", sd.revision)
        s = ss.get_session(s.session_uuid)
        ss.remove_device(sd.id, s.revision)
        with pytest.raises(InvalidTransitionError):
            ss.update_device_status(sd.id, "recording", sd.revision)


# ── MCC: Capture stream ──────────────────────────────────────────────────────

class TestCaptureStream:

    def test_mcc_01_create_stream(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        cs = ss.create_capture_stream(sd.id, "video", {"resolution": "1920x1080", "fps": 30})
        assert cs.stream_type == "video"
        assert cs.preset_json["fps"] == 30

    def test_mcc_02_duplicate_stream_idempotent(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, _, _ = devices
        sd = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        cs1 = ss.create_capture_stream(sd.id, "video", {"resolution": "1920x1080", "fps": 30})
        cs2 = ss.create_capture_stream(sd.id, "video", {"resolution": "1920x1080", "fps": 30})
        assert cs1.id == cs2.id

    def test_mcc_03_stream_different_device_ok(self, db, users, devices):
        ss = SessionService(db)
        s = ss.create_session(users[0].id)
        ipad, iphone, _ = devices
        sd1 = ss.register_device(s.session_uuid, ipad.device_uuid, "instructor_primary")
        sd2 = ss.register_device(s.session_uuid, iphone.device_uuid, "player_primary")
        cs1 = ss.create_capture_stream(sd1.id, "video", {"resolution": "1920x1080", "fps": 30})
        cs2 = ss.create_capture_stream(sd2.id, "video", {"resolution": "1920x1080", "fps": 30})
        assert cs1.id != cs2.id


# ── MCM: Migration ───────────────────────────────────────────────────────────

class TestMigration:

    def test_mcm_01_tables_exist(self, db):
        from sqlalchemy import inspect
        inspector = inspect(db.bind)
        tables = inspector.get_table_names()
        for t in ["managed_devices", "multicamera_sessions", "session_participants", "session_devices", "capture_streams"]:
            assert t in tables, f"Table {t} not found"

    def test_mcm_02_session_status_check(self, db):
        from sqlalchemy import text
        result = db.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname = 'ck_mcs_status'"
        )).fetchone()
        assert result is not None

    def test_mcm_03_device_role_check(self, db):
        from sqlalchemy import text
        result = db.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname = 'ck_sd_device_role'"
        )).fetchone()
        assert result is not None

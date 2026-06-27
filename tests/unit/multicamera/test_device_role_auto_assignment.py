"""ORCH-4 — Device Role Auto-Assignment + CA-E2E tests.

DRA-01..07: Backend _resolve_device_role logic.
CA-E2E-01..04: Full API flow — instructor + multi-player sessions.
"""
import uuid as _uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal
from app.models.user import User, UserRole

client = TestClient(app)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_user(db, role=UserRole.INSTRUCTOR):
    tag = _uuid.uuid4().hex[:8]
    u = User(
        name=f"DRA-{tag}", email=f"dra-{tag}@test.com",
        password_hash="x", role=role, is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth(user):
    from app.core.auth import create_access_token
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _create_session(headers):
    r = client.post(
        "/api/v1/multicamera/sessions",
        json={"max_participants": 4, "max_devices": 8},
        headers=headers,
    )
    assert r.status_code == 201
    return r.json()


def _join_session(session_uuid, headers, role="player"):
    r = client.post(
        f"/api/v1/multicamera/sessions/{session_uuid}/join",
        json={"role": role},
        headers=headers,
    )
    assert r.status_code in (200, 201), f"join_session failed: {r.status_code} {r.text}"
    return r.json()


def _get_session(session_uuid, headers):
    r = client.get(f"/api/v1/multicamera/sessions/{session_uuid}", headers=headers)
    assert r.status_code == 200
    return r.json()


def _register_device(session_uuid, headers, participant_id, hint_role="player_primary"):
    """Register a device with given role hint; returns the SessionDeviceDTO."""
    dev_uuid = str(_uuid.uuid4())
    r = client.post(
        f"/api/v1/multicamera/sessions/{session_uuid}/devices",
        json={
            "device_uuid": dev_uuid,
            "device_type": "iphone",
            "device_name": f"Test-{dev_uuid[:6]}",
            "device_role": hint_role,
            "participant_id": participant_id,
        },
        headers=headers,
    )
    assert r.status_code == 201, f"register_device failed: {r.status_code} {r.text}"
    return r.json()


# ── DRA: Device Role Auto-Assignment unit-style API tests ────────────────────

class TestDeviceRoleAutoAssignment:

    def test_dra_01_instructor_device_gets_instructor_primary(self, db):
        """Instructor participant → always instructor_primary regardless of hint."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]
        participant_id = session["participants"][0]["id"]

        sd = _register_device(uuid, _auth(instructor), participant_id, hint_role="player_primary")
        assert sd["device_role"] == "instructor_primary", (
            f"Expected instructor_primary, got {sd['device_role']}"
        )

    def test_dra_02_first_player_gets_player_primary(self, db):
        """First player device → player_primary."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        player = _create_user(db, UserRole.STUDENT)
        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]

        p = _join_session(uuid, _auth(player), role="player")
        sd = _register_device(uuid, _auth(player), p["id"], hint_role="player_primary")
        assert sd["device_role"] == "player_primary"

    def test_dra_03_second_player_gets_player_secondary(self, db):
        """Second player device → player_secondary even if client sends player_primary."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        p1_user = _create_user(db, UserRole.STUDENT)
        p2_user = _create_user(db, UserRole.STUDENT)
        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]

        p1 = _join_session(uuid, _auth(p1_user), role="player")
        _register_device(uuid, _auth(p1_user), p1["id"], hint_role="player_primary")

        p2 = _join_session(uuid, _auth(p2_user), role="player")
        sd2 = _register_device(uuid, _auth(p2_user), p2["id"], hint_role="player_primary")
        assert sd2["device_role"] == "player_secondary", (
            f"Expected player_secondary, got {sd2['device_role']}"
        )

    def test_dra_04_third_player_gets_player_secondary(self, db):
        """Third player device → player_secondary."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]

        players = [_create_user(db, UserRole.STUDENT) for _ in range(3)]
        for i, pu in enumerate(players):
            p = _join_session(uuid, _auth(pu), role="player")
            sd = _register_device(uuid, _auth(pu), p["id"])
            if i == 0:
                assert sd["device_role"] == "player_primary"
            else:
                assert sd["device_role"] == "player_secondary", (
                    f"Player {i+1}: expected player_secondary, got {sd['device_role']}"
                )

    def test_dra_05_player_gets_primary_even_when_instructor_present(self, db):
        """Player device registers in instructor-present session → still player_primary (first player)."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        player = _create_user(db, UserRole.STUDENT)
        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]

        # Register instructor device first
        inst_p_id = session["participants"][0]["id"]
        _register_device(uuid, _auth(instructor), inst_p_id, hint_role="instructor_primary")

        # Player joins and registers
        p = _join_session(uuid, _auth(player), role="player")
        sd = _register_device(uuid, _auth(player), p["id"], hint_role="player_primary")
        assert sd["device_role"] == "player_primary"

    def test_dra_06_client_hints_secondary_but_is_first_player_gets_primary(self, db):
        """Client sends player_secondary hint, but is first player → backend assigns player_primary."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        player = _create_user(db, UserRole.STUDENT)
        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]

        p = _join_session(uuid, _auth(player), role="player")
        sd = _register_device(uuid, _auth(player), p["id"], hint_role="player_secondary")
        assert sd["device_role"] == "player_primary", (
            "Backend must override player_secondary hint to player_primary for first player"
        )

    def test_dra_07_client_hints_primary_but_slot_taken_gets_secondary(self, db):
        """Client sends player_primary hint, slot already taken → backend assigns player_secondary."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        p1_user = _create_user(db, UserRole.STUDENT)
        p2_user = _create_user(db, UserRole.STUDENT)
        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]

        p1 = _join_session(uuid, _auth(p1_user), role="player")
        _register_device(uuid, _auth(p1_user), p1["id"], hint_role="player_primary")

        p2 = _join_session(uuid, _auth(p2_user), role="player")
        sd2 = _register_device(uuid, _auth(p2_user), p2["id"], hint_role="player_primary")
        assert sd2["device_role"] == "player_secondary", (
            "Backend must override player_primary hint to player_secondary when slot taken"
        )


# ── CA-E2E: Full Session Capture Authority E2E ───────────────────────────────

class TestCaptureAuthorityE2E:

    def test_ca_e2e_01_instructor_plus_one_player(self, db):
        """1 instructor + 1 player: instructor→instructor_primary, player→player_primary."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        player = _create_user(db, UserRole.STUDENT)

        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]
        inst_p_id = session["participants"][0]["id"]

        inst_sd = _register_device(uuid, _auth(instructor), inst_p_id, hint_role="instructor_primary")
        assert inst_sd["device_role"] == "instructor_primary"

        p = _join_session(uuid, _auth(player), role="player")
        player_sd = _register_device(uuid, _auth(player), p["id"], hint_role="player_primary")
        assert player_sd["device_role"] == "player_primary"

        full = _get_session(uuid, _auth(instructor))
        device_roles = {d["id"]: d["device_role"] for d in full["devices"]}
        assert device_roles[inst_sd["id"]] == "instructor_primary"
        assert device_roles[player_sd["id"]] == "player_primary"

    def test_ca_e2e_02_two_players_no_instructor(self, db):
        """2-player session (no instructor): first→player_primary, second→player_secondary.

        Uses service layer directly because the API always auto-joins the session
        creator as 'instructor'. This test models the pure player-only scenario.
        """
        import uuid as _uuid_mod
        from app.services.multicamera.session_service import SessionService
        from app.services.multicamera.device_service import DeviceService

        p1_user = _create_user(db, UserRole.STUDENT)
        p2_user = _create_user(db, UserRole.STUDENT)

        ss = SessionService(db)
        ds = DeviceService(db)

        session_obj = ss.create_session(p1_user.id)

        p1_part = ss.join_session(session_obj.session_uuid, p1_user.id, "player")
        p2_part = ss.join_session(session_obj.session_uuid, p2_user.id, "player")
        db.refresh(session_obj)

        dev1 = ds.register_managed_device_with_uuid(p1_user.id, _uuid_mod.uuid4(), "iphone", "P1")
        sd1 = ss.register_device(session_obj.session_uuid, dev1.device_uuid, "player_primary", p1_part.id)
        assert sd1.device_role == "player_primary"

        dev2 = ds.register_managed_device_with_uuid(p2_user.id, _uuid_mod.uuid4(), "iphone", "P2")
        sd2 = ss.register_device(session_obj.session_uuid, dev2.device_uuid, "player_primary", p2_part.id)
        assert sd2.device_role == "player_secondary", (
            f"Second player must get player_secondary, got {sd2.device_role}"
        )

    def test_ca_e2e_03_instructor_plus_two_players(self, db):
        """1 instructor + 2 players: instructor_primary + player_primary + player_secondary."""
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        p1_user = _create_user(db, UserRole.STUDENT)
        p2_user = _create_user(db, UserRole.STUDENT)

        session = _create_session(_auth(instructor))
        uuid = session["session_uuid"]
        inst_p_id = session["participants"][0]["id"]

        inst_sd = _register_device(uuid, _auth(instructor), inst_p_id, hint_role="instructor_primary")

        p1 = _join_session(uuid, _auth(p1_user), role="player")
        sd1 = _register_device(uuid, _auth(p1_user), p1["id"])

        p2 = _join_session(uuid, _auth(p2_user), role="player")
        sd2 = _register_device(uuid, _auth(p2_user), p2["id"])

        assert inst_sd["device_role"] == "instructor_primary"
        assert sd1["device_role"] == "player_primary"
        assert sd2["device_role"] == "player_secondary"

    def test_ca_e2e_04_removed_primary_slot_reopens_for_new_player(self, db):
        """After player_primary device is removed (service layer), next player gets player_primary."""
        import uuid as _uuid_mod
        from app.services.multicamera.session_service import SessionService
        from app.services.multicamera.device_service import DeviceService

        instructor = _create_user(db, UserRole.INSTRUCTOR)
        p1_user = _create_user(db, UserRole.STUDENT)
        p2_user = _create_user(db, UserRole.STUDENT)

        ss = SessionService(db)
        ds = DeviceService(db)

        # Create session with instructor (max_participants=4 to fit instructor + p1 + p2)
        session_obj = ss.create_session(instructor.id, max_participants=4, max_devices=8)
        ss.join_session(session_obj.session_uuid, instructor.id, "instructor")
        db.refresh(session_obj)

        # Player 1 joins and registers
        ss.join_session(session_obj.session_uuid, p1_user.id, "player")
        db.refresh(session_obj)
        p1_participant = next(
            p for p in session_obj.participants if p.user_id == p1_user.id
        )
        dev1 = ds.register_managed_device_with_uuid(
            p1_user.id, _uuid_mod.uuid4(), "iphone", "P1-Device"
        )
        sd1 = ss.register_device(
            session_obj.session_uuid, dev1.device_uuid,
            "player_primary", p1_participant.id
        )
        assert sd1.device_role == "player_primary"

        # Remove player_primary device
        db.refresh(session_obj)
        ss.remove_device(sd1.id, session_obj.revision)
        db.refresh(session_obj)

        # Player 2 joins and registers — slot should be open
        ss.join_session(session_obj.session_uuid, p2_user.id, "player")
        db.refresh(session_obj)
        p2_participant = next(
            p for p in session_obj.participants if p.user_id == p2_user.id
        )
        dev2 = ds.register_managed_device_with_uuid(
            p2_user.id, _uuid_mod.uuid4(), "iphone", "P2-Device"
        )
        sd2 = ss.register_device(
            session_obj.session_uuid, dev2.device_uuid,
            "player_primary", p2_participant.id
        )
        assert sd2.device_role == "player_primary", (
            "After player_primary removal, next player must get player_primary"
        )

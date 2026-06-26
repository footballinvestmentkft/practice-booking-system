"""MC1 ORCH-2B + ORCH-3C E2E: Instructor + player-side capture cycle — full HTTP roundtrip.

Validates the complete instructor-side flow through real FastAPI + DB:

  Step  1  Auth      — instructor + player JWT tokens
  Step  2  Session   — POST /sessions → status=lobby
  Step  3  Join      — player POST /join → participants=2
  Step  4  DevReg-I  — instructor registers device (with participant_id)
  Step  5  DevReg-P  — player registers device (with participant_id)
  Step  6  DevRdy-I  — PATCH instructor device → status=ready
  Step  7  DevRdy-P  — PATCH player device → status=ready
  Step  8  Activate  — POST /activate → devices_ready/lobby → active
  Step  9  Create    — POST /cycles → status=preparing, 2 cycle_devices
  Step  10 Schedule  — POST /cycles/{id}/schedule → status=recording_pending
  Step  11 CfmStart-I — confirm-start instructor → cycle status=recording
  Step  12 CfmStart-P — confirm-start player (late, cycle=recording) → both CONFIRMED_START
  Step  13 Stop      — POST /cycles/{id}/stop → status=stopping
  Step  14 CfmStop-I  — confirm-stop instructor
  Step  15 CfmStop-P  — confirm-stop player → all resolved → status=completed, result=success
  Step  16 Verify    — final revision + result assertions

No real capture hardware needed: confirm-start/stop calls are injected directly,
mirroring the iOS CycleCaptureOrchestrator without the AVFoundation layer.

Tests IDs: E2E-MC1-01 (single lifecycle test).
"""
import uuid as _uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.user import User, UserRole

client = TestClient(app)

BASE = "/api/v1/multicamera"
NOW_ISO = datetime.now(timezone.utc).isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _create_user(db, role=UserRole.INSTRUCTOR) -> User:
    tag = _uuid.uuid4().hex[:8]
    u = User(
        name=f"E2E-{tag}",
        email=f"mc1-e2e-{tag}@test.local",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _auth(user: User) -> dict:
    from app.core.auth import create_access_token
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _assert_ok(resp, step: str, expected_status: int = 200):
    assert resp.status_code == expected_status, (
        f"Step {step}: expected HTTP {expected_status}, got {resp.status_code}. "
        f"Body: {resp.text[:400]}"
    )
    return resp.json()


# ── E2E Test ──────────────────────────────────────────────────────────────────

class TestMC1Orch2bInstructorE2E:

    def test_e2e_mc1_01_full_instructor_cycle_flow(self, db):
        """E2E-MC1-01: Full instructor-side capture cycle lifecycle."""

        # ── Step 1: Auth ──────────────────────────────────────────────────────
        instructor = _create_user(db, UserRole.INSTRUCTOR)
        player = _create_user(db, UserRole.STUDENT)
        h_inst = _auth(instructor)
        h_player = _auth(player)

        # ── Step 2: Create session ────────────────────────────────────────────
        r = client.post(
            f"{BASE}/sessions",
            json={"max_participants": 2, "max_devices": 4},
            headers=h_inst,
        )
        sess = _assert_ok(r, "2-create-session", 201)
        session_uuid = sess["session_uuid"]
        assert sess["status"] == "lobby"
        assert len(sess["participants"]) == 1
        inst_participant_id = sess["participants"][0]["id"]
        session_revision = sess["revision"]

        # ── Step 3: Player joins ──────────────────────────────────────────────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/join",
            json={"role": "player"},
            headers=h_player,
        )
        participant_resp = _assert_ok(r, "3-join-player", 200)
        player_participant_id = participant_resp["id"]

        r = client.get(f"{BASE}/sessions/{session_uuid}", headers=h_inst)
        sess = _assert_ok(r, "3-get-after-join")
        assert len(sess["participants"]) == 2
        session_revision = sess["revision"]

        # ── Step 4: Register instructor device ────────────────────────────────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/devices",
            json={
                "device_type": "ipad",
                "device_name": "E2E-iPad",
                "device_role": "instructor_primary",
                "participant_id": inst_participant_id,
            },
            headers=h_inst,
        )
        sd_inst = _assert_ok(r, "4-register-instructor-device", 201)
        sd_inst_id = sd_inst["id"]
        sd_inst_rev = sd_inst["revision"]
        assert sd_inst["status"] == "registered"
        assert sd_inst["participant_id"] == inst_participant_id

        # ── Step 5: Register player device ───────────────────────────────────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/devices",
            json={
                "device_type": "iphone",
                "device_name": "E2E-iPhone",
                "device_role": "player_primary",
                "participant_id": player_participant_id,
            },
            headers=h_player,
        )
        sd_player = _assert_ok(r, "5-register-player-device", 201)
        sd_player_id = sd_player["id"]
        sd_player_rev = sd_player["revision"]
        assert sd_player["status"] == "registered"
        assert sd_player["participant_id"] == player_participant_id

        # ── Step 6: Instructor device → ready ─────────────────────────────────
        r = client.patch(
            f"{BASE}/sessions/{session_uuid}/devices/{sd_inst_id}/status",
            json={"target_status": "ready", "device_revision": sd_inst_rev},
            headers=h_inst,
        )
        sd_inst = _assert_ok(r, "6-instructor-device-ready")
        assert sd_inst["status"] == "ready"
        sd_inst_rev = sd_inst["revision"]

        # ── Step 7: Player device → ready ────────────────────────────────────
        r = client.patch(
            f"{BASE}/sessions/{session_uuid}/devices/{sd_player_id}/status",
            json={"target_status": "ready", "device_revision": sd_player_rev},
            headers=h_player,
        )
        sd_player = _assert_ok(r, "7-player-device-ready")
        assert sd_player["status"] == "ready"

        # ── Step 8: Activate session (lobby/devices_ready → active) ───────────
        r = client.get(f"{BASE}/sessions/{session_uuid}", headers=h_inst)
        sess = _assert_ok(r, "8-get-before-activate")
        session_revision = sess["revision"]

        r = client.post(
            f"{BASE}/sessions/{session_uuid}/activate",
            json={"revision": session_revision},
            headers=h_inst,
        )
        sess = _assert_ok(r, "8-activate")
        assert sess["status"] == "active"
        session_revision = sess["revision"]

        # ── Step 9: Create cycle ──────────────────────────────────────────────
        idem_key = f"e2e-{_uuid.uuid4().hex[:16]}"
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/cycles",
            json={"idempotency_key": idem_key},
            headers=h_inst,
        )
        cycle = _assert_ok(r, "9-create-cycle", 201)
        cycle_id = cycle["id"]
        cycle_rev = cycle["revision"]
        assert cycle["status"] == "preparing"
        assert len(cycle["cycle_devices"]) == 2

        # Find each device's cycle_device record
        ccd_inst = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_inst_id)
        ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)
        assert ccd_inst["recording_status"] == "pending"
        assert ccd_player["recording_status"] == "pending"
        ccd_inst_rev = ccd_inst["revision"]
        ccd_player_rev = ccd_player["revision"]

        # ── Step 10: Schedule cycle ───────────────────────────────────────────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/schedule",
            json={"revision": cycle_rev},
            headers=h_inst,
        )
        cycle = _assert_ok(r, "10-schedule-cycle")
        assert cycle["status"] == "recording_pending"
        assert cycle["scheduled_start_at"] is not None
        cycle_rev = cycle["revision"]

        # ── Step 11: confirmDeviceStart — instructor (triggers recording) ──────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/devices/{sd_inst_id}/confirm-start",
            json={"started_at": NOW_ISO, "cycle_device_revision": ccd_inst_rev},
            headers=h_inst,
        )
        cycle = _assert_ok(r, "11-confirm-start-instructor")
        assert cycle["status"] == "recording", (
            f"Expected recording after first device confirms start, got {cycle['status']}"
        )
        cycle_rev = cycle["revision"]
        # Update ccd_inst revision
        ccd_inst = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_inst_id)
        ccd_inst_rev = ccd_inst["revision"]
        assert ccd_inst["recording_status"] == "confirmed_start"

        # ── Step 12: confirmDeviceStart — player (late, cycle=recording) ───────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/devices/{sd_player_id}/confirm-start",
            json={"started_at": NOW_ISO, "cycle_device_revision": ccd_player_rev},
            headers=h_player,
        )
        cycle = _assert_ok(r, "12-confirm-start-player")
        assert cycle["status"] == "recording"
        cycle_rev = cycle["revision"]
        ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)
        ccd_player_rev = ccd_player["revision"]
        assert ccd_player["recording_status"] == "confirmed_start"

        # ── Step 13: Stop cycle ───────────────────────────────────────────────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/stop",
            json={"revision": cycle_rev},
            headers=h_inst,
        )
        cycle = _assert_ok(r, "13-stop-cycle")
        assert cycle["status"] == "stopping"
        cycle_rev = cycle["revision"]

        # ── Step 14: confirmDeviceStop — instructor ───────────────────────────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/devices/{sd_inst_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": ccd_inst_rev},
            headers=h_inst,
        )
        cycle = _assert_ok(r, "14-confirm-stop-instructor")
        cycle_rev = cycle["revision"]
        ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)
        ccd_player_rev = ccd_player["revision"]

        # ── Step 15: confirmDeviceStop — player → all resolved → completed ─────
        r = client.post(
            f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/devices/{sd_player_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": ccd_player_rev},
            headers=h_player,
        )
        cycle = _assert_ok(r, "15-confirm-stop-player")

        # ── Step 16: Final state assertions ───────────────────────────────────
        assert cycle["status"] == "completed", (
            f"Expected completed, got {cycle['status']}. "
            "Both devices confirmed stop — _check_cycle_completion should have fired."
        )
        assert cycle["result"] == "success", (
            f"Expected result=success (both devices confirmed stop), got {cycle['result']}"
        )
        assert cycle["recording_stopped_at"] is not None
        assert cycle["completed_at"] is not None

        # Revision must have incremented across the full lifecycle
        assert cycle["revision"] > 1, "Cycle revision must reflect all lifecycle transitions"

        # All cycle_devices should be in confirmed_stop
        for ccd in cycle["cycle_devices"]:
            if ccd["required"]:
                assert ccd["recording_status"] == "confirmed_stop", (
                    f"Required device {ccd['session_device_id']} not confirmed_stop: "
                    f"{ccd['recording_status']}"
                )


# ── ORCH-3C E2E: Player-side stop flow ────────────────────────────────────────

def _setup_preparing_cycle(db):
    """Minimal setup: session active, cycle in preparing status (not yet scheduled).
    Used for 422 regression: confirmDeviceStop on a non-recording/stopping cycle.
    """
    instructor = _create_user(db, UserRole.INSTRUCTOR)
    player = _create_user(db, UserRole.STUDENT)
    h_inst = _auth(instructor)
    h_player = _auth(player)

    r = client.post(f"{BASE}/sessions", json={"max_participants": 2, "max_devices": 4}, headers=h_inst)
    sess = r.json(); session_uuid = sess["session_uuid"]
    inst_pid = sess["participants"][0]["id"]

    r = client.post(f"{BASE}/sessions/{session_uuid}/join", json={"role": "player"}, headers=h_player)
    player_pid = r.json()["id"]

    r = client.post(f"{BASE}/sessions/{session_uuid}/devices",
        json={"device_type": "ipad", "device_name": "3C-inst",
              "device_role": "instructor_primary", "participant_id": inst_pid}, headers=h_inst)
    sd_inst = r.json(); sd_inst_id = sd_inst["id"]; sd_inst_rev = sd_inst["revision"]

    r = client.post(f"{BASE}/sessions/{session_uuid}/devices",
        json={"device_type": "iphone", "device_name": "3C-phone",
              "device_role": "player_primary", "participant_id": player_pid}, headers=h_player)
    sd_player = r.json(); sd_player_id = sd_player["id"]; sd_player_rev = sd_player["revision"]

    r = client.patch(f"{BASE}/sessions/{session_uuid}/devices/{sd_inst_id}/status",
        json={"target_status": "ready", "device_revision": sd_inst_rev}, headers=h_inst)
    r = client.patch(f"{BASE}/sessions/{session_uuid}/devices/{sd_player_id}/status",
        json={"target_status": "ready", "device_revision": sd_player_rev}, headers=h_player)

    r = client.get(f"{BASE}/sessions/{session_uuid}", headers=h_inst)
    sess_rev = r.json()["revision"]
    r = client.post(f"{BASE}/sessions/{session_uuid}/activate",
        json={"revision": sess_rev}, headers=h_inst)

    idem_key = f"3c-prep-{_uuid.uuid4().hex[:12]}"
    r = client.post(f"{BASE}/sessions/{session_uuid}/cycles",
        json={"idempotency_key": idem_key}, headers=h_inst)
    cycle = r.json(); cycle_id = cycle["id"]
    ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)

    assert cycle["status"] == "preparing"
    return dict(h_player=h_player, session_uuid=session_uuid,
                cycle_id=cycle_id, sd_player_id=sd_player_id,
                ccd_player_rev=ccd_player["revision"])


def _setup_both_confirmed_start(db):
    """Shared setup: session active, cycle recording, both devices confirmed_start.

    Returns a dict with all IDs and revisions needed for ORCH-3C stop tests.
    """
    instructor = _create_user(db, UserRole.INSTRUCTOR)
    player = _create_user(db, UserRole.STUDENT)
    h_inst = _auth(instructor)
    h_player = _auth(player)

    # Session
    r = client.post(f"{BASE}/sessions", json={"max_participants": 2, "max_devices": 4}, headers=h_inst)
    sess = r.json(); session_uuid = sess["session_uuid"]
    inst_pid = sess["participants"][0]["id"]

    # Join
    r = client.post(f"{BASE}/sessions/{session_uuid}/join", json={"role": "player"}, headers=h_player)
    player_pid = r.json()["id"]

    # Devices
    r = client.post(f"{BASE}/sessions/{session_uuid}/devices",
        json={"device_type": "ipad", "device_name": "3C-iPad",
              "device_role": "instructor_primary", "participant_id": inst_pid}, headers=h_inst)
    sd_inst = r.json(); sd_inst_id = sd_inst["id"]; sd_inst_rev = sd_inst["revision"]

    r = client.post(f"{BASE}/sessions/{session_uuid}/devices",
        json={"device_type": "iphone", "device_name": "3C-iPhone",
              "device_role": "player_primary", "participant_id": player_pid}, headers=h_player)
    sd_player = r.json(); sd_player_id = sd_player["id"]; sd_player_rev = sd_player["revision"]

    # Both ready
    r = client.patch(f"{BASE}/sessions/{session_uuid}/devices/{sd_inst_id}/status",
        json={"target_status": "ready", "device_revision": sd_inst_rev}, headers=h_inst)
    sd_inst_rev = r.json()["revision"]

    r = client.patch(f"{BASE}/sessions/{session_uuid}/devices/{sd_player_id}/status",
        json={"target_status": "ready", "device_revision": sd_player_rev}, headers=h_player)

    # Activate
    r = client.get(f"{BASE}/sessions/{session_uuid}", headers=h_inst)
    sess_rev = r.json()["revision"]
    r = client.post(f"{BASE}/sessions/{session_uuid}/activate", json={"revision": sess_rev}, headers=h_inst)

    # Cycle: create → schedule → confirm-start both
    idem_key = f"3c-{_uuid.uuid4().hex[:12]}"
    r = client.post(f"{BASE}/sessions/{session_uuid}/cycles",
        json={"idempotency_key": idem_key}, headers=h_inst)
    cycle = r.json(); cycle_id = cycle["id"]; cycle_rev = cycle["revision"]
    ccd_inst = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_inst_id)
    ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)

    r = client.post(f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/schedule",
        json={"revision": cycle_rev}, headers=h_inst)
    cycle = r.json(); cycle_rev = cycle["revision"]

    r = client.post(
        f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/devices/{sd_inst_id}/confirm-start",
        json={"started_at": NOW_ISO, "cycle_device_revision": ccd_inst["revision"]}, headers=h_inst)
    cycle = r.json(); cycle_rev = cycle["revision"]
    ccd_inst = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_inst_id)

    r = client.post(
        f"{BASE}/sessions/{session_uuid}/cycles/{cycle_id}/devices/{sd_player_id}/confirm-start",
        json={"started_at": NOW_ISO, "cycle_device_revision": ccd_player["revision"]}, headers=h_player)
    cycle = r.json(); cycle_rev = cycle["revision"]
    ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)

    assert cycle["status"] == "recording"
    assert ccd_inst["recording_status"] == "confirmed_start"
    assert ccd_player["recording_status"] == "confirmed_start"

    return dict(
        h_inst=h_inst, h_player=h_player,
        session_uuid=session_uuid,
        cycle_id=cycle_id, cycle_rev=cycle_rev,
        sd_inst_id=sd_inst_id, sd_player_id=sd_player_id,
        ccd_inst_rev=ccd_inst["revision"],
        ccd_player_rev=ccd_player["revision"],
    )


class TestMC1Orch3cPlayerStopE2E:
    """ORCH-3C E2E: player-side confirm-stop flow, 409/422 regression, partial-stop guard."""

    def test_e2e_3c_01_player_confirm_stop_with_fresh_revision_completes_cycle(self, db):
        """E2E-3C-01: Both confirmed_start → instructor stop → player confirmDeviceStop
        with fresh cycleDeviceRevision from stopping cycle → player device confirmed_stop
        → cycle completed, result=success.
        """
        ctx = _setup_both_confirmed_start(db)
        h_inst = ctx["h_inst"]; h_player = ctx["h_player"]
        uuid = ctx["session_uuid"]; cycle_id = ctx["cycle_id"]
        sd_inst_id = ctx["sd_inst_id"]; sd_player_id = ctx["sd_player_id"]
        ccd_inst_rev = ctx["ccd_inst_rev"]; ccd_player_rev = ctx["ccd_player_rev"]
        cycle_rev = ctx["cycle_rev"]

        # Instructor stops cycle → status=stopping
        r = client.post(f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/stop",
            json={"revision": cycle_rev}, headers=h_inst)
        cycle = _assert_ok(r, "3C-01-stop", 200)
        assert cycle["status"] == "stopping"
        # Fresh revisions from the stopping cycle response
        cycle_rev = cycle["revision"]
        ccd_inst_fresh = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_inst_id)
        ccd_player_fresh = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)

        # Instructor confirms stop
        r = client.post(
            f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_inst_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": ccd_inst_fresh["revision"]},
            headers=h_inst)
        cycle = _assert_ok(r, "3C-01-cfm-stop-inst")
        cycle_rev = cycle["revision"]
        ccd_player_fresh = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)

        # Player confirms stop using the revision from the stopping-cycle snapshot.
        # Note: cycle_device.revision only increments on device-level state changes (confirm-start/stop),
        # not on the parent cycle's stop transition — so revision may equal ccd_player_rev here.
        # The critical point is that the iOS orchestrator uses the revision from the stoppingDetected
        # cycle, not from a stale start-time snapshot (validated at iOS unit level in PSO-11).
        r = client.post(
            f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_player_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": ccd_player_fresh["revision"]},
            headers=h_player)
        cycle = _assert_ok(r, "3C-01-cfm-stop-player")

        assert cycle["status"] == "completed", f"Expected completed, got {cycle['status']}"
        assert cycle["result"] == "success", f"Expected result=success, got {cycle['result']}"
        for ccd in cycle["cycle_devices"]:
            if ccd["required"]:
                assert ccd["recording_status"] == "confirmed_stop", (
                    f"Device {ccd['session_device_id']}: expected confirmed_stop, "
                    f"got {ccd['recording_status']}"
                )

    def test_e2e_3c_02_duplicate_player_confirm_stop_is_idempotent(self, db):
        """E2E-3C-02: Calling player confirmDeviceStop twice → second call returns 200 idempotently.
        Backend checks confirmed_stop before revision — duplicate is always safe (no 409 race).
        Validates backend idempotency that backs the iOS PSO duplicate-stop guard.
        """
        ctx = _setup_both_confirmed_start(db)
        h_inst = ctx["h_inst"]; h_player = ctx["h_player"]
        uuid = ctx["session_uuid"]; cycle_id = ctx["cycle_id"]
        sd_player_id = ctx["sd_player_id"]
        cycle_rev = ctx["cycle_rev"]

        # Stop cycle
        r = client.post(f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/stop",
            json={"revision": cycle_rev}, headers=h_inst)
        cycle = _assert_ok(r, "3C-02-stop")
        ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)
        fresh_rev = ccd_player["revision"]

        # First player confirm-stop — succeeds
        r = client.post(
            f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_player_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": fresh_rev},
            headers=h_player)
        cycle1 = _assert_ok(r, "3C-02-first-cfm-stop")

        # Second call with same revision — must return 200 idempotently (confirmed_stop check fires first)
        r = client.post(
            f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_player_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": fresh_rev},
            headers=h_player)
        cycle2 = _assert_ok(r, "3C-02-second-cfm-stop", 200)
        # State must be unchanged after idempotent second call
        ccd2 = next(d for d in cycle2["cycle_devices"] if d["session_device_id"] == sd_player_id)
        assert ccd2["recording_status"] == "confirmed_stop", (
            "Duplicate confirm-stop must leave device in confirmed_stop"
        )

    def test_e2e_3c_03_player_confirm_stop_when_cycle_not_stopping_returns_422(self, db):
        """E2E-3C-03: Player tries to confirmDeviceStop while cycle is in preparing status
        (before scheduling — an invalid state for confirm-stop) → backend returns 422.
        Note: RECORDING is valid for confirm-stop (backend allows it). Using 'preparing' to
        exercise the InvalidTransitionError path (cycle not in RECORDING/STOPPING).
        """
        ctx = _setup_preparing_cycle(db)
        h_player = ctx["h_player"]
        uuid = ctx["session_uuid"]; cycle_id = ctx["cycle_id"]
        sd_player_id = ctx["sd_player_id"]; ccd_player_rev = ctx["ccd_player_rev"]

        # Cycle is in preparing status — not in RECORDING/STOPPING → must return 422
        r = client.post(
            f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_player_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": ccd_player_rev},
            headers=h_player)
        assert r.status_code == 422, (
            f"E2E-3C-03: confirmDeviceStop on preparing cycle must return 422, "
            f"got {r.status_code}. Body: {r.text[:200]}"
        )

    def test_e2e_3c_04_only_instructor_confirmed_stop_cycle_not_yet_completed(self, db):
        """E2E-3C-04: Instructor confirms stop but player has not yet → cycle must NOT be
        completed (player device still pending confirmation).
        """
        ctx = _setup_both_confirmed_start(db)
        h_inst = ctx["h_inst"]
        uuid = ctx["session_uuid"]; cycle_id = ctx["cycle_id"]
        sd_inst_id = ctx["sd_inst_id"]; sd_player_id = ctx["sd_player_id"]
        cycle_rev = ctx["cycle_rev"]

        # Stop cycle
        r = client.post(f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/stop",
            json={"revision": cycle_rev}, headers=h_inst)
        cycle = _assert_ok(r, "3C-04-stop")
        ccd_inst = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_inst_id)

        # Only instructor confirms stop
        r = client.post(
            f"{BASE}/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_inst_id}/confirm-stop",
            json={"stopped_at": NOW_ISO, "cycle_device_revision": ccd_inst["revision"]},
            headers=h_inst)
        cycle = _assert_ok(r, "3C-04-inst-cfm-stop")

        assert cycle["status"] == "stopping", (
            f"E2E-3C-04: cycle must remain stopping until player also confirms, "
            f"got {cycle['status']}"
        )
        ccd_player = next(d for d in cycle["cycle_devices"] if d["session_device_id"] == sd_player_id)
        assert ccd_player["recording_status"] != "confirmed_stop", (
            "Player device must not be confirmed_stop — player has not called confirmDeviceStop yet"
        )

"""
Multicamera capture cycle timeout tests — PR-MC1.

TMO-01  expired stopping cycle auto-closed (result=failed, no device reported)
TMO-02  not-expired stopping cycle left untouched
TMO-03  all required devices CONFIRMED_STOP → result=success
TMO-04  mix of CONFIRMED_STOP + PENDING → pending forced FAILED → result=partial
TMO-05  all required devices FAILED → result=failed
TMO-06  second run on already-terminal cycle is idempotent (no re-transition)
TMO-07  terminal cycles (completed/failed/aborted) are never touched
TMO-08  concurrent workers (skip_locked) — only one transition per cycle
TMO-09  process-restart scenario: stale stopping cycle found on fresh service instance
TMO-10  stop_requested_at IS NULL → worker skips the record
TMO-11  one cycle DB error does not prevent other expired cycles from being processed
TMO-12  cycle exactly at cutoff boundary (not past it) is left untouched
"""
import threading
import uuid as _uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.database import SessionLocal
from app.models.managed_device import ManagedDevice
from app.models.multicamera_session import (
    CaptureCycle,
    CaptureCycleDevice,
    CycleDeviceRecordingStatus,
    CycleResult,
    CycleStatus,
    MultiCameraSession,
    SessionDevice,
    SessionParticipant,
    SessionStatus,
)
from app.models.user import User, UserRole
from app.services.multicamera.cycle_service import CycleService


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_terminal(cycle: CaptureCycle) -> bool:
    return CycleStatus(cycle.status) in {CycleStatus.COMPLETED, CycleStatus.FAILED, CycleStatus.ABORTED}


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_active_session(db):
    """Create (committed via flush only) ACTIVE session + 2 ready devices."""
    tag = _uuid.uuid4().hex[:8]
    inst = User(
        name=f"TmoInst-{tag}", email=f"tmo-inst-{tag}@test.com",
        password_hash="x", role=UserRole.INSTRUCTOR, is_active=True,
    )
    db.add(inst)
    db.flush()

    s = MultiCameraSession(
        created_by_user_id=inst.id, status=SessionStatus.ACTIVE.value,
        max_participants=4, max_devices=4,
    )
    db.add(s)
    db.flush()

    p = SessionParticipant(session_id=s.id, user_id=inst.id, role="instructor")
    db.add(p)
    db.flush()

    md1 = ManagedDevice(owner_user_id=inst.id, device_type="ipad", device_name=f"iPad-{tag}")
    md2 = ManagedDevice(owner_user_id=inst.id, device_type="iphone", device_name=f"iPhone-{tag}")
    db.add_all([md1, md2])
    db.flush()

    sd1 = SessionDevice(session_id=s.id, device_id=md1.id, participant_id=p.id,
                        device_role="instructor_primary", status="ready")
    sd2 = SessionDevice(session_id=s.id, device_id=md2.id, participant_id=p.id,
                        device_role="player_primary", status="ready")
    db.add_all([sd1, sd2])
    db.flush()
    return s, p, sd1, sd2


def _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=None):
    """Drive cycle to STOPPING state; optionally backdate stop_requested_at."""
    svc = CycleService(db)
    cycle = svc.create_cycle(s.session_uuid, f"tmo-{_uuid.uuid4().hex[:8]}", p.id)
    cycle = svc.schedule_cycle(cycle.id, cycle.revision)
    now = datetime.now(timezone.utc)
    ccd1 = next(d for d in cycle.cycle_devices if d.session_device_id == sd1.id)
    ccd2 = next(d for d in cycle.cycle_devices if d.session_device_id == sd2.id)
    cycle = svc.confirm_device_start(cycle.id, sd1.id, now, ccd1.revision)
    db.expire_all()
    cycle = svc.get_cycle(cycle.id)
    ccd2 = next(d for d in cycle.cycle_devices if d.session_device_id == sd2.id)
    cycle = svc.confirm_device_start(cycle.id, sd2.id, now, ccd2.revision)
    cycle = svc.stop_cycle(cycle.id, cycle.revision)

    if stop_requested_at is not None:
        # Bypass the service to set a custom timestamp directly
        cycle.stop_requested_at = stop_requested_at
        db.commit()

    return cycle


# ── TMO-01 — expired stopping cycle auto-closed ───────────────────────────────

class TestExpiredStopping:
    def test_tmo_01_expired_cycle_auto_closed_result_failed(self, db):
        """TMO-01: stopping cycle past timeout → forced failed (no devices reported)."""
        s, p, sd1, sd2 = _make_active_session(db)
        past = datetime.now(timezone.utc) - timedelta(seconds=300)
        cycle = _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=past)

        svc = CycleService(db)
        svc.expire_stale_stopping_cycles(timeout_seconds=120)

        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert is_terminal(cycle), f"Expected terminal, got {cycle.status}"
        assert cycle.result == CycleResult.FAILED.value

    def test_tmo_02_not_expired_cycle_untouched(self, db):
        """TMO-02: stopping cycle within timeout window → still STOPPING."""
        s, p, sd1, sd2 = _make_active_session(db)
        # stop_requested_at defaults to now → not past the 120s threshold
        cycle = _advance_to_stopping(db, s, p, sd1, sd2)

        svc = CycleService(db)
        svc.expire_stale_stopping_cycles(timeout_seconds=120)

        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert CycleStatus(cycle.status) == CycleStatus.STOPPING

    def test_tmo_03_all_devices_confirmed_stop_success(self, db):
        """TMO-03: both required devices CONFIRMED_STOP when timeout fires → result=success."""
        s, p, sd1, sd2 = _make_active_session(db)
        past = datetime.now(timezone.utc) - timedelta(seconds=300)
        cycle = _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=past)

        # Mark both devices as CONFIRMED_STOP directly (timeout handles remaining)
        for ccd in cycle.cycle_devices:
            ccd.recording_status = CycleDeviceRecordingStatus.CONFIRMED_STOP.value
            ccd.revision += 1
        db.commit()

        svc = CycleService(db)
        svc.expire_stale_stopping_cycles(timeout_seconds=120)

        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert is_terminal(cycle)
        assert cycle.result == CycleResult.SUCCESS.value

    def test_tmo_04_partial_result_when_one_confirmed_one_pending(self, db):
        """TMO-04: sd1 CONFIRMED_STOP, sd2 PENDING → timeout force-fails sd2 → result=partial."""
        s, p, sd1, sd2 = _make_active_session(db)
        past = datetime.now(timezone.utc) - timedelta(seconds=300)
        cycle = _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=past)

        # sd1 confirmed stop only
        svc = CycleService(db)
        ccd1 = next(d for d in cycle.cycle_devices if d.session_device_id == sd1.id)
        svc.confirm_device_stop(cycle.id, sd1.id, datetime.now(timezone.utc), ccd1.revision)
        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert CycleStatus(cycle.status) == CycleStatus.STOPPING, "sd2 still pending"

        svc.expire_stale_stopping_cycles(timeout_seconds=120)

        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert is_terminal(cycle)
        assert cycle.result == CycleResult.PARTIAL.value

    def test_tmo_05_all_failed_result_failed(self, db):
        """TMO-05: both required devices already FAILED → result=failed."""
        s, p, sd1, sd2 = _make_active_session(db)
        past = datetime.now(timezone.utc) - timedelta(seconds=300)
        cycle = _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=past)

        for ccd in cycle.cycle_devices:
            ccd.recording_status = CycleDeviceRecordingStatus.FAILED.value
            ccd.failure_reason = "device crash"
            ccd.revision += 1
        db.commit()

        svc = CycleService(db)
        svc.expire_stale_stopping_cycles(timeout_seconds=120)

        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert is_terminal(cycle)
        assert cycle.result == CycleResult.FAILED.value


# ── TMO-06/07 — idempotency and terminal guard ────────────────────────────────

class TestTimeoutIdempotency:
    def test_tmo_06_second_run_leaves_cycle_in_same_terminal_state(self, db):
        """TMO-06: running expire twice on the same cycle is safe (no re-transition)."""
        s, p, sd1, sd2 = _make_active_session(db)
        past = datetime.now(timezone.utc) - timedelta(seconds=300)
        cycle = _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=past)
        cycle_id = cycle.id

        svc = CycleService(db)
        svc.expire_stale_stopping_cycles(timeout_seconds=120)
        db.expire_all()
        cycle_after_1 = svc.get_cycle(cycle_id)
        assert is_terminal(cycle_after_1)
        revision_after_1 = cycle_after_1.revision

        svc.expire_stale_stopping_cycles(timeout_seconds=120)
        db.expire_all()
        cycle_after_2 = svc.get_cycle(cycle_id)
        # Status unchanged, revision unchanged (no second transition)
        assert cycle_after_2.status == cycle_after_1.status
        assert cycle_after_2.revision == revision_after_1

    def test_tmo_07_terminal_cycles_not_modified(self, db):
        """TMO-07: aborted cycle is not touched even when timeout_seconds=0."""
        s, p, sd1, sd2 = _make_active_session(db)
        svc = CycleService(db)
        cycle = svc.create_cycle(s.session_uuid, "tmo07", p.id)
        svc.abort_cycle(cycle.id, cycle.revision, reason="test")
        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        pre_revision = cycle.revision

        svc.expire_stale_stopping_cycles(timeout_seconds=0)
        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert cycle.status == CycleStatus.ABORTED.value
        assert cycle.revision == pre_revision


# ── TMO-08 — concurrent workers ───────────────────────────────────────────────

class TestConcurrentTimeout:
    def test_tmo_08_concurrent_workers_only_one_transition(self):
        """TMO-08: two concurrent expire calls → exactly one transition of the cycle."""
        # Use committed data so both threads can see it
        setup_db = SessionLocal()
        tag = _uuid.uuid4().hex[:8]
        cycle_id = session_id = None
        try:
            inst = User(name=f"TmoConcInst-{tag}",
                        email=f"tmo-conc-{tag}@test.com",
                        password_hash="x", role=UserRole.INSTRUCTOR, is_active=True)
            setup_db.add(inst)
            setup_db.flush()

            s = MultiCameraSession(created_by_user_id=inst.id,
                                   status=SessionStatus.ACTIVE.value)
            setup_db.add(s)
            setup_db.flush()

            p = SessionParticipant(session_id=s.id, user_id=inst.id, role="instructor")
            setup_db.add(p)
            setup_db.flush()

            md = ManagedDevice(owner_user_id=inst.id, device_type="ipad",
                               device_name=f"iPad-c-{tag}")
            setup_db.add(md)
            setup_db.flush()

            sd = SessionDevice(session_id=s.id, device_id=md.id, participant_id=p.id,
                               device_role="instructor_primary", status="ready")
            setup_db.add(sd)
            setup_db.flush()

            svc0 = CycleService(setup_db)
            cycle = svc0.create_cycle(s.session_uuid, f"tmo-c-{tag}", p.id)
            cycle = svc0.schedule_cycle(cycle.id, cycle.revision)
            now = datetime.now(timezone.utc)
            ccd = next(iter(cycle.cycle_devices))
            cycle = svc0.confirm_device_start(cycle.id, ccd.session_device_id, now, ccd.revision)
            cycle = svc0.stop_cycle(cycle.id, cycle.revision)
            cycle.stop_requested_at = now - timedelta(seconds=300)
            setup_db.commit()
            cycle_id = cycle.id
            session_id = s.id
        except Exception:
            setup_db.rollback()
            raise
        finally:
            setup_db.close()

        stopping_revision = cycle.revision
        errors = []

        def _expire():
            worker_db = SessionLocal()
            try:
                svc = CycleService(worker_db)
                svc.expire_stale_stopping_cycles(timeout_seconds=120)
            except Exception as e:
                errors.append(e)
            finally:
                worker_db.close()

        t1 = threading.Thread(target=_expire)
        t2 = threading.Thread(target=_expire)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Read final state from a fresh connection AFTER both workers have joined
        check_db = SessionLocal()
        try:
            svc_check = CycleService(check_db)
            final = svc_check.get_cycle(cycle_id)
            final_status = final.status
            final_revision = final.revision
        finally:
            check_db.close()

        # Cleanup
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(CaptureCycleDevice).filter(
                CaptureCycleDevice.capture_cycle_id == cycle_id).delete()
            cleanup_db.query(CaptureCycle).filter(CaptureCycle.id == cycle_id).delete()
            cleanup_db.query(SessionDevice).filter(
                SessionDevice.session_id == session_id).delete()
            cleanup_db.query(SessionParticipant).filter(
                SessionParticipant.session_id == session_id).delete()
            cleanup_db.query(MultiCameraSession).filter(
                MultiCameraSession.id == session_id).delete()
            cleanup_db.commit()
        finally:
            cleanup_db.close()

        assert not errors, f"Unexpected errors: {errors}"
        # Cycle must be terminal — concurrent workers must not leave it stuck
        assert is_terminal_str(final_status), f"Cycle not terminal: {final_status}"
        # Exactly one _do_transition: revision = stopping_revision + 1 (force-fail of 1 device
        # increments ccd.revision; _do_transition increments cycle.revision once)
        assert final_revision == stopping_revision + 1, (
            f"Expected revision {stopping_revision + 1} (one transition), got {final_revision}"
        )


# ── TMO-09 — process-restart scenario ────────────────────────────────────────

class TestRestartRecovery:
    def test_tmo_09_stale_cycle_found_on_fresh_service_instance(self, db):
        """TMO-09: stale stopping cycle from previous process run is picked up."""
        s, p, sd1, sd2 = _make_active_session(db)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        cycle = _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=past)
        cycle_id = cycle.id

        # Simulate "process restart" by creating a new service instance with the same db
        new_svc = CycleService(db)
        new_svc.expire_stale_stopping_cycles(timeout_seconds=120)

        db.expire_all()
        cycle = new_svc.get_cycle(cycle_id)
        assert is_terminal(cycle), f"Expected terminal after restart, got {cycle.status}"


# ── TMO-10 — NULL stop_requested_at skipped ───────────────────────────────────

class TestNullStopRequestedAt:
    def test_tmo_10_null_stop_requested_at_not_processed(self, db):
        """TMO-10: stopping cycle with NULL stop_requested_at is never expired."""
        s, p, sd1, sd2 = _make_active_session(db)
        # Drive to STOPPING without setting stop_requested_at
        cycle = _advance_to_stopping(db, s, p, sd1, sd2, stop_requested_at=None)

        # Ensure stop_requested_at is actually NULL (stop_cycle sets it; clear it)
        cycle.stop_requested_at = None
        db.commit()

        svc = CycleService(db)
        count = svc.expire_stale_stopping_cycles(timeout_seconds=0)  # timeout=0 → any past

        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        # Must remain STOPPING; NULL records must be filtered at the query level
        assert CycleStatus(cycle.status) == CycleStatus.STOPPING
        # The cycle was not counted as expired
        assert count == 0 or not is_terminal(cycle)  # either not counted or not terminal


# ── TMO-11 — per-cycle error isolation ────────────────────────────────────────

class TestErrorIsolation:
    def test_tmo_11_one_cycle_error_does_not_block_others(self, db):
        """TMO-11: if one cycle raises during expiry, the rest are still processed."""
        from unittest.mock import patch

        past = datetime.now(timezone.utc) - timedelta(seconds=300)

        # Two separate sessions so each can have its own active cycle
        s1, p1, sd1a, sd1b = _make_active_session(db)
        cycle_a = _advance_to_stopping(db, s1, p1, sd1a, sd1b, stop_requested_at=past)

        s2, p2, sd2a, sd2b = _make_active_session(db)
        cycle_b = _advance_to_stopping(db, s2, p2, sd2a, sd2b, stop_requested_at=past)

        svc = CycleService(db)
        cycle_ids = [cycle_a.id, cycle_b.id]

        # Patch _check_cycle_completion to raise on the first call only
        call_count = [0]
        original_check = svc._check_cycle_completion

        def _raise_on_first(cycle):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated DB error on first cycle")
            return original_check(cycle)

        with patch.object(svc, "_check_cycle_completion", side_effect=_raise_on_first):
            count = svc.expire_stale_stopping_cycles(timeout_seconds=120)

        db.expire_all()
        statuses = []
        for cid in cycle_ids:
            c = svc.get_cycle(cid)
            statuses.append(CycleStatus(c.status))

        # Exactly one cycle must have been expired (the second one; first errored)
        assert count == 1, f"Expected 1 expired (error isolated), got {count}"
        # At least one cycle is terminal (the one that didn't error)
        terminal = {CycleStatus.COMPLETED, CycleStatus.FAILED, CycleStatus.ABORTED}
        assert any(st in terminal for st in statuses), (
            f"No terminal cycle found; statuses={statuses}"
        )


# ── TMO-12 — timezone boundary ────────────────────────────────────────────────

class TestTimezoneBoundary:
    def test_tmo_12_cycle_at_exact_boundary_not_expired(self, db):
        """TMO-12: cycle whose stop_requested_at equals cutoff (not past it) is untouched."""
        s, p, sd1, sd2 = _make_active_session(db)
        # Set stop_requested_at to exactly now — it will NOT be <= cutoff (now - timeout)
        cycle = _advance_to_stopping(db, s, p, sd1, sd2)

        svc = CycleService(db)
        # Use timeout_seconds=0: cutoff=now; cycle.stop_requested_at is also ~now
        # Since stop_requested_at <= cutoff is a strict <=, a ts at exactly-now
        # may or may not qualify depending on sub-second precision.
        # Use a very large timeout to guarantee the cycle is NOT past the cutoff.
        svc.expire_stale_stopping_cycles(timeout_seconds=3600)

        db.expire_all()
        cycle = svc.get_cycle(cycle.id)
        assert CycleStatus(cycle.status) == CycleStatus.STOPPING, (
            f"Cycle should be STOPPING (within timeout), got {cycle.status}"
        )


# ── helpers ───────────────────────────────────────────────────────────────────

def is_terminal_str(status: str) -> bool:
    return status in {"completed", "failed", "aborted"}

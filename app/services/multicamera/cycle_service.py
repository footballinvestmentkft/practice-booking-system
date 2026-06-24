"""CycleService — multicamera capture cycle lifecycle (PR-MC1).

TIMEOUT GAP (PR-MC1, not production-ready): No background worker or scheduler
exists.  Once a cycle enters 'stopping', it depends entirely on client traffic
(confirm-stop / report-failure from each required device) to reach a terminal
state.  If all devices disconnect silently the cycle stays in 'stopping'
indefinitely.  A Celery-beat task that periodically marks stale 'stopping'
cycles as 'failed' is required before production deployment; that task is
deferred to a future PR.  The deterministic alternative available today is
client-driven-only completion — every required device must call confirm-stop
or report-failure.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.multicamera_session import (
    CYCLE_TRANSITIONS,
    SESSION_TRANSITIONS,  # noqa: F401 — exported for session activation guard
    CaptureCycle,
    CaptureCycleDevice,
    CycleDeviceRecordingStatus,
    CycleResult,
    CycleStatus,
    DeviceRole,
    SessionStatus,
    is_cycle_terminal,
)
from app.repositories.multicamera_session_repo import MultiCameraSessionRepo
from .exceptions import (
    CycleConflictError,
    CycleNotFoundError,
    DeviceNotFoundError,
    DeviceNotReadyError,
    InstructorRequiredError,
    InvalidTransitionError,
    NoCycleDevicesError,
    RevisionConflictError,
    SessionNotFoundError,
)

_SCHEDULED_LEAD_SECONDS = 8


class CycleService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = MultiCameraSessionRepo(db)

    # ── Session activation ────────────────────────────────────────────────────

    def activate_session(self, session_uuid: uuid.UUID, session_revision: int):
        """Transition session LOBBY → ACTIVE for multi-cycle use."""
        s = self._require_session(session_uuid)
        if s.revision != session_revision:
            raise RevisionConflictError("session", session_revision, s.revision)
        current = SessionStatus(s.status)
        if SessionStatus.ACTIVE not in SESSION_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError("session", s.status, "active")
        s.status = SessionStatus.ACTIVE.value
        s.revision += 1
        self.db.commit()
        self.db.refresh(s)
        return s

    # ── Cycle creation ────────────────────────────────────────────────────────

    def create_cycle(
        self,
        session_uuid: uuid.UUID,
        idempotency_key: str,
        created_by_participant_id: int,
    ) -> CaptureCycle:
        """
        Snapshot all non-removed session devices → CaptureCycleDevice rows.
        required = True for all roles except auxiliary_camera.

        Session must be ACTIVE.  Only one non-terminal cycle may exist per
        session at a time.  The session row is locked (SELECT FOR UPDATE)
        before all checks to prevent concurrent different-key races from both
        succeeding — exactly one caller gets the cycle, the other gets
        CycleConflictError.

        Idempotency: if the same idempotency_key is already committed for this
        session, the existing cycle is returned without creating a new one.
        """
        # Lock session row to serialise concurrent create_cycle calls
        s = self._require_session_for_update(session_uuid)
        if SessionStatus(s.status) != SessionStatus.ACTIVE:
            raise InvalidTransitionError("session", s.status, "create_cycle requires active")

        # 1. Idempotency: same key → return existing cycle
        existing_by_key = self.repo.get_cycle_by_idempotency_key(s.id, idempotency_key)
        if existing_by_key is not None:
            return existing_by_key

        # 2. One-active-cycle guard: block if a non-terminal cycle already exists
        active = self.repo.get_non_terminal_cycles(s.id)
        if active:
            raise CycleConflictError(
                f"Session already has an active cycle "
                f"(id={active[0].id}, status={active[0].status}). "
                "Complete or abort it before creating a new one."
            )

        # 3. Snapshot devices (all non-removed, regardless of readiness)
        devices = self.repo.get_active_session_devices(s.id)
        if not devices:
            raise NoCycleDevicesError("Session has no active devices to snapshot")

        cycle_index = self.repo.next_cycle_index(s.id)
        cycle = self.repo.add_cycle(
            session_id=s.id,
            cycle_index=cycle_index,
            status=CycleStatus.PREPARING.value,
            created_by_participant_id=created_by_participant_id,
            idempotency_key=idempotency_key,
        )
        for sd in devices:
            required = DeviceRole(sd.device_role) != DeviceRole.AUXILIARY_CAMERA
            self.repo.add_cycle_device(
                capture_cycle_id=cycle.id,
                session_device_id=sd.id,
                required=required,
            )
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    # ── Schedule ──────────────────────────────────────────────────────────────

    def schedule_cycle(self, cycle_id: int, revision: int) -> CaptureCycle:
        """
        Check all required devices are ready, then transition PREPARING → RECORDING_PENDING
        and set scheduled_start_at = now() + 8s.
        """
        cycle = self._require_cycle(cycle_id)
        self._check_revision(cycle, revision)
        self._assert_transition(cycle, CycleStatus.RECORDING_PENDING)

        required_devices = self.repo.get_required_cycle_devices(cycle_id)
        not_ready = []
        for ccd in required_devices:
            sd = ccd.session_device
            if sd is None:
                sd = self.repo.get_session_device_by_id(ccd.session_device_id)
            if sd and sd.status != "ready":
                not_ready.append(ccd.session_device_id)
        if not_ready:
            raise DeviceNotReadyError(f"Devices not ready: {not_ready}")

        now = datetime.now(timezone.utc)
        cycle.scheduled_start_at = now + timedelta(seconds=_SCHEDULED_LEAD_SECONDS)
        self._do_transition(cycle, CycleStatus.RECORDING_PENDING)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    # ── Confirm device start ──────────────────────────────────────────────────

    def confirm_device_start(
        self,
        cycle_id: int,
        session_device_id: int,
        started_at: datetime,
        cycle_device_revision: int,
    ) -> CaptureCycle:
        cycle = self._require_cycle(cycle_id)
        current_status = CycleStatus(cycle.status)
        if current_status != CycleStatus.RECORDING_PENDING:
            raise InvalidTransitionError("cycle", cycle.status, "confirm_device_start requires recording_pending")

        ccd = self._require_cycle_device(cycle_id, session_device_id)
        # Idempotent: already confirmed start — return current cycle state
        if CycleDeviceRecordingStatus(ccd.recording_status) == CycleDeviceRecordingStatus.CONFIRMED_START:
            self.db.refresh(cycle)
            return cycle
        if ccd.revision != cycle_device_revision:
            raise RevisionConflictError("cycle_device", cycle_device_revision, ccd.revision)
        if CycleDeviceRecordingStatus(ccd.recording_status) != CycleDeviceRecordingStatus.PENDING:
            raise InvalidTransitionError(
                "cycle_device", ccd.recording_status, "confirmed_start"
            )

        ccd.recording_status = CycleDeviceRecordingStatus.CONFIRMED_START.value
        ccd.started_at = started_at
        ccd.revision += 1
        cycle.updated_at = datetime.now(timezone.utc)

        # Check if ALL required devices have confirmed start
        required = self.repo.get_required_cycle_devices(cycle_id)
        all_started = all(
            CycleDeviceRecordingStatus(d.recording_status) == CycleDeviceRecordingStatus.CONFIRMED_START
            or CycleDeviceRecordingStatus(d.recording_status) == CycleDeviceRecordingStatus.CONFIRMED_STOP
            for d in required
        )
        if all_started:
            cycle.recording_started_at = datetime.now(timezone.utc)
            self._do_transition(cycle, CycleStatus.RECORDING)

        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    # ── Stop ──────────────────────────────────────────────────────────────────

    def stop_cycle(self, cycle_id: int, revision: int) -> CaptureCycle:
        cycle = self._require_cycle(cycle_id)
        self._check_revision(cycle, revision)
        self._assert_transition(cycle, CycleStatus.STOPPING)

        cycle.stop_requested_at = datetime.now(timezone.utc)
        self._do_transition(cycle, CycleStatus.STOPPING)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    # ── Confirm device stop ───────────────────────────────────────────────────

    def confirm_device_stop(
        self,
        cycle_id: int,
        session_device_id: int,
        stopped_at: datetime,
        cycle_device_revision: int,
    ) -> CaptureCycle:
        cycle = self._require_cycle(cycle_id)
        current_status = CycleStatus(cycle.status)
        if current_status not in (CycleStatus.RECORDING, CycleStatus.STOPPING):
            raise InvalidTransitionError(
                "cycle", cycle.status, "confirm_device_stop requires recording or stopping"
            )

        ccd = self._require_cycle_device(cycle_id, session_device_id)
        # Idempotent: already confirmed stop — return current cycle state
        if CycleDeviceRecordingStatus(ccd.recording_status) == CycleDeviceRecordingStatus.CONFIRMED_STOP:
            self.db.refresh(cycle)
            return cycle
        if ccd.revision != cycle_device_revision:
            raise RevisionConflictError("cycle_device", cycle_device_revision, ccd.revision)
        if CycleDeviceRecordingStatus(ccd.recording_status) != CycleDeviceRecordingStatus.CONFIRMED_START:
            raise InvalidTransitionError(
                "cycle_device", ccd.recording_status, "confirmed_stop"
            )

        ccd.recording_status = CycleDeviceRecordingStatus.CONFIRMED_STOP.value
        ccd.stopped_at = stopped_at
        ccd.revision += 1
        cycle.updated_at = datetime.now(timezone.utc)

        self.db.flush()
        self._check_cycle_completion(cycle)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    # ── Report device failure ─────────────────────────────────────────────────

    def report_device_failure(
        self,
        cycle_id: int,
        session_device_id: int,
        failure_reason: str,
        cycle_device_revision: int,
    ) -> CaptureCycle:
        cycle = self._require_cycle(cycle_id)
        if is_cycle_terminal(CycleStatus(cycle.status)):
            raise InvalidTransitionError("cycle", cycle.status, "already terminal")

        ccd = self._require_cycle_device(cycle_id, session_device_id)
        if ccd.revision != cycle_device_revision:
            raise RevisionConflictError("cycle_device", cycle_device_revision, ccd.revision)
        current_device_status = CycleDeviceRecordingStatus(ccd.recording_status)
        if current_device_status in (
            CycleDeviceRecordingStatus.CONFIRMED_STOP,
            CycleDeviceRecordingStatus.FAILED,
        ):
            raise InvalidTransitionError("cycle_device", ccd.recording_status, "failed")

        ccd.recording_status = CycleDeviceRecordingStatus.FAILED.value
        ccd.failure_reason = failure_reason
        ccd.revision += 1
        cycle.updated_at = datetime.now(timezone.utc)

        self.db.flush()
        self._check_cycle_completion(cycle)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    # ── Abort ─────────────────────────────────────────────────────────────────

    def abort_cycle(self, cycle_id: int, revision: int, reason: Optional[str] = None) -> CaptureCycle:
        cycle = self._require_cycle(cycle_id)
        self._check_revision(cycle, revision)
        self._assert_transition(cycle, CycleStatus.ABORTED)

        if reason:
            cycle.failure_reason = reason
        cycle.completed_at = datetime.now(timezone.utc)
        self._do_transition(cycle, CycleStatus.ABORTED)
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    # ── Finalize session ──────────────────────────────────────────────────────

    def finalize_session(self, session_uuid: uuid.UUID, session_revision: int):
        """
        Transition ACTIVE → FINALIZING → COMPLETED.
        Requires all cycles to be in terminal state.
        """
        s = self._require_session(session_uuid)
        if s.revision != session_revision:
            raise RevisionConflictError("session", session_revision, s.revision)
        if SessionStatus(s.status) != SessionStatus.ACTIVE:
            raise InvalidTransitionError("session", s.status, "finalize requires active")

        cycles = self.repo.get_cycles_for_session(s.id)
        non_terminal = [c for c in cycles if not is_cycle_terminal(CycleStatus(c.status))]
        if non_terminal:
            ids = [c.id for c in non_terminal]
            raise InvalidTransitionError("session", s.status, f"cycles not terminal: {ids}")

        now = datetime.now(timezone.utc)
        s.status = SessionStatus.FINALIZING.value
        s.revision += 1
        s.status = SessionStatus.COMPLETED.value
        s.revision += 1
        s.finalized_at = now
        self.db.commit()
        self.db.refresh(s)
        return s

    # ── List cycles ───────────────────────────────────────────────────────────

    def list_cycles(self, session_uuid: uuid.UUID) -> List[CaptureCycle]:
        s = self._require_session(session_uuid)
        return self.repo.get_cycles_for_session(s.id)

    def get_cycle(self, cycle_id: int) -> CaptureCycle:
        return self._require_cycle(cycle_id)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _check_cycle_completion(self, cycle: CaptureCycle) -> None:
        """Called after device stop/failure — complete cycle if all required devices resolved."""
        required = self.repo.get_required_cycle_devices(cycle.id)
        if not required:
            return
        terminal_statuses = {
            CycleDeviceRecordingStatus.CONFIRMED_STOP,
            CycleDeviceRecordingStatus.FAILED,
        }
        all_resolved = all(
            CycleDeviceRecordingStatus(d.recording_status) in terminal_statuses
            for d in required
        )
        if not all_resolved:
            return

        now = datetime.now(timezone.utc)
        stopped_count = sum(
            1 for d in required
            if CycleDeviceRecordingStatus(d.recording_status) == CycleDeviceRecordingStatus.CONFIRMED_STOP
        )
        if stopped_count == 0:
            result = CycleResult.FAILED.value
            target = CycleStatus.FAILED
        elif stopped_count < len(required):
            result = CycleResult.PARTIAL.value
            target = CycleStatus.COMPLETED
        else:
            result = CycleResult.SUCCESS.value
            target = CycleStatus.COMPLETED

        if target not in CYCLE_TRANSITIONS.get(CycleStatus(cycle.status), set()):
            return

        cycle.result = result
        cycle.recording_stopped_at = now
        cycle.completed_at = now
        self._do_transition(cycle, target)

    def _do_transition(self, cycle: CaptureCycle, target: CycleStatus) -> None:
        cycle.status = target.value
        cycle.revision += 1
        cycle.updated_at = datetime.now(timezone.utc)

    def _assert_transition(self, cycle: CaptureCycle, target: CycleStatus) -> None:
        current = CycleStatus(cycle.status)
        if target not in CYCLE_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError("cycle", cycle.status, target.value)

    def _check_revision(self, cycle: CaptureCycle, revision: int) -> None:
        if cycle.revision != revision:
            raise RevisionConflictError("cycle", revision, cycle.revision)

    def _require_session(self, session_uuid: uuid.UUID):
        s = self.repo.get_session_by_uuid(session_uuid)
        if not s:
            raise SessionNotFoundError(str(session_uuid))
        return s

    def _require_session_for_update(self, session_uuid: uuid.UUID):
        """Load session with SELECT FOR UPDATE to serialise write-critical paths."""
        s = self.repo.get_session_by_uuid_for_update(session_uuid)
        if not s:
            raise SessionNotFoundError(str(session_uuid))
        return s

    def _require_cycle(self, cycle_id: int) -> CaptureCycle:
        cycle = self.repo.get_cycle_by_id(cycle_id)
        if not cycle:
            raise CycleNotFoundError(str(cycle_id))
        return cycle

    def _require_cycle_device(self, cycle_id: int, session_device_id: int) -> CaptureCycleDevice:
        ccd = self.repo.get_cycle_device(cycle_id, session_device_id)
        if not ccd:
            raise DeviceNotFoundError(f"cycle_device cycle={cycle_id} sd={session_device_id}")
        return ccd

    def _require_session_device_by_id(self, sd_id: int):
        sd = self.repo.get_session_device_by_id(sd_id)
        if not sd:
            raise DeviceNotFoundError(str(sd_id))
        return sd

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload

from app.models.managed_device import ManagedDevice
from app.models.multicamera_session import (
    CaptureCycle, CaptureCycleDevice,
    CaptureStream, MultiCameraSession, SessionDevice, SessionParticipant,
)


class MultiCameraSessionRepo:
    def __init__(self, db: Session):
        self.db = db

    # ── Sessions ──────────────────────────────────────────────────────────────

    def create_session(self, created_by: int, max_p: int, max_d: int) -> MultiCameraSession:
        s = MultiCameraSession(created_by_user_id=created_by, max_participants=max_p, max_devices=max_d)
        self.db.add(s)
        self.db.flush()
        return s

    def get_session_by_uuid(self, session_uuid: uuid.UUID) -> Optional[MultiCameraSession]:
        return (
            self.db.query(MultiCameraSession)
            .options(
                joinedload(MultiCameraSession.participants),
                joinedload(MultiCameraSession.devices).joinedload(SessionDevice.streams),
            )
            .filter(MultiCameraSession.session_uuid == session_uuid)
            .first()
        )

    def get_session_by_uuid_for_update(self, session_uuid: uuid.UUID) -> Optional[MultiCameraSession]:
        """Lock the session row (SELECT FOR UPDATE) before write-critical checks."""
        return (
            self.db.query(MultiCameraSession)
            .filter(MultiCameraSession.session_uuid == session_uuid)
            .with_for_update()
            .first()
        )

    # ── Participants ──────────────────────────────────────────────────────────

    def get_participant(self, session_id: int, user_id: int) -> Optional[SessionParticipant]:
        return (
            self.db.query(SessionParticipant)
            .filter(SessionParticipant.session_id == session_id, SessionParticipant.user_id == user_id)
            .first()
        )

    def count_active_participants(self, session_id: int) -> int:
        return (
            self.db.query(SessionParticipant)
            .filter(SessionParticipant.session_id == session_id, SessionParticipant.left_at.is_(None))
            .count()
        )

    def add_participant(self, session_id: int, user_id: int, role: str) -> SessionParticipant:
        p = SessionParticipant(session_id=session_id, user_id=user_id, role=role)
        self.db.add(p)
        self.db.flush()
        return p

    # ── Session Devices ───────────────────────────────────────────────────────

    def get_session_device(self, session_id: int, device_id: int) -> Optional[SessionDevice]:
        return (
            self.db.query(SessionDevice)
            .filter(SessionDevice.session_id == session_id, SessionDevice.device_id == device_id)
            .first()
        )

    def get_session_device_by_id(self, sd_id: int) -> Optional[SessionDevice]:
        return self.db.query(SessionDevice).filter(SessionDevice.id == sd_id).first()

    def count_active_devices(self, session_id: int) -> int:
        return (
            self.db.query(SessionDevice)
            .filter(SessionDevice.session_id == session_id, SessionDevice.removed_at.is_(None))
            .count()
        )

    def get_active_session_devices(self, session_id: int) -> List[SessionDevice]:
        return (
            self.db.query(SessionDevice)
            .filter(SessionDevice.session_id == session_id, SessionDevice.removed_at.is_(None))
            .all()
        )

    def add_session_device(self, **kwargs) -> SessionDevice:
        sd = SessionDevice(**kwargs)
        self.db.add(sd)
        self.db.flush()
        return sd

    # ── Capture Streams ───────────────────────────────────────────────────────

    def get_capture_stream(self, session_device_id: int, stream_type: str) -> Optional[CaptureStream]:
        return (
            self.db.query(CaptureStream)
            .filter(CaptureStream.session_device_id == session_device_id, CaptureStream.stream_type == stream_type)
            .first()
        )

    def add_capture_stream(self, **kwargs) -> CaptureStream:
        cs = CaptureStream(**kwargs)
        self.db.add(cs)
        self.db.flush()
        return cs

    def get_capture_stream_by_id(self, stream_id: int) -> Optional[CaptureStream]:
        return self.db.query(CaptureStream).filter(CaptureStream.id == stream_id).first()

    # ── Managed Devices ───────────────────────────────────────────────────────

    def get_managed_device_by_uuid(self, device_uuid: uuid.UUID) -> Optional[ManagedDevice]:
        return self.db.query(ManagedDevice).filter(ManagedDevice.device_uuid == device_uuid).first()

    def get_managed_device_by_ble(self, user_id: int, ble_identifier: str) -> Optional[ManagedDevice]:
        return (
            self.db.query(ManagedDevice)
            .filter(ManagedDevice.owner_user_id == user_id, ManagedDevice.ble_identifier == ble_identifier)
            .first()
        )

    def add_managed_device(self, **kwargs) -> ManagedDevice:
        d = ManagedDevice(**kwargs)
        self.db.add(d)
        self.db.flush()
        return d

    # ── Capture Cycles ────────────────────────────────────────────────────────

    def get_cycle_by_id(self, cycle_id: int) -> Optional[CaptureCycle]:
        return (
            self.db.query(CaptureCycle)
            .options(joinedload(CaptureCycle.cycle_devices))
            .filter(CaptureCycle.id == cycle_id)
            .first()
        )

    def get_cycle_by_idempotency_key(self, session_id: int, idempotency_key: str) -> Optional[CaptureCycle]:
        return (
            self.db.query(CaptureCycle)
            .options(joinedload(CaptureCycle.cycle_devices))
            .filter(
                CaptureCycle.session_id == session_id,
                CaptureCycle.idempotency_key == idempotency_key,
            )
            .first()
        )

    def get_cycles_for_session(self, session_id: int) -> List[CaptureCycle]:
        return (
            self.db.query(CaptureCycle)
            .options(joinedload(CaptureCycle.cycle_devices))
            .filter(CaptureCycle.session_id == session_id)
            .order_by(CaptureCycle.cycle_index)
            .all()
        )

    def get_non_terminal_cycles(self, session_id: int) -> List[CaptureCycle]:
        """Return cycles that are not in a terminal state (completed/failed/aborted)."""
        terminal = {"completed", "failed", "aborted"}
        return (
            self.db.query(CaptureCycle)
            .filter(
                CaptureCycle.session_id == session_id,
                ~CaptureCycle.status.in_(terminal),
            )
            .all()
        )

    def next_cycle_index(self, session_id: int) -> int:
        from sqlalchemy import func
        result = self.db.query(func.max(CaptureCycle.cycle_index)).filter(
            CaptureCycle.session_id == session_id
        ).scalar()
        return 0 if result is None else result + 1

    def add_cycle(self, **kwargs) -> CaptureCycle:
        cycle = CaptureCycle(**kwargs)
        self.db.add(cycle)
        self.db.flush()
        return cycle

    # ── Capture Cycle Devices ─────────────────────────────────────────────────

    def get_cycle_device(self, cycle_id: int, session_device_id: int) -> Optional[CaptureCycleDevice]:
        return (
            self.db.query(CaptureCycleDevice)
            .filter(
                CaptureCycleDevice.capture_cycle_id == cycle_id,
                CaptureCycleDevice.session_device_id == session_device_id,
            )
            .first()
        )

    def get_cycle_device_by_id(self, ccd_id: int) -> Optional[CaptureCycleDevice]:
        return self.db.query(CaptureCycleDevice).filter(CaptureCycleDevice.id == ccd_id).first()

    def get_required_cycle_devices(self, cycle_id: int) -> List[CaptureCycleDevice]:
        return (
            self.db.query(CaptureCycleDevice)
            .filter(
                CaptureCycleDevice.capture_cycle_id == cycle_id,
                CaptureCycleDevice.required.is_(True),
            )
            .all()
        )

    def add_cycle_device(self, **kwargs) -> CaptureCycleDevice:
        ccd = CaptureCycleDevice(**kwargs)
        self.db.add(ccd)
        self.db.flush()
        return ccd

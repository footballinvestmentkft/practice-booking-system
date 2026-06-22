from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.managed_device import ManagedDevice
from app.models.multicamera_session import (
    CaptureStream, MultiCameraSession, SessionDevice, SessionParticipant,
)


class MultiCameraSessionRepo:
    def __init__(self, db: Session):
        self.db = db

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

    def add_session_device(self, **kwargs) -> SessionDevice:
        sd = SessionDevice(**kwargs)
        self.db.add(sd)
        self.db.flush()
        return sd

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

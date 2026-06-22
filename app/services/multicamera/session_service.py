from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.multicamera_session import (
    SESSION_TRANSITIONS, DEVICE_TRANSITIONS,
    DeviceRole, DeviceStatus, SessionStatus,
    MultiCameraSession, SessionDevice, SessionParticipant,
)
from app.repositories.multicamera_session_repo import MultiCameraSessionRepo
from .exceptions import (
    CrossSessionReferenceError,
    DeviceNotFoundError,
    DeviceRoleViolationError,
    InvalidTransitionError,
    ParticipantNotFoundError,
    RevisionConflictError,
    SessionFullError,
    SessionNotFoundError,
)


class SessionService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = MultiCameraSessionRepo(db)

    def create_session(self, creator_id: int, max_participants: int = 2, max_devices: int = 4):
        s = self.repo.create_session(creator_id, max_participants, max_devices)
        self.db.commit()
        self.db.refresh(s)
        return s

    def get_session(self, session_uuid: uuid.UUID):
        s = self.repo.get_session_by_uuid(session_uuid)
        if not s:
            raise SessionNotFoundError(str(session_uuid))
        return s

    def join_session(self, session_uuid: uuid.UUID, user_id: int, role: str):
        s = self._require_session(session_uuid)
        existing = self.repo.get_participant(s.id, user_id)
        if existing:
            return existing
        count = self.repo.count_active_participants(s.id)
        if count >= s.max_participants:
            raise SessionFullError("participants", count, s.max_participants)
        p = self.repo.add_participant(s.id, user_id, role)
        self.db.commit()
        self.db.refresh(p)
        return p

    def leave_session(self, session_uuid: uuid.UUID, user_id: int):
        s = self._require_session(session_uuid)
        p = self.repo.get_participant(s.id, user_id)
        if not p:
            raise ParticipantNotFoundError(f"user {user_id}")
        if p.left_at is not None:
            return p
        p.left_at = datetime.now(timezone.utc)
        s.revision += 1
        self.db.commit()
        self.db.refresh(p)
        return p

    def register_device(
        self,
        session_uuid: uuid.UUID,
        device_uuid: uuid.UUID,
        device_role: str,
        participant_id: Optional[int] = None,
        managed_by_device_id: Optional[int] = None,
    ):
        s = self._require_session(session_uuid)
        device = self.repo.get_managed_device_by_uuid(device_uuid)
        if not device:
            raise DeviceNotFoundError(str(device_uuid))

        existing = self.repo.get_session_device(s.id, device.id)
        if existing:
            return existing

        count = self.repo.count_active_devices(s.id)
        if count >= s.max_devices:
            raise SessionFullError("devices", count, s.max_devices)

        self._validate_device_role_invariants(s, device_role, participant_id, managed_by_device_id)

        sd = self.repo.add_session_device(
            session_id=s.id,
            device_id=device.id,
            participant_id=participant_id,
            managed_by_device_id=managed_by_device_id,
            device_role=device_role,
        )
        s.revision += 1
        self.db.commit()
        self.db.refresh(sd)
        return sd

    def update_device_status(self, session_device_id: int, new_status: str, device_revision: int):
        sd = self.repo.get_session_device_by_id(session_device_id)
        if not sd:
            raise DeviceNotFoundError(str(session_device_id))
        if sd.removed_at is not None:
            raise InvalidTransitionError("device", "removed", new_status)
        if sd.revision != device_revision:
            raise RevisionConflictError("session_device", device_revision, sd.revision)
        current = DeviceStatus(sd.status)
        target = DeviceStatus(new_status)
        if target not in DEVICE_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError("device", sd.status, new_status)
        sd.status = new_status
        sd.revision += 1
        self.db.commit()
        self.db.refresh(sd)
        return sd

    def heartbeat(self, session_device_id: int):
        sd = self.repo.get_session_device_by_id(session_device_id)
        if not sd:
            raise DeviceNotFoundError(str(session_device_id))
        if sd.removed_at is not None:
            raise DeviceNotFoundError(f"device {session_device_id} removed")
        now = datetime.now(timezone.utc)
        sd.last_heartbeat = now
        self.db.commit()
        return now

    def remove_device(self, session_device_id: int, session_revision: int):
        sd = self.repo.get_session_device_by_id(session_device_id)
        if not sd:
            raise DeviceNotFoundError(str(session_device_id))
        session = sd.session
        if session.revision != session_revision:
            raise RevisionConflictError("session", session_revision, session.revision)
        sd.removed_at = datetime.now(timezone.utc)
        sd.revision += 1
        session.revision += 1
        self.db.commit()
        self.db.refresh(sd)
        return sd

    def transition_session(self, session_uuid: uuid.UUID, target_status: str, session_revision: int):
        s = self._require_session(session_uuid)
        if s.revision != session_revision:
            raise RevisionConflictError("session", session_revision, s.revision)
        current = SessionStatus(s.status)
        target = SessionStatus(target_status)
        if target not in SESSION_TRANSITIONS.get(current, set()):
            raise InvalidTransitionError("session", s.status, target_status)
        s.status = target_status
        s.revision += 1
        now = datetime.now(timezone.utc)
        if target == SessionStatus.RECORDING:
            s.started_at = now
        elif target == SessionStatus.STOPPED:
            s.stopped_at = now
        elif target == SessionStatus.COMPLETED:
            s.finalized_at = now
        elif target == SessionStatus.CANCELLED:
            s.cancelled_at = now
        self.db.commit()
        self.db.refresh(s)
        return s

    def create_capture_stream(self, session_device_id: int, stream_type: str, preset_json: dict):
        sd = self.repo.get_session_device_by_id(session_device_id)
        if not sd:
            raise DeviceNotFoundError(str(session_device_id))
        existing = self.repo.get_capture_stream(session_device_id, stream_type)
        if existing:
            return existing
        cs = self.repo.add_capture_stream(
            session_device_id=session_device_id,
            stream_type=stream_type,
            preset_json=preset_json,
        )
        self.db.commit()
        self.db.refresh(cs)
        return cs

    def _require_session(self, session_uuid: uuid.UUID) -> MultiCameraSession:
        s = self.repo.get_session_by_uuid(session_uuid)
        if not s:
            raise SessionNotFoundError(str(session_uuid))
        return s

    def _validate_device_role_invariants(
        self,
        session: MultiCameraSession,
        device_role: str,
        participant_id: Optional[int],
        managed_by_device_id: Optional[int],
    ):
        role = DeviceRole(device_role)
        if role == DeviceRole.AUXILIARY_CAMERA:
            if participant_id is not None:
                raise DeviceRoleViolationError("auxiliary_camera must not have participant_id")
            if managed_by_device_id is None:
                raise DeviceRoleViolationError("auxiliary_camera requires managed_by_device_id")
            manager = self.repo.get_session_device_by_id(managed_by_device_id)
            if not manager:
                raise DeviceNotFoundError(f"manager device {managed_by_device_id}")
            if manager.session_id != session.id:
                raise CrossSessionReferenceError("managed_by_device_id belongs to different session")
            if managed_by_device_id == managed_by_device_id and manager.managed_by_device_id is not None:
                raise CrossSessionReferenceError("managed_by_device_id chain depth > 1 (cycle risk)")
        else:
            if managed_by_device_id is not None:
                raise DeviceRoleViolationError(f"{device_role} must not have managed_by_device_id")
            if participant_id is not None:
                p = self.db.query(SessionParticipant).filter(SessionParticipant.id == participant_id).first()
                if not p:
                    raise ParticipantNotFoundError(str(participant_id))
                if p.session_id != session.id:
                    raise CrossSessionReferenceError("participant_id belongs to different session")

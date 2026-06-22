"""Multicamera session API — AN-3B PR-4B3A.

6 endpoints. No UI, no shutter, no recording, no media.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_active_user
from .....models.user import User
from .....models.multicamera_session import SessionParticipant
from .....schemas.multicamera_session import (
    DeviceRole, DeviceType, MultiCameraSessionDTO, ParticipantRole,
    SessionDeviceDTO, SessionParticipantDTO, SessionStatus,
)
from .....services.multicamera.session_service import SessionService
from .....services.multicamera.device_service import DeviceService
from .....services.multicamera.exceptions import (
    CrossSessionReferenceError, DeviceNotFoundError, DeviceRoleViolationError,
    InvalidTransitionError, ParticipantNotFoundError, RevisionConflictError,
    SessionFullError, SessionNotFoundError,
)

router = APIRouter()


# ── Request / Response schemas ───────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    max_participants: int = Field(2, ge=1, le=4)
    max_devices: int = Field(4, ge=1, le=8)


class JoinSessionRequest(BaseModel):
    role: ParticipantRole


class TransitionRequest(BaseModel):
    target_status: SessionStatus
    revision: int


class RegisterDeviceRequest(BaseModel):
    device_uuid: Optional[uuid.UUID] = None
    device_type: Optional[DeviceType] = None
    device_name: Optional[str] = None
    ble_identifier: Optional[str] = None
    device_role: DeviceRole
    participant_id: Optional[int] = None
    managed_by_device_id: Optional[int] = None


class HeartbeatResponse(BaseModel):
    session_device_id: int
    last_heartbeat: datetime


# ── Guards ───────────────────────────────────────────────────────────────────

def _require_participant(db: Session, session_uuid: uuid.UUID, user: User) -> tuple:
    ss = SessionService(db)
    session = ss.get_session(session_uuid)
    participant = db.query(SessionParticipant).filter(
        SessionParticipant.session_id == session.id,
        SessionParticipant.user_id == user.id,
        SessionParticipant.left_at.is_(None),
    ).first()
    if not participant:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a session participant")
    return session, participant


def _require_instructor(db: Session, session_uuid: uuid.UUID, user: User) -> tuple:
    session, participant = _require_participant(db, session_uuid, user)
    if participant.role != "instructor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only instructor can transition session")
    return session, participant


def _handle_service_error(e: Exception):
    if isinstance(e, SessionNotFoundError):
        raise HTTPException(status_code=404, detail="Session not found")
    if isinstance(e, DeviceNotFoundError):
        raise HTTPException(status_code=404, detail="Device not found")
    if isinstance(e, ParticipantNotFoundError):
        raise HTTPException(status_code=404, detail="Participant not found")
    if isinstance(e, RevisionConflictError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, SessionFullError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, InvalidTransitionError):
        raise HTTPException(status_code=422, detail=str(e))
    if isinstance(e, CrossSessionReferenceError):
        raise HTTPException(status_code=422, detail=str(e))
    if isinstance(e, DeviceRoleViolationError):
        raise HTTPException(status_code=422, detail=str(e))
    raise


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/sessions", status_code=status.HTTP_201_CREATED, response_model=MultiCameraSessionDTO)
def create_session(
    body: CreateSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        ss = SessionService(db)
        session = ss.create_session(current_user.id, body.max_participants, body.max_devices)
        ss.join_session(session.session_uuid, current_user.id, "instructor")
        return ss.get_session(session.session_uuid)
    except Exception as e:
        _handle_service_error(e)


@router.get("/sessions/{session_uuid}", response_model=MultiCameraSessionDTO)
def get_session(
    session_uuid: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        session, _ = _require_participant(db, session_uuid, current_user)
        return SessionService(db).get_session(session_uuid)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post("/sessions/{session_uuid}/join", response_model=SessionParticipantDTO)
def join_session(
    session_uuid: uuid.UUID,
    body: JoinSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        return SessionService(db).join_session(session_uuid, current_user.id, body.role.value)
    except Exception as e:
        _handle_service_error(e)


@router.patch("/sessions/{session_uuid}/status", response_model=MultiCameraSessionDTO)
def transition_session(
    session_uuid: uuid.UUID,
    body: TransitionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_instructor(db, session_uuid, current_user)
        ss = SessionService(db)
        ss.transition_session(session_uuid, body.target_status.value, body.revision)
        return ss.get_session(session_uuid)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post("/sessions/{session_uuid}/devices", status_code=status.HTTP_201_CREATED, response_model=SessionDeviceDTO)
def register_device(
    session_uuid: uuid.UUID,
    body: RegisterDeviceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        session, participant = _require_participant(db, session_uuid, current_user)
        ss = SessionService(db)

        if body.device_uuid:
            from .....repositories.multicamera_session_repo import MultiCameraSessionRepo
            repo = MultiCameraSessionRepo(db)
            md = repo.get_managed_device_by_uuid(body.device_uuid)
            if not md:
                raise HTTPException(status_code=404, detail="Managed device not found")
            if md.owner_user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Not device owner")
            device_uuid = body.device_uuid
        elif body.device_type:
            ds = DeviceService(db)
            md = ds.register_managed_device(
                current_user.id, body.device_type.value,
                body.device_name, body.ble_identifier,
            )
            device_uuid = md.device_uuid
        else:
            raise HTTPException(status_code=422, detail="Provide device_uuid or device_type")

        if body.managed_by_device_id is not None:
            from .....repositories.multicamera_session_repo import MultiCameraSessionRepo
            repo = MultiCameraSessionRepo(db)
            manager_sd = repo.get_session_device_by_id(body.managed_by_device_id)
            if manager_sd and manager_sd.participant_id:
                manager_participant = db.query(SessionParticipant).filter(
                    SessionParticipant.id == manager_sd.participant_id
                ).first()
                if manager_participant and manager_participant.user_id != current_user.id:
                    raise HTTPException(status_code=403, detail="Not authorized to manage this device")

        return ss.register_device(
            session_uuid, device_uuid, body.device_role.value,
            participant_id=body.participant_id,
            managed_by_device_id=body.managed_by_device_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post("/sessions/{session_uuid}/devices/{session_device_id}/heartbeat", response_model=HeartbeatResponse)
def heartbeat(
    session_uuid: uuid.UUID,
    session_device_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        session, _ = _require_participant(db, session_uuid, current_user)

        from .....repositories.multicamera_session_repo import MultiCameraSessionRepo
        repo = MultiCameraSessionRepo(db)
        sd = repo.get_session_device_by_id(session_device_id)
        if not sd or sd.session_id != session.id:
            raise HTTPException(status_code=404, detail="Session device not found")

        authorized = False
        if sd.participant_id:
            p = db.query(SessionParticipant).filter(SessionParticipant.id == sd.participant_id).first()
            if p and p.user_id == current_user.id:
                authorized = True
        if not authorized and sd.managed_by_device_id:
            manager = repo.get_session_device_by_id(sd.managed_by_device_id)
            if manager and manager.participant_id:
                mp = db.query(SessionParticipant).filter(SessionParticipant.id == manager.participant_id).first()
                if mp and mp.user_id == current_user.id:
                    authorized = True
        if not authorized:
            raise HTTPException(status_code=403, detail="Not authorized for this device")

        ss = SessionService(db)
        ts = ss.heartbeat(session_device_id)
        return HeartbeatResponse(session_device_id=session_device_id, last_heartbeat=ts)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)

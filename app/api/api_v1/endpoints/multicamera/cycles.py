"""Multicamera capture cycle domain commands (PR-MC1).

9 endpoints:
  POST   /multicamera/sessions/{uuid}/activate
  POST   /multicamera/sessions/{uuid}/cycles
  POST   /multicamera/sessions/{uuid}/cycles/{cycle_id}/schedule
  POST   /multicamera/sessions/{uuid}/cycles/{cycle_id}/stop
  POST   /multicamera/sessions/{uuid}/cycles/{cycle_id}/abort
  POST   /multicamera/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_id}/confirm-start
  POST   /multicamera/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_id}/confirm-stop
  POST   /multicamera/sessions/{uuid}/cycles/{cycle_id}/devices/{sd_id}/report-failure
  POST   /multicamera/sessions/{uuid}/finalize
  GET    /multicamera/sessions/{uuid}/cycles
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_active_user
from .....models.multicamera_session import SessionParticipant
from .....models.user import User
from .....repositories.multicamera_session_repo import MultiCameraSessionRepo
from .....schemas.multicamera_cycle import (
    AbortCycleRequest,
    CaptureCycleDTO,
    ConfirmDeviceStartRequest,
    ConfirmDeviceStopRequest,
    CreateCycleRequest,
    FinalizeSessionRequest,
    ReportDeviceFailureRequest,
    ScheduleCycleRequest,
    StopCycleRequest,
)
from .....schemas.multicamera_session import MultiCameraSessionDTO
from .....services.multicamera.cycle_service import CycleService
from .....services.multicamera.exceptions import (
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

router = APIRouter()


# ── Guards ────────────────────────────────────────────────────────────────────

def _require_participant(db: Session, session_uuid: uuid.UUID, user: User):
    from .....services.multicamera.session_service import SessionService
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


def _require_instructor(db: Session, session_uuid: uuid.UUID, user: User):
    session, participant = _require_participant(db, session_uuid, user)
    if participant.role != "instructor":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only instructor can perform this action")
    return session, participant


def _require_cycle_in_session(db: Session, session_uuid: uuid.UUID, cycle_id: int):
    repo = MultiCameraSessionRepo(db)
    session = repo.get_session_by_uuid(session_uuid)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    cycle = repo.get_cycle_by_id(cycle_id)
    if not cycle or cycle.session_id != session.id:
        raise HTTPException(status_code=404, detail="Cycle not found in this session")
    return session, cycle


def _handle_service_error(e: Exception):
    if isinstance(e, SessionNotFoundError):
        raise HTTPException(status_code=404, detail="Session not found")
    if isinstance(e, CycleNotFoundError):
        raise HTTPException(status_code=404, detail="Cycle not found")
    if isinstance(e, DeviceNotFoundError):
        raise HTTPException(status_code=404, detail="Device not found")
    if isinstance(e, RevisionConflictError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, CycleConflictError):
        raise HTTPException(status_code=409, detail=str(e))
    if isinstance(e, InvalidTransitionError):
        raise HTTPException(status_code=422, detail=str(e))
    if isinstance(e, DeviceNotReadyError):
        raise HTTPException(status_code=422, detail=str(e))
    if isinstance(e, NoCycleDevicesError):
        raise HTTPException(status_code=422, detail=str(e))
    if isinstance(e, InstructorRequiredError):
        raise HTTPException(status_code=403, detail=str(e))
    raise


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/sessions/{session_uuid}/activate",
    response_model=MultiCameraSessionDTO,
    summary="Activate session for multi-cycle recording (LOBBY → ACTIVE)",
)
def activate_session(
    session_uuid: uuid.UUID,
    body: ScheduleCycleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_instructor(db, session_uuid, current_user)
        svc = CycleService(db)
        session = svc.activate_session(session_uuid, body.revision)
        db.expire_all()
        from .....services.multicamera.session_service import SessionService
        return SessionService(db).get_session(session_uuid)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/cycles",
    status_code=status.HTTP_201_CREATED,
    response_model=CaptureCycleDTO,
    summary="Create new capture cycle (snapshot of active devices)",
)
def create_cycle(
    session_uuid: uuid.UUID,
    body: CreateCycleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _, participant = _require_instructor(db, session_uuid, current_user)
        svc = CycleService(db)
        cycle = svc.create_cycle(session_uuid, body.idempotency_key, participant.id)
        return cycle
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/cycles/{cycle_id}/schedule",
    response_model=CaptureCycleDTO,
    summary="Schedule cycle: check device readiness, set start time, → RECORDING_PENDING",
)
def schedule_cycle(
    session_uuid: uuid.UUID,
    cycle_id: int,
    body: ScheduleCycleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_instructor(db, session_uuid, current_user)
        _require_cycle_in_session(db, session_uuid, cycle_id)
        svc = CycleService(db)
        return svc.schedule_cycle(cycle_id, body.revision)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/cycles/{cycle_id}/stop",
    response_model=CaptureCycleDTO,
    summary="Request cycle stop: → STOPPING, devices report confirm-stop independently",
)
def stop_cycle(
    session_uuid: uuid.UUID,
    cycle_id: int,
    body: StopCycleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_instructor(db, session_uuid, current_user)
        _require_cycle_in_session(db, session_uuid, cycle_id)
        svc = CycleService(db)
        return svc.stop_cycle(cycle_id, body.revision)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/cycles/{cycle_id}/abort",
    response_model=CaptureCycleDTO,
    summary="Abort cycle from any non-terminal state → ABORTED",
)
def abort_cycle(
    session_uuid: uuid.UUID,
    cycle_id: int,
    body: AbortCycleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_instructor(db, session_uuid, current_user)
        _require_cycle_in_session(db, session_uuid, cycle_id)
        svc = CycleService(db)
        return svc.abort_cycle(cycle_id, body.revision, body.reason)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/cycles/{cycle_id}/devices/{session_device_id}/confirm-start",
    response_model=CaptureCycleDTO,
    summary="Device confirms recording started; all required → RECORDING",
)
def confirm_device_start(
    session_uuid: uuid.UUID,
    cycle_id: int,
    session_device_id: int,
    body: ConfirmDeviceStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_participant(db, session_uuid, current_user)
        _require_cycle_in_session(db, session_uuid, cycle_id)
        svc = CycleService(db)
        return svc.confirm_device_start(
            cycle_id, session_device_id, body.started_at, body.cycle_device_revision
        )
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/cycles/{cycle_id}/devices/{session_device_id}/confirm-stop",
    response_model=CaptureCycleDTO,
    summary="Device confirms recording stopped; all required → COMPLETED",
)
def confirm_device_stop(
    session_uuid: uuid.UUID,
    cycle_id: int,
    session_device_id: int,
    body: ConfirmDeviceStopRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_participant(db, session_uuid, current_user)
        _require_cycle_in_session(db, session_uuid, cycle_id)
        svc = CycleService(db)
        return svc.confirm_device_stop(
            cycle_id, session_device_id, body.stopped_at, body.cycle_device_revision
        )
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/cycles/{cycle_id}/devices/{session_device_id}/report-failure",
    response_model=CaptureCycleDTO,
    summary="Device reports failure; may auto-complete cycle if all required resolved",
)
def report_device_failure(
    session_uuid: uuid.UUID,
    cycle_id: int,
    session_device_id: int,
    body: ReportDeviceFailureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_participant(db, session_uuid, current_user)
        _require_cycle_in_session(db, session_uuid, cycle_id)
        svc = CycleService(db)
        return svc.report_device_failure(
            cycle_id, session_device_id, body.failure_reason, body.cycle_device_revision
        )
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.post(
    "/sessions/{session_uuid}/finalize",
    response_model=MultiCameraSessionDTO,
    summary="Finalize session (ACTIVE → COMPLETED); all cycles must be terminal",
)
def finalize_session(
    session_uuid: uuid.UUID,
    body: FinalizeSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_instructor(db, session_uuid, current_user)
        svc = CycleService(db)
        svc.finalize_session(session_uuid, body.revision)
        db.expire_all()
        from .....services.multicamera.session_service import SessionService
        return SessionService(db).get_session(session_uuid)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)


@router.get(
    "/sessions/{session_uuid}/cycles",
    response_model=List[CaptureCycleDTO],
    summary="List all capture cycles for a session (ordered by cycle_index)",
)
def list_cycles(
    session_uuid: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        _require_participant(db, session_uuid, current_user)
        svc = CycleService(db)
        return svc.list_cycles(session_uuid)
    except HTTPException:
        raise
    except Exception as e:
        _handle_service_error(e)

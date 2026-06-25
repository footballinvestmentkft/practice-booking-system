"""Pydantic schemas for multicamera capture cycles (PR-MC1)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class CycleStatus(str, Enum):
    PREPARING = "preparing"
    RECORDING_PENDING = "recording_pending"
    RECORDING = "recording"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class CycleDeviceRecordingStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED_START = "confirmed_start"
    CONFIRMED_STOP = "confirmed_stop"
    FAILED = "failed"


class CycleResult(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class CaptureCycleDeviceDTO(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    capture_cycle_id: int
    session_device_id: int
    required: bool
    recording_status: CycleDeviceRecordingStatus
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    revision: int


class CaptureCycleDTO(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    session_id: int
    cycle_index: int
    status: CycleStatus
    result: Optional[CycleResult] = None
    scheduled_start_at: Optional[datetime] = None
    recording_started_at: Optional[datetime] = None
    stop_requested_at: Optional[datetime] = None
    recording_stopped_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None
    created_by_participant_id: int
    idempotency_key: str
    revision: int
    created_at: datetime
    updated_at: datetime
    cycle_devices: List[CaptureCycleDeviceDTO] = []


class CreateCycleRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=1, max_length=64)


class ScheduleCycleRequest(BaseModel):
    revision: int


class StopCycleRequest(BaseModel):
    revision: int


class AbortCycleRequest(BaseModel):
    revision: int
    reason: Optional[str] = None


class ConfirmDeviceStartRequest(BaseModel):
    started_at: datetime
    cycle_device_revision: int

    @field_validator("started_at")
    @classmethod
    def require_timezone(cls, v):
        if v.tzinfo is None:
            raise ValueError("started_at must be timezone-aware UTC")
        return v


class ConfirmDeviceStopRequest(BaseModel):
    stopped_at: datetime
    cycle_device_revision: int

    @field_validator("stopped_at")
    @classmethod
    def require_timezone(cls, v):
        if v.tzinfo is None:
            raise ValueError("stopped_at must be timezone-aware UTC")
        return v


class ReportDeviceFailureRequest(BaseModel):
    failure_reason: str = Field(..., min_length=1, max_length=500)
    cycle_device_revision: int


class FinalizeSessionRequest(BaseModel):
    revision: int

"""Multicamera session contract DTOs — AN-3B PR-4B2.

Pydantic schemas for session, participant, device, stream, and calibration.
No API endpoint in this PR — HTTP mapping deferred to PR-4B3A.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    LOBBY = "lobby"
    DEVICES_READY = "devices_ready"
    RECORDING = "recording"
    STOPPED = "stopped"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ParticipantRole(str, Enum):
    INSTRUCTOR = "instructor"
    PLAYER = "player"
    OBSERVER = "observer"


class DeviceType(str, Enum):
    IPHONE = "iphone"
    IPAD = "ipad"
    GOPRO = "gopro"


class DeviceRole(str, Enum):
    PLAYER_PRIMARY = "player_primary"
    PLAYER_SECONDARY = "player_secondary"
    INSTRUCTOR_PRIMARY = "instructor_primary"
    AUXILIARY_CAMERA = "auxiliary_camera"


class DeviceStatus(str, Enum):
    REGISTERED = "registered"
    READY = "ready"
    RECORDING = "recording"
    STOPPED = "stopped"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class StreamType(str, Enum):
    VIDEO = "video"
    SKELETON_2D = "skeleton_2d"
    SKELETON_3D = "skeleton_3d"
    AUDIO = "audio"
    TELEMETRY = "telemetry"


class ManagedDeviceDTO(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    device_uuid: uuid.UUID
    owner_user_id: int
    device_type: DeviceType
    device_name: Optional[str] = None
    ble_identifier: Optional[str] = None
    is_active: bool
    last_seen_at: Optional[datetime] = None
    removed_at: Optional[datetime] = None


class SessionParticipantDTO(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    session_id: int
    user_id: int
    role: ParticipantRole
    revision: int
    joined_at: datetime
    left_at: Optional[datetime] = None


class SessionDeviceDTO(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    session_id: int
    device_id: int
    participant_id: Optional[int] = None
    managed_by_device_id: Optional[int] = None
    device_role: DeviceRole
    status: DeviceStatus
    revision: int
    last_heartbeat: Optional[datetime] = None
    registered_at: datetime
    removed_at: Optional[datetime] = None


class CaptureStreamDTO(BaseModel):
    """Contract placeholder. Multiple streams per device+type
    (recording attempts, restarts) handled in PR-4B3B."""
    model_config = {"from_attributes": True}
    id: int
    session_device_id: int
    stream_type: StreamType
    preset_json: dict
    revision: int
    created_at: datetime
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None


class CalibrationPlaceholder(BaseModel):
    schema_version: int = Field(1, ge=1)
    calibration_id: Optional[uuid.UUID] = None
    world_origin_camera_id: Optional[int] = None
    intrinsic_cameras: list = Field(default_factory=list)
    stereo_pairs: list = Field(default_factory=list)
    sync_metadata: Optional[dict] = None


class MultiCameraSessionDTO(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    session_uuid: uuid.UUID
    status: SessionStatus
    created_by_user_id: int
    max_participants: int
    max_devices: int
    revision: int
    calibration: Optional[CalibrationPlaceholder] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    finalized_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    participants: List[SessionParticipantDTO] = []
    devices: List[SessionDeviceDTO] = []
    streams: List[CaptureStreamDTO] = []

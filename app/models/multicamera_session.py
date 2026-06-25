import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, SmallInteger, String, Text,
    ForeignKey, CheckConstraint, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import text

from ..database import Base


class SessionStatus(str, enum.Enum):
    LOBBY = "lobby"
    DEVICES_READY = "devices_ready"
    RECORDING_PENDING = "recording_pending"
    RECORDING = "recording"
    STOPPED = "stopped"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ACTIVE = "active"


class ParticipantRole(str, enum.Enum):
    INSTRUCTOR = "instructor"
    PLAYER = "player"
    OBSERVER = "observer"


class DeviceRole(str, enum.Enum):
    PLAYER_PRIMARY = "player_primary"
    PLAYER_SECONDARY = "player_secondary"
    INSTRUCTOR_PRIMARY = "instructor_primary"
    AUXILIARY_CAMERA = "auxiliary_camera"


class DeviceStatus(str, enum.Enum):
    REGISTERED = "registered"
    READY = "ready"
    RECORDING = "recording"
    STOPPED = "stopped"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class StreamType(str, enum.Enum):
    VIDEO = "video"
    SKELETON_2D = "skeleton_2d"
    SKELETON_3D = "skeleton_3d"
    AUDIO = "audio"
    TELEMETRY = "telemetry"


class CycleStatus(str, enum.Enum):
    PREPARING = "preparing"
    RECORDING_PENDING = "recording_pending"
    RECORDING = "recording"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class CycleDeviceRecordingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED_START = "confirmed_start"
    CONFIRMED_STOP = "confirmed_stop"
    FAILED = "failed"


class CycleResult(str, enum.Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


SESSION_TRANSITIONS = {
    SessionStatus.LOBBY: {SessionStatus.DEVICES_READY, SessionStatus.ACTIVE, SessionStatus.CANCELLED},
    SessionStatus.DEVICES_READY: {SessionStatus.RECORDING_PENDING, SessionStatus.LOBBY, SessionStatus.CANCELLED},
    SessionStatus.RECORDING_PENDING: {SessionStatus.RECORDING, SessionStatus.DEVICES_READY, SessionStatus.CANCELLED},
    SessionStatus.RECORDING: {SessionStatus.STOPPED},
    SessionStatus.STOPPED: {SessionStatus.FINALIZING, SessionStatus.CANCELLED},
    SessionStatus.ACTIVE: {SessionStatus.FINALIZING, SessionStatus.CANCELLED},
    SessionStatus.FINALIZING: {SessionStatus.COMPLETED},
    SessionStatus.COMPLETED: set(),
    SessionStatus.CANCELLED: set(),
}

DEVICE_TRANSITIONS = {
    DeviceStatus.REGISTERED: {DeviceStatus.READY},
    DeviceStatus.READY: {DeviceStatus.RECORDING},
    DeviceStatus.RECORDING: {DeviceStatus.STOPPED, DeviceStatus.DISCONNECTED, DeviceStatus.ERROR},
    DeviceStatus.DISCONNECTED: {DeviceStatus.RECORDING, DeviceStatus.ERROR},
    DeviceStatus.STOPPED: set(),
    DeviceStatus.ERROR: set(),
}

CYCLE_TRANSITIONS = {
    CycleStatus.PREPARING: {CycleStatus.RECORDING_PENDING, CycleStatus.ABORTED},
    CycleStatus.RECORDING_PENDING: {CycleStatus.RECORDING, CycleStatus.FAILED, CycleStatus.ABORTED},
    CycleStatus.RECORDING: {CycleStatus.STOPPING, CycleStatus.ABORTED},
    CycleStatus.STOPPING: {CycleStatus.COMPLETED, CycleStatus.FAILED, CycleStatus.ABORTED},
    CycleStatus.COMPLETED: set(),
    CycleStatus.FAILED: set(),
    CycleStatus.ABORTED: set(),
}

_CYCLE_TERMINAL = {CycleStatus.COMPLETED, CycleStatus.FAILED, CycleStatus.ABORTED}


def is_cycle_terminal(status: CycleStatus) -> bool:
    return status in _CYCLE_TERMINAL


class MultiCameraSession(Base):
    __tablename__ = "multicamera_sessions"

    id = Column(Integer, primary_key=True)
    session_uuid = Column(
        PG_UUID(as_uuid=True), nullable=False, unique=True,
        server_default=text("gen_random_uuid()"),
    )
    status = Column(String(30), nullable=False, server_default="lobby")
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    max_participants = Column(SmallInteger, nullable=False, server_default="2")
    max_devices = Column(SmallInteger, nullable=False, server_default="4")
    revision = Column(Integer, nullable=False, server_default="1")
    calibration_json = Column(JSONB, nullable=True)
    scheduled_start_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True))
    stopped_at = Column(DateTime(timezone=True))
    finalized_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('lobby','devices_ready','recording_pending','recording','stopped',"
            "'finalizing','completed','cancelled','active')",
            name="ck_mcs_status",
        ),
        CheckConstraint("max_participants BETWEEN 1 AND 4", name="ck_mcs_max_participants"),
        CheckConstraint("max_devices BETWEEN 1 AND 8", name="ck_mcs_max_devices"),
    )

    creator = relationship("User", foreign_keys=[created_by_user_id])
    participants = relationship("SessionParticipant", back_populates="session", cascade="all, delete-orphan")
    devices = relationship("SessionDevice", back_populates="session", cascade="all, delete-orphan")
    cycles = relationship("CaptureCycle", back_populates="session", cascade="all, delete-orphan")


class SessionParticipant(Base):
    __tablename__ = "session_participants"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("multicamera_sessions.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    role = Column(String(30), nullable=False)
    revision = Column(Integer, nullable=False, server_default="1")
    joined_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    left_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("role IN ('instructor','player','observer')", name="ck_sp_role"),
        UniqueConstraint("session_id", "user_id", name="uq_sp_session_user"),
    )

    session = relationship("MultiCameraSession", back_populates="participants")
    user = relationship("User", foreign_keys=[user_id])
    devices = relationship("SessionDevice", back_populates="participant")


class SessionDevice(Base):
    __tablename__ = "session_devices"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("multicamera_sessions.id", ondelete="CASCADE"), nullable=False)
    device_id = Column(Integer, ForeignKey("managed_devices.id"), nullable=False)
    participant_id = Column(Integer, ForeignKey("session_participants.id"), nullable=True)
    managed_by_device_id = Column(Integer, ForeignKey("session_devices.id"), nullable=True)
    device_role = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, server_default="registered")
    revision = Column(Integer, nullable=False, server_default="1")
    last_heartbeat = Column(DateTime(timezone=True))
    registered_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    removed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "device_role IN ('player_primary','player_secondary','instructor_primary','auxiliary_camera')",
            name="ck_sd_device_role",
        ),
        CheckConstraint(
            "status IN ('registered','ready','recording','stopped','disconnected','error')",
            name="ck_sd_status",
        ),
        UniqueConstraint("session_id", "device_id", name="uq_sd_session_device"),
    )

    session = relationship("MultiCameraSession", back_populates="devices")
    device = relationship("ManagedDevice", back_populates="session_devices")
    participant = relationship("SessionParticipant", back_populates="devices")
    manager_device = relationship("SessionDevice", remote_side=[id], foreign_keys=[managed_by_device_id])
    streams = relationship("CaptureStream", back_populates="session_device", cascade="all, delete-orphan")
    cycle_devices = relationship("CaptureCycleDevice", back_populates="session_device")


class CaptureStream(Base):
    """Contract placeholder — no media lifecycle in PR-4B2.
    Multiple streams per device+type (recording attempts) handled in PR-4B3B."""
    __tablename__ = "capture_streams"

    id = Column(Integer, primary_key=True)
    session_device_id = Column(Integer, ForeignKey("session_devices.id", ondelete="CASCADE"), nullable=False)
    capture_cycle_id = Column(Integer, ForeignKey("capture_cycles.id"), nullable=True)
    stream_type = Column(String(20), nullable=False)
    preset_json = Column(JSONB, nullable=False)
    revision = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True))
    stopped_at = Column(DateTime(timezone=True))
    capture_result = Column(String(20))

    __table_args__ = (
        CheckConstraint(
            "stream_type IN ('video','skeleton_2d','skeleton_3d','audio','telemetry')",
            name="ck_cs_stream_type",
        ),
        CheckConstraint(
            "capture_result IS NULL OR capture_result IN ('success','error','interrupted')",
            name="ck_cs_capture_result",
        ),
    )

    session_device = relationship("SessionDevice", back_populates="streams")
    cycle = relationship("CaptureCycle", back_populates="streams")


class CaptureCycle(Base):
    __tablename__ = "capture_cycles"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("multicamera_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    cycle_index = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, server_default="preparing")
    result = Column(String(20), nullable=True)
    scheduled_start_at = Column(DateTime(timezone=True), nullable=True)
    recording_started_at = Column(DateTime(timezone=True), nullable=True)
    stop_requested_at = Column(DateTime(timezone=True), nullable=True)
    recording_stopped_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_by_participant_id = Column(Integer, ForeignKey("session_participants.id"), nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint(
            "status IN ('preparing','recording_pending','recording','stopping',"
            "'completed','failed','aborted')",
            name="ck_cc_status",
        ),
        CheckConstraint(
            "result IS NULL OR result IN ('success','partial','failed')",
            name="ck_cc_result",
        ),
        CheckConstraint("cycle_index >= 0", name="ck_cc_cycle_index_nonneg"),
        UniqueConstraint("session_id", "cycle_index", name="uq_cc_session_cycle"),
        UniqueConstraint("session_id", "idempotency_key", name="uq_cc_session_idempotency"),
    )

    session = relationship("MultiCameraSession", back_populates="cycles")
    creator_participant = relationship("SessionParticipant", foreign_keys=[created_by_participant_id])
    cycle_devices = relationship("CaptureCycleDevice", back_populates="cycle", cascade="all, delete-orphan")
    streams = relationship("CaptureStream", back_populates="cycle")


class CaptureCycleDevice(Base):
    __tablename__ = "capture_cycle_devices"

    id = Column(Integer, primary_key=True)
    capture_cycle_id = Column(Integer, ForeignKey("capture_cycles.id", ondelete="CASCADE"), nullable=False, index=True)
    session_device_id = Column(Integer, ForeignKey("session_devices.id"), nullable=False)
    required = Column(Boolean, nullable=False, server_default="true")
    recording_status = Column(String(20), nullable=False, server_default="pending")
    started_at = Column(DateTime(timezone=True), nullable=True)
    stopped_at = Column(DateTime(timezone=True), nullable=True)
    failure_reason = Column(Text, nullable=True)
    revision = Column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "recording_status IN ('pending','confirmed_start','confirmed_stop','failed')",
            name="ck_ccd_recording_status",
        ),
        UniqueConstraint("capture_cycle_id", "session_device_id", name="uq_ccd_cycle_device"),
    )

    cycle = relationship("CaptureCycle", back_populates="cycle_devices")
    session_device = relationship("SessionDevice", back_populates="cycle_devices")

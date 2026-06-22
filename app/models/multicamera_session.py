import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Integer, SmallInteger, String, Text,
    ForeignKey, CheckConstraint, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import text

from ..database import Base


class SessionStatus(str, enum.Enum):
    LOBBY = "lobby"
    DEVICES_READY = "devices_ready"
    RECORDING = "recording"
    STOPPED = "stopped"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


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


SESSION_TRANSITIONS = {
    SessionStatus.LOBBY: {SessionStatus.DEVICES_READY, SessionStatus.CANCELLED},
    SessionStatus.DEVICES_READY: {SessionStatus.RECORDING, SessionStatus.LOBBY, SessionStatus.CANCELLED},
    SessionStatus.RECORDING: {SessionStatus.STOPPED},
    SessionStatus.STOPPED: {SessionStatus.FINALIZING, SessionStatus.CANCELLED},
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
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True))
    stopped_at = Column(DateTime(timezone=True))
    finalized_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('lobby','devices_ready','recording','stopped','finalizing','completed','cancelled')",
            name="ck_mcs_status",
        ),
        CheckConstraint("max_participants BETWEEN 1 AND 4", name="ck_mcs_max_participants"),
        CheckConstraint("max_devices BETWEEN 1 AND 8", name="ck_mcs_max_devices"),
    )

    creator = relationship("User", foreign_keys=[created_by_user_id])
    participants = relationship("SessionParticipant", back_populates="session", cascade="all, delete-orphan")
    devices = relationship("SessionDevice", back_populates="session", cascade="all, delete-orphan")


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


class CaptureStream(Base):
    """Contract placeholder — no media lifecycle in PR-4B2.
    Multiple streams per device+type (recording attempts) handled in PR-4B3B."""
    __tablename__ = "capture_streams"

    id = Column(Integer, primary_key=True)
    session_device_id = Column(Integer, ForeignKey("session_devices.id", ondelete="CASCADE"), nullable=False)
    stream_type = Column(String(20), nullable=False)
    preset_json = Column(JSONB, nullable=False)
    revision = Column(Integer, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True))
    stopped_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "stream_type IN ('video','skeleton_2d','skeleton_3d','audio','telemetry')",
            name="ck_cs_stream_type",
        ),
    )

    session_device = relationship("SessionDevice", back_populates="streams")

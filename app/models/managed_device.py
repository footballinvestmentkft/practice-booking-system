import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, String, Text,
    ForeignKey, CheckConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import text

from ..database import Base


class DeviceType(str, enum.Enum):
    IPHONE = "iphone"
    IPAD = "ipad"
    GOPRO = "gopro"


class ManagedDevice(Base):
    __tablename__ = "managed_devices"

    id = Column(Integer, primary_key=True)
    device_uuid = Column(
        PG_UUID(as_uuid=True), nullable=False, unique=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_type = Column(String(20), nullable=False)
    device_name = Column(String(100))
    ble_identifier = Column(String(100))
    is_active = Column(Boolean, nullable=False, server_default="true")
    last_seen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    removed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "device_type IN ('iphone','ipad','gopro')",
            name="ck_md_device_type",
        ),
    )

    owner = relationship("User", foreign_keys=[owner_user_id])
    session_devices = relationship("SessionDevice", back_populates="device")

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.repositories.multicamera_session_repo import MultiCameraSessionRepo


class DeviceService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = MultiCameraSessionRepo(db)

    def register_managed_device(
        self,
        user_id: int,
        device_type: str,
        device_name: str = None,
        ble_identifier: str = None,
    ):
        if ble_identifier:
            existing = self.repo.get_managed_device_by_ble(user_id, ble_identifier)
            if existing:
                return existing
        d = self.repo.add_managed_device(
            owner_user_id=user_id,
            device_type=device_type,
            device_name=device_name,
            ble_identifier=ble_identifier,
        )
        self.db.commit()
        self.db.refresh(d)
        return d

    def deactivate_device(self, device_uuid: uuid.UUID):
        d = self.repo.get_managed_device_by_uuid(device_uuid)
        if not d:
            return None
        d.is_active = False
        d.removed_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(d)
        return d

    def list_user_devices(self, user_id: int, active_only: bool = True):
        from app.models.managed_device import ManagedDevice
        q = self.db.query(ManagedDevice).filter(ManagedDevice.owner_user_id == user_id)
        if active_only:
            q = q.filter(ManagedDevice.is_active.is_(True))
        return q.all()

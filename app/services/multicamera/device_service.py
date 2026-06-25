from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
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

    def register_managed_device_with_uuid(
        self,
        user_id: int,
        device_uuid: _uuid.UUID,
        device_type: str,
        device_name: str = None,
        ble_identifier: str = None,
    ):
        """Create-or-get a ManagedDevice with a client-provided UUID.

        If a concurrent request already inserted the same UUID, catches the
        unique-constraint violation, rolls back the failed INSERT, and returns
        the existing row.
        """
        existing = self.repo.get_managed_device_by_uuid(device_uuid)
        if existing:
            return existing
        try:
            d = self.repo.add_managed_device(
                owner_user_id=user_id,
                device_type=device_type,
                device_name=device_name,
                ble_identifier=ble_identifier,
                device_uuid=device_uuid,
            )
            self.db.commit()
            self.db.refresh(d)
            return d
        except IntegrityError:
            self.db.rollback()
            return self.repo.get_managed_device_by_uuid(device_uuid)

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

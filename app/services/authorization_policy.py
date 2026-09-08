"""Canonical object-level authorization policies shared by web and API routes."""

from typing import Any

from app.models.user import UserRole


def _role_value(actor: Any) -> str | None:
    role = getattr(actor, "role", None)
    return getattr(role, "value", role)


class AuthorizationPolicy:
    """Pure authorization decisions; callers map denial to their transport."""

    @staticmethod
    def can_access_adaptive_session(actor: Any, session: Any) -> bool:
        return (
            actor is not None
            and session is not None
            and getattr(actor, "id", None) == getattr(session, "user_id", None)
        )

    @staticmethod
    def can_manage_attendance(actor: Any, session: Any) -> bool:
        if actor is None or session is None:
            return False
        role = _role_value(actor)
        if role == UserRole.ADMIN.value:
            return True
        return (
            role == UserRole.INSTRUCTOR.value
            and getattr(session, "instructor_id", None) == getattr(actor, "id", None)
        )

"""Transport mapping for canonical Player participation errors."""
from fastapi import HTTPException

from app.services.player_participation_service import ParticipationError


_NOT_FOUND = {"SESSION_NOT_FOUND", "BOOKING_NOT_FOUND"}
_FORBIDDEN = {
    "PLAYER_ROLE_REQUIRED",
    "NOT_BOOKING_OWNER",
    "ADMIN_REQUIRED",
    "ATTENDANCE_MANAGER_REQUIRED",
    "INSTRUCTOR_NOT_ASSIGNED",
    "INSTRUCTOR_SCOPE_DENIED",
    "CANONICAL_COACH_ENTITLEMENT_REQUIRED",
}
_CONFLICT = {
    "SESSION_AT_CAPACITY",
    "BOOKING_CONCURRENCY_CONFLICT",
    "ATTENDANCE_CONCURRENCY_CONFLICT",
}


def participation_http_error(error: ParticipationError) -> HTTPException:
    if error.code in _NOT_FOUND:
        status_code = 404
    elif error.code in _FORBIDDEN:
        status_code = 403
    elif error.code in _CONFLICT:
        status_code = 409
    else:
        status_code = 400
    return HTTPException(status_code=status_code, detail=error.code)

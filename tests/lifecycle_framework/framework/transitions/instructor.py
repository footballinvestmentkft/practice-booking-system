"""Instructor assignment and acceptance transitions."""
from __future__ import annotations

import requests

from .._http import require_ok


def assign_instructor(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    instructor_id: int,
    message: str = "Assigned via lifecycle framework",
) -> dict:
    """POST /api/v1/tournaments/{id}/assign-instructor (open pool flow)."""
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/assign-instructor",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"instructor_id": instructor_id, "assignment_message": message},
        timeout=15,
    )
    return require_ok(resp, "assign_instructor")


def direct_assign_instructor(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    instructor_id: int,
    message: str = "Direct-assigned via lifecycle framework",
) -> dict:
    """POST /api/v1/tournaments/{id}/direct-assign-instructor."""
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/direct-assign-instructor",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"instructor_id": instructor_id, "assignment_message": message},
        timeout=15,
    )
    return require_ok(resp, "direct_assign_instructor")


def accept_instructor_assignment(
    base_url: str,
    instructor_token: str,
    tournament_id: int,
) -> dict:
    """POST /api/v1/tournaments/{id}/instructor-assignment/accept."""
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/instructor-assignment/accept",
        headers={"Authorization": f"Bearer {instructor_token}"},
        json={},
        timeout=15,
    )
    return require_ok(resp, "accept_instructor_assignment")

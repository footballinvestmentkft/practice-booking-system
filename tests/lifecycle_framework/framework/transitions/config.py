"""Schedule and reward config transitions."""
from __future__ import annotations

import requests

from .._http import require_ok


def set_schedule_config(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    *,
    match_duration_minutes: int = 90,
    break_duration_minutes: int = 15,
    parallel_fields: int = 1,
) -> dict:
    """PATCH /api/v1/tournaments/{id}/schedule-config."""
    resp = requests.patch(
        f"{base_url}/api/v1/tournaments/{tournament_id}/schedule-config",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "match_duration_minutes": match_duration_minutes,
            "break_duration_minutes": break_duration_minutes,
            "parallel_fields": parallel_fields,
        },
        timeout=15,
    )
    return require_ok(resp, "set_schedule_config")


def set_reward_config(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    *,
    skill_mappings: list[dict] | None = None,
) -> dict:
    """POST /api/v1/tournaments/{id}/reward-config."""
    if skill_mappings is None:
        skill_mappings = [
            {"skill": "speed", "weight": 1.0, "category": "PHYSICAL", "enabled": True}
        ]
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/reward-config",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"skill_mappings": skill_mappings},
        timeout=15,
    )
    return require_ok(resp, "set_reward_config")

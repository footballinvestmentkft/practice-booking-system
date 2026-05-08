"""Tournament creation and status transition helpers."""
from __future__ import annotations

import requests

from .._http import require_ok


def create_tournament(
    base_url: str,
    admin_token: str,
    *,
    scenario: str = "smoke_test",
    player_count: int = 0,
    max_players: int = 16,
    tournament_format: str = "HEAD_TO_HEAD",
    tournament_type_code: str = "knockout",
    auto_generate_sessions: bool = False,
    simulation_mode: str = "manual",
    age_group: str = "AMATEUR",
    enrollment_cost: int = 0,
    initial_tournament_status: str = "SEEKING_INSTRUCTOR",
    campus_ids: list[int] | None = None,
    dry_run: bool = False,
    confirmed: bool = False,
    participant_type: str = "INDIVIDUAL",
) -> dict:
    """POST /api/v1/tournaments/ops/run-scenario and return the response dict."""
    payload: dict = {
        "scenario": scenario,
        "player_count": player_count,
        "max_players": max_players,
        "tournament_format": tournament_format,
        "tournament_type_code": tournament_type_code,
        "auto_generate_sessions": auto_generate_sessions,
        "simulation_mode": simulation_mode,
        "age_group": age_group,
        "enrollment_cost": enrollment_cost,
        "initial_tournament_status": initial_tournament_status,
        "dry_run": dry_run,
        "confirmed": confirmed,
        "participant_type": participant_type,
    }
    if campus_ids is not None:
        payload["campus_ids"] = campus_ids

    resp = requests.post(
        f"{base_url}/api/v1/tournaments/ops/run-scenario",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=payload,
        timeout=30,
    )
    return require_ok(resp, "create_tournament")


def transition_status(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    new_status: str,
    reason: str = "",
) -> dict:
    """PATCH /api/v1/tournaments/{id}/status."""
    resp = requests.patch(
        f"{base_url}/api/v1/tournaments/{tournament_id}/status",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"new_status": new_status, "reason": reason or f"framework → {new_status}"},
        timeout=15,
    )
    return require_ok(resp, f"transition:{new_status}")

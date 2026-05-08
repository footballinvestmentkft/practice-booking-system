"""Campus assignment and player enrollment transitions."""
from __future__ import annotations

import requests

from .._http import require_ok


def assign_campus(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    campus_id: int,
) -> dict:
    """PATCH /api/v1/tournaments/{id} to set campus_id."""
    resp = requests.patch(
        f"{base_url}/api/v1/tournaments/{tournament_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"campus_id": campus_id},
        timeout=15,
    )
    return require_ok(resp, "assign_campus")


def enroll_player(
    base_url: str,
    player_token: str,
    tournament_id: int,
) -> dict:
    """POST /api/v1/tournaments/{id}/enroll for a single player."""
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/enroll",
        headers={"Authorization": f"Bearer {player_token}"},
        json={},
        timeout=15,
    )
    return require_ok(resp, "enroll_player")


def enroll_players(
    base_url: str,
    player_tokens: list[str],
    tournament_id: int,
) -> list[dict]:
    """Enroll multiple players, returning one response dict per player."""
    return [enroll_player(base_url, tok, tournament_id) for tok in player_tokens]

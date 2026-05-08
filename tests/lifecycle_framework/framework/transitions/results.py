"""Ranking calculation and reward distribution transitions."""
from __future__ import annotations

import requests

from .._http import require_ok


def calculate_rankings(
    base_url: str,
    admin_token: str,
    tournament_id: int,
) -> dict:
    """POST /api/v1/tournaments/{id}/calculate-rankings."""
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/calculate-rankings",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={},
        timeout=30,
    )
    return require_ok(resp, "calculate_rankings")


def distribute_rewards(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    *,
    force_redistribution: bool = False,
) -> dict:
    """POST /api/v1/tournaments/{id}/distribute-rewards-v2.

    Auto-transitions tournament to REWARDS_DISTRIBUTED on success.
    tournament_id is required in the request body by the endpoint schema.
    """
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/distribute-rewards-v2",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"tournament_id": tournament_id, "force_redistribution": force_redistribution},
        timeout=30,
    )
    return require_ok(resp, "distribute_rewards")

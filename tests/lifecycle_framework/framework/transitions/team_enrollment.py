"""Admin team enrollment transitions — team creation, member add, team enroll."""
from __future__ import annotations

import requests

from .._http import require_ok


def create_team(
    base_url: str,
    admin_token: str,
    name: str,
    captain_user_id: int,
    specialization_type: str = "",
) -> dict:
    """POST /api/v1/teams — admin creates a team with an explicit captain."""
    resp = requests.post(
        f"{base_url}/api/v1/teams",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": name,
            "captain_user_id": captain_user_id,
            "specialization_type": specialization_type,
        },
        timeout=15,
    )
    return require_ok(resp, f"create_team:{name}")


def add_team_member(
    base_url: str,
    admin_token: str,
    team_id: int,
    user_id: int,
    role: str = "PLAYER",
) -> dict:
    """POST /api/v1/teams/{team_id}/members — admin adds a user directly (no invite)."""
    resp = requests.post(
        f"{base_url}/api/v1/teams/{team_id}/members",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"user_id": user_id, "role": role},
        timeout=15,
    )
    return require_ok(resp, f"add_team_member:team={team_id},user={user_id}")


def enroll_team(
    base_url: str,
    admin_token: str,
    tournament_id: int,
    team_id: int,
) -> dict:
    """POST /api/v1/tournaments/{tournament_id}/enroll-team — admin enrolls a team."""
    resp = requests.post(
        f"{base_url}/api/v1/tournaments/{tournament_id}/enroll-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": team_id},
        timeout=15,
    )
    return require_ok(resp, f"enroll_team:tournament={tournament_id},team={team_id}")


def set_participant_type_team(
    base_url: str,
    admin_token: str,
    tournament_id: int,
) -> dict:
    """PATCH /api/v1/tournaments/{id} to set participant_type=TEAM.

    WHY THIS EXISTS — ops endpoint gap:
      OpsScenarioRequest schema (ops_scenario/schemas.py) has no participant_type
      field. The ops endpoint hardcodes participant_type="INDIVIDUAL" at
      TournamentConfiguration creation (ops_scenario/__init__.py lines 870, 883,
      1179). Any participant_type value in the framework's create_tournament() JSON
      payload is silently dropped by Pydantic before reaching the endpoint handler.

      TODO: Fix ops/run-scenario participant_type propagation (separate PR).
        Root cause: OpsScenarioRequest schema + hardcoded "INDIVIDUAL" in __init__.py.
        Scope: ops_scenario/schemas.py + ops_scenario/__init__.py only. No lifecycle
        model or domain change needed.

    WHEN THIS IS SAFE TO CALL:
      Only in SEEKING_INSTRUCTOR status — before any enrollment or session generation.
      Calling it after ENROLLMENT_OPEN may leave enrolled participants in an
      inconsistent state (individual enrollments against a TEAM config).

    NOT A LIFECYCLE BYPASS:
      Uses the production admin PATCH endpoint (/api/v1/tournaments/{id}) with admin
      auth. This is the same endpoint used by human admins to configure tournaments
      before they open. All lifecycle guards (ENROLLMENT_OPEN, CHECK_IN_OPEN,
      IN_PROGRESS) run after this call against the corrected participant_type=TEAM.
    """
    resp = requests.patch(
        f"{base_url}/api/v1/tournaments/{tournament_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"participant_type": "TEAM"},
        timeout=15,
    )
    return require_ok(resp, f"set_participant_type_team:tournament={tournament_id}")

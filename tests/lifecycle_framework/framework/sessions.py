"""Session completion strategies and orchestration loop."""
from __future__ import annotations

import time
from typing import Protocol

import requests

from ._http import LifecycleError, require_ok


class SessionCompletionStrategy(Protocol):
    """Format-specific logic for completing a single session."""

    def complete(
        self,
        base_url: str,
        instructor_token: str,
        tournament_id: int,
        session: dict,
    ) -> bool:
        """Check in and submit results for one session.

        Returns True if the session was processed, False if skipped
        (e.g. knockout future-round with no participants yet).
        """
        ...


class H2HResultStrategy:
    """HEAD_TO_HEAD: check-in + submit head-to-head-results (winner/loser).

    Sessions with < 2 participants are skipped — knockout future-round sessions
    start empty and get populated only after KnockoutProgressionService fires
    for the preceding round.
    """

    def complete(
        self,
        base_url: str,
        instructor_token: str,
        tournament_id: int,
        session: dict,
    ) -> bool:
        """Return True if session was completed, False if skipped (TBD participants)."""
        session_id = session["id"]
        participants = session.get("participant_user_ids") or []

        if len(participants) < 2:
            # Future knockout round — participants not yet assigned by progression service.
            return False

        headers = {"Authorization": f"Bearer {instructor_token}"}

        # Check in
        resp = requests.post(
            f"{base_url}/api/v1/sessions/{session_id}/check-in",
            headers=headers,
            json={},
            timeout=15,
        )
        if resp.status_code == 400:
            # FRAGILE: 400 is accepted ONLY when the detail indicates the session is
            # already in_progress. Any other 400 (wrong instructor, not found, etc.)
            # must surface as LifecycleError. String-match is a pragmatic compromise —
            # the proper fix is an error-code field on the API response.
            try:
                detail = str(resp.json().get("detail", resp.text))
            except Exception:
                detail = resp.text
            if "in_progress" in detail.lower() or "already" in detail.lower():
                return True  # already checked in — acceptable, continue to results
            raise LifecycleError(f"session:{session_id}:check-in", 400, detail)
        elif resp.status_code not in (200, 201):
            require_ok(resp, f"session:{session_id}:check-in")

        # Submit HEAD_TO_HEAD results — participant[0] wins 2-0 (deterministic)
        results = [
            {"user_id": participants[0], "score": 2},
            {"user_id": participants[1], "score": 0},
        ]
        resp = requests.patch(
            f"{base_url}/api/v1/sessions/{session_id}/head-to-head-results",
            headers=headers,
            json={"results": results},
            timeout=15,
        )
        require_ok(resp, f"session:{session_id}:h2h-results")
        return True


def _fetch_pending_sessions(
    base_url: str,
    instructor_token: str,
    tournament_id: int,
) -> list[dict]:
    """Return sessions where result_submitted is False."""
    resp = requests.get(
        f"{base_url}/api/v1/tournaments/{tournament_id}/sessions",
        headers={"Authorization": f"Bearer {instructor_token}"},
        timeout=15,
    )
    data = require_ok(resp, "fetch_sessions")
    sessions: list[dict] = data if isinstance(data, list) else data.get("sessions", [])
    return [s for s in sessions if not s.get("result_submitted", False)]


def complete_all_sessions(
    base_url: str,
    instructor_token: str,
    tournament_id: int,
    strategy: SessionCompletionStrategy,
    *,
    max_rounds: int = 10,
    poll_delay_s: float = 0.5,
) -> int:
    """Drive a round-by-round loop until no pending sessions remain.

    Re-queries after each round to pick up knockout progression
    (next-round participants assigned after previous round completes).

    Returns the total number of sessions completed.
    """
    completed = 0
    for round_num in range(1, max_rounds + 1):
        pending = _fetch_pending_sessions(base_url, instructor_token, tournament_id)
        if not pending:
            break
        made_progress = False
        for session in pending:
            processed = strategy.complete(base_url, instructor_token, tournament_id, session)
            if processed:
                completed += 1
                made_progress = True
        if not made_progress:
            raise RuntimeError(
                f"complete_all_sessions: round {round_num} — {len(pending)} pending sessions "
                "all have empty participant_user_ids (possible session generator issue)"
            )
        if poll_delay_s > 0:
            time.sleep(poll_delay_s)
    else:
        remaining = _fetch_pending_sessions(base_url, instructor_token, tournament_id)
        if remaining:
            raise RuntimeError(
                f"complete_all_sessions: still {len(remaining)} pending after {max_rounds} rounds"
            )
    return completed

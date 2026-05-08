"""INDIVIDUAL_RANKING scenario — Phase 2A.

Validates that SessionCompletionStrategy generalises beyond HEAD_TO_HEAD with zero
changes to ScenarioRunner, the Protocol, complete_all_sessions(), or ScenarioConfig.

Key structural differences from H2H:
  - 1 session containing ALL participants (not one session per pair)
  - result_submitted = bool(session.game_results) — requires finalization, not submit alone
  - Two mandatory API calls per session: submit results → finalize session
  - No check-in required (submit_game_results has no prior check-in prerequisite)

Run:
    PYTHONPATH=. python -m tests.lifecycle_framework.scenarios.individual_ranking
"""
from __future__ import annotations

import requests

from ..framework._http import require_ok
from ..framework.auth import AuthContext
from ..framework.logging import ColoredConsoleLogger
from ..framework.preflight import build_standard_registry
from ..framework.sessions import SessionCompletionStrategy, complete_all_sessions
from ..framework.transitions import (
    accept_instructor_assignment,
    direct_assign_instructor,
    enroll_players,
    set_reward_config,
    set_schedule_config,
)
from .base import ScenarioConfig, ScenarioResult, ScenarioRunner


class IndividualResultStrategy:
    """INDIVIDUAL_RANKING session completion: submit placement results then finalize.

    INDIVIDUAL_RANKING generates one session containing all enrolled participants.
    After submit_game_results, rounds_data is populated and session_status = completed,
    but game_results remains NULL → result_submitted = False.
    Finalization writes game_results → result_submitted = True, and also satisfies
    the COMPLETED lifecycle guard (rewards.py checks game_results IS NOT NULL).

    No check-in is performed — the result submission endpoint does not require it.
    """

    def complete(
        self,
        base_url: str,
        instructor_token: str,
        tournament_id: int,
        session: dict,
    ) -> bool:
        """Submit + finalize one INDIVIDUAL_RANKING session.

        Returns True when processed. Returns False only if participant_user_ids is
        empty — unlike H2H knockout future-round sessions this should not occur for
        INDIVIDUAL_RANKING (all participants are assigned at session generation time).
        A False here signals a generation defect, not a normal pending state.
        """
        session_id = session["id"]
        participants = session.get("participant_user_ids") or []

        if not participants:
            return False

        headers = {"Authorization": f"Bearer {instructor_token}"}

        # Step 1 — submit placement results.
        # Deterministic: participant[0] rank=1/score=100, participant[1] rank=2/score=90, …
        # rounds_data is set here; game_results is NOT yet set.
        results = [
            {"user_id": uid, "score": float(100 - idx * 10), "rank": idx + 1}
            for idx, uid in enumerate(participants)
        ]
        resp = requests.patch(
            f"{base_url}/api/v1/sessions/{session_id}/results",
            headers=headers,
            json={"results": results},
            timeout=15,
        )
        require_ok(resp, f"session:{session_id}:individual-results")

        # Step 2 — finalize session.
        # Writes game_results → result_submitted becomes True.
        # Required before: COMPLETED transition guard + distribute_rewards.
        # Uses instructor_token; yellow flag: session.instructor_id must equal the
        # direct-assigned instructor. If 403, a LifecycleError surfaces naturally
        # via require_ok — see Phase 2A discovery report yellow-flag note.
        resp = requests.post(
            f"{base_url}/api/v1/tournaments/{tournament_id}/sessions/{session_id}/finalize",
            headers=headers,
            timeout=15,
        )
        require_ok(resp, f"session:{session_id}:finalize")

        return True


class IndividualRankingStrategy:
    """ScenarioStrategy for INDIVIDUAL_RANKING tournaments.

    All 6 hooks delegate to existing transition functions — no new orchestration.
    The only format-specific logic is IndividualResultStrategy.
    """

    def setup_instructor(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        direct_assign_instructor(
            cfg.base_url,
            auth.admin_token,
            tournament_id,
            auth.instructor_id,
            message="Framework: Individual ranking Phase 2A scenario",
        )
        accept_instructor_assignment(
            cfg.base_url,
            auth.instructor_token,
            tournament_id,
        )

    def enroll_participants(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        tokens = list(auth.player_tokens.values())
        enroll_players(cfg.base_url, tokens, tournament_id)

    def extra_pre_checkin_steps(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        """Set schedule config — required before CHECK_IN_OPEN (SCHEDULE_CONFIG_MISSING guard)."""
        set_schedule_config(
            cfg.base_url,
            auth.admin_token,
            tournament_id,
            match_duration_minutes=cfg.match_duration_minutes,
            break_duration_minutes=cfg.break_duration_minutes,
            parallel_fields=cfg.parallel_fields,
        )

    def extra_pre_in_progress_steps(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        """Set reward config — required before IN_PROGRESS (REWARD_CONFIG_MISSING guard)."""
        set_reward_config(
            cfg.base_url,
            auth.admin_token,
            tournament_id,
        )

    def complete_sessions(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> int:
        return complete_all_sessions(
            cfg.base_url,
            auth.instructor_token,
            tournament_id,
            strategy=self.session_completion_strategy(),
        )

    def session_completion_strategy(self) -> SessionCompletionStrategy:
        return IndividualResultStrategy()


class IndividualRankingScenario:
    """Ready-to-run INDIVIDUAL_RANKING scenario with default bootstrap credentials.

    tournament_type_code is present in ScenarioConfig but ignored by the ops endpoint
    for INDIVIDUAL_RANKING — tournament_type_id is always NULL for this format.
    scoring_type defaults to PLACEMENT inside the ops endpoint.
    """

    DEFAULT_CONFIG = ScenarioConfig(
        base_url="http://localhost:8000",
        admin_email="admin@lfa.com",
        admin_password="admin123",
        instructor_email="instructor@lfa.com",
        instructor_password="instructor123",
        player_password="Bootstrap#123",
        player_email_pattern="lfa-adult-%@lfa.com",
        player_count=4,
        age_group="AMATEUR",
        tournament_format="INDIVIDUAL_RANKING",
        tournament_type_code="knockout",  # ignored by ops endpoint for INDIVIDUAL_RANKING
        max_players=16,
        enrollment_cost=0,
    )

    def run(
        self,
        base_url: str = "http://localhost:8000",
        config: ScenarioConfig | None = None,
    ) -> ScenarioResult:
        cfg = config or ScenarioConfig(
            **{**self.DEFAULT_CONFIG.__dict__, "base_url": base_url}
        )
        strategy = IndividualRankingStrategy()
        registry = build_standard_registry(
            instructor_email=cfg.instructor_email,
            age_group=cfg.age_group,
            player_email_pattern=cfg.player_email_pattern,
            min_players=cfg.player_count,
        )
        runner = ScenarioRunner(
            config=cfg,
            strategy=strategy,
            registry=registry,
            logger=ColoredConsoleLogger(),
        )
        result = runner.run()
        result.api_verification.assert_all()
        result.db_verification.assert_all()
        return result


if __name__ == "__main__":
    import sys

    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    print(f"\n{'═' * 60}")
    print("  LFA — Individual Ranking Lifecycle Scenario")
    print(f"  Target: {base_url}")
    print(f"{'═' * 60}\n")

    result = IndividualRankingScenario().run(base_url=base_url)

    print(f"\n{'═' * 60}")
    print(f"  ✅  PASS — tournament_id={result.tournament_id}")
    print(f"  Sessions completed : {result.sessions_completed}")
    print(f"  Players enrolled   : {result.players_enrolled}")
    print(f"  API checks passed  : {len(result.api_verification.passed)}")
    print(f"  DB checks passed   : {len(result.db_verification.passed)}")
    print(f"{'═' * 60}\n")

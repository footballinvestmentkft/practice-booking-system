"""HEAD_TO_HEAD knockout scenario — reference implementation.

Reproduces the same REWARDS_DISTRIBUTED end-state as
scripts/seed_rewards_distributed_poc.py using the framework abstractions.

Run:
    PYTHONPATH=. python -m tests.lifecycle_framework.scenarios.h2h_knockout
"""
from __future__ import annotations

from ..framework.auth import AuthContext
from ..framework.logging import ColoredConsoleLogger
from ..framework.preflight import build_standard_registry
from ..framework.sessions import H2HResultStrategy, SessionCompletionStrategy, complete_all_sessions
from ..framework.transitions import (
    direct_assign_instructor,
    accept_instructor_assignment,
    enroll_players,
    set_schedule_config,
    set_reward_config,
)
from .base import ScenarioConfig, ScenarioResult, ScenarioRunner


class H2HKnockoutStrategy:
    """ScenarioStrategy for HEAD_TO_HEAD knockout tournaments.

    Owns ALL format-specific configuration:
      - Instructor: direct-assign + accept
      - Schedule config: set in extra_pre_checkin_steps (required before CHECK_IN_OPEN)
      - Reward config: set in extra_pre_in_progress_steps (required before IN_PROGRESS)
      - Session completion: H2HResultStrategy (check-in + head-to-head-results)
    """

    def setup_instructor(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        """Direct-assign instructor then have them accept."""
        direct_assign_instructor(
            cfg.base_url,
            auth.admin_token,
            tournament_id,
            auth.instructor_id,
            message="Framework: H2H knockout reference scenario",
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
        """Enroll all resolved seed players."""
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
        """Drive the round-by-round H2H session completion loop."""
        return complete_all_sessions(
            cfg.base_url,
            auth.instructor_token,
            tournament_id,
            strategy=self.session_completion_strategy(),
        )

    def session_completion_strategy(self) -> SessionCompletionStrategy:
        return H2HResultStrategy()


class H2HKnockoutScenario:
    """Ready-to-run H2H knockout scenario with default bootstrap credentials."""

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
        tournament_format="HEAD_TO_HEAD",
        tournament_type_code="knockout",
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
        strategy = H2HKnockoutStrategy()
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
    print("  LFA — H2H Knockout Lifecycle Scenario")
    print(f"  Target: {base_url}")
    print(f"{'═' * 60}\n")

    result = H2HKnockoutScenario().run(base_url=base_url)

    print(f"\n{'═' * 60}")
    print(f"  ✅  PASS — tournament_id={result.tournament_id}")
    print(f"  Sessions completed : {result.sessions_completed}")
    print(f"  Players enrolled   : {result.players_enrolled}")
    print(f"  API checks passed  : {len(result.api_verification.passed)}")
    print(f"  DB checks passed   : {len(result.db_verification.passed)}")
    print(f"{'═' * 60}\n")

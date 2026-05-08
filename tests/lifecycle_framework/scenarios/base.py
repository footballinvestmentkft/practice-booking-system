"""Scenario orchestration primitives.

ScenarioStrategy — Protocol for format-specific logic (setup, enroll, config, complete).
ScenarioRunner   — Orchestration-only: drives the lifecycle transitions, delegates
                   ALL format-specific work to the strategy. The runner has NO knowledge
                   of schedule config, reward config, or session result format.
ScenarioResult   — Outcome of a scenario run.
ScenarioFailure  — Raised when the scenario fails (wraps the original exception).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..framework._http import LifecycleError, PreflightError
from ..framework.auth import AuthContext, login
from ..framework.fixtures import (
    CampusFixture,
    resolve_campus,
    resolve_instructor,
    resolve_players_db,
)
from ..framework.logging import ColoredConsoleLogger, ScenarioLogger, TransitionEvent, TimedStep
from ..framework.preflight import HiddenDependencyRegistry, PreflightChecker
from ..framework.sessions import SessionCompletionStrategy, complete_all_sessions
from ..framework.transitions import (
    create_tournament,
    transition_status,
    calculate_rankings,
    distribute_rewards,
)
from ..framework.verification import ApiVerifier, DbVerifier, VerificationResult


@dataclass
class ScenarioConfig:
    base_url: str
    admin_email: str
    admin_password: str
    instructor_email: str
    instructor_password: str
    player_password: str = "Bootstrap#123"
    player_email_pattern: str = "lfa-adult-%@lfa.com"
    player_count: int = 4
    age_group: str = "AMATEUR"
    tournament_format: str = "HEAD_TO_HEAD"
    tournament_type_code: str = "knockout"
    max_players: int = 16
    enrollment_cost: int = 0
    match_duration_minutes: int = 90
    break_duration_minutes: int = 15
    parallel_fields: int = 1


@dataclass
class ScenarioResult:
    tournament_id: int
    sessions_completed: int
    players_enrolled: int
    api_verification: VerificationResult
    db_verification: VerificationResult
    events: list[TransitionEvent] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.api_verification.ok and self.db_verification.ok


class ScenarioFailure(Exception):
    """Raised when a scenario step fails. Wraps the original exception."""

    def __init__(self, step: str, cause: Exception) -> None:
        self.step = step
        self.cause = cause
        super().__init__(f"Scenario failed at [{step}]: {cause}")


@runtime_checkable
class ScenarioStrategy(Protocol):
    """Format-specific hooks called by ScenarioRunner at the right lifecycle moment.

    The runner calls these in order:
      setup_instructor        → after tournament created
      enroll_participants     → after ENROLLMENT_OPEN
      extra_pre_checkin_steps → after ENROLLMENT_CLOSED, before CHECK_IN_OPEN
                                (strategy MUST call set_schedule_config here)
      extra_pre_in_progress_steps → after CHECK_IN_OPEN, before IN_PROGRESS
                                (strategy MUST call set_reward_config here)
      complete_sessions       → after IN_PROGRESS
    """

    def setup_instructor(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        """Assign and confirm the instructor for the tournament."""
        ...

    def enroll_participants(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        """Enroll players after ENROLLMENT_OPEN."""
        ...

    def extra_pre_checkin_steps(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        """Steps between ENROLLMENT_CLOSED and CHECK_IN_OPEN.

        REQUIRED: call set_schedule_config() here — the CHECK_IN_OPEN transition
        guard enforces SCHEDULE_CONFIG_MISSING and will 400 without it.
        """
        ...

    def extra_pre_in_progress_steps(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        """Steps between CHECK_IN_OPEN and IN_PROGRESS.

        REQUIRED: call set_reward_config() here — the IN_PROGRESS transition
        guard enforces REWARD_CONFIG_MISSING and will 400 without it.
        """
        ...

    def complete_sessions(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> int:
        """Complete all pending sessions. Returns the number of sessions completed."""
        ...

    def session_completion_strategy(self) -> SessionCompletionStrategy:
        """Return the format-specific session completion strategy."""
        ...


class ScenarioRunner:
    """Orchestration engine — drives the full REWARDS_DISTRIBUTED lifecycle.

    Orchestration-only: the runner knows WHEN to call lifecycle transitions
    and WHEN to invoke strategy hooks. It has NO knowledge of:
      - schedule configuration parameters (strategy's responsibility)
      - reward configuration skill mappings (strategy's responsibility)
      - session result format (strategy's responsibility)
      - instructor assignment mechanism (strategy's responsibility)

    Lifecycle sequence (15 steps):
      preflight → auth → resolve_fixtures → create_tournament →
      setup_instructor → enrollment_open → enroll_participants →
      enrollment_close → extra_pre_checkin (schedule config lives here) →
      checkin_open → extra_pre_in_progress (reward config lives here) →
      in_progress → complete_sessions → calculate_rankings →
      completed → distribute_rewards → verify
    """

    def __init__(
        self,
        config: ScenarioConfig,
        strategy: ScenarioStrategy,
        registry: HiddenDependencyRegistry,
        logger: ScenarioLogger | None = None,
    ) -> None:
        self._cfg = config
        self._strategy = strategy
        self._registry = registry
        self._delegate = logger or ColoredConsoleLogger()
        self._events: list[TransitionEvent] = []

    # ── ScenarioLogger implementation ────────────────────────────────────────
    # The runner acts as its own logger so TimedStep can call self.log() and
    # have events stored in self._events AND printed via the delegate logger.

    def log(self, event: TransitionEvent) -> None:
        self._events.append(event)
        self._delegate.log(event)

    def summary(self, events: list[TransitionEvent]) -> None:
        self._delegate.summary(events)

    def run(self) -> ScenarioResult:
        cfg = self._cfg
        try:
            self._step_preflight()
            auth = self._step_auth()
            campus = self._step_resolve_fixtures(auth)
            tournament_id = self._step_create_tournament(auth, campus)
            self._step_instructor(auth, tournament_id)
            self._step_enrollment_open(auth, tournament_id)
            self._step_enroll_participants(auth, tournament_id)
            self._step_enrollment_close(auth, tournament_id)
            self._step_extra_pre_checkin(auth, tournament_id)
            self._step_checkin_open(auth, tournament_id)
            self._step_extra_pre_in_progress(auth, tournament_id)
            self._step_in_progress(auth, tournament_id)
            sessions_completed = self._step_complete_sessions(auth, tournament_id)
            self._step_calculate_rankings(auth, tournament_id)
            self._step_completed(auth, tournament_id)
            self._step_distribute_rewards(auth, tournament_id)
            api_result, db_result = self._step_verify(
                auth, tournament_id, cfg.player_count
            )
        except (PreflightError, LifecycleError, ScenarioFailure):
            self.summary(self._events)
            raise
        except Exception as exc:
            self.summary(self._events)
            raise ScenarioFailure("unknown", exc) from exc

        result = ScenarioResult(
            tournament_id=tournament_id,
            sessions_completed=sessions_completed,
            players_enrolled=cfg.player_count,
            api_verification=api_result,
            db_verification=db_result,
            events=list(self._events),
        )
        self.summary(self._events)
        return result

    # ── Step implementations ─────────────────────────────────────────────────

    def _step_preflight(self) -> None:
        with TimedStep("preflight", self) as step:
            checker = PreflightChecker(self._registry)
            passed = checker.run(fail_fast=True)
            step.ok(f"{len(passed)} checks passed")

    def _step_auth(self) -> AuthContext:
        with TimedStep("auth", self) as step:
            cfg = self._cfg
            admin_token = login(cfg.base_url, cfg.admin_email, cfg.admin_password)
            instructor_token = login(cfg.base_url, cfg.instructor_email, cfg.instructor_password)
            instructor = resolve_instructor(cfg.base_url, admin_token, cfg.instructor_email)
            auth = AuthContext(
                admin_token=admin_token,
                instructor_token=instructor_token,
                instructor_id=instructor.id,
            )
            step.ok(f"admin + instructor (id={instructor.id})")
        return auth

    def _step_resolve_fixtures(self, auth: AuthContext) -> CampusFixture:
        with TimedStep("resolve_fixtures", self) as step:
            cfg = self._cfg
            campus = resolve_campus(cfg.base_url, auth.admin_token)
            players = resolve_players_db(
                email_pattern=cfg.player_email_pattern,
                count=cfg.player_count,
            )
            for p in players:
                tok = login(cfg.base_url, p.email, cfg.player_password)
                auth.player_tokens[p.email] = tok
                auth.player_ids[p.email] = p.id
            step.ok(f"campus_id={campus.id}, {len(players)} players")
        return campus

    def _step_create_tournament(
        self, auth: AuthContext, campus: CampusFixture
    ) -> int:
        with TimedStep("create_tournament", self) as step:
            cfg = self._cfg
            data = create_tournament(
                cfg.base_url,
                auth.admin_token,
                tournament_format=cfg.tournament_format,
                tournament_type_code=cfg.tournament_type_code,
                age_group=cfg.age_group,
                max_players=cfg.max_players,
                enrollment_cost=cfg.enrollment_cost,
                campus_ids=[campus.id],
            )
            tid = data["tournament_id"]
            step.ok(f"id={tid} → SEEKING_INSTRUCTOR")
        return tid

    def _step_instructor(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("setup_instructor", self) as step:
            self._strategy.setup_instructor(self._cfg, auth, tid)
            step.ok("→ INSTRUCTOR_CONFIRMED")

    def _step_enrollment_open(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("enrollment_open", self) as step:
            cfg = self._cfg
            # campus_ids already passed to create_tournament; no separate assignment needed
            transition_status(cfg.base_url, auth.admin_token, tid, "ENROLLMENT_OPEN")
            step.ok("INSTRUCTOR_CONFIRMED → ENROLLMENT_OPEN")

    def _step_enroll_participants(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("enroll_participants", self) as step:
            self._strategy.enroll_participants(self._cfg, auth, tid)
            step.ok(f"{self._cfg.player_count} players enrolled")

    def _step_enrollment_close(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("enrollment_close", self) as step:
            cfg = self._cfg
            transition_status(cfg.base_url, auth.admin_token, tid, "ENROLLMENT_CLOSED")
            step.ok("ENROLLMENT_OPEN → ENROLLMENT_CLOSED")

    def _step_extra_pre_checkin(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("extra_pre_checkin", self) as step:
            self._strategy.extra_pre_checkin_steps(self._cfg, auth, tid)
            step.ok("schedule config set (SCHEDULE_CONFIG_MISSING guard cleared)")

    def _step_checkin_open(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("checkin_open", self) as step:
            cfg = self._cfg
            transition_status(cfg.base_url, auth.admin_token, tid, "CHECK_IN_OPEN")
            step.ok("ENROLLMENT_CLOSED → CHECK_IN_OPEN")

    def _step_extra_pre_in_progress(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("extra_pre_in_progress", self) as step:
            self._strategy.extra_pre_in_progress_steps(self._cfg, auth, tid)
            step.ok("reward config set (REWARD_CONFIG_MISSING guard cleared)")

    def _step_in_progress(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("in_progress", self) as step:
            cfg = self._cfg
            transition_status(cfg.base_url, auth.admin_token, tid, "IN_PROGRESS")
            step.ok("CHECK_IN_OPEN → IN_PROGRESS (sessions auto-generated)")

    def _step_complete_sessions(self, auth: AuthContext, tid: int) -> int:
        with TimedStep("complete_sessions", self) as step:
            count = self._strategy.complete_sessions(self._cfg, auth, tid)
            step.ok(f"{count} session(s) completed")
        return count

    def _step_calculate_rankings(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("calculate_rankings", self) as step:
            cfg = self._cfg
            calculate_rankings(cfg.base_url, auth.admin_token, tid)
            step.ok("rankings calculated")

    def _step_completed(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("completed", self) as step:
            cfg = self._cfg
            transition_status(cfg.base_url, auth.admin_token, tid, "COMPLETED")
            step.ok("IN_PROGRESS → COMPLETED")

    def _step_distribute_rewards(self, auth: AuthContext, tid: int) -> None:
        with TimedStep("distribute_rewards", self) as step:
            cfg = self._cfg
            distribute_rewards(cfg.base_url, auth.admin_token, tid)
            step.ok("COMPLETED → REWARDS_DISTRIBUTED (auto-transition in rewards_v2.py:92)")

    def _step_verify(
        self,
        auth: AuthContext,
        tid: int,
        enrolled_count: int,
    ) -> tuple[VerificationResult, VerificationResult]:
        with TimedStep("verify", self) as step:
            cfg = self._cfg
            api = ApiVerifier(cfg.base_url, auth.admin_token, auth.instructor_token)
            api_result = api.verify_rewards_distributed(tid, enrolled_count)
            db = DbVerifier()
            db_result = db.verify_rewards_distributed(tid, enrolled_count)
            all_ok = api_result.ok and db_result.ok
            step.ok(
                f"API {len(api_result.passed)} passed, "
                f"DB {len(db_result.passed)} passed — {'PASS' if all_ok else 'FAIL'}"
            )
        return api_result, db_result

"""TEAM HEAD_TO_HEAD league scenario — reference implementation.

Drives a 4-team round-robin league to REWARDS_DISTRIBUTED.

Run:
    PYTHONPATH=. python -m tests.lifecycle_framework.scenarios.team_league

Invariants verified:
  - 4 active TournamentTeamEnrollment records
  - 6 league match sessions (C(4,2)=6)
  - 6 completed team sessions (rounds_data.round_results present)
  - 4 TournamentRanking records (one per team)
  - 12 TournamentParticipation records (3 members × 4 teams)
  - reward_policy_snapshot IS NOT NULL
  - tournament_status = REWARDS_DISTRIBUTED
"""
from __future__ import annotations

import time

import requests

from ..framework._http import require_ok
from ..framework.auth import AuthContext
from ..framework.fixtures import resolve_players_db
from ..framework.logging import ColoredConsoleLogger, TimedStep
from ..framework.preflight import build_standard_registry
from ..framework.transitions import (
    direct_assign_instructor,
    accept_instructor_assignment,
    set_schedule_config,
    set_reward_config,
    set_participant_type_team,
    create_team,
    add_team_member,
    enroll_team,
)
from ..framework.verification import TeamApiVerifier, TeamDbVerifier
from .base import ScenarioConfig, ScenarioResult, ScenarioRunner

_TEAM_COUNT = 4
_MEMBERS_PER_TEAM = 3   # captain (index 0) + 2 players
_PLAYER_COUNT = _TEAM_COUNT * _MEMBERS_PER_TEAM  # 12 seed players needed


# ── Team session completion ──────────────────────────────────────────────────

def _complete_all_team_sessions(
    base_url: str,
    admin_token: str,
    tournament_id: int,
) -> int:
    """Complete all TEAM league sessions using DB-direct status check.

    Uses DB-direct lookup because the sessions list API does not expose
    participant_team_ids or session_status — both needed to submit correct results.

    PATCH /sessions/{id}/team-results sets session_status="completed" and writes
    rounds_data.round_results. game_results is NOT set, so result_submitted=False
    always (bool(game_results)=False). Standard complete_all_sessions() would loop
    forever — this function checks session_status directly instead.
    """
    from app.database import SessionLocal
    from app.models.session import Session as SessionModel, EventCategory

    max_rounds = 10
    total_completed = 0
    headers = {"Authorization": f"Bearer {admin_token}"}

    for _ in range(max_rounds):
        db = SessionLocal()
        try:
            pending = (
                db.query(SessionModel)
                .filter(
                    SessionModel.semester_id == tournament_id,
                    SessionModel.event_category == EventCategory.MATCH,
                    SessionModel.session_status != "completed",
                )
                .all()
            )
            # Snapshot team IDs while session is still attached to DB session
            pending_data = [
                {
                    "id": s.id,
                    "team_ids": list(s.participant_team_ids or []),
                }
                for s in pending
            ]
        finally:
            db.close()

        if not pending_data:
            break

        for s in pending_data:
            session_id = s["id"]
            team_ids = s["team_ids"]
            if len(team_ids) < 2:
                continue  # session not yet populated (should not occur in league)

            results = [
                {"team_id": team_ids[0], "score": 2},
                {"team_id": team_ids[1], "score": 0},
            ]
            resp = requests.patch(
                f"{base_url}/api/v1/sessions/{session_id}/team-results",
                headers=headers,
                json={"results": results, "round_number": 1},
                timeout=15,
            )
            require_ok(resp, f"team-results:session={session_id}")
            total_completed += 1

        time.sleep(0.2)

    return total_completed


# ── Strategy ─────────────────────────────────────────────────────────────────

class TeamLeagueStrategy:
    """ScenarioStrategy for TEAM HEAD_TO_HEAD league tournaments.

    Participant setup:
      - Resolves 12 seed players from DB (same lfa-adult-% pattern)
      - Splits into 4 groups of 3: player[0] = captain, [1]/[2] = PLAYER members
      - Creates 4 teams via admin API, adds members, enrolls into tournament

    The strategy stores team_ids so _complete_all_team_sessions can skip the
    sessions-list API gap (participant_team_ids not in sessions list response).
    """

    def __init__(self) -> None:
        self._team_ids: list[int] = []

    def setup_instructor(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
        # TODO: Fix ops/run-scenario participant_type propagation (separate PR).
        #   Root cause: OpsScenarioRequest schema has no participant_type field;
        #   ops/__init__.py hardcodes "INDIVIDUAL" at TournamentConfiguration creation
        #   (lines 870, 883, 1179). Fix = add field to schema + read it in __init__.py.
        #   This call is a legitimate admin config update (PATCH /tournaments/{id})
        #   in SEEKING_INSTRUCTOR status — not a lifecycle bypass. All subsequent
        #   guards (ENROLLMENT_OPEN, CHECK_IN_OPEN, IN_PROGRESS) run after this.
        set_participant_type_team(cfg.base_url, auth.admin_token, tournament_id)
        direct_assign_instructor(
            cfg.base_url,
            auth.admin_token,
            tournament_id,
            auth.instructor_id,
            message="Framework: Team league reference scenario",
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
        players = resolve_players_db(
            email_pattern=cfg.player_email_pattern,
            count=_PLAYER_COUNT,
        )
        self._team_ids = []
        for i in range(_TEAM_COUNT):
            group = players[i * _MEMBERS_PER_TEAM : (i + 1) * _MEMBERS_PER_TEAM]
            captain = group[0]
            team_data = create_team(
                cfg.base_url,
                auth.admin_token,
                name=f"Team {i + 1}",
                captain_user_id=captain.id,
            )
            team_id = team_data["id"]
            self._team_ids.append(team_id)
            # Add remaining members (captain is already a member via create_team)
            for member in group[1:]:
                add_team_member(
                    cfg.base_url,
                    auth.admin_token,
                    team_id=team_id,
                    user_id=member.id,
                )
            enroll_team(
                cfg.base_url,
                auth.admin_token,
                tournament_id=tournament_id,
                team_id=team_id,
            )

    def extra_pre_checkin_steps(
        self,
        cfg: ScenarioConfig,
        auth: AuthContext,
        tournament_id: int,
    ) -> None:
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
        return _complete_all_team_sessions(
            cfg.base_url,
            auth.admin_token,
            tournament_id,
        )

    def session_completion_strategy(self):
        raise NotImplementedError(
            "TeamLeagueStrategy uses _complete_all_team_sessions directly — "
            "session_completion_strategy() is not used"
        )


# ── Runner subclass ──────────────────────────────────────────────────────────

class _TeamScenarioRunner(ScenarioRunner):
    """ScenarioRunner subclass that wires TEAM-specific verifiers into _step_verify.

    Sole override: _step_verify — replaces ApiVerifier/DbVerifier with
    TeamApiVerifier/TeamDbVerifier so the verify log accurately reflects
    TEAM tournament state instead of a false FAIL from result_submitted checks.

    Standard ApiVerifier._check_sessions_completed uses result_submitted=False
    (always for TEAM, because /team-results never sets game_results), and
    DbVerifier._check_ranking_coverage counts individual players, not teams.
    Neither failure is real — this override corrects the verification path.

    All 14 other orchestration steps are inherited unchanged from ScenarioRunner.
    The core ApiVerifier, DbVerifier, and complete_all_sessions() are untouched.
    """

    def _step_verify(
        self,
        auth: AuthContext,
        tid: int,
        enrolled_count: int,  # not used — TEAM verification counts teams, not players
    ) -> tuple:
        with TimedStep("verify", self) as step:
            cfg = self._cfg
            team_api = TeamApiVerifier(cfg.base_url, auth.admin_token, auth.instructor_token)
            team_db = TeamDbVerifier()
            api_result = team_api.verify_team_tournament(tid, team_count=_TEAM_COUNT)
            db_result = team_db.verify_team_tournament(
                tid, team_count=_TEAM_COUNT, member_count=_PLAYER_COUNT
            )
            all_ok = api_result.ok and db_result.ok
            step.ok(
                f"API {len(api_result.passed)} passed, "
                f"DB {len(db_result.passed)} passed — {'PASS' if all_ok else 'FAIL'}"
            )
        return api_result, db_result


# ── Scenario ─────────────────────────────────────────────────────────────────

class TeamLeagueScenario:
    """Ready-to-run TEAM league scenario with default bootstrap credentials."""

    DEFAULT_CONFIG = ScenarioConfig(
        base_url="http://localhost:8000",
        admin_email="admin@lfa.com",
        admin_password="admin123",
        instructor_email="instructor@lfa.com",
        instructor_password="instructor123",
        player_password="Bootstrap#123",
        player_email_pattern="lfa-adult-%@lfa.com",
        # player_count=4: ScenarioRunner resolves 4 seed players for auth fixtures.
        # TeamLeagueStrategy independently resolves 12 (4 teams × 3 members) from DB.
        # _TeamScenarioRunner._step_verify uses _TEAM_COUNT/_PLAYER_COUNT directly,
        # so enrolled_count=4 passed by the runner is intentionally ignored there.
        player_count=4,
        age_group="AMATEUR",
        tournament_format="HEAD_TO_HEAD",
        tournament_type_code="league",
        max_players=16,
        enrollment_cost=0,
        participant_type="TEAM",
    )

    def run(
        self,
        base_url: str = "http://localhost:8000",
        config: ScenarioConfig | None = None,
    ) -> ScenarioResult:
        cfg = config or ScenarioConfig(
            **{**self.DEFAULT_CONFIG.__dict__, "base_url": base_url}
        )
        strategy = TeamLeagueStrategy()
        registry = build_standard_registry(
            instructor_email=cfg.instructor_email,
            age_group=cfg.age_group,
            player_email_pattern=cfg.player_email_pattern,
            min_players=_PLAYER_COUNT,
        )
        runner = _TeamScenarioRunner(
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
    print("  LFA — TEAM League Lifecycle Scenario")
    print(f"  Target: {base_url}")
    print(f"{'═' * 60}\n")

    result = TeamLeagueScenario().run(base_url=base_url)

    print(f"\n{'═' * 60}")
    print(f"  ✅  PASS — tournament_id={result.tournament_id}")
    print(f"  Sessions completed : {result.sessions_completed}")
    print(f"  API checks passed  : {len(result.api_verification.passed)}")
    print(f"  DB checks passed   : {len(result.db_verification.passed)}")
    print(f"{'═' * 60}\n")

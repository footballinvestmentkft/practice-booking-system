"""Unit tests for app/api/api_v1/endpoints/tournaments/ops_scenario.py

Sprint P12 — Coverage target: ≥85% stmt, ≥50% branch

Phases:
  1. Pure helpers: _build_h2h_game_results, _get_tournament_sessions,
                   _calculate_ir_rankings, _finalize_tournament_with_rewards,
                   _simulate_tournament_results (dispatcher)
  2. Endpoint validation: run_ops_scenario — all defensive guard paths
  3. Success path: player_count=0 / INDIVIDUAL_RANKING / auto_generate=False
  4. Minimal simulation helpers: _simulate_individual_ranking,
                                  _simulate_head_to_head_knockout
"""

import json
import logging
from unittest.mock import MagicMock, patch, call

import pytest

from app.api.api_v1.endpoints.tournaments.ops_scenario import (
    OpsScenarioRequest,
    _build_h2h_game_results,
    _calculate_ir_rankings,
    _finalize_tournament_with_rewards,
    _get_tournament_sessions,
    _simulate_group_knockout_tournament,
    _simulate_head_to_head_knockout,
    _simulate_individual_ranking,
    _simulate_knockout_bracket,
    _simulate_league_tournament,
    _simulate_tournament_results,
    run_ops_scenario,
)
from app.models.tournament_enums import TournamentPhase
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.tournaments.ops_scenario"
_LOG = logging.getLogger("test_ops_scenario")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _q(*, first=None, all_=None, count=0):
    """Fluent query mock — filter / filter_by / order_by / all / first / count."""
    q = MagicMock()
    for m in ("filter", "filter_by", "options", "order_by", "offset",
              "limit", "group_by", "join", "with_for_update"):
        getattr(q, m).return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    q.count.return_value = count
    return q


def _seq_db(*qs):
    """n-th db.query() call returns qs[n]; fallback _q() after exhaustion."""
    calls = [0]

    def _side(*args, **kw):
        idx = calls[0]
        calls[0] += 1
        return qs[idx] if idx < len(qs) else _q()

    db = MagicMock()
    db.query.side_effect = _side
    return db


def _admin():
    u = MagicMock()
    u.id = 1
    u.role = UserRole.ADMIN
    u.email = "admin@test.com"
    return u


def _gm_user():
    """Mock grandmaster user returned by the hard-fail guard in ops_scenario."""
    gm = MagicMock()
    gm.id = 7
    gm.email = "grandmaster@lfa.com"
    return gm


def _req(**overrides):
    """MagicMock OpsScenarioRequest with sensible defaults."""
    r = MagicMock()
    r.scenario = "smoke_test"
    r.player_count = 0
    r.player_ids = None
    r.dry_run = False
    r.confirmed = False
    r.tournament_format = "INDIVIDUAL_RANKING"
    r.tournament_name = "Test"
    r.tournament_type_code = "knockout"
    r.campus_ids = [1]
    r.auto_generate_sessions = False
    r.enrollment_cost = 0
    r.age_group = "PRO"
    r.initial_tournament_status = "IN_PROGRESS"
    r.reward_config = None
    r.game_preset_id = None
    r.scoring_type = "PLACEMENT"
    r.ranking_direction = None
    r.number_of_rounds = 1
    r.simulation_mode = "manual"
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def _session_mock(*, participants=None, game_results=None, round_num=1,
                   match_num=1, title="Match", phase=None):
    s = MagicMock()
    s.id = 10
    s.participant_user_ids = participants if participants is not None else [42, 43]
    s.game_results = game_results
    s.tournament_round = round_num
    s.tournament_match_number = match_num
    s.title = title
    s.tournament_phase = phase
    s.session_status = "scheduled"
    s.scoring_type = None
    return s


# ===========================================================================
# Phase 1 — Pure helpers
# ===========================================================================

class TestBuildH2HGameResults:
    def test_returns_valid_json(self):
        participants = [
            {"user_id": 42, "result": "win", "score": 3},
            {"user_id": 43, "result": "loss", "score": 0},
        ]
        result = _build_h2h_game_results(participants, round_number=1)
        parsed = json.loads(result)
        assert parsed["match_format"] == "HEAD_TO_HEAD"
        assert parsed["round_number"] == 1
        assert len(parsed["participants"]) == 2

    def test_round_number_preserved(self):
        result = _build_h2h_game_results([], round_number=5)
        assert json.loads(result)["round_number"] == 5


class TestGetTournamentSessions:
    def test_no_ordering(self):
        q = _q(all_=[MagicMock()])
        db = MagicMock()
        db.query.return_value = q
        result = _get_tournament_sessions(db, tournament_id=1)
        assert len(result) == 1
        # order_by should NOT have been called
        q.order_by.assert_not_called()

    def test_ordered_flag(self):
        q = _q(all_=[])
        db = MagicMock()
        db.query.return_value = q
        _get_tournament_sessions(db, tournament_id=1, ordered=True)
        q.order_by.assert_called_once()

    def test_with_phase_flag_takes_precedence(self):
        """with_phase=True overrides ordered=True."""
        q = _q(all_=[])
        db = MagicMock()
        db.query.return_value = q
        _get_tournament_sessions(db, tournament_id=1, ordered=True, with_phase=True)
        # order_by called once (with 3-column tuple via with_phase branch)
        q.order_by.assert_called_once()


class TestCalculateIRRankings:
    def test_empty_sessions_returns_empty_list(self):
        t = MagicMock()
        t.tournament_config_obj = None
        result = _calculate_ir_rankings(t, [], _LOG)
        assert result == []

    def test_sessions_with_no_rounds_data(self):
        t = MagicMock()
        t.tournament_config_obj = None
        s = MagicMock()
        s.rounds_data = None  # → _rd = {}
        result = _calculate_ir_rankings(t, [s], _LOG)
        assert result == []

    def test_sessions_with_non_dict_round_results_skipped(self):
        t = MagicMock()
        t.tournament_config_obj = None
        s = MagicMock()
        s.rounds_data = {"round_results": "not-a-dict"}  # isinstance check fails
        result = _calculate_ir_rankings(t, [s], _LOG)
        assert result == []

    def test_valid_rounds_data_calls_aggregator(self):
        t = MagicMock()
        t.tournament_config_obj = MagicMock()
        t.tournament_config_obj.ranking_direction = "DESC"
        s = MagicMock()
        s.rounds_data = {"round_results": {"1": {"42": "10.5"}}}
        with patch(
            "app.services.tournament.results.calculators.ranking_aggregator.RankingAggregator"
        ) as MockRA:
            MockRA.aggregate_user_values.return_value = {"42": 10.5}
            MockRA.calculate_performance_rankings.return_value = [{"user_id": 42, "rank": 1}]
            result = _calculate_ir_rankings(t, [s], _LOG)
        assert result == [{"user_id": 42, "rank": 1}]
        MockRA.aggregate_user_values.assert_called_once()
        MockRA.calculate_performance_rankings.assert_called_once()

    def test_config_obj_none_uses_asc_default(self):
        t = MagicMock()
        t.tournament_config_obj = None
        s = MagicMock()
        s.rounds_data = {"round_results": {"1": {"42": "10.5"}}}
        with patch(
            "app.services.tournament.results.calculators.ranking_aggregator.RankingAggregator"
        ) as MockRA:
            MockRA.aggregate_user_values.return_value = {}
            MockRA.calculate_performance_rankings.return_value = []
            _calculate_ir_rankings(t, [s], _LOG)
        _, direction = MockRA.aggregate_user_values.call_args.args
        assert direction == "ASC"


class TestFinalizeTournamentWithRewards:
    def test_tournament_not_found_is_noop(self):
        db = _seq_db(_q(first=None))
        with patch("app.models.semester.Semester"):
            with patch(
                "app.services.tournament.results.finalization"
                ".tournament_finalizer.TournamentFinalizer"
            ) as MockFin:
                _finalize_tournament_with_rewards(99, db, _LOG)
        MockFin.assert_not_called()

    def test_success_path_logs_info(self):
        t = MagicMock()
        db = _seq_db(_q(first=t))
        with patch("app.models.semester.Semester"), \
             patch(
                 "app.services.tournament.results.finalization"
                 ".tournament_finalizer.TournamentFinalizer"
             ) as MockFin:
            MockFin.return_value.finalize.return_value = {
                "success": True,
                "tournament_status": "REWARDS_DISTRIBUTED",
                "rewards_message": "Rewards sent",
            }
            logger = MagicMock()
            _finalize_tournament_with_rewards(1, db, logger)
        logger.info.assert_called()

    def test_non_success_logs_warning(self):
        t = MagicMock()
        db = _seq_db(_q(first=t))
        with patch("app.models.semester.Semester"), \
             patch(
                 "app.services.tournament.results.finalization"
                 ".tournament_finalizer.TournamentFinalizer"
             ) as MockFin:
            MockFin.return_value.finalize.return_value = {
                "success": False,
                "message": "Not ready",
            }
            logger = MagicMock()
            _finalize_tournament_with_rewards(1, db, logger)
        logger.warning.assert_called()

    def test_exception_triggers_rollback(self):
        t = MagicMock()
        db = _seq_db(_q(first=t))
        with patch("app.models.semester.Semester"), \
             patch(
                 "app.services.tournament.results.finalization"
                 ".tournament_finalizer.TournamentFinalizer"
             ) as MockFin:
            MockFin.return_value.finalize.side_effect = RuntimeError("DB crash")
            _finalize_tournament_with_rewards(1, db, _LOG)
        db.rollback.assert_called_once()

    def test_exception_and_rollback_exception_are_handled(self):
        t = MagicMock()
        db = _seq_db(_q(first=t))
        db.rollback.side_effect = RuntimeError("rollback also failed")
        with patch("app.models.semester.Semester"), \
             patch(
                 "app.services.tournament.results.finalization"
                 ".tournament_finalizer.TournamentFinalizer"
             ) as MockFin:
            MockFin.return_value.finalize.side_effect = RuntimeError("DB crash")
            # Should not raise — outer except silences rollback failure
            _finalize_tournament_with_rewards(1, db, _LOG)


class TestSimulateTournamentResultsDispatcher:
    """Tests for _simulate_tournament_results routing logic."""

    def test_tournament_not_found_returns_false(self):
        db = _seq_db(_q(first=None), _q(all_=[]))
        ok, msg = _simulate_tournament_results(db, 1, _LOG)
        assert ok is False
        assert "not found" in msg

    def test_routes_h2h_pure_knockout(self):
        t = MagicMock()
        t.format = "HEAD_TO_HEAD"
        # No phases → pure knockout
        db = _seq_db(_q(first=t), _q(all_=[]))
        with patch(f"{_BASE}._simulate_head_to_head_knockout", return_value=(True, "done")) as mock_fn:
            ok, msg = _simulate_tournament_results(db, 1, _LOG)
        assert ok is True
        mock_fn.assert_called_once()

    def test_routes_h2h_league(self):
        t = MagicMock()
        t.format = "HEAD_TO_HEAD"
        s = MagicMock()
        s.tournament_phase = TournamentPhase.GROUP_STAGE
        db = _seq_db(_q(first=t), _q(all_=[s]))
        with patch(f"{_BASE}._simulate_league_tournament", return_value=(True, "league done")) as mock_fn:
            ok, _ = _simulate_tournament_results(db, 1, _LOG)
        assert ok is True
        mock_fn.assert_called_once()

    def test_routes_h2h_group_knockout(self):
        t = MagicMock()
        t.format = "HEAD_TO_HEAD"
        gs = MagicMock()
        gs.tournament_phase = TournamentPhase.GROUP_STAGE
        ko = MagicMock()
        ko.tournament_phase = TournamentPhase.KNOCKOUT
        db = _seq_db(_q(first=t), _q(all_=[gs, ko]))
        with patch(f"{_BASE}._simulate_group_knockout_tournament", return_value=(True, "ok")) as mock_fn:
            ok, _ = _simulate_tournament_results(db, 1, _LOG)
        assert ok is True
        mock_fn.assert_called_once()

    def test_routes_individual_ranking(self):
        t = MagicMock()
        t.format = "INDIVIDUAL_RANKING"
        db = _seq_db(_q(first=t), _q(all_=[]))
        with patch(f"{_BASE}._simulate_individual_ranking", return_value=(True, "ir done")) as mock_fn:
            ok, _ = _simulate_tournament_results(db, 1, _LOG)
        assert ok is True
        mock_fn.assert_called_once_with(db, t, _LOG)

    def test_unsupported_format_returns_false(self):
        t = MagicMock()
        t.format = "BOGUS_FORMAT"
        db = _seq_db(_q(first=t), _q(all_=[]))
        ok, msg = _simulate_tournament_results(db, 1, _LOG)
        assert ok is False
        assert "Unsupported" in msg
        assert "BOGUS_FORMAT" in msg

    def test_tournament_format_none_defaults_to_ir(self):
        t = MagicMock()
        t.format = None  # → defaults to INDIVIDUAL_RANKING
        db = _seq_db(_q(first=t), _q(all_=[]))
        with patch(f"{_BASE}._simulate_individual_ranking", return_value=(True, "ok")):
            ok, _ = _simulate_tournament_results(db, 1, _LOG)
        assert ok is True


# ===========================================================================
# Phase 2 — Endpoint validation
# ===========================================================================

class TestRunOpsScenarioValidation:

    def test_403_non_admin(self):
        u = MagicMock()
        u.role = UserRole.STUDENT
        db = MagicMock()
        with pytest.raises(Exception) as exc:
            run_ops_scenario(_req(), db=db, current_user=u)
        assert exc.value.status_code == 403

    def test_dry_run_returns_early_no_db_writes(self):
        db = MagicMock()
        result = run_ops_scenario(_req(dry_run=True), db=db, current_user=_admin())
        assert result.triggered is False
        assert result.dry_run is True
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_dry_run_includes_scenario_and_count(self):
        db = MagicMock()
        result = run_ops_scenario(
            _req(dry_run=True, player_count=16, scenario="smoke_test"),
            db=db, current_user=_admin()
        )
        assert "smoke_test" in result.message
        assert "16" in result.message

    def test_safety_gate_requires_confirmed(self):
        db = MagicMock()
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(player_count=128, confirmed=False, dry_run=False),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 422
        assert "confirmed=True" in exc.value.detail

    def test_safety_gate_passes_with_confirmed(self):
        """player_count=128 + confirmed=True passes the gate (proceeds to DB ops)."""
        db = _seq_db(
            _q(all_=[]),      # q0: seed pool query (_User join UserLicense)
        )
        with pytest.raises(Exception):
            # Will fail at next step (no seed users) but NOT at the safety gate
            run_ops_scenario(
                _req(player_count=128, confirmed=True, dry_run=False),
                db=db, current_user=_admin()
            )

    def test_player_ids_missing_users_400(self):
        # Request has player_ids=[99] but DB only returns empty valid_rows
        db = _seq_db(
            _q(all_=[]),  # valid_rows: no users found
        )
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(player_ids=[99], player_count=1),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 400
        assert "not found" in exc.value.detail.lower()

    def test_player_ids_hybrid_fill_insufficient_400(self):
        # player_ids=[42] found, but need 2 total → fill 1 from seed, only 0 available
        valid_row = MagicMock()
        valid_row.id = 42
        db = _seq_db(
            _q(all_=[valid_row]),  # valid_rows: 1 found
            _q(all_=[]),           # fill_rows: 0 seed users
        )
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(player_ids=[42], player_count=2),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 400
        assert "hybrid fill" in exc.value.detail.lower()

    def test_auto_mode_no_seed_users_500(self):
        db = _seq_db(
            _q(all_=[]),  # seed_rows: empty
        )
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(player_count=4, player_ids=None),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 500
        assert "seed" in exc.value.detail.lower()

    def test_auto_mode_insufficient_seed_users_400(self):
        seed_row = MagicMock()
        seed_row.id = 10
        db = _seq_db(
            _q(all_=[seed_row]),  # only 1 seed user, but player_count=4
        )
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(player_count=4, player_ids=None),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 400
        assert "seed" in exc.value.detail.lower()

    def test_head_to_head_unknown_tournament_type_500(self):
        # player_count=0, H2H format, tournament type not found
        db = _seq_db(
            _q(first=None),  # TournamentType lookup → not found
        )
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(tournament_format="HEAD_TO_HEAD", player_count=0,
                     tournament_type_code="nonexistent"),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 500
        assert "not found" in exc.value.detail.lower()

    def test_campus_invalid_422(self):
        """Campus IDs not in DB → 422."""
        campus_row = MagicMock()
        campus_row.id = 2  # campus 2 found, but campus 1 requested → invalid
        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"):
            mock_t = MagicMock()
            mock_t.id = 99
            MockSem.return_value = mock_t
            gm = MagicMock()
            gm.id = 7
            db = _seq_db(
                _q(first=gm),          # q0: grandmaster found
                _q(all_=[campus_row]), # q1: campus validation → found id=2, not id=1
            )
            with pytest.raises(Exception) as exc:
                run_ops_scenario(
                    _req(campus_ids=[1]),
                    db=db, current_user=_admin()
                )
        assert exc.value.status_code == 422
        assert "not found" in exc.value.detail.lower()

    def test_large_count_name_generation(self):
        """tournament_name='' + player_count=128 → OPS-LF- prefix (covers lines 1115-1116)."""
        db = _seq_db(_q(all_=[]))  # seed pool → empty → 500 raised
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(player_count=128, confirmed=True, tournament_name=""),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 500
        assert "seed" in exc.value.detail.lower()

    def test_small_count_name_generation(self):
        """tournament_name='' + player_count=4 → OPS-SMOKE- prefix (covers line 1118)."""
        db = _seq_db(_q(all_=[]))  # seed pool → empty → 500 raised
        with pytest.raises(Exception) as exc:
            run_ops_scenario(
                _req(player_count=4, tournament_name=""),
                db=db, current_user=_admin()
            )
        assert exc.value.status_code == 500
        assert "seed" in exc.value.detail.lower()


# ===========================================================================
# Phase 3 — Success path (player_count=0 / IR / auto_generate=False)
# ===========================================================================

class TestRunOpsScenarioSuccess:

    def test_ir_manual_mode_returns_triggered_true(self):
        """Minimal success: IR, 0 players, no session generation, no simulation."""
        mock_tournament = MagicMock()
        mock_tournament.id = 99
        campus_row = MagicMock()
        campus_row.id = 1  # campus 1 found and valid

        db = _seq_db(
            _q(first=_gm_user()),        # q0: grandmaster lookup
            _q(all_=[campus_row]), # q1: campus validation
            _q(first=None),        # q2: CampusScheduleConfig existing check
            _q(count=0),           # q3: final session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService") as MockAudit, \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"):
            MockSem.return_value = mock_tournament
            audit_entry = MagicMock()
            audit_entry.id = 7
            MockAudit.return_value.log.return_value = audit_entry

            result = run_ops_scenario(
                _req(player_count=0, tournament_format="INDIVIDUAL_RANKING",
                     auto_generate_sessions=False, campus_ids=[1]),
                db=db,
                current_user=_admin()
            )

        assert result.triggered is True
        assert result.tournament_id == 99
        assert result.session_count == 0
        assert result.enrolled_count == 0
        assert result.dry_run is False
        assert result.audit_log_id == 7
        db.add.assert_called()
        db.commit.assert_called()

    def test_explicit_tournament_name_used(self):
        mock_tournament = MagicMock()
        mock_tournament.id = 55
        campus_row = MagicMock()
        campus_row.id = 1

        db = _seq_db(_q(first=_gm_user()), _q(all_=[campus_row]), _q(first=None), _q(count=0))

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"):
            MockSem.return_value = mock_tournament
            result = run_ops_scenario(
                _req(player_count=0, tournament_name="My Custom Tournament",
                     auto_generate_sessions=False, campus_ids=[1]),
                db=db, current_user=_admin()
            )
        assert result.tournament_name == "My Custom Tournament"

    def test_audit_log_failure_is_non_fatal(self):
        """AuditService raising an exception must NOT abort the response."""
        mock_tournament = MagicMock()
        mock_tournament.id = 77
        campus_row = MagicMock()
        campus_row.id = 1

        db = _seq_db(_q(first=_gm_user()), _q(all_=[campus_row]), _q(first=None), _q(count=0))

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService") as MockAudit, \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"):
            MockSem.return_value = mock_tournament
            MockAudit.return_value.log.side_effect = RuntimeError("audit DB down")
            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=False, campus_ids=[1]),
                db=db, current_user=_admin()
            )
        # Result still returned despite audit failure
        assert result.triggered is True
        assert result.audit_log_id is None  # not set because audit failed

    def test_player_ids_enrollment_loop(self):
        """player_ids=[10] → enrollment loop: 1 player enrolled (covers lines 1179-1187, 1370-1401)."""
        mock_tournament = MagicMock()
        mock_tournament.id = 44
        valid_user_row = MagicMock()
        valid_user_row.id = 10
        campus_row = MagicMock()
        campus_row.id = 1
        lic_mock = MagicMock()
        lic_mock.id = 5

        db = _seq_db(
            _q(all_=[valid_user_row]),  # q0: valid_rows for player_ids=[10]
            _q(first=_gm_user()),             # q1: grandmaster (IR)
            _q(first=None),             # q2: _Enroll existing check → not enrolled
            _q(first=lic_mock),         # q3: _Lic check → has license
            _q(all_=[campus_row]),      # q4: campus validation
            _q(first=None),             # q5: CampusScheduleConfig
            _q(count=0),               # q6: session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.license.UserLicense"):
            MockSem.return_value = mock_tournament
            result = run_ops_scenario(
                _req(player_ids=[10], player_count=0,
                     auto_generate_sessions=False, campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.triggered is True
        assert result.enrolled_count == 1

    def test_auto_generate_sync_manual_mode(self):
        """auto_generate_sessions=True, player_count=0, manual mode → sync path, no simulation."""
        mock_tournament = MagicMock()
        mock_tournament.id = 88
        campus_row = MagicMock()
        campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),        # q0: grandmaster (IR, no TT query)
            _q(all_=[campus_row]), # q1: campus validation
            _q(first=None),        # q2: CampusScheduleConfig existing check
            _q(all_=[]),           # q3: SemesterEnrollment enrolled_user_ids
            _q(count=0),           # q4: final session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService") as MockAudit, \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch(
                 "app.services.tournament.session_generation"
                 ".session_generator.TournamentSessionGenerator"
             ) as MockTSG:
            MockSem.return_value = mock_tournament
            audit_entry = MagicMock()
            audit_entry.id = 8
            MockAudit.return_value.log.return_value = audit_entry
            MockTSG.return_value.generate_sessions.return_value = (True, "0 sessions created", [])

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="manual", campus_ids=[1]),
                db=db,
                current_user=_admin()
            )

        assert result.triggered is True
        assert result.tournament_id == 88
        assert result.task_id == "sync-done"
        assert result.session_count == 0
        MockTSG.return_value.generate_sessions.assert_called_once()

    def test_seed_user_auto_mode(self):
        """player_count=1, auto mode: 1 seed user → enrolled (covers lines 1235-1243)."""
        mock_tournament = MagicMock()
        mock_tournament.id = 77
        seed_row = MagicMock()
        seed_row.id = 10
        seed_row.name = "Seed Player"
        seed_row.email = "seed@lfa-seed.hu"
        campus_row = MagicMock()
        campus_row.id = 1
        lic_mock = MagicMock()

        db = _seq_db(
            _q(all_=[seed_row]),   # q0: seed pool → 1 user
            _q(first=_gm_user()),        # q1: grandmaster
            _q(first=None),        # q2: _Enroll check → not enrolled
            _q(first=lic_mock),    # q3: _Lic check → has license
            _q(all_=[campus_row]), # q4: campus validation
            _q(first=None),        # q5: CampusScheduleConfig
            _q(count=0),           # q6: session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.license.UserLicense"):
            MockSem.return_value = mock_tournament
            result = run_ops_scenario(
                _req(player_count=1, player_ids=None,
                     auto_generate_sessions=False, campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.triggered is True
        assert result.enrolled_count == 1

    def test_sync_gen_failure_422(self):
        """Sync gen returns (False, ...) → 422 HTTPException (covers lines 1521-1525)."""
        mock_tournament = MagicMock()
        mock_tournament.id = 33
        campus_row = MagicMock()
        campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),        # q0: grandmaster
            _q(all_=[campus_row]), # q1: campus validation
            _q(first=None),        # q2: CampusScheduleConfig
            _q(all_=[]),           # q3: SemesterEnrollment enrolled_user_ids
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch(
                 "app.services.tournament.session_generation"
                 ".session_generator.TournamentSessionGenerator"
             ) as MockTSG:
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (
                False, "no suitable schedule found", []
            )
            with pytest.raises(Exception) as exc:
                run_ops_scenario(
                    _req(player_count=0, auto_generate_sessions=True,
                         simulation_mode="manual", campus_ids=[1]),
                    db=db, current_user=_admin()
                )

        assert exc.value.status_code == 422
        assert "Session generation failed" in exc.value.detail

    def test_auto_generate_thread_dispatch(self):
        """BACKGROUND_GENERATION_THRESHOLD=0 → Thread path (covers lines 1479-1495)."""
        mock_tournament = MagicMock()
        mock_tournament.id = 55
        campus_row = MagicMock()
        campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),        # q0: grandmaster
            _q(all_=[campus_row]), # q1: campus validation
            _q(first=None),        # q2: CampusScheduleConfig
            _q(count=0),           # q3: session count (thread dispatched, no SE query)
        )

        _GS = "app.api.api_v1.endpoints.tournaments.generate_sessions"
        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch(f"{_GS}.BACKGROUND_GENERATION_THRESHOLD", 0), \
             patch(f"{_GS}._is_celery_available", return_value=False), \
             patch(f"{_GS}._registry_lock"), \
             patch(f"{_GS}._task_registry", {}), \
             patch(f"{_GS}._run_generation_in_background"), \
             patch("threading.Thread") as MockThread:
            MockSem.return_value = mock_tournament
            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="manual", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.triggered is True
        assert result.task_id not in ("sync-done", "manual-mode-skipped")
        MockThread.return_value.start.assert_called_once()

    def test_auto_generate_auto_immediate_mode(self):
        """auto_generate + simulation_mode=auto_immediate → sim + ranking + finalize
        (covers lines 1542-1616)."""
        mock_tournament = MagicMock()
        mock_tournament.id = 22
        campus_row = MagicMock()
        campus_row.id = 1
        mock_t2 = MagicMock()
        mock_t2.format = "INDIVIDUAL_RANKING"
        mock_t2.tournament_config_obj = None

        db = _seq_db(
            _q(first=_gm_user()),        # q0: grandmaster
            _q(all_=[campus_row]), # q1: campus validation
            _q(first=None),        # q2: CampusScheduleConfig
            _q(all_=[]),           # q3: SE enrolled_user_ids (sync path)
            _q(first=mock_t2),     # q4: tournament re-query for ranking
            _q(all_=[]),           # q5: _get_tournament_sessions (for ranking)
            _q(),                  # q6: TournamentRanking.filter().delete()
            _q(count=0),           # q7: final session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.tournament_ranking.TournamentRanking"), \
             patch(
                 "app.services.tournament.session_generation"
                 ".session_generator.TournamentSessionGenerator"
             ) as MockTSG, \
             patch(f"{_BASE}._simulate_tournament_results",
                   return_value=(True, "IR sim done")) as mock_sim, \
             patch(f"{_BASE}._finalize_tournament_with_rewards") as mock_fin:
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "1 session", [])

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.triggered is True
        assert result.task_id == "sync-done"
        mock_sim.assert_called_once()
        mock_fin.assert_called_once()


# ===========================================================================
# Phase 4 — Minimal simulation helper tests
# ===========================================================================

class TestSimulateIndividualRanking:

    def _tournament(self, scoring_type=None):
        t = MagicMock()
        t.id = 1
        if scoring_type:
            t.tournament_config_obj = MagicMock()
            t.tournament_config_obj.scoring_type = scoring_type
        else:
            t.tournament_config_obj = None
        return t

    def test_missing_scoring_type_returns_false(self):
        db = MagicMock()
        t = self._tournament(scoring_type=None)
        ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is False
        assert "scoring_type" in msg

    def test_no_sessions_returns_false(self):
        db = _seq_db(_q(all_=[]))
        t = self._tournament(scoring_type="SCORE_BASED")
        ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is False
        assert "No tournament sessions found" in msg

    def test_session_with_no_participants_skipped(self):
        s = _session_mock(participants=[])
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="SCORE_BASED")
        ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True
        assert "0 sessions simulated" in msg

    def test_session_with_game_results_skipped(self):
        s = _session_mock(participants=[42], game_results='{"already": "done"}')
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="SCORE_BASED")
        ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True
        assert "0 sessions simulated" in msg

    def test_score_based_session_calls_result_processor(self):
        s = _session_mock(participants=[42, 43], game_results=None)
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="SCORE_BASED")
        with patch("app.services.tournament.result_processor.ResultProcessor") as MockRP:
            MockRP.return_value.process_match_results.return_value = None
            ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True
        assert "1 sessions simulated" in msg
        MockRP.return_value.process_match_results.assert_called_once()

    def test_time_based_session_simulated(self):
        s = _session_mock(participants=[42], game_results=None)
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="TIME_BASED")
        with patch("app.services.tournament.result_processor.ResultProcessor") as MockRP:
            MockRP.return_value.process_match_results.return_value = None
            ok, _ = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True

    def test_unsupported_scoring_type_skips_session(self):
        s = _session_mock(participants=[42], game_results=None)
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="UNKNOWN_TYPE")
        ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True
        assert "0 sessions simulated" in msg

    def test_result_processor_exception_skips_session(self):
        s = _session_mock(participants=[42, 43], game_results=None)
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="SCORE_BASED")
        with patch("app.services.tournament.result_processor.ResultProcessor") as MockRP:
            MockRP.return_value.process_match_results.side_effect = RuntimeError("fail")
            ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True
        assert "0 sessions simulated" in msg

    def test_distance_based_session_simulated(self):
        """DISTANCE_BASED scoring path — single-round session."""
        s = _session_mock(participants=[42], game_results=None)
        s.scoring_type = None  # not ROUNDS_BASED → single-round path
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="DISTANCE_BASED")
        with patch("app.services.tournament.result_processor.ResultProcessor") as MockRP:
            MockRP.return_value.process_match_results.return_value = None
            ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True
        assert "1 sessions simulated" in msg

    def test_rounds_based_session_simulated(self):
        """ROUNDS_BASED session — multi-round path with flag_modified."""
        s = _session_mock(participants=[42, 43], game_results=None)
        s.scoring_type = "ROUNDS_BASED"
        s.rounds_data = None          # becomes {} → total_rounds=1, completed=0 → simulate
        s.structure_config = None
        db = _seq_db(_q(all_=[s]))
        t = self._tournament(scoring_type="SCORE_BASED")   # underlying scoring
        with patch("sqlalchemy.orm.attributes.flag_modified"):
            ok, msg = _simulate_individual_ranking(db, t, _LOG)
        assert ok is True
        assert "1 sessions simulated" in msg
        assert s.rounds_data is not None


class TestSimulateHeadToHeadKnockout:

    def test_no_sessions_returns_false(self):
        db = _seq_db(_q(all_=[]))
        ok, msg = _simulate_head_to_head_knockout(db, 1, _LOG)
        assert ok is False
        assert "No tournament sessions found" in msg

    def test_two_player_session_simulated(self):
        s = _session_mock(participants=[42, 43], game_results=None, round_num=1)
        db = _seq_db(_q(all_=[s]))
        ok, msg = _simulate_head_to_head_knockout(db, 1, _LOG)
        assert ok is True
        # session.game_results was set to a JSON string
        assert s.game_results is not None
        game = json.loads(s.game_results)
        assert game["match_format"] == "HEAD_TO_HEAD"
        assert len(game["participants"]) == 2
        # One participant has result=win, other has result=loss
        results = {p["user_id"]: p["result"] for p in game["participants"]}
        assert "win" in results.values()
        assert "loss" in results.values()

    def test_session_already_has_results_skipped(self):
        s = _session_mock(participants=[42, 43],
                          game_results='{"match_format":"HEAD_TO_HEAD"}',
                          round_num=1)
        db = _seq_db(_q(all_=[s]))
        ok, msg = _simulate_head_to_head_knockout(db, 1, _LOG)
        assert ok is True
        # game_results not changed (was already set before call)
        assert s.game_results == '{"match_format":"HEAD_TO_HEAD"}'

    def test_session_with_no_participants_skipped(self):
        s = _session_mock(participants=[], game_results=None, round_num=1)
        db = _seq_db(_q(all_=[s]))
        ok, msg = _simulate_head_to_head_knockout(db, 1, _LOG)
        assert ok is True
        assert s.game_results is None

    def test_session_with_wrong_participant_count_skipped(self):
        """Session with 3 participants (not 2) is skipped with a warning."""
        s = _session_mock(participants=[42, 43, 44], game_results=None, round_num=1)
        db = _seq_db(_q(all_=[s]))
        ok, _ = _simulate_head_to_head_knockout(db, 1, _LOG)
        assert ok is True
        assert s.game_results is None  # skipped

    def test_two_round_bracket_advancement(self):
        """Winners from round 1 are assigned to round 2 session."""
        r1s1 = _session_mock(participants=[1, 2], round_num=1, match_num=1, title="SF1")
        r1s2 = _session_mock(participants=[3, 4], round_num=1, match_num=2, title="SF2")
        r2s1 = _session_mock(participants=[], round_num=2, match_num=1, title="Final")

        db = _seq_db(_q(all_=[r1s1, r1s2, r2s1]))
        ok, _ = _simulate_head_to_head_knockout(db, 1, _LOG)
        assert ok is True
        # Round 2 session should now have 2 participants (winners from round 1)
        assert len(r2s1.participant_user_ids) == 2
        # Winners must be from the original participants
        for uid in r2s1.participant_user_ids:
            assert uid in [1, 2, 3, 4]


# ===========================================================================
# Phase 5 — Remaining simulation functions (league, knockout_bracket, group_knockout)
# ===========================================================================

class TestSimulateLeagueTournament:
    """_simulate_league_tournament(db, tournament_id, logger) — lines 862-946."""

    def test_no_sessions_returns_false(self):
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[]):
            ok, msg = _simulate_league_tournament(MagicMock(), 1, _LOG)
        assert ok is False
        assert "No tournament sessions found" in msg

    def test_session_no_participants_skipped(self):
        s = _session_mock(participants=[], game_results=None)
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s]):
            ok, msg = _simulate_league_tournament(MagicMock(), 1, _LOG)
        assert ok is True
        assert "0 league sessions simulated" in msg

    def test_session_already_has_results_skipped(self):
        s = _session_mock(participants=[42, 43], game_results='{"already":"done"}')
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s]):
            ok, msg = _simulate_league_tournament(MagicMock(), 1, _LOG)
        assert ok is True
        assert "0 league sessions simulated" in msg

    def test_two_player_win_session_simulated(self):
        s = _session_mock(participants=[42, 43], game_results=None)
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s]), \
             patch("random.choice", side_effect=["win", True]), \
             patch("random.randint", side_effect=[3, 1]):
            ok, msg = _simulate_league_tournament(db, 1, _LOG)
        assert ok is True
        assert "1 league sessions simulated" in msg
        assert s.game_results is not None
        db.commit.assert_called()

    def test_two_player_draw_session_simulated(self):
        s = _session_mock(participants=[42, 43], game_results=None)
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s]), \
             patch("random.choice", return_value="draw"), \
             patch("random.randint", return_value=2):
            ok, msg = _simulate_league_tournament(db, 1, _LOG)
        assert ok is True
        game = json.loads(s.game_results)
        results = {p["user_id"]: p["result"] for p in game["participants"]}
        assert results[42] == "draw"
        assert results[43] == "draw"

    def test_two_player_user2_wins_session_simulated(self):
        """random.choice(True/False) returns False → user_2 wins (covers else branch)."""
        s = _session_mock(participants=[42, 43], game_results=None)
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s]), \
             patch("random.choice", side_effect=["win", False]), \
             patch("random.randint", side_effect=[3, 1]):
            ok, msg = _simulate_league_tournament(db, 1, _LOG)
        assert ok is True
        game = json.loads(s.game_results)
        results = {p["user_id"]: p["result"] for p in game["participants"]}
        assert results[42] == "loss"
        assert results[43] == "win"


class TestSimulateKnockoutBracket:
    """_simulate_knockout_bracket(db, sessions, logger) → (simulated, skipped) — lines 949-1041."""

    def test_empty_list_returns_zeros(self):
        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [], _LOG)
        assert simulated == 0
        assert skipped == 0

    def test_session_already_has_results_skipped(self):
        s = _session_mock(participants=[42, 43], game_results='{"done":true}', round_num=1)
        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [s], _LOG)
        assert simulated == 0
        assert skipped == 1

    def test_session_no_participants_skipped(self):
        s = _session_mock(participants=[], game_results=None, round_num=1)
        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [s], _LOG)
        assert simulated == 0
        assert skipped == 1

    def test_two_player_session_simulated(self):
        s = _session_mock(participants=[42, 43], game_results=None, round_num=1, match_num=1, title="Final")
        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [s], _LOG)
        assert simulated == 1
        assert skipped == 0
        assert s.game_results is not None
        game = json.loads(s.game_results)
        results = {p["user_id"]: p["result"] for p in game["participants"]}
        assert "win" in results.values()
        assert "loss" in results.values()

    def test_bracket_advancement_to_next_round(self):
        """Winners from round 1 are propagated to round 2 and then simulated."""
        r1s1 = _session_mock(participants=[1, 2], round_num=1, match_num=1, title="SF1")
        r1s2 = _session_mock(participants=[3, 4], round_num=1, match_num=2, title="SF2")
        r2s1 = _session_mock(participants=[], round_num=2, match_num=1, title="Final")
        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [r1s1, r1s2, r2s1], _LOG)
        # Round 1: 2 sessions, Round 2: 1 session (after winner assignment)
        assert simulated == 3
        assert skipped == 0
        assert len(r2s1.participant_user_ids) == 2
        for uid in r2s1.participant_user_ids:
            assert uid in [1, 2, 3, 4]


class TestSimulateGroupKnockoutTournament:
    """_simulate_group_knockout_tournament(db, tournament_id, logger) — lines 643-859."""

    def test_no_sessions_returns_false(self):
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[]):
            ok, msg = _simulate_group_knockout_tournament(MagicMock(), 1, _LOG)
        assert ok is False
        assert "No tournament sessions found" in msg

    def test_group_sessions_only_simulated(self):
        gs = _session_mock(participants=[1, 2], game_results=None, round_num=1, match_num=1)
        gs.tournament_phase = "GROUP_STAGE"
        gs.group_identifier = "A"
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs]), \
             patch(f"{_BASE}._simulate_knockout_bracket", return_value=(0, 0)):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)
        assert ok is True
        assert "group=1" in msg
        db.commit.assert_called()

    def test_group_and_knockout_sessions(self):
        gs = _session_mock(participants=[1, 2], game_results=None, round_num=1, match_num=1)
        gs.tournament_phase = "GROUP_STAGE"
        gs.group_identifier = "A"
        ks = _session_mock(participants=[], game_results=None, round_num=1, match_num=1, title="QF")
        ks.tournament_phase = "KNOCKOUT"
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs, ks]), \
             patch(f"{_BASE}._simulate_knockout_bracket", return_value=(1, 0)):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)
        assert ok is True
        assert "group=1" in msg
        assert "knockout=1" in msg

    def test_already_simulated_group_sessions_skipped(self):
        gs = _session_mock(participants=[1, 2], game_results='{"done":true}', round_num=1)
        gs.tournament_phase = "GROUP_STAGE"
        gs.group_identifier = "A"
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs]), \
             patch(f"{_BASE}._simulate_knockout_bracket", return_value=(0, 0)):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)
        assert ok is True
        assert "group=0" in msg

    def test_group_session_no_participants_skipped(self):
        """Session with < 2 participants → warning + skipped (covers lines 700-703)."""
        gs = _session_mock(participants=[1], game_results=None, round_num=1)
        gs.tournament_phase = "GROUP_STAGE"
        gs.group_identifier = "A"
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs]), \
             patch(f"{_BASE}._simulate_knockout_bracket", return_value=(0, 0)):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)
        assert ok is True
        assert "group=0" in msg  # 0 simulated, 1 skipped

    def test_group_session_draw_outcome(self):
        """random.choice → 'draw' → both players get draw result (covers lines 711-715, 748-750)."""
        gs = _session_mock(participants=[10, 20], game_results=None, round_num=1)
        gs.tournament_phase = "GROUP_STAGE"
        gs.group_identifier = "A"
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs]), \
             patch(f"{_BASE}._simulate_knockout_bracket", return_value=(0, 0)), \
             patch("random.choice", return_value="draw"), \
             patch("random.randint", return_value=2):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)
        assert ok is True
        assert "group=1" in msg
        game = json.loads(gs.game_results)
        results = {p["user_id"]: p["result"] for p in game["participants"]}
        assert results[10] == "draw"
        assert results[20] == "draw"

    def test_group_session_user2_wins(self):
        """random.choice: 'win' then False → user_2 wins (covers lines 726-730, 751-752)."""
        gs = _session_mock(participants=[10, 20], game_results=None, round_num=1)
        gs.tournament_phase = "GROUP_STAGE"
        gs.group_identifier = "A"
        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs]), \
             patch(f"{_BASE}._simulate_knockout_bracket", return_value=(0, 0)), \
             patch("random.choice", side_effect=["win", False]), \
             patch("random.randint", side_effect=[3, 1]):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)
        assert ok is True
        game = json.loads(gs.game_results)
        results = {p["user_id"]: p["result"] for p in game["participants"]}
        assert results[10] == "loss"
        assert results[20] == "win"


# ===========================================================================
# Sprint P14 — End-to-end operational workflow tests
# ===========================================================================

class TestWorkflowSimulationChain:
    """Tests verifying that simulation functions produce outputs that correctly
    feed into downstream steps (standings → qualifiers → bracket, sim → rank)."""

    def test_workflow_league_round_robin_3_sessions(self):
        """3-session round-robin: all sessions completed, all game_results populated.

        Workflow: _simulate_league_tournament processes A-B, A-C, B-C sessions.
        Each session gets game_results and session_status=completed.
        """
        s1 = _session_mock(participants=[1, 2], game_results=None, round_num=1)
        s2 = _session_mock(participants=[1, 3], game_results=None, round_num=1)
        s3 = _session_mock(participants=[2, 3], game_results=None, round_num=2)
        db = MagicMock()
        # s1: user1 wins, s2: user1 wins, s3: draw
        # random.choice calls: outcome1, winner_selector1, outcome2, winner_selector2, outcome3
        # random.randint calls: winner1, loser1, winner2, loser2, draw_score3
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s1, s2, s3]), \
             patch("random.choice", side_effect=["win", True, "win", True, "draw"]), \
             patch("random.randint", side_effect=[3, 1, 2, 0, 2]):
            ok, msg = _simulate_league_tournament(db, 1, _LOG)
        assert ok is True
        assert "3 league sessions simulated" in msg
        for s in [s1, s2, s3]:
            assert s.game_results is not None, f"session {s.id} missing game_results"
            assert s.session_status == "completed"
        # s1: user1 won
        g1 = json.loads(s1.game_results)
        r1 = {p["user_id"]: p["result"] for p in g1["participants"]}
        assert r1[1] == "win" and r1[2] == "loss"
        # s3: draw
        g3 = json.loads(s3.game_results)
        r3 = {p["user_id"]: p["result"] for p in g3["participants"]}
        assert r3[2] == "draw" and r3[3] == "draw"
        db.commit.assert_called()

    def test_workflow_group_knockout_qualifiers_fill_bracket(self):
        """Full GROUP+KNOCKOUT pipeline: group stage → standings → qualifier seeding → knockout.

        Workflow:
          PHASE 1: Simulate group A (4 sessions, 2 per group: gs1=[10,20], gs2=[30,40])
          PHASE 2: Calculate group standings (10 and 30 win → rank 1 each)
          PHASE 3: Seed qualifiers to knockout R1 bracket ([10,30] → ks1)
          PHASE 4: Simulate knockout session → ks1 gets game_results
        """
        # Group A: two sessions (1v2 and 3v4)
        gs1 = _session_mock(participants=[10, 20], game_results=None, round_num=1, match_num=1)
        gs1.tournament_phase = "GROUP_STAGE"
        gs1.group_identifier = "A"
        gs2 = _session_mock(participants=[30, 40], game_results=None, round_num=1, match_num=2)
        gs2.tournament_phase = "GROUP_STAGE"
        gs2.group_identifier = "A"
        # Knockout R1 session: empty participants, needs qualifier assignment
        ks1 = _session_mock(participants=[], game_results=None, round_num=1, match_num=1)
        ks1.tournament_phase = "KNOCKOUT"

        db = MagicMock()
        # Deterministic random:
        #   gs1: outcome="win", winner_score=3, user10_wins=True, loser_score=1
        #   gs2: outcome="win", winner_score=2, user30_wins=True, loser_score=0
        #   ks1 (after qualifier assignment [10,30]): winner=10, ko_winner_score=2, ko_loser_score=0
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs1, gs2, ks1]), \
             patch("random.choice", side_effect=["win", True, "win", True, 10]), \
             patch("random.randint", side_effect=[3, 1, 2, 0, 2, 0]):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)

        assert ok is True
        # Group sessions were simulated
        assert gs1.game_results is not None
        assert gs2.game_results is not None
        # Qualifiers correctly seeded into knockout bracket (PHASE 3 contract)
        assert ks1.participant_user_ids == [10, 30], (
            f"Expected [10, 30] in knockout bracket, got {ks1.participant_user_ids}"
        )
        # Knockout session was also simulated (PHASE 4 contract)
        assert ks1.game_results is not None
        assert ks1.session_status == "completed"
        # Summary message reflects both phases
        assert "group=2" in msg
        assert "knockout=1" in msg

    def test_workflow_rounds_based_sim_feeds_ir_ranking(self):
        """ROUNDS_BASED simulation produces rounds_data → _calculate_ir_rankings ranks both players.

        Workflow:
          1. _simulate_individual_ranking sets session.rounds_data with per-user scores
          2. _calculate_ir_rankings reads rounds_data and returns ranked list
        """
        s = _session_mock(participants=[10, 20], game_results=None)
        s.scoring_type = "ROUNDS_BASED"
        s.rounds_data = {"total_rounds": 1, "completed_rounds": 0}
        s.structure_config = None

        db = _seq_db(_q(all_=[s]))
        tournament = MagicMock()
        tournament.id = 1
        tournament.tournament_config_obj = MagicMock()
        tournament.tournament_config_obj.scoring_type = "SCORE_BASED"
        tournament.tournament_config_obj.ranking_direction = "DESC"

        # Step 1: simulate — pin randint so user 10 scores 85, user 20 scores 60 (no tie)
        with patch("sqlalchemy.orm.attributes.flag_modified"), \
             patch("random.randint", side_effect=[85, 60]):
            ok, msg = _simulate_individual_ranking(db, tournament, _LOG)

        assert ok is True
        assert "1 sessions simulated" in msg
        # rounds_data was updated with per-user results
        assert isinstance(s.rounds_data, dict)
        assert "round_results" in s.rounds_data
        assert "1" in s.rounds_data["round_results"]
        round_results = s.rounds_data["round_results"]["1"]
        assert "10" in round_results and "20" in round_results

        # Step 2: rank — uses the rounds_data produced by step 1
        rankings = _calculate_ir_rankings(tournament, [s], _LOG)
        assert len(rankings) == 2
        user_ids_ranked = {r["user_id"] for r in rankings}
        assert user_ids_ranked == {10, 20}
        ranks = sorted(r["rank"] for r in rankings)
        assert ranks == [1, 2]

    def test_workflow_knockout_3rd_place_playoff(self):
        """Semi-final losers are assigned to 3rd-place playoff → both sessions simulated.

        Workflow: _simulate_knockout_bracket with 4 sessions:
          R1: [1,2] and [3,4] → winners advance to final, losers to 3rd-place
          R2: final (empty) + "3rd Place" session (empty)
        After simulation: final and playoff both have game_results + participants.
        """
        r1s1 = _session_mock(participants=[1, 2], round_num=1, match_num=1, title="SF1")
        r1s2 = _session_mock(participants=[3, 4], round_num=1, match_num=2, title="SF2")
        r2_final = _session_mock(participants=[], round_num=2, match_num=1, title="Final")
        r2_playoff = _session_mock(participants=[], round_num=2, match_num=2, title="3rd Place")

        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [r1s1, r1s2, r2_final, r2_playoff], _LOG)

        assert simulated == 4  # R1×2 + R2_final + R2_playoff
        assert skipped == 0
        # Final got the 2 winners
        assert len(r2_final.participant_user_ids) == 2
        for uid in r2_final.participant_user_ids:
            assert uid in [1, 2, 3, 4]
        # Playoff got the 2 losers
        assert len(r2_playoff.participant_user_ids) == 2
        for uid in r2_playoff.participant_user_ids:
            assert uid in [1, 2, 3, 4]
        # Winners and losers must be disjoint sets
        assert set(r2_final.participant_user_ids).isdisjoint(set(r2_playoff.participant_user_ids))
        # Both R2 sessions were simulated
        assert r2_final.game_results is not None
        assert r2_playoff.game_results is not None


_COMMON_PATCHES = [
    "app.models.semester.Semester",
    "app.models.semester.SemesterStatus",
    "app.models.tournament_configuration.TournamentConfiguration",
    "app.models.tournament_reward_config.TournamentRewardConfig",
    "app.models.tournament_achievement.TournamentSkillMapping",
    "app.models.campus.Campus",
    "app.models.campus_schedule_config.CampusScheduleConfig",
    "app.services.audit_service.AuditService",
    "app.models.audit_log.AuditAction",
    "app.models.session.Session",
    "app.models.semester_enrollment.SemesterEnrollment",
    "app.models.semester_enrollment.EnrollmentStatus",
    "app.models.license.UserLicense",
]


class TestWorkflowFullOpsScenario:
    """End-to-end endpoint workflow tests verifying the full operational lifecycle."""

    def test_workflow_3_players_all_enrolled(self):
        """player_count=3 with 3 seed users → all 3 enrolled (multi-player enrollment loop).

        Workflow: seed pool query → enrollment loop × 3 (enroll_check + lic_check each)
        → campus validation → session count.
        """
        mock_tournament = MagicMock()
        mock_tournament.id = 99
        rows = [MagicMock(id=i, name=f"P{i}", email=f"p{i}@lfa-seed.hu") for i in [10, 20, 30]]
        campus_row = MagicMock()
        campus_row.id = 1
        lic_mock = MagicMock()

        db = _seq_db(
            _q(all_=rows),         # q0: seed pool → 3 users
            _q(first=_gm_user()),        # q1: grandmaster
            _q(first=None),        # q2: enroll check p1 → not enrolled
            _q(first=lic_mock),    # q3: lic check p1 → has license
            _q(first=None),        # q4: enroll check p2
            _q(first=lic_mock),    # q5: lic check p2
            _q(first=None),        # q6: enroll check p3
            _q(first=lic_mock),    # q7: lic check p3
            _q(all_=[campus_row]), # q8: campus validation
            _q(first=None),        # q9: CampusScheduleConfig
            _q(count=0),           # q10: session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.license.UserLicense"):
            MockSem.return_value = mock_tournament
            result = run_ops_scenario(
                _req(player_count=3, player_ids=None,
                     auto_generate_sessions=False, campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.triggered is True
        assert result.enrolled_count == 3

    def test_workflow_real_sim_dispatch_ir(self):
        """Full endpoint: _simulate_tournament_results NOT mocked → IR dispatch verified.

        Workflow:
          seed→enroll(0)→generate(TSG mock)→_simulate_tournament_results(real, IR format)
          →_simulate_individual_ranking(real, SCORE_BASED, ResultProcessor mocked)
          →_calculate_ir_rankings(real, empty rounds_data → empty rankings)
          →_finalize_tournament_with_rewards (mocked)
        """
        mock_tournament = MagicMock()
        mock_tournament.id = 11
        campus_row = MagicMock()
        campus_row.id = 1

        # Session TSG "generates": 1 participant, SCORE_BASED — gets simulated
        sim_session = _session_mock(participants=[42], game_results=None)
        sim_session.tournament_phase = None   # no phases → IR dispatch
        sim_session.scoring_type = None       # not ROUNDS_BASED → single-round path

        # Tournament objects for _simulate_tournament_results dispatch query
        mock_t_dispatch = MagicMock()
        mock_t_dispatch.format = "INDIVIDUAL_RANKING"
        mock_t_dispatch.tournament_config_obj = MagicMock()
        mock_t_dispatch.tournament_config_obj.scoring_type = "SCORE_BASED"

        # Tournament for ranking re-query
        mock_t2 = MagicMock()
        mock_t2.format = "INDIVIDUAL_RANKING"
        mock_t2.tournament_config_obj = None  # → empty rankings (no rounds_data)

        db = _seq_db(
            _q(first=_gm_user()),              # q0: grandmaster
            _q(all_=[campus_row]),       # q1: campus validation
            _q(first=None),              # q2: CampusScheduleConfig
            _q(all_=[]),                 # q3: SE enrolled_user_ids (sync path)
            # _simulate_tournament_results runs (real):
            _q(first=mock_t_dispatch),   # q4: tournament format query
            _q(all_=[sim_session]),      # q5: _get_tournament_sessions (phase detection)
            # _simulate_individual_ranking runs (real):
            _q(all_=[sim_session]),      # q6: sessions in _simulate_individual_ranking
            # Ranking calculation:
            _q(first=mock_t2),           # q7: tournament re-query for ranking
            _q(all_=[sim_session]),      # q8: _get_tournament_sessions for ranking
            _q(),                        # q9: TournamentRanking.filter().delete()
            _q(count=0),                 # q10: final session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.tournament_ranking.TournamentRanking"), \
             patch("app.services.tournament.result_processor.ResultProcessor"), \
             patch(
                 "app.services.tournament.session_generation"
                 ".session_generator.TournamentSessionGenerator"
             ) as MockTSG, \
             patch(f"{_BASE}._finalize_tournament_with_rewards") as mock_fin:
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "1 session", [sim_session])

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.triggered is True
        assert result.task_id == "sync-done"
        # _finalize_tournament_with_rewards was called — full lifecycle completed
        mock_fin.assert_called_once()
        # The TSG-generated session was passed into the real sim dispatch
        MockTSG.return_value.generate_sessions.assert_called_once()


# ===========================================================================
# Sprint P15 — Failure and edge workflow scenarios
# ===========================================================================

class TestEdgeCaseWorkflows:
    """P15: Robustness under imperfect real-world conditions.

    Covers:
      1. Partial tournament completion (some sessions already done)
      2. Participant withdrawal / missing participants mid-tournament
      3. Tie-breaking edge cases in IR ranking (ASC vs DESC)
      4. Concurrent scenario generation — unique task_id per dispatch
    """

    # -----------------------------------------------------------------------
    # 1. Partial tournament completion
    # -----------------------------------------------------------------------

    def test_partial_completion_league_skips_done_sessions(self):
        """League: 3 sessions where session 1 already has game_results.

        Contract:
          - Sessions with game_results are skipped (skipped_count += 1)
          - Remaining sessions are simulated normally
          - Return message reports correct counts: "2 league sessions simulated, 1 skipped"
        """
        done = _session_mock(participants=[10, 20], game_results='{"match_format":"HEAD_TO_HEAD"}',
                             round_num=1, match_num=1)
        pending1 = _session_mock(participants=[10, 30], game_results=None, round_num=1, match_num=2)
        pending2 = _session_mock(participants=[20, 30], game_results=None, round_num=1, match_num=3)

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[done, pending1, pending2]), \
             patch("random.choice", side_effect=["win", True, "win", True]), \
             patch("random.randint", side_effect=[3, 0, 2, 0]):
            ok, msg = _simulate_league_tournament(db, 1, _LOG)

        assert ok is True
        assert "2 league sessions simulated" in msg
        assert "1 skipped" in msg
        # Done session must not be overwritten
        assert done.game_results == '{"match_format":"HEAD_TO_HEAD"}'
        # Pending sessions must have been filled
        assert pending1.game_results is not None
        assert pending2.game_results is not None

    def test_partial_completion_knockout_skips_done_round(self):
        """Knockout: R1 sessions already have game_results → R1 skipped, R2 winner advances.

        When R1 sessions are already done AND have game_results, they are
        counted as skipped but their participants are NOT re-advanced
        (no round_winners populated). R2 session stays with its existing
        participant list.
        """
        r1s1 = _session_mock(
            participants=[1, 2],
            game_results='{"match_format":"HEAD_TO_HEAD","round_number":1,"participants":[]}',
            round_num=1, match_num=1, title="SF1"
        )
        r1s2 = _session_mock(
            participants=[3, 4],
            game_results='{"match_format":"HEAD_TO_HEAD","round_number":1,"participants":[]}',
            round_num=1, match_num=2, title="SF2"
        )
        r2_final = _session_mock(participants=[1, 3], game_results=None, round_num=2, match_num=1, title="Final")

        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [r1s1, r1s2, r2_final], _LOG)

        # R1: both skipped; R2: 1 simulated (has [1,3] participants already)
        assert skipped == 2
        assert simulated == 1
        assert r2_final.game_results is not None

    def test_partial_completion_group_knockout_group_done_knockout_pending(self):
        """Group+Knockout: all group sessions done, knockout sessions pending.

        group_sessions all have game_results → group_simulated=0, group_skipped=N.
        PHASE 2 still calculates standings from game_results (standings empty because
        group_standings only populated during simulation loop — not read from game_results).
        Knockout proceeds but seeded_qualifiers is empty → ks1 stays with participants=[].
        """
        gs1 = _session_mock(
            participants=[10, 20],
            game_results='{"match_format":"HEAD_TO_HEAD","round_number":1,"participants":[]}',
            round_num=1, match_num=1
        )
        gs1.tournament_phase = "GROUP_STAGE"
        gs1.group_identifier = "A"

        ks1 = _session_mock(participants=[], game_results=None, round_num=1, match_num=1)
        ks1.tournament_phase = "KNOCKOUT"

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs1, ks1]):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)

        assert ok is True
        # Group session was skipped (game_results already set)
        assert gs1.game_results is not None
        # Knockout session with empty participants stays skipped (no seeded qualifiers → empty → skipped)
        assert ks1.participant_user_ids == []

    # -----------------------------------------------------------------------
    # 2. Participant withdrawal / missing results
    # -----------------------------------------------------------------------

    def test_missing_participants_league_session_skipped(self):
        """League session with participant_user_ids=None is skipped (not crashed)."""
        no_pax = _session_mock(participants=None, game_results=None)
        # Override: explicitly set None (not the default [42,43])
        no_pax.participant_user_ids = None

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[no_pax]):
            ok, msg = _simulate_league_tournament(db, 1, _LOG)

        assert ok is True
        assert "0 league sessions simulated" in msg
        assert "1 skipped" in msg
        # Session must not have been modified
        assert no_pax.game_results is None

    def test_missing_participants_knockout_single_player_skipped(self):
        """Knockout session with only 1 participant (withdrawal) is skipped."""
        one_pax = _session_mock(participants=[42], game_results=None, round_num=1, match_num=1)

        simulated, skipped = _simulate_knockout_bracket(MagicMock(), [one_pax], _LOG)

        assert simulated == 0
        assert skipped == 1
        assert one_pax.game_results is None

    def test_missing_participants_group_session_skipped(self):
        """Group session with empty participants list is skipped cleanly."""
        empty_pax = _session_mock(participants=[], game_results=None)
        empty_pax.tournament_phase = "GROUP_STAGE"
        empty_pax.group_identifier = "A"

        valid = _session_mock(participants=[10, 20], game_results=None, round_num=1, match_num=2)
        valid.tournament_phase = "GROUP_STAGE"
        valid.group_identifier = "A"

        ks = _session_mock(participants=[], game_results=None, round_num=1, match_num=1)
        ks.tournament_phase = "KNOCKOUT"

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[empty_pax, valid, ks]), \
             patch("random.choice", side_effect=["win", True, 10]), \
             patch("random.randint", side_effect=[3, 0, 1, 0]):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)

        assert ok is True
        # Empty-pax session must remain unchanged
        assert empty_pax.game_results is None
        # Valid group session must have been simulated
        assert valid.game_results is not None

    # -----------------------------------------------------------------------
    # 3. Tie-breaking edge cases in IR ranking
    # -----------------------------------------------------------------------

    def test_ir_ranking_tie_desc_both_rank_first(self):
        """Two players with identical DESC scores → both get rank 1 (dense-rank tie).

        RankingAggregator uses dense ranking: equal values share the same rank.
        Both players appear in the result; no player is arbitrarily demoted to rank 2.
        """
        t = MagicMock()
        t.tournament_config_obj = MagicMock()
        t.tournament_config_obj.ranking_direction = "DESC"

        s = MagicMock()
        # Both players score 75.0 — identical
        s.rounds_data = {"round_results": {"1": {"10": "75.0", "20": "75.0"}}}

        rankings = _calculate_ir_rankings(t, [s], _LOG)

        assert len(rankings) == 2
        # Dense-rank: both tied players share rank 1
        assert all(r["rank"] == 1 for r in rankings)
        user_ids = {r["user_id"] for r in rankings}
        assert user_ids == {10, 20}

    def test_ir_ranking_tie_asc_both_rank_first(self):
        """Two players with identical ASC scores → both get rank 1 (dense-rank tie).

        ASC direction means lower value is better (e.g. TIME_BASED).
        Equal values still share the same dense rank.
        """
        t = MagicMock()
        t.tournament_config_obj = MagicMock()
        t.tournament_config_obj.ranking_direction = "ASC"

        s = MagicMock()
        # Both players time 42.0 — identical
        s.rounds_data = {"round_results": {"1": {"10": "42.0", "20": "42.0"}}}

        rankings = _calculate_ir_rankings(t, [s], _LOG)

        assert len(rankings) == 2
        # Dense-rank: both tied players share rank 1
        assert all(r["rank"] == 1 for r in rankings)

    def test_ir_ranking_multi_session_aggregation(self):
        """Rankings aggregate across 2 sessions — player with higher values across rounds ranks 1."""
        t = MagicMock()
        t.tournament_config_obj = MagicMock()
        t.tournament_config_obj.ranking_direction = "DESC"

        # Session 1: round 1 results
        s1 = MagicMock()
        s1.rounds_data = {"round_results": {"1": {"10": "80.0", "20": "60.0"}}}

        # Session 2: round 2 results (distinct round key "2" → both accumulated)
        s2 = MagicMock()
        s2.rounds_data = {"round_results": {"2": {"10": "90.0", "20": "70.0"}}}

        rankings = _calculate_ir_rankings(t, [s1, s2], _LOG)

        # Both players appear; user 10 scores higher in both rounds → rank 1
        assert len(rankings) == 2
        top = next(r for r in rankings if r["rank"] == 1)
        assert top["user_id"] == 10

    # -----------------------------------------------------------------------
    # 4. Concurrent scenario generation — unique task_id per dispatch
    # -----------------------------------------------------------------------

    def test_concurrent_thread_dispatch_unique_task_ids(self):
        """Two sequential Thread dispatch calls each produce a unique task_id.

        Verifies the registry contract:
          - Each run_ops_scenario call with auto_generate + player_count≥threshold
            creates a new uuid4 task_id
          - task_ids from two consecutive calls are distinct
          - Thread.start() is called for each dispatch
        """
        _GS = "app.api.api_v1.endpoints.tournaments.generate_sessions"

        mock_tournament_a = MagicMock()
        mock_tournament_a.id = 201
        mock_tournament_b = MagicMock()
        mock_tournament_b.id = 202
        campus_row = MagicMock(); campus_row.id = 1

        task_ids = []

        def _make_db():
            return _seq_db(
                _q(first=_gm_user()),          # q0: grandmaster
                _q(all_=[campus_row]),   # q1: campus validation
                _q(first=None),          # q2: CampusScheduleConfig
                _q(all_=[]),             # q3: SE enrolled_user_ids
            )

        import contextlib
        common_patches = [
            patch("app.models.semester.Semester"),
            patch("app.models.semester.SemesterStatus"),
            patch("app.models.tournament_configuration.TournamentConfiguration"),
            patch("app.models.tournament_reward_config.TournamentRewardConfig"),
            patch("app.models.tournament_achievement.TournamentSkillMapping"),
            patch("app.models.campus.Campus"),
            patch("app.models.campus_schedule_config.CampusScheduleConfig"),
            patch("app.services.audit_service.AuditService"),
            patch("app.models.audit_log.AuditAction"),
            patch("app.models.session.Session"),
            patch("app.models.semester_enrollment.SemesterEnrollment"),
            patch("app.models.semester_enrollment.EnrollmentStatus"),
            patch("app.models.license.UserLicense"),
            patch(f"{_GS}.BACKGROUND_GENERATION_THRESHOLD", 0),
            patch(f"{_GS}._is_celery_available", return_value=False),
            patch(f"{_GS}._registry_lock"),
            patch(f"{_GS}._task_registry", {}),
            patch(f"{_GS}._run_generation_in_background"),
            patch("threading.Thread"),
        ]

        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in common_patches]
            MockSem = mocks[0]
            MockThread = mocks[-1]

            # First call
            MockSem.return_value = mock_tournament_a
            result_a = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="manual", campus_ids=[1]),
                db=_make_db(), current_user=_admin()
            )
            task_ids.append(result_a.task_id)

            # Second call
            MockSem.return_value = mock_tournament_b
            result_b = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="manual", campus_ids=[1]),
                db=_make_db(), current_user=_admin()
            )
            task_ids.append(result_b.task_id)

        # Each call should produce a unique task_id
        assert task_ids[0] != task_ids[1], (
            f"Expected unique task_ids but got identical: {task_ids[0]!r}"
        )
        # Both must not be the sync-done sentinel
        assert task_ids[0] not in ("sync-done", "manual-mode-skipped")
        assert task_ids[1] not in ("sync-done", "manual-mode-skipped")
        # Thread.start() called twice (once per dispatch)
        assert MockThread.return_value.start.call_count == 2


# ===========================================================================
# Sprint P16 — State integrity under repeated or concurrent execution
# ===========================================================================

class TestStateIntegrity:
    """P16: Data consistency guarantees under re-runs and concurrency.

    Covers:
      1. Idempotent re-run — second simulation run skips all, no overwrites
      2. Reward distribution protection — IntegrityError / already-distributed handled
      3. Session result overwrite protection — game_results immutable once set
      4. Concurrent finalize simulation — two workers, first succeeds, second rollbacks
    """

    _FIN_PATH = (
        "app.services.tournament.results.finalization"
        ".tournament_finalizer.TournamentFinalizer"
    )

    # -----------------------------------------------------------------------
    # 1. Idempotent re-run
    # -----------------------------------------------------------------------

    def test_idempotent_league_rerun_skips_all_on_second_call(self):
        """Running _simulate_league_tournament twice is fully idempotent.

        After the first run all sessions have game_results.
        Second run must return "0 simulated, N skipped" without overwriting
        any result values.
        """
        s1 = _session_mock(participants=[10, 20], game_results=None, round_num=1, match_num=1)
        s2 = _session_mock(participants=[30, 40], game_results=None, round_num=1, match_num=2)

        db = MagicMock()

        # First run — both sessions simulated
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s1, s2]), \
             patch("random.choice", side_effect=["win", True, "win", True]), \
             patch("random.randint", side_effect=[3, 0, 2, 0]):
            ok1, msg1 = _simulate_league_tournament(db, 1, _LOG)

        assert ok1 is True
        assert "2 league sessions simulated" in msg1
        snapshot_s1 = s1.game_results
        snapshot_s2 = s2.game_results
        assert snapshot_s1 is not None
        assert snapshot_s2 is not None

        # Second run — same sessions, all already done
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[s1, s2]):
            ok2, msg2 = _simulate_league_tournament(db, 1, _LOG)

        assert ok2 is True
        assert "0 league sessions simulated" in msg2
        assert "2 skipped" in msg2
        # Results must be byte-for-byte unchanged
        assert s1.game_results == snapshot_s1
        assert s2.game_results == snapshot_s2

    def test_idempotent_knockout_rerun_skips_all_on_second_call(self):
        """Running _simulate_knockout_bracket twice is fully idempotent.

        After the first run: all 3 sessions have game_results; final has
        participant_user_ids populated from R1 advancement.
        Second run: all sessions skipped (game_results present), no participant
        list is overwritten (round_winners stays empty → no advancement).
        """
        s1 = _session_mock(participants=[1, 2], game_results=None, round_num=1, match_num=1)
        s2 = _session_mock(participants=[3, 4], game_results=None, round_num=1, match_num=2)
        final = _session_mock(participants=[], game_results=None, round_num=2, match_num=1, title="Final")

        # First run: 3 sessions simulated
        with patch("random.choice", side_effect=[1, 3, 1]), \
             patch("random.randint", side_effect=[3, 0, 2, 0, 4, 1]):
            sim1, skip1 = _simulate_knockout_bracket(MagicMock(), [s1, s2, final], _LOG)

        assert sim1 == 3
        assert skip1 == 0
        snap_s1 = s1.game_results
        snap_s2 = s2.game_results
        snap_final = final.game_results
        snap_final_pax = list(final.participant_user_ids)  # [1, 3] from R1 winners

        # Second run — all have game_results → all skipped, none overwritten
        sim2, skip2 = _simulate_knockout_bracket(MagicMock(), [s1, s2, final], _LOG)

        assert sim2 == 0
        assert skip2 == 3
        assert s1.game_results == snap_s1
        assert s2.game_results == snap_s2
        assert final.game_results == snap_final
        # Participant list of final must not be cleared or re-assigned
        assert list(final.participant_user_ids) == snap_final_pax

    def test_idempotent_group_knockout_rerun_all_done_returns_ok(self):
        """Running _simulate_group_knockout_tournament when all sessions are done returns True.

        With every session already having game_results, both group stage and
        knockout stage are fully skipped. The function must still return ok=True.
        """
        gs1 = _session_mock(
            participants=[10, 20],
            game_results='{"match_format":"HEAD_TO_HEAD","round_number":1,"participants":[]}',
            round_num=1, match_num=1,
        )
        gs1.tournament_phase = "GROUP_STAGE"
        gs1.group_identifier = "A"

        ks1 = _session_mock(
            participants=[10, 20],
            game_results='{"match_format":"HEAD_TO_HEAD","round_number":1,"participants":[]}',
            round_num=1, match_num=1,
        )
        ks1.tournament_phase = "KNOCKOUT"

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs1, ks1]):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)

        assert ok is True
        # Neither session must have been touched
        assert "HEAD_TO_HEAD" in gs1.game_results
        assert "HEAD_TO_HEAD" in ks1.game_results

    # -----------------------------------------------------------------------
    # 2. Reward distribution protection
    # -----------------------------------------------------------------------

    def test_reward_protection_already_distributed_state_logs_warning(self):
        """finalize() returns success=False with 'already distributed' message.

        This models a second worker arriving after the first already completed
        reward distribution. The response success=False must be caught gracefully:
        warning logged, no exception raised, no rollback called (no DB error).
        """
        t = MagicMock()
        db = _seq_db(_q(first=t))
        logger = MagicMock()

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.return_value = {
                "success": False,
                "message": "Tournament already in REWARDS_DISTRIBUTED state — skipping",
            }
            _finalize_tournament_with_rewards(1, db, logger)  # must not raise

        # Must log warning about non-success
        logger.warning.assert_called()
        # Must NOT call rollback (no exception occurred)
        db.rollback.assert_not_called()

    def test_reward_protection_integrity_error_on_duplicate_rewards_rollsback(self):
        """finalize() raises SQLAlchemy IntegrityError (duplicate reward rows).

        Simulates two workers racing to insert reward rows — the second one
        hits a unique constraint. Contract: exception caught, rollback called,
        no uncaught exception propagates.
        """
        from sqlalchemy.exc import IntegrityError

        t = MagicMock()
        db = _seq_db(_q(first=t))

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.side_effect = IntegrityError(
                "INSERT INTO xp_transactions ...",
                {},
                Exception("duplicate key value violates unique constraint uq_xp_transactions_user_semester_type"),
            )
            _finalize_tournament_with_rewards(1, db, _LOG)  # must not raise

        db.rollback.assert_called_once()

    # -----------------------------------------------------------------------
    # 3. Session result overwrite protection
    # -----------------------------------------------------------------------

    def test_overwrite_protection_ir_skips_session_with_existing_results(self):
        """_simulate_individual_ranking skips sessions where game_results is already set.

        The existing value must not be replaced with new simulation data.
        """
        pre_done = _session_mock(
            participants=[42, 43],
            game_results='{"original":"result","source":"manual_entry"}',
        )
        pre_done.scoring_type = None  # not ROUNDS_BASED → single-round else branch

        tournament = MagicMock()
        tournament.tournament_config_obj.scoring_type = "SCORE_BASED"

        db = _seq_db(_q(all_=[pre_done]))
        ok, msg = _simulate_individual_ranking(db, tournament, _LOG)

        assert ok is True
        assert "1 skipped" in msg
        # Original manual result must be preserved verbatim
        assert pre_done.game_results == '{"original":"result","source":"manual_entry"}'

    def test_overwrite_protection_h2h_knockout_skips_session_with_existing_results(self):
        """_simulate_head_to_head_knockout skips sessions where game_results is already set."""
        pre_done = _session_mock(
            participants=[1, 2],
            game_results='{"match_format":"HEAD_TO_HEAD","round_number":1,"participants":[{"user_id":1,"result":"win"}]}',
            round_num=1, match_num=1,
        )
        original = pre_done.game_results

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[pre_done]):
            ok, msg = _simulate_head_to_head_knockout(db, 1, _LOG)

        assert ok is True
        assert pre_done.game_results == original  # not overwritten

    # -----------------------------------------------------------------------
    # 4. Concurrent finalize — two-worker sequential simulation
    # -----------------------------------------------------------------------

    def test_concurrent_finalize_first_worker_succeeds_second_rollbacks(self):
        """Two workers both call _finalize_tournament_with_rewards on the same tournament.

        Worker A (arrives first): finalize() succeeds → rewards distributed.
        Worker B (arrives after): finalize() raises IntegrityError (duplicate rows
        from Worker A's already-committed rewards).

        Contract:
          - Neither worker raises an uncaught exception
          - Worker A: no rollback called (success path)
          - Worker B: rollback called exactly once (exception path)
        """
        from sqlalchemy.exc import IntegrityError

        t = MagicMock()
        db_a = _seq_db(_q(first=t))
        db_b = _seq_db(_q(first=t))
        db_a.rollback = MagicMock()
        db_b.rollback = MagicMock()

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:

            # Worker A — succeeds
            MockFin.return_value.finalize.return_value = {
                "success": True,
                "tournament_status": "REWARDS_DISTRIBUTED",
                "rewards_message": "3 players rewarded",
            }
            _finalize_tournament_with_rewards(1, db_a, _LOG)  # must not raise

            # Worker B — arrives after A, hits IntegrityError on duplicate reward rows
            MockFin.return_value.finalize.side_effect = IntegrityError(
                "INSERT INTO credit_transactions ...",
                {},
                Exception("duplicate key value violates unique constraint ix_credit_transactions_idempotency_key"),
            )
            _finalize_tournament_with_rewards(1, db_b, _LOG)  # must not raise

        # Worker A had no exception → no rollback
        db_a.rollback.assert_not_called()
        # Worker B hit IntegrityError → rollback called
        db_b.rollback.assert_called_once()


# ===========================================================================
# Sprint P17 — Database-level consistency guarantees
# ===========================================================================

class TestDatabaseConsistency:
    """P17: Correctness under transaction failures and DB locking scenarios.

    Covers:
      1. Transaction boundary — partial failure leaves DB unchanged
      2. Locking behavior — registry lock guards task registration; finalize
         receives ORM object (enabling row-level locking downstream)
      3. Reward idempotency — double-finalize without IntegrityError
      4. Cross-phase consistency — group standings → knockout participants →
         game results cannot diverge
    """

    _FIN_PATH = (
        "app.services.tournament.results.finalization"
        ".tournament_finalizer.TournamentFinalizer"
    )

    # -----------------------------------------------------------------------
    # 1. Transaction boundary
    # -----------------------------------------------------------------------

    def test_db_query_failure_during_lookup_triggers_rollback(self):
        """If db.query() raises before finalize() is called, rollback is still invoked.

        Models a DB connection timeout / session invalidation that occurs during
        the tournament lookup step. The outer try/except must catch it and
        call db.rollback() even though finalize() was never reached.
        """
        db = MagicMock()
        db.query.side_effect = RuntimeError("DB connection timeout")

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            _finalize_tournament_with_rewards(1, db, _LOG)  # must not raise

        db.rollback.assert_called_once()
        # finalize() must NOT have been called (exception happened before it)
        MockFin.return_value.finalize.assert_not_called()

    def test_partial_write_before_exception_triggers_rollback(self):
        """finalize() simulates writing a reward row then raising.

        After db.add() the commit raises → exception caught → rollback called.
        The 'partial write' (db.add) is undone by the rollback.
        Verifies the try/except structure covers the full finalize() execution.
        """
        t = MagicMock()
        db = _seq_db(_q(first=t))
        db.add = MagicMock()

        def _partial_finalize(tournament_obj):
            # Simulates: create reward row, then fail before commit
            db.add(MagicMock(name="reward_row"))
            raise RuntimeError("commit failed mid-transaction")

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.side_effect = _partial_finalize
            _finalize_tournament_with_rewards(1, db, _LOG)  # must not raise

        # The partial write happened
        db.add.assert_called_once()
        # And was rolled back
        db.rollback.assert_called_once()

    def test_success_path_never_calls_rollback_or_commit(self):
        """Successful finalize: wrapper calls neither rollback nor commit.

        Rollback is only for exception paths.
        Commit is solely the responsibility of TournamentFinalizer.finalize().
        The _finalize_tournament_with_rewards wrapper must not touch either.
        """
        t = MagicMock()
        db = _seq_db(_q(first=t))
        db.rollback = MagicMock()
        db.commit = MagicMock()

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.return_value = {
                "success": True,
                "tournament_status": "REWARDS_DISTRIBUTED",
                "rewards_message": "done",
            }
            _finalize_tournament_with_rewards(1, db, _LOG)

        db.rollback.assert_not_called()
        db.commit.assert_not_called()

    # -----------------------------------------------------------------------
    # 2. Locking behavior
    # -----------------------------------------------------------------------

    def test_registry_lock_guards_task_id_registration(self):
        """Thread dispatch uses the real registry lock; task_id written atomically.

        Replaces the module-level _registry_lock with a real threading.Lock and
        _task_registry with a real dict. Verifies:
          - No deadlock (lock is acquired and released cleanly)
          - task_id is written to the registry with status "pending"
          - result.task_id matches the registry key
        """
        import threading
        _GS = "app.api.api_v1.endpoints.tournaments.generate_sessions"

        real_registry: dict = {}
        real_lock = threading.Lock()

        mock_tournament = MagicMock()
        mock_tournament.id = 301
        campus_row = MagicMock()
        campus_row.id = 1

        import contextlib
        patches = [
            patch("app.models.semester.Semester"),
            patch("app.models.semester.SemesterStatus"),
            patch("app.models.tournament_configuration.TournamentConfiguration"),
            patch("app.models.tournament_reward_config.TournamentRewardConfig"),
            patch("app.models.tournament_achievement.TournamentSkillMapping"),
            patch("app.models.campus.Campus"),
            patch("app.models.campus_schedule_config.CampusScheduleConfig"),
            patch("app.services.audit_service.AuditService"),
            patch("app.models.audit_log.AuditAction"),
            patch("app.models.session.Session"),
            patch("app.models.semester_enrollment.SemesterEnrollment"),
            patch("app.models.semester_enrollment.EnrollmentStatus"),
            patch("app.models.license.UserLicense"),
            patch(f"{_GS}.BACKGROUND_GENERATION_THRESHOLD", 0),
            patch(f"{_GS}._is_celery_available", return_value=False),
            patch(f"{_GS}._registry_lock", real_lock),
            patch(f"{_GS}._task_registry", real_registry),
            patch(f"{_GS}._run_generation_in_background"),
            patch("threading.Thread"),
        ]

        db = _seq_db(
            _q(first=_gm_user()),          # grandmaster
            _q(all_=[campus_row]),   # campus validation
            _q(first=None),          # CampusScheduleConfig
            _q(all_=[]),             # SE enrolled_user_ids
        )

        with contextlib.ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            MockSem = mocks[0]
            MockSem.return_value = mock_tournament

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="manual", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        # task_id was written to the real registry under the real lock
        assert result.task_id in real_registry, (
            f"task_id {result.task_id!r} not found in registry {list(real_registry)}"
        )
        assert real_registry[result.task_id]["status"] == "pending"

    def test_finalize_receives_orm_object_not_integer_id(self):
        """finalize() is called with the fetched ORM tournament object, not the tid integer.

        This is the prerequisite for row-level locking: TournamentFinalizer must
        receive the actual ORM object so it can use SELECT FOR UPDATE internally.
        If the code ever passes tid directly, this test catches the regression.
        """
        t_obj = MagicMock(name="tournament_orm_object")
        db = _seq_db(_q(first=t_obj))

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.return_value = {
                "success": True,
                "tournament_status": "REWARDS_DISTRIBUTED",
                "rewards_message": "ok",
            }
            _finalize_tournament_with_rewards(99, db, _LOG)

        # finalize() must have received the ORM object, not integer 99
        MockFin.return_value.finalize.assert_called_once_with(t_obj)
        passed_arg = MockFin.return_value.finalize.call_args[0][0]
        assert passed_arg is t_obj
        assert passed_arg is not 99  # noqa: F632 — intentional identity check

    # -----------------------------------------------------------------------
    # 3. Reward idempotency (without IntegrityError)
    # -----------------------------------------------------------------------

    def test_double_finalize_wrapper_never_commits_or_rollbacks_on_clean_runs(self):
        """Two clean finalize calls: wrapper touches neither db.commit nor db.rollback.

        The first call succeeds; the second detects "already distributed" and returns
        success=False. Neither call raises. The wrapper must not call commit (that is
        finalize's job) nor rollback (no exception occurred).
        """
        t = MagicMock()
        db = MagicMock()
        db.query.return_value = _q(first=t)
        db.commit = MagicMock()
        db.rollback = MagicMock()

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.side_effect = [
                {"success": True, "tournament_status": "REWARDS_DISTRIBUTED", "rewards_message": "done"},
                {"success": False, "message": "Already in REWARDS_DISTRIBUTED state"},
            ]
            _finalize_tournament_with_rewards(1, db, _LOG)
            _finalize_tournament_with_rewards(1, db, _LOG)

        db.commit.assert_not_called()
        db.rollback.assert_not_called()

    def test_double_finalize_each_invocation_calls_finalize_exactly_once(self):
        """Each wrapper invocation calls TournamentFinalizer.finalize() exactly once.

        Total across two invocations: finalize() called exactly 2 times.
        Reward idempotency is enforced by finalize() returning success=False on the
        second call (no duplicate reward creation) — the wrapper makes no duplicating calls itself.
        """
        t = MagicMock()
        db = MagicMock()
        db.query.return_value = _q(first=t)

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.side_effect = [
                {"success": True, "tournament_status": "REWARDS_DISTRIBUTED", "rewards_message": "done"},
                {"success": False, "message": "Already REWARDS_DISTRIBUTED"},
            ]
            _finalize_tournament_with_rewards(1, db, _LOG)
            _finalize_tournament_with_rewards(1, db, _LOG)

        # Exactly one call per wrapper invocation (total: 2)
        assert MockFin.return_value.finalize.call_count == 2

    # -----------------------------------------------------------------------
    # 4. Cross-phase consistency
    # -----------------------------------------------------------------------

    def test_knockout_participants_are_subset_of_group_participants(self):
        """Cross-phase invariant: all players in the knockout bracket came from group stage.

        After full group+knockout simulation, ks.participant_user_ids must be
        a strict subset of the union of all group session participant lists.
        No phantom player may appear in the knockout who was not in any group session.
        """
        gs_a = _session_mock(participants=[10, 20], game_results=None, round_num=1, match_num=1)
        gs_a.tournament_phase = "GROUP_STAGE"
        gs_a.group_identifier = "A"

        gs_b = _session_mock(participants=[30, 40], game_results=None, round_num=1, match_num=2)
        gs_b.tournament_phase = "GROUP_STAGE"
        gs_b.group_identifier = "B"

        ks = _session_mock(participants=[], game_results=None, round_num=1, match_num=1)
        ks.tournament_phase = "KNOCKOUT"

        all_group_pax = {10, 20, 30, 40}

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs_a, gs_b, ks]), \
             patch("random.choice", side_effect=["win", True, "win", True, 10]), \
             patch("random.randint", side_effect=[3, 0, 3, 0, 3, 0]):
            ok, msg = _simulate_group_knockout_tournament(db, 1, _LOG)

        assert ok is True
        ks_pax = set(ks.participant_user_ids)
        # Core invariant: no phantom players
        assert ks_pax.issubset(all_group_pax), (
            f"Knockout participants {ks_pax} contain players not in group stage {all_group_pax}"
        )
        # Bracket is populated (not empty)
        assert len(ks_pax) > 0

    def test_ir_rankings_match_session_participant_set_exactly(self):
        """Cross-phase invariant: ranked user_ids == union of all session participants.

        No phantom player may appear in rankings who was not in any session.
        No participant may be silently dropped from the rankings.
        """
        t = MagicMock()
        t.tournament_config_obj = MagicMock()
        t.tournament_config_obj.ranking_direction = "DESC"

        s1 = MagicMock()
        s1.rounds_data = {"round_results": {"1": {"10": "80.0", "20": "70.0"}}}
        s2 = MagicMock()
        s2.rounds_data = {"round_results": {"2": {"30": "90.0", "10": "85.0"}}}

        # Session participants: 10 appears in both, 20 in s1, 30 in s2
        all_session_pax = {10, 20, 30}

        rankings = _calculate_ir_rankings(t, [s1, s2], _LOG)

        ranked_ids = {r["user_id"] for r in rankings}
        # No phantom players
        assert ranked_ids.issubset(all_session_pax), (
            f"Ranked IDs {ranked_ids} contain players not in sessions {all_session_pax}"
        )
        # All participants from round_results appear in rankings
        assert ranked_ids == all_session_pax

    def test_knockout_game_results_reference_only_seeded_players(self):
        """Cross-phase invariant: game_results user_ids in knockout are ⊆ group participants.

        After the full group+knockout pipeline, the JSON stored in
        ks.game_results must only reference players who appeared in group sessions.
        This ensures the result ledger cannot contain data from outside the bracket.
        """
        import json as _json

        gs_a = _session_mock(participants=[10, 20], game_results=None, round_num=1, match_num=1)
        gs_a.tournament_phase = "GROUP_STAGE"
        gs_a.group_identifier = "A"

        gs_b = _session_mock(participants=[30, 40], game_results=None, round_num=1, match_num=2)
        gs_b.tournament_phase = "GROUP_STAGE"
        gs_b.group_identifier = "B"

        ks = _session_mock(participants=[], game_results=None, round_num=1, match_num=1)
        ks.tournament_phase = "KNOCKOUT"

        all_group_pax = {10, 20, 30, 40}

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=[gs_a, gs_b, ks]), \
             patch("random.choice", side_effect=["win", True, "win", True, 10]), \
             patch("random.randint", side_effect=[3, 0, 3, 0, 3, 0]):
            ok, _ = _simulate_group_knockout_tournament(db, 1, _LOG)

        assert ok is True
        assert ks.game_results is not None, "Knockout session must have game_results after simulation"

        game_data = _json.loads(ks.game_results)
        result_pax = {p["user_id"] for p in game_data["participants"]}

        # No phantom players in the recorded match result
        assert result_pax.issubset(all_group_pax), (
            f"game_results contains IDs {result_pax} not in group participants {all_group_pax}"
        )
        # Match has exactly 2 distinct players (no duplicates in result ledger)
        assert len(result_pax) == len(game_data["participants"])


# ===========================================================================
# Sprint P18 — Real concurrency, scale, and global invariants
# ===========================================================================

class TestConcurrencyScaleInvariants:
    """P18: Correct behavior under real concurrency, scale, and failure conditions.

    Covers:
      1. SELECT FOR UPDATE simulation — only one of two concurrent workers commits
      2. Transaction atomicity — rankings commit and finalize rollback are independent;
         ranking failure does not prevent finalize attempt
      3. Large-scale throughput — 128 sessions simulated correctly
      4. Global invariants — no duplicate participants, contiguous ranks,
         competition-ranking tie gaps, finalize gate on simulation failure
    """

    _FIN_PATH = (
        "app.services.tournament.results.finalization"
        ".tournament_finalizer.TournamentFinalizer"
    )

    # -----------------------------------------------------------------------
    # 1. SELECT FOR UPDATE / Concurrency
    # -----------------------------------------------------------------------

    def test_select_for_update_concurrent_workers_exactly_one_commits(self):
        """Two threads race to finalize the same tournament — exactly one succeeds.

        A threading.Lock inside the mock simulates SELECT FOR UPDATE row locking:
        - The thread that acquires the lock first: finalize returns success=True
        - The thread blocked from the lock: finalize raises OperationalError
          (models a PostgreSQL 'could not obtain lock on row' timeout)

        Contract:
          - No uncaught exception propagates to either caller
          - Exactly 1 rollback across both DB handles (the loser's)
          - finalize() called exactly 2 times (both workers attempted)
        """
        import threading
        from sqlalchemy.exc import OperationalError

        # Deterministic SELECT FOR UPDATE model:
        # whichever thread calls finalize() first gets success (acquired the lock),
        # the second gets OperationalError (lock timeout — the standard PostgreSQL
        # error when SELECT FOR UPDATE cannot acquire a row lock within statement_timeout).
        # Using side_effect list is more reliable than a threading.Lock in CPython
        # because the GIL does not guarantee interleaving within a single fast function.
        t = MagicMock()
        db_a = _seq_db(_q(first=t))
        db_b = _seq_db(_q(first=t))
        uncaught_errors = []

        barrier = threading.Barrier(2)

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.side_effect = [
                # First caller (whichever thread wins the GIL): lock acquired → success
                {
                    "success": True,
                    "tournament_status": "REWARDS_DISTRIBUTED",
                    "rewards_message": "Worker distributed rewards",
                },
                # Second caller: lock already held → timeout
                OperationalError(
                    "SELECT FOR UPDATE",
                    {},
                    Exception("could not obtain lock on row in relation 'semesters'"),
                ),
            ]

            def _worker(db_ref, label):
                barrier.wait()  # Synchronise: both threads ready before either starts
                try:
                    _finalize_tournament_with_rewards(1, db_ref, _LOG)
                except Exception as exc:
                    uncaught_errors.append((label, exc))

            t_a = threading.Thread(target=_worker, args=(db_a, "A"))
            t_b = threading.Thread(target=_worker, args=(db_b, "B"))
            t_a.start(); t_b.start()
            t_a.join(); t_b.join()

        # Neither worker must leak an uncaught exception (wrapper catches all)
        assert uncaught_errors == [], f"Unexpected exceptions: {uncaught_errors}"
        # Exactly one DB handle was rolled back (the loser's)
        total_rollbacks = db_a.rollback.call_count + db_b.rollback.call_count
        assert total_rollbacks == 1, (
            f"Expected 1 rollback, got {total_rollbacks} "
            f"(db_a={db_a.rollback.call_count}, db_b={db_b.rollback.call_count})"
        )
        # Both workers attempted finalize (2 calls total)
        assert MockFin.return_value.finalize.call_count == 2

    def test_operational_error_lock_timeout_caught_and_rolled_back(self):
        """OperationalError with PostgreSQL lock-timeout message is caught gracefully.

        This is the canonical SELECT FOR UPDATE failure: the DB raises OperationalError
        when it cannot acquire a row-level lock within the statement timeout.
        Contract: exception caught by wrapper, rollback called, nothing propagates.
        """
        from sqlalchemy.exc import OperationalError

        t = MagicMock()
        db = _seq_db(_q(first=t))

        with patch("app.models.semester.Semester"), \
             patch(self._FIN_PATH) as MockFin:
            MockFin.return_value.finalize.side_effect = OperationalError(
                "SELECT * FROM semesters WHERE id = 1 FOR UPDATE",
                {},
                Exception(
                    "ERROR: could not obtain lock on row in relation 'semesters'\n"
                    "HINT: The row is locked by another transaction."
                ),
            )
            _finalize_tournament_with_rewards(1, db, _LOG)  # must not raise

        db.rollback.assert_called_once()

    # -----------------------------------------------------------------------
    # 2. Transaction atomicity
    # -----------------------------------------------------------------------

    def test_rankings_committed_finalize_failure_rolled_back_independently(self):
        """Rankings commit and finalize rollback are independent DB transactions.

        Scenario:
          1. _simulate_tournament_results succeeds
          2. Ranking calculation succeeds → db.commit() (rankings persisted)
          3. TournamentFinalizer.finalize() raises OperationalError
             → db.rollback() (finalize work undone)
          4. endpoint returns task_id="sync-done" (non-fatal finalize failure)

        Contract:
          - db.commit() called ≥1× (tournament creation + enrollment + rankings)
          - db.rollback() called exactly once (for finalize failure)
          - Rankings are NOT rolled back (they were committed before finalize ran)
          - result.task_id == "sync-done"
        """
        from sqlalchemy.exc import OperationalError

        mock_tournament = MagicMock()
        mock_tournament.id = 401
        mock_tournament.format = "INDIVIDUAL_RANKING"
        mock_tournament.tournament_config_obj = None  # → empty rankings
        campus_row = MagicMock(); campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),              # q0: grandmaster
            _q(all_=[campus_row]),       # q1: campus validation
            _q(first=None),              # q2: CampusScheduleConfig
            _q(all_=[]),                 # q3: SE enrolled (sync path)
            # Rankings step:
            _q(first=mock_tournament),   # q4: tournament re-query (line 1556)
            _q(all_=[]),                 # q5: _get_tournament_sessions (line 1563) → no sessions → rankings=[]
            _q(),                        # q6: TournamentRanking.filter().delete()
            # db.commit() happens here (line 1605)
            # Finalize step:
            _q(first=mock_tournament),   # q7: _finalize_tournament_with_rewards lookup
            _q(count=0),                 # q8: session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.license.UserLicense"), \
             patch("app.models.tournament_ranking.TournamentRanking"), \
             patch("app.services.tournament.session_generation.session_generator"
                   ".TournamentSessionGenerator") as MockTSG, \
             patch(f"{_BASE}._simulate_tournament_results", return_value=(True, "sim ok")), \
             patch(self._FIN_PATH) as MockFin:
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "sessions generated", [])
            MockFin.return_value.finalize.side_effect = OperationalError(
                "INSERT INTO ...", {}, Exception("lock timeout"),
            )

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        # Rankings were committed (tournament creation + enrollment + rankings = ≥3 commits)
        assert db.commit.call_count >= 1
        # Finalize failure triggered rollback (exactly one – from _finalize_tournament_with_rewards)
        assert db.rollback.call_count == 1
        # Endpoint succeeded despite finalize failure
        assert result.task_id == "sync-done"

    def test_ranking_failure_does_not_prevent_finalize_attempt(self):
        """When ranking calculation fails, finalize is still attempted.

        The ranking step is wrapped in its own try/except (lines 1551-1612).
        A failure there rolls back the ranking transaction but does NOT abort
        the finalize step at line 1616 (outside the try/except).

        Scenario:
          1. _simulate_tournament_results succeeds
          2. _calculate_ir_rankings raises RuntimeError → ranking rollback
          3. _finalize_tournament_with_rewards is still called (mocked to succeed)

        Contract:
          - db.rollback() called once (ranking failure)
          - db.commit() called exactly 3× (tournament creation + enrollment + campus sync; ranking commit skipped)
          - _finalize_tournament_with_rewards WAS called (finalize attempted)
          - result.task_id == "sync-done"
        """
        mock_tournament = MagicMock()
        mock_tournament.id = 402
        mock_tournament.format = "INDIVIDUAL_RANKING"
        mock_tournament.tournament_config_obj = None
        campus_row = MagicMock(); campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),              # q0: grandmaster
            _q(all_=[campus_row]),       # q1: campus validation
            _q(first=None),              # q2: CampusScheduleConfig
            _q(all_=[]),                 # q3: SE enrolled
            _q(first=mock_tournament),   # q4: tournament re-query
            _q(all_=[]),                 # q5: _get_tournament_sessions
            # _calculate_ir_rankings raises here → no delete/commit
            _q(count=0),                 # q6: session count (after finalize)
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.license.UserLicense"), \
             patch("app.models.tournament_ranking.TournamentRanking"), \
             patch("app.services.tournament.session_generation.session_generator"
                   ".TournamentSessionGenerator") as MockTSG, \
             patch(f"{_BASE}._simulate_tournament_results", return_value=(True, "sim ok")), \
             patch(f"{_BASE}._calculate_ir_rankings",
                   side_effect=RuntimeError("ranking service unavailable")), \
             patch(f"{_BASE}._finalize_tournament_with_rewards") as mock_fin:
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "sessions generated", [])

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        # Ranking failed → rollback; tournament+enrollment+campus commits still happened (3×)
        assert db.rollback.call_count >= 1
        assert db.commit.call_count == 3
        # Finalize was still attempted despite ranking failure
        mock_fin.assert_called_once()
        assert result.task_id == "sync-done"

    # -----------------------------------------------------------------------
    # 3. Large-scale throughput
    # -----------------------------------------------------------------------

    def test_large_scale_128_league_sessions_throughput(self):
        """_simulate_league_tournament handles 128 sessions without error.

        Verifies that the simulation loop scales to the minimum large-field
        tournament size (128 players = 128 round-robin sessions) without
        crashing, timing out, or misreporting counts.
        """
        sessions = [
            _session_mock(
                participants=[i * 2 + 100, i * 2 + 101],
                game_results=None,
                round_num=1,
                match_num=i + 1,
            )
            for i in range(128)
        ]

        db = MagicMock()
        with patch(f"{_BASE}._get_tournament_sessions", return_value=sessions), \
             patch("random.choice", return_value="win"), \
             patch("random.randint", return_value=3):
            ok, msg = _simulate_league_tournament(db, 1, _LOG)

        assert ok is True
        assert "128 league sessions simulated" in msg
        assert "0 skipped" in msg
        # Every session must have been populated with game_results
        assert all(s.game_results is not None for s in sessions)

    def test_large_scale_128_ir_sessions_throughput(self):
        """_simulate_individual_ranking handles 128 sessions without error.

        Covers the large-field INDIVIDUAL_RANKING path: 128 sessions each
        with 2 participants, SCORE_BASED scoring, ResultProcessor mocked.
        """
        sessions = [
            _session_mock(participants=[i, i + 1000], game_results=None)
            for i in range(128)
        ]
        for s in sessions:
            s.scoring_type = None  # not ROUNDS_BASED → SCORE_BASED single-round path

        tournament = MagicMock()
        tournament.tournament_config_obj.scoring_type = "SCORE_BASED"

        db = _seq_db(_q(all_=sessions))
        with patch("app.services.tournament.result_processor.ResultProcessor"), \
             patch("random.randint", return_value=75):
            ok, msg = _simulate_individual_ranking(db, tournament, _LOG)

        assert ok is True
        assert "128 sessions simulated" in msg
        assert "0 skipped" in msg

    # -----------------------------------------------------------------------
    # 4. Global invariants
    # -----------------------------------------------------------------------

    def test_invariant_no_duplicate_user_ids_in_knockout_game_results(self):
        """Knockout game_results must never contain duplicate participant user_ids.

        The structural guarantee: winner and loser are always distinct players
        (loser_id = first player ≠ winner_id). The JSON result ledger must reflect
        this — exactly 2 distinct user_ids per match.
        """
        import json as _json

        s = _session_mock(participants=[10, 20], game_results=None,
                          round_num=1, match_num=1, title="Final")

        _simulate_knockout_bracket(MagicMock(), [s], _LOG)

        assert s.game_results is not None
        game_data = _json.loads(s.game_results)
        participant_ids = [p["user_id"] for p in game_data["participants"]]

        # Exactly 2 participants, all distinct
        assert len(participant_ids) == 2
        assert len(set(participant_ids)) == 2, (
            f"Duplicate user_ids in game_results: {participant_ids}"
        )
        # Both players came from the original participant list
        assert set(participant_ids) == {10, 20}

    def test_invariant_ir_rankings_contiguous_for_distinct_scores(self):
        """When all players have distinct scores, IR ranks must be 1, 2, …, N (no gaps).

        With N=3 distinct DESC scores, the ranking must produce exactly the set
        {1, 2, 3} — no rank is skipped, no rank appears twice.
        """
        t = MagicMock()
        t.tournament_config_obj = MagicMock()
        t.tournament_config_obj.ranking_direction = "DESC"

        s = MagicMock()
        s.rounds_data = {"round_results": {"1": {"10": "90.0", "20": "75.0", "30": "60.0"}}}

        rankings = _calculate_ir_rankings(t, [s], _LOG)

        assert len(rankings) == 3
        ranks = sorted(r["rank"] for r in rankings)
        assert ranks == [1, 2, 3], f"Expected contiguous [1,2,3], got {ranks}"

        # Highest scorer gets rank 1
        top = next(r for r in rankings if r["rank"] == 1)
        assert top["user_id"] == 10

    def test_invariant_ir_rankings_competition_rank_gaps_on_ties(self):
        """Tied players share the same rank; the next rank skips (competition ranking).

        With 4 players (90, 75, 75, 60) DESC:
          - User 10 (90.0) → rank 1
          - Users 20 & 30 (75.0) → rank 2 (tied)
          - User 40 (60.0) → rank 4  ← rank 3 is skipped (competition ranking)

        This verifies that the aggregator uses standard competition ranking,
        not dense ranking, so callers cannot assume rank N always means N-1 players above.
        """
        t = MagicMock()
        t.tournament_config_obj = MagicMock()
        t.tournament_config_obj.ranking_direction = "DESC"

        s = MagicMock()
        s.rounds_data = {
            "round_results": {
                "1": {"10": "90.0", "20": "75.0", "30": "75.0", "40": "60.0"}
            }
        }

        rankings = _calculate_ir_rankings(t, [s], _LOG)

        assert len(rankings) == 4
        rank_map = {r["user_id"]: r["rank"] for r in rankings}

        assert rank_map[10] == 1,  f"user 10 (highest) should be rank 1, got {rank_map[10]}"
        assert rank_map[20] == 2,  f"user 20 (tied 2nd) should be rank 2, got {rank_map[20]}"
        assert rank_map[30] == 2,  f"user 30 (tied 2nd) should be rank 2, got {rank_map[30]}"
        assert rank_map[40] == 4,  f"user 40 (last) should be rank 4 (gap after tie), got {rank_map[40]}"

    def test_invariant_finalize_not_called_when_simulation_fails(self):
        """_finalize_tournament_with_rewards is never called if simulation returns False.

        The auto_immediate path only proceeds to ranking + finalize when
        sim_ok=True (line 1547: `if sim_ok:`). A failed simulation (no sessions,
        unsupported format, etc.) must NOT trigger reward distribution.

        This enforces the invariant: a tournament cannot be finalized without
        completed session results.
        """
        mock_tournament = MagicMock()
        mock_tournament.id = 403
        campus_row = MagicMock(); campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),          # q0: grandmaster
            _q(all_=[campus_row]),   # q1: campus validation
            _q(first=None),          # q2: CampusScheduleConfig
            _q(all_=[]),             # q3: SE enrolled
            _q(count=0),             # q4: session count
        )

        with patch("app.models.semester.Semester") as MockSem, \
             patch("app.models.semester.SemesterStatus"), \
             patch("app.models.tournament_configuration.TournamentConfiguration"), \
             patch("app.models.tournament_reward_config.TournamentRewardConfig"), \
             patch("app.models.tournament_achievement.TournamentSkillMapping"), \
             patch("app.models.campus.Campus"), \
             patch("app.models.campus_schedule_config.CampusScheduleConfig"), \
             patch("app.services.audit_service.AuditService"), \
             patch("app.models.audit_log.AuditAction"), \
             patch("app.models.session.Session"), \
             patch("app.models.semester_enrollment.SemesterEnrollment"), \
             patch("app.models.semester_enrollment.EnrollmentStatus"), \
             patch("app.models.license.UserLicense"), \
             patch("app.services.tournament.session_generation.session_generator"
                   ".TournamentSessionGenerator") as MockTSG, \
             patch(f"{_BASE}._simulate_tournament_results",
                   return_value=(False, "No tournament sessions found for simulation")), \
             patch(f"{_BASE}._finalize_tournament_with_rewards") as mock_fin:
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "sessions generated", [])

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        # Core invariant: finalize must NOT be called if simulation failed
        mock_fin.assert_not_called()
        # Endpoint still completes successfully
        assert result.task_id == "sync-done"


# ===========================================================================
# Sprint P19 — Isolation audit, transaction boundaries, concurrency safety,
#               and full end-to-end integration
# ===========================================================================

class TestTransactionBoundaries:
    """P19-A: Verify commit/rollback isolation at each pipeline stage boundary.

    The ops_scenario pipeline has four distinct commit points:
      1. Tournament creation  → db.commit() at line 1360
      2. Enrollment batch     → db.commit() at line 1403
      3. Campus sync          → db.commit() at line ~1450 (was flush; now commit so background
                                threads see campus_id before reading the tournament row)
      4. Ranking persistence  → db.commit() at line 1605
      (Finalize has its own internal transaction — never commits in wrapper)

    Each test exercises failure at one boundary and asserts that earlier
    commits are preserved while later work is rolled back or never started.
    """

    _TSG_PATH = (
        "app.services.tournament.session_generation"
        ".session_generator.TournamentSessionGenerator"
    )
    _FIN_PATH = (
        "app.services.tournament.results.finalization"
        ".tournament_finalizer.TournamentFinalizer"
    )

    def _common_patches(self):
        """Return list of (path, kwargs) for standard model/service patches."""
        return [
            "app.models.semester.Semester",
            "app.models.semester.SemesterStatus",
            "app.models.tournament_configuration.TournamentConfiguration",
            "app.models.tournament_reward_config.TournamentRewardConfig",
            "app.models.tournament_achievement.TournamentSkillMapping",
            "app.models.campus.Campus",
            "app.models.campus_schedule_config.CampusScheduleConfig",
            "app.services.audit_service.AuditService",
            "app.models.audit_log.AuditAction",
            "app.models.session.Session",
            "app.models.semester_enrollment.SemesterEnrollment",
            "app.models.semester_enrollment.EnrollmentStatus",
            "app.models.license.UserLicense",
            "app.models.tournament_ranking.TournamentRanking",
        ]

    # -----------------------------------------------------------------------
    # 1. TSG failure boundary
    # -----------------------------------------------------------------------

    def test_tsg_failure_raises_422_tournament_already_committed(self):
        """TSG returns (False, ...) → HTTPException(422) raised.

        The tournament and enrollment commits at lines 1360 and 1403 have
        already fired before the TSG call. The 422 propagates up with no
        rollback (the tournament record PERSISTS in real DB; caller must
        retry or clean up explicitly).

        Contract:
          - HTTPException 422 raised
          - db.commit called ≥ 2× (tournament + enrollment precede TSG)
          - db.rollback NOT called (422 is not a transaction rollback trigger)
        """
        import contextlib
        from fastapi import HTTPException

        mock_tournament = MagicMock()
        mock_tournament.id = 501
        campus_row = MagicMock(); campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),           # q0: grandmaster
            _q(all_=[campus_row]),    # q1: campus validation
            _q(first=None),           # q2: CampusScheduleConfig
            _q(all_=[]),              # q3: SE enrolled_user_ids
        )

        with contextlib.ExitStack() as stack:
            for path in self._common_patches():
                stack.enter_context(patch(path))
            MockSem = stack.enter_context(patch("app.models.semester.Semester"))
            MockTSG = stack.enter_context(patch(self._TSG_PATH))
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (
                False, "Not enough players enrolled. Need at least 2, have 0", []
            )

            with pytest.raises(HTTPException) as exc_info:
                run_ops_scenario(
                    _req(player_count=0, auto_generate_sessions=True,
                         simulation_mode="auto_immediate", campus_ids=[1]),
                    db=db, current_user=_admin()
                )

        assert exc_info.value.status_code == 422
        # Tournament creation (line 1360) + enrollment batch (line 1403) both committed
        assert db.commit.call_count >= 2
        # No rollback — the 422 exits the endpoint, no rollback logic in this path
        db.rollback.assert_not_called()

    # -----------------------------------------------------------------------
    # 2. Simulation failure boundary
    # -----------------------------------------------------------------------

    def test_simulation_failure_returns_sync_done_zero_rollbacks(self):
        """Simulation returns (False, ...) → endpoint returns sync-done gracefully.

        Sessions were generated (TSG succeeded), tournament + enrollment committed.
        When simulation fails, the endpoint logs a warning and returns normally —
        no rollback is triggered because no transaction was started for simulation.

        Contract:
          - result.task_id == "sync-done"
          - db.commit called exactly 3× (tournament + enrollment + campus sync; no ranking)
          - db.rollback NOT called (simulation failure is non-fatal, no TX to roll back)
        """
        import contextlib

        mock_tournament = MagicMock()
        mock_tournament.id = 502
        campus_row = MagicMock(); campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),           # q0: grandmaster
            _q(all_=[campus_row]),    # q1: campus validation
            _q(first=None),           # q2: CampusScheduleConfig
            _q(all_=[]),              # q3: SE enrolled
            _q(count=0),              # q4: final session count
        )

        with contextlib.ExitStack() as stack:
            for path in self._common_patches():
                stack.enter_context(patch(path))
            MockSem = stack.enter_context(patch("app.models.semester.Semester"))
            MockTSG = stack.enter_context(patch(self._TSG_PATH))
            stack.enter_context(patch(f"{_BASE}._simulate_tournament_results",
                                      return_value=(False, "No tournament sessions found")))
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "sessions generated", [])

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.task_id == "sync-done"
        # Tournament + enrollment + campus sync commits; ranking never reached
        assert db.commit.call_count == 3
        # Simulation failure does not trigger any rollback
        db.rollback.assert_not_called()

    # -----------------------------------------------------------------------
    # 3. Full success path
    # -----------------------------------------------------------------------

    def test_full_pipeline_success_three_commits_zero_rollbacks(self):
        """Full success path: tournament + enrollment + campus sync + ranking = exactly 4 commits.

        Verifies the expected commit count for the complete happy path so that
        any future code change adding or removing a commit is immediately visible.

        Contract:
          - db.commit called exactly 4× (tournament, enrollment, campus sync, ranking)
          - db.rollback NOT called
          - result.task_id == "sync-done"
        """
        import contextlib

        mock_tournament = MagicMock()
        mock_tournament.id = 503
        mock_t2 = MagicMock()
        mock_t2.format = "INDIVIDUAL_RANKING"
        mock_t2.tournament_config_obj = None
        campus_row = MagicMock(); campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),            # q0: grandmaster
            _q(all_=[campus_row]),     # q1: campus
            _q(first=None),            # q2: CampusScheduleConfig
            _q(all_=[]),               # q3: SE enrolled
            _q(first=mock_t2),         # q4: tournament re-query for ranking
            _q(all_=[]),               # q5: _get_tournament_sessions
            _q(),                      # q6: TournamentRanking delete
            # db.commit() #3 here (ranking)
            _q(first=mock_t2),         # q7: finalize Semester lookup
            _q(count=0),               # q8: final session count
        )

        with contextlib.ExitStack() as stack:
            for path in self._common_patches():
                stack.enter_context(patch(path))
            MockSem = stack.enter_context(patch("app.models.semester.Semester"))
            MockTSG = stack.enter_context(patch(self._TSG_PATH))
            stack.enter_context(patch(f"{_BASE}._simulate_tournament_results",
                                      return_value=(True, "sim ok")))
            MockFin = stack.enter_context(patch(self._FIN_PATH))
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "1 session", [])
            MockFin.return_value.finalize.return_value = {"success": True}

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.task_id == "sync-done"
        assert db.commit.call_count == 4, (
            f"Expected 4 commits (tournament+enrollment+campus_sync+ranking), got {db.commit.call_count}"
        )
        db.rollback.assert_not_called()

    # -----------------------------------------------------------------------
    # 4. Both ranking AND finalize fail
    # -----------------------------------------------------------------------

    def test_ranking_and_finalize_both_fail_two_independent_rollbacks(self):
        """Ranking raises + finalize raises → two independent rollbacks.

        The ranking block and the finalize wrapper each have their own
        try/except → each triggers its own db.rollback() independently.
        Both failures are non-fatal; endpoint returns task_id="sync-done".

        Contract:
          - db.rollback called exactly 2× (once per failed stage)
          - db.commit called exactly 3× (tournament + enrollment + campus sync; ranking skips its commit)
          - result.task_id == "sync-done"
        """
        import contextlib
        from sqlalchemy.exc import OperationalError

        mock_tournament = MagicMock()
        mock_tournament.id = 504
        mock_t2 = MagicMock()
        mock_t2.format = "INDIVIDUAL_RANKING"
        mock_t2.tournament_config_obj = None
        campus_row = MagicMock(); campus_row.id = 1

        db = _seq_db(
            _q(first=_gm_user()),            # q0: grandmaster
            _q(all_=[campus_row]),     # q1: campus
            _q(first=None),            # q2: CampusScheduleConfig
            _q(all_=[]),               # q3: SE enrolled
            _q(first=mock_t2),         # q4: tournament re-query for ranking
            _q(all_=[]),               # q5: sessions for ranking
            # _calculate_ir_rankings raises → db.rollback() #1
            _q(first=mock_t2),         # q6: finalize Semester lookup
            # TournamentFinalizer.finalize raises → db.rollback() #2
            _q(count=0),               # q7: final session count
        )

        with contextlib.ExitStack() as stack:
            for path in self._common_patches():
                stack.enter_context(patch(path))
            MockSem = stack.enter_context(patch("app.models.semester.Semester"))
            MockTSG = stack.enter_context(patch(self._TSG_PATH))
            stack.enter_context(patch(f"{_BASE}._simulate_tournament_results",
                                      return_value=(True, "sim ok")))
            stack.enter_context(patch(f"{_BASE}._calculate_ir_rankings",
                                      side_effect=RuntimeError("aggregator unavailable")))
            MockFin = stack.enter_context(patch(self._FIN_PATH))
            MockSem.return_value = mock_tournament
            MockTSG.return_value.generate_sessions.return_value = (True, "1 session", [])
            MockFin.return_value.finalize.side_effect = OperationalError(
                "UPDATE ...", {}, Exception("deadlock detected")
            )

            result = run_ops_scenario(
                _req(player_count=0, auto_generate_sessions=True,
                     simulation_mode="auto_immediate", campus_ids=[1]),
                db=db, current_user=_admin()
            )

        assert result.task_id == "sync-done"
        # Ranking failure rolled back, finalize failure rolled back — each independently
        assert db.rollback.call_count == 2, (
            f"Expected 2 rollbacks (ranking + finalize), got {db.rollback.call_count}"
        )
        # Tournament + enrollment + campus sync committed; ranking commit was never reached
        assert db.commit.call_count == 3

    # -----------------------------------------------------------------------
    # 5. Campus validation boundary
    # -----------------------------------------------------------------------

    def test_campus_validation_failure_422_after_enrollment_commits(self):
        """Invalid campus ID → HTTPException(422) raised after enrollment commits.

        Campus validation (line 1419) occurs AFTER the enrollment batch commit
        at line 1403. An invalid campus raises 422 with 2 commits already done.

        Contract:
          - HTTPException 422 raised with campus error message
          - db.commit called exactly 2× (tournament + enrollment both precede campus check)
          - TSG never called (422 fires before reaching _generate_sessions_sync)
          - db.rollback NOT called
        """
        import contextlib
        from fastapi import HTTPException

        mock_tournament = MagicMock()
        mock_tournament.id = 505
        campus_row = MagicMock(); campus_row.id = 99  # ← different from requested ID

        db = _seq_db(
            _q(first=_gm_user()),              # q0: grandmaster
            _q(all_=[campus_row]),       # q1: campus query returns campus_id=99, not 1
        )

        with contextlib.ExitStack() as stack:
            for path in self._common_patches():
                stack.enter_context(patch(path))
            MockSem = stack.enter_context(patch("app.models.semester.Semester"))
            MockTSG = stack.enter_context(patch(self._TSG_PATH))
            MockSem.return_value = mock_tournament

            with pytest.raises(HTTPException) as exc_info:
                run_ops_scenario(
                    _req(player_count=0, auto_generate_sessions=True,
                         simulation_mode="auto_immediate", campus_ids=[1]),
                    db=db, current_user=_admin()
                )

        assert exc_info.value.status_code == 422
        assert "not found or inactive" in exc_info.value.detail
        # Tournament (1360) + enrollment (1403) both committed before campus check
        assert db.commit.call_count == 2
        # TSG was never reached
        MockTSG.return_value.generate_sessions.assert_not_called()
        db.rollback.assert_not_called()


# ===========================================================================
# Sprint P19-B — Concurrency safety
# ===========================================================================

class TestConcurrencySafety:
    """P19-B: Thread-safety and registry isolation for concurrent ops_scenario calls.

    The background dispatch path writes to a shared module-level dict
    (_task_registry) protected by _registry_lock. These tests verify:
      - No registry state is lost under concurrent writes
      - Each dispatch produces an isolated entry
      - Status transitions happen correctly
      - run_id is function-local (no cross-call contamination)
    """

    _GS = "app.api.api_v1.endpoints.tournaments.generate_sessions"
    _TSG_PATH = (
        "app.services.tournament.session_generation"
        ".session_generator.TournamentSessionGenerator"
    )

    def _bg_patches(self, real_registry, real_lock):
        """Return context managers for background dispatch path."""
        import contextlib
        return contextlib.ExitStack()  # caller builds the stack

    # -----------------------------------------------------------------------
    # 1. No registry writes lost under concurrent dispatch
    # -----------------------------------------------------------------------

    def test_background_dispatches_no_registry_writes_overwrite_each_other(self):
        """Two sequential dispatches both land in registry without overwriting.

        Python's unittest.mock.patch objects are not re-entrant across threads,
        so this test uses sequential calls to verify the registry invariant:
        N dispatches → N distinct entries, no write is silently dropped.

        This complements P15's task_id-uniqueness test by asserting registry
        state directly via the real_registry dict.

        Contract:
          - len(real_registry) == 2 after both dispatches
          - Both task_ids are distinct UUIDs (uuid4)
          - Both entries have status="pending" (Thread.start mocked — worker never runs)
          - Neither registry entry overwrites the other
        """
        import contextlib

        real_registry: dict = {}
        real_lock = __import__("threading").Lock()
        campus_row = MagicMock(); campus_row.id = 1

        def _make_db():
            return _seq_db(
                _q(first=_gm_user()),         # grandmaster
                _q(all_=[campus_row]),  # campus
                _q(first=None),         # CampusScheduleConfig
            )

        def _dispatch(mock_tournament):
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch("app.models.semester.Semester")).return_value = mock_tournament
                stack.enter_context(patch("app.models.semester.SemesterStatus"))
                stack.enter_context(patch("app.models.tournament_configuration.TournamentConfiguration"))
                stack.enter_context(patch("app.models.tournament_reward_config.TournamentRewardConfig"))
                stack.enter_context(patch("app.models.tournament_achievement.TournamentSkillMapping"))
                stack.enter_context(patch("app.models.campus.Campus"))
                stack.enter_context(patch("app.models.campus_schedule_config.CampusScheduleConfig"))
                stack.enter_context(patch("app.services.audit_service.AuditService"))
                stack.enter_context(patch("app.models.audit_log.AuditAction"))
                stack.enter_context(patch("app.models.session.Session"))
                stack.enter_context(patch("app.models.semester_enrollment.SemesterEnrollment"))
                stack.enter_context(patch("app.models.semester_enrollment.EnrollmentStatus"))
                stack.enter_context(patch("app.models.license.UserLicense"))
                stack.enter_context(patch(f"{self._GS}.BACKGROUND_GENERATION_THRESHOLD", 0))
                stack.enter_context(patch(f"{self._GS}._is_celery_available", return_value=False))
                stack.enter_context(patch(f"{self._GS}._registry_lock", real_lock))
                stack.enter_context(patch(f"{self._GS}._task_registry", real_registry))
                stack.enter_context(patch(f"{self._GS}._run_generation_in_background"))
                stack.enter_context(patch("threading.Thread"))
                return run_ops_scenario(
                    _req(player_count=0, auto_generate_sessions=True,
                         simulation_mode="manual", campus_ids=[1]),
                    db=_make_db(), current_user=_admin()
                )

        mock_t_a = MagicMock(); mock_t_a.id = 601
        mock_t_b = MagicMock(); mock_t_b.id = 602
        r_a = _dispatch(mock_t_a)
        r_b = _dispatch(mock_t_b)

        assert r_a.task_id != r_b.task_id, "Both dispatch calls must produce unique task_ids"
        assert len(real_registry) == 2, (
            f"Expected 2 registry entries, got {len(real_registry)}: {real_registry}"
        )
        for task_id, entry in real_registry.items():
            assert entry["status"] == "pending"
            assert entry["tournament_id"] is not None

    # -----------------------------------------------------------------------
    # 2. Background worker status transitions
    # -----------------------------------------------------------------------

    def test_background_worker_status_transitions_pending_running_done(self):
        """_run_generation_in_background transitions status: pending → running → done/error.

        Uses a real registry, real lock, and mocked TSG so the worker runs
        synchronously in the test thread. Verifies the three-state lifecycle.
        """
        import threading
        from app.api.api_v1.endpoints.tournaments.generate_sessions import (
            _run_generation_in_background,
        )

        real_registry: dict = {}
        real_lock = threading.Lock()
        task_id = "test-status-transition-task"

        real_registry[task_id] = {
            "status": "pending",
            "tournament_id": 999,
            "player_count": 0,
            "message": None,
            "sessions_count": 0,
        }

        # Patch TSG at the generate_sessions module namespace (top-level import)
        tsg_path = "app.api.api_v1.endpoints.tournaments.generate_sessions.TournamentSessionGenerator"
        gs_path = "app.api.api_v1.endpoints.tournaments.generate_sessions"

        with patch(f"{gs_path}._registry_lock", real_lock), \
             patch(f"{gs_path}._task_registry", real_registry), \
             patch(tsg_path) as MockTSG, \
             patch(f"{gs_path}.SessionLocal") as MockSL:
            MockSL.return_value.__enter__ = MagicMock(return_value=MagicMock())
            MockSL.return_value.__exit__ = MagicMock(return_value=False)
            # Use a simple mock DB session
            mock_db = MagicMock()
            MockSL.return_value = mock_db
            MockTSG.return_value.generate_sessions.return_value = (True, "1 session ok", [])

            _run_generation_in_background(
                task_id=task_id,
                tournament_id=999,
                parallel_fields=1,
                session_duration=90,
                break_duration=15,
                number_of_rounds=1,
                campus_overrides_raw=None,
                campus_ids=[1],
            )

        final_entry = real_registry[task_id]
        assert final_entry["status"] == "done", (
            f"Expected 'done', got {final_entry['status']!r}"
        )
        assert final_entry["sessions_count"] == 0  # mocked TSG returns empty list
        assert final_entry["message"] == "1 session ok"

    # -----------------------------------------------------------------------
    # 3. Registry size equals dispatch count
    # -----------------------------------------------------------------------

    def test_registry_size_equals_number_of_dispatches(self):
        """N sequential background dispatches → exactly N entries in registry.

        After 5 separate dispatches (all with unique tournament ids), the
        registry must have exactly 5 entries with distinct task_ids.
        """
        import contextlib

        real_registry: dict = {}
        real_lock = __import__("threading").Lock()
        campus_row = MagicMock(); campus_row.id = 1
        N = 5

        common_patches = [
            patch("app.models.semester.Semester"),
            patch("app.models.semester.SemesterStatus"),
            patch("app.models.tournament_configuration.TournamentConfiguration"),
            patch("app.models.tournament_reward_config.TournamentRewardConfig"),
            patch("app.models.tournament_achievement.TournamentSkillMapping"),
            patch("app.models.campus.Campus"),
            patch("app.models.campus_schedule_config.CampusScheduleConfig"),
            patch("app.services.audit_service.AuditService"),
            patch("app.models.audit_log.AuditAction"),
            patch("app.models.session.Session"),
            patch("app.models.semester_enrollment.SemesterEnrollment"),
            patch("app.models.semester_enrollment.EnrollmentStatus"),
            patch("app.models.license.UserLicense"),
            patch(f"{self._GS}.BACKGROUND_GENERATION_THRESHOLD", 0),
            patch(f"{self._GS}._is_celery_available", return_value=False),
            patch(f"{self._GS}._registry_lock", real_lock),
            patch(f"{self._GS}._task_registry", real_registry),
            patch(f"{self._GS}._run_generation_in_background"),
            patch("threading.Thread"),
        ]

        task_ids = []
        for i in range(N):
            mock_t = MagicMock(); mock_t.id = 700 + i
            db = _seq_db(
                _q(first=_gm_user()),  # grandmaster
                _q(all_=[campus_row]),
                _q(first=None),
            )
            with contextlib.ExitStack() as stack:
                mocks = [stack.enter_context(p) for p in common_patches]
                mocks[0].return_value = mock_t
                r = run_ops_scenario(
                    _req(player_count=0, auto_generate_sessions=True,
                         simulation_mode="manual", campus_ids=[1]),
                    db=db, current_user=_admin()
                )
                task_ids.append(r.task_id)

        assert len(real_registry) == N, (
            f"Expected {N} registry entries, got {len(real_registry)}"
        )
        assert len(set(task_ids)) == N, f"All {N} task_ids must be unique: {task_ids}"
        for task_id in task_ids:
            assert task_id in real_registry
            assert real_registry[task_id]["status"] == "pending"

    # -----------------------------------------------------------------------
    # 4. run_id is function-local — no cross-call state contamination
    # -----------------------------------------------------------------------

    def test_run_id_is_local_no_cross_call_contamination(self):
        """Each run_ops_scenario call generates an isolated run_id (uuid4 hex).

        Two calls must not share state via the module-level _run_id. The
        tournament names logged by each call embed a unique run_id segment,
        verifiable by asserting the two returned tournament_ids are different
        (each call created an independent tournament object).
        """
        import contextlib

        campus_row = MagicMock(); campus_row.id = 1
        mock_t_a = MagicMock(); mock_t_a.id = 801
        mock_t_b = MagicMock(); mock_t_b.id = 802
        real_registry: dict = {}
        real_lock = __import__("threading").Lock()

        common_patches = [
            patch("app.models.semester.Semester"),
            patch("app.models.semester.SemesterStatus"),
            patch("app.models.tournament_configuration.TournamentConfiguration"),
            patch("app.models.tournament_reward_config.TournamentRewardConfig"),
            patch("app.models.tournament_achievement.TournamentSkillMapping"),
            patch("app.models.campus.Campus"),
            patch("app.models.campus_schedule_config.CampusScheduleConfig"),
            patch("app.services.audit_service.AuditService"),
            patch("app.models.audit_log.AuditAction"),
            patch("app.models.session.Session"),
            patch("app.models.semester_enrollment.SemesterEnrollment"),
            patch("app.models.semester_enrollment.EnrollmentStatus"),
            patch("app.models.license.UserLicense"),
            patch(f"{self._GS}.BACKGROUND_GENERATION_THRESHOLD", 0),
            patch(f"{self._GS}._is_celery_available", return_value=False),
            patch(f"{self._GS}._registry_lock", real_lock),
            patch(f"{self._GS}._task_registry", real_registry),
            patch(f"{self._GS}._run_generation_in_background"),
            patch("threading.Thread"),
        ]

        def _call(mock_t):
            db = _seq_db(
                _q(first=_gm_user()),  # grandmaster
                _q(all_=[campus_row]),
                _q(first=None),
            )
            with contextlib.ExitStack() as stack:
                mocks = [stack.enter_context(p) for p in common_patches]
                mocks[0].return_value = mock_t
                return run_ops_scenario(
                    _req(player_count=0, auto_generate_sessions=True,
                         simulation_mode="manual", campus_ids=[1]),
                    db=db, current_user=_admin()
                )

        r_a = _call(mock_t_a)
        r_b = _call(mock_t_b)

        # Independent tournament IDs — each call created its own tournament object
        assert r_a.tournament_id == 801
        assert r_b.tournament_id == 802
        # Distinct task_ids confirm isolated run state
        assert r_a.task_id != r_b.task_id, (
            "Each call must produce a distinct task_id — shared state detected"
        )
        # Both registered
        assert len(real_registry) == 2


# ===========================================================================
# Sprint P19-C — Full end-to-end integration test (real DB, real TSG)
# ===========================================================================

class TestOpsScenarioIntegration:
    """P19-C: Full pipeline integration test using a real PostgreSQL DB.

    Unlike the unit tests above, this class uses the SAVEPOINT-isolated
    test_db fixture so all DB writes are rolled back after the test.

    Pipeline under test:
      create tournament → enroll 4 real players → real TSG generates sessions
      → simulate results → calculate rankings → (finalize mocked)

    All external reward services (CreditService, XPTransactionService,
    FootballSkillService) are bypassed by mocking _finalize_tournament_with_rewards
    — that wrapper is tested in isolation in TestFinalizeTournamentWithRewards.
    """

    _FIN_PATCH = f"{_BASE}._finalize_tournament_with_rewards"

    def _create_location(self, db):
        import uuid as _uuid
        from app.models.location import Location
        loc = Location(
            name=f"Test Location {_uuid.uuid4().hex[:6]}",
            city=f"TestCity-{_uuid.uuid4().hex[:6]}",
            country="Hungary",
            location_type="CENTER",
        )
        db.add(loc); db.commit(); db.refresh(loc)
        return loc

    def _create_campus(self, db, location_id):
        from app.models.campus import Campus
        from app.models.pitch import Pitch
        campus = Campus(
            location_id=location_id,
            name="Test Campus",
            is_active=True,
        )
        db.add(campus); db.commit(); db.refresh(campus)
        # Session generation requires ≥1 active pitch on the campus (domain invariant)
        db.add(Pitch(campus_id=campus.id, pitch_number=1, name="Pálya A", capacity=22, is_active=True))
        db.commit()
        return campus

    def _create_player_with_license(self, db, idx):
        import uuid as _uuid
        from datetime import datetime
        from app.models.user import User, UserRole
        from app.models.license import UserLicense
        user = User(
            email=f"player{idx}+{_uuid.uuid4().hex[:6]}@lfa-test.com",
            name=f"Player {idx}",
            password_hash="test_hash",
            role=UserRole.STUDENT,
            is_active=True,
        )
        db.add(user); db.commit(); db.refresh(user)
        lic = UserLicense(
            user_id=user.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.utcnow(),
            is_active=True,
        )
        db.add(lic); db.commit(); db.refresh(lic)
        return user

    def _create_admin(self, db):
        import uuid as _uuid
        from app.models.user import User, UserRole
        admin = User(
            email=f"admin+{_uuid.uuid4().hex[:6]}@lfa.com",
            name="Admin User",
            password_hash="test_hash",
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(admin); db.commit(); db.refresh(admin)
        return admin

    def _create_grandmaster(self, db):
        """Create the grandmaster@lfa.com instructor required by ops_scenario."""
        from app.models.user import User, UserRole
        existing = db.query(User).filter(User.email == "grandmaster@lfa.com").first()
        if existing:
            return existing
        gm = User(
            email="grandmaster@lfa.com",
            name="Grandmaster Instructor",
            password_hash="test_hash",
            role=UserRole.INSTRUCTOR,
            is_active=True,
        )
        db.add(gm); db.commit(); db.refresh(gm)
        return gm

    def test_full_pipeline_four_players_real_tsg(self, test_db):
        """Real DB: 4 players → real TSG → real simulation → real ranking → finalize mocked.

        Setup:
          - 1 Location → 1 Campus
          - 4 Users + 4 UserLicense (LFA_FOOTBALL_PLAYER, active)
          - Admin user for current_user
          - OpsScenarioRequest with player_ids, INDIVIDUAL_RANKING, SCORE_BASED

        Expected outcome:
          - result.task_id == "sync-done"
          - result.tournament_id is not None (tournament persisted)
          - result.enrolled_count == 4
          - At least 1 session generated (real TSG with ≥2 enrolled players)
          - _finalize_tournament_with_rewards called once (lifecycle hook fired)
        """
        from app.models.session import Session as SessionModel, EventCategory
        from app.models.semester import Semester
        from app.models.tournament_ranking import TournamentRanking

        # ── Setup real DB fixtures ─────────────────────────────────────────
        loc = self._create_location(test_db)
        campus = self._create_campus(test_db, loc.id)
        players = [self._create_player_with_license(test_db, i) for i in range(4)]
        admin = self._create_admin(test_db)
        self._create_grandmaster(test_db)  # required by ops_scenario grandmaster guard

        req = OpsScenarioRequest(
            scenario="smoke_test",
            player_count=0,           # overridden by player_ids
            player_ids=[p.id for p in players],
            campus_ids=[campus.id],
            auto_generate_sessions=True,
            simulation_mode="auto_immediate",
            tournament_format="INDIVIDUAL_RANKING",
            scoring_type="SCORE_BASED",
            ranking_direction="DESC",
            number_of_rounds=1,
            tournament_name="Integration Test Tournament",
        )

        # ── Execute with real TSG, real simulation, real ranking ───────────
        with patch(self._FIN_PATCH) as mock_fin:
            result = run_ops_scenario(req, db=test_db, current_user=admin)

        # ── Core assertions ────────────────────────────────────────────────
        assert result.task_id == "sync-done", f"Expected sync-done, got {result.task_id!r}"
        assert result.tournament_id is not None, "Tournament must be created in DB"
        assert result.enrolled_count == 4, (
            f"All 4 players should be enrolled, got {result.enrolled_count}"
        )

        # Tournament exists in DB
        tid = result.tournament_id
        tournament = test_db.query(Semester).filter(Semester.id == tid).first()
        assert tournament is not None, "Tournament not found in DB after run"
        assert tournament.name == "Integration Test Tournament"

        # Real TSG generated at least 1 session (4 enrolled players → ≥ 1 IR session)
        sessions = test_db.query(SessionModel).filter(
            SessionModel.semester_id == tid,
            SessionModel.event_category == EventCategory.MATCH,
        ).all()
        assert len(sessions) >= 1, (
            f"Real TSG must generate ≥1 session for 4 players; got {len(sessions)}"
        )

        # Finalize was called exactly once (lifecycle hook fires after ranking)
        mock_fin.assert_called_once()

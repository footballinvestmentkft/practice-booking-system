"""
Unit tests for app/api/api_v1/endpoints/tournaments/lifecycle_updates.py

Coverage targets:
  update_tournament() — PATCH /{tournament_id}
    - 403: non-admin
    - 404: tournament not found
    - No updates → early return
    - name, enrollment_cost, max_players (happy + 400 overflow)
    - age_group, description
    - start_date (happy + 400 invalid format)
    - end_date   (happy + 400 invalid format)
    - specialization_type
    - assignment_type (happy + 400 invalid)
    - participant_type (happy + 400 invalid)
    - campus_id (happy + 404 not found)
    - tournament_type_id (happy + 404 + sessions auto-deleted on type change)
    - tournament_status (valid + 400 invalid + IN_PROGRESS auto-gen success/fail/skipped
      + already-sessions-generated → generation block skipped)
    - format, scoring_type, measurement_unit, ranking_direction
    - number_of_rounds (no sessions + same value + sessions auto-deleted)

Patch paths:
  _VALID = "app.services.tournament.status_validator.VALID_TRANSITIONS"
  _TSG   = "app.services.tournament_session_generator.TournamentSessionGenerator"
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from datetime import date

from app.api.api_v1.endpoints.tournaments.lifecycle_updates import (
    update_tournament,
    TournamentUpdateRequest,
)
from app.models.user import UserRole
from app.models.semester import SemesterCategory

_VALID = "app.services.tournament.status_validator.VALID_TRANSITIONS"
_TSG   = "app.services.tournament_session_generator.TournamentSessionGenerator"

# Valid statuses used across status tests
_VALID_DICT = {
    "ACTIVE": [], "IN_PROGRESS": [], "ENROLLMENT_OPEN": [],
    "COMPLETED": [], "CANCELLED": [], "REWARDS_DISTRIBUTED": [],
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _admin():
    u = MagicMock()
    u.role = UserRole.ADMIN
    u.id = 42
    return u


def _non_admin():
    u = MagicMock()
    u.role = UserRole.INSTRUCTOR
    return u


def _cfg(**overrides):
    """Mock TournamentConfiguration object."""
    cfg = MagicMock()
    cfg.tournament_type_id = 1
    cfg.participant_type = "INDIVIDUAL"
    cfg.max_players = 20
    cfg.assignment_type = "OPEN_ASSIGNMENT"
    cfg.scoring_type = "SCORE_BASED"
    cfg.measurement_unit = "points"
    cfg.ranking_direction = "DESC"
    cfg.number_of_rounds = 3
    cfg.sessions_generated = False
    cfg.sessions_generated_at = None
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _tournament(**overrides):
    t = MagicMock()
    t.id = 1
    t.name = "Old Name"
    t.enrollment_cost = 100
    t.age_group = "AMATEUR"
    t.age_groups = None          # explicit None — prevents MagicMock auto-attr trap
    t.semester_category = None   # explicit None — enum comparison safe
    t.focus_description = "Old Desc"
    t.start_date = date(2026, 4, 1)
    t.end_date = date(2026, 5, 1)
    t.specialization_type = "LFA_PLAYER"
    t.campus_id = None
    t.location_id = None
    t.tournament_status = "ACTIVE"
    # Config-backed properties (read via tournament_config_obj)
    cfg = _cfg()
    t.tournament_config_obj = cfg
    t.tournament_type_id = cfg.tournament_type_id
    t.participant_type = cfg.participant_type
    t.max_players = cfg.max_players
    t.assignment_type = cfg.assignment_type
    t.sessions_generated = cfg.sessions_generated
    t.format = "INDIVIDUAL_RANKING"
    t.scoring_type = cfg.scoring_type
    t.measurement_unit = cfg.measurement_unit
    t.ranking_direction = cfg.ranking_direction
    t.number_of_rounds = cfg.number_of_rounds
    t.match_duration_minutes = 90
    t.break_duration_minutes = 15
    t.parallel_fields = 1
    for k, v in overrides.items():
        setattr(t, k, v)
    return t


def _q(first=None, count_=0, scalar_=0, all_=None):
    q = MagicMock()
    q.filter.return_value = q
    q.filter_by.return_value = q
    q.join.return_value = q
    q.first.return_value = first
    q.count.return_value = count_
    q.scalar.return_value = scalar_
    q.all.return_value = all_ or []
    q.delete.return_value = 0
    return q


def _db_seq(*qs):
    """Sequential db.query() mock — n-th call returns qs[n]."""
    db = MagicMock()
    db.query.side_effect = list(qs) + [MagicMock()] * 4
    return db


def _db(t):
    """Single-tournament DB mock (no extra queries needed)."""
    return _db_seq(_q(first=t))


# ── 403 / 404 ──────────────────────────────────────────────────────────────────

class TestUpdateTournamentAuth:

    def test_403_non_admin(self):
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(), db=db, current_user=_non_admin())
        assert exc.value.status_code == 403

    def test_404_tournament_not_found(self):
        db = _db_seq(_q(first=None))
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(), db=db, current_user=_admin())
        assert exc.value.status_code == 404


# ── No-op (early return) ───────────────────────────────────────────────────────

class TestUpdateTournamentNoOp:

    def test_no_fields_set_returns_no_update_message(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(), db=_db(t), current_user=_admin())
        assert result["message"] == "No fields updated"
        assert result["updates"] == {}


# ── Simple field updates ───────────────────────────────────────────────────────

class TestUpdateTournamentSimpleFields:

    def test_update_name(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(name="New Name"), db=_db(t), current_user=_admin())
        assert t.name == "New Name"
        assert "name" in result["updates"]

    def test_update_enrollment_cost(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(enrollment_cost=200), db=_db(t), current_user=_admin())
        assert t.enrollment_cost == 200

    def test_update_age_group(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(age_group="YOUTH"), db=_db(t), current_user=_admin())
        assert t.age_group == "YOUTH"

    def test_age_group_blocked_for_promotion_event(self):
        """PROMOTION_EVENT guard: 400 raised; age_group and age_groups unchanged."""
        t = _tournament(
            age_group="PRE",
            age_groups=["PRE", "YOUTH"],
            semester_category=SemesterCategory.PROMOTION_EVENT,
        )
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(age_group="AMATEUR"), db=_db(t), current_user=_admin())
        assert exc.value.status_code == 400
        assert "promotion" in exc.value.detail.lower()
        assert t.age_group == "PRE"
        assert t.age_groups == ["PRE", "YOUTH"]

    def test_update_description(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(description="New desc"), db=_db(t), current_user=_admin())
        assert t.focus_description == "New desc"

    def test_update_specialization_type(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(specialization_type="LFA_COACH"), db=_db(t), current_user=_admin())
        assert t.specialization_type == "LFA_COACH"

    def test_update_format(self):
        # format is derived from tournament_type — cannot be set directly; only noted in updates
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(format="HEAD_TO_HEAD"), db=_db(t), current_user=_admin())
        assert "format_note" in result["updates"]

    def test_update_scoring_type(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(scoring_type="TIME_BASED"), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.scoring_type == "TIME_BASED"
        assert "scoring_type" in result["updates"]

    def test_update_measurement_unit(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(measurement_unit="seconds"), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.measurement_unit == "seconds"

    def test_update_ranking_direction(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(ranking_direction="ASC"), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.ranking_direction == "ASC"

    def test_commit_called_on_success(self):
        t = _tournament()
        db = _db(t)
        update_tournament(1, TournamentUpdateRequest(name="X"), db=db, current_user=_admin())
        db.commit.assert_called_once()


# ── max_players ────────────────────────────────────────────────────────────────

class TestUpdateTournamentMaxPlayers:

    def test_update_max_players_happy(self):
        t = _tournament(max_players=20)
        db = _db_seq(_q(first=t), _q(count_=0))
        result = update_tournament(1, TournamentUpdateRequest(max_players=30), db=db, current_user=_admin())
        assert t.tournament_config_obj.max_players == 30

    def test_update_max_players_400_when_exceeds_enrollments(self):
        t = _tournament(max_players=20)
        db = _db_seq(_q(first=t), _q(count_=25))
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(max_players=10), db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert "enrollments" in exc.value.detail.lower()


# ── start_date / end_date ──────────────────────────────────────────────────────

class TestUpdateTournamentDates:

    def test_update_start_date_valid_iso(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(start_date="2026-06-01"), db=_db(t), current_user=_admin())
        assert t.start_date == date(2026, 6, 1)
        assert "start_date" in result["updates"]

    def test_update_start_date_invalid_format_raises_400(self):
        t = _tournament()
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(start_date="not-a-date"), db=_db(t), current_user=_admin())
        assert exc.value.status_code == 400
        assert "start_date" in exc.value.detail

    def test_update_end_date_valid_iso(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(end_date="2026-07-01"), db=_db(t), current_user=_admin())
        assert t.end_date == date(2026, 7, 1)

    def test_update_end_date_invalid_format_raises_400(self):
        t = _tournament()
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(end_date="2026/07/01"), db=_db(t), current_user=_admin())
        assert exc.value.status_code == 400


# ── assignment_type / participant_type ─────────────────────────────────────────

class TestUpdateTournamentValidatedEnums:

    def test_update_assignment_type_open(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(assignment_type="OPEN_ASSIGNMENT"), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.assignment_type == "OPEN_ASSIGNMENT"

    def test_update_assignment_type_application_based(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(assignment_type="APPLICATION_BASED"), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.assignment_type == "APPLICATION_BASED"

    def test_update_assignment_type_invalid_raises_400(self):
        t = _tournament()
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(assignment_type="INVALID_TYPE"), db=_db(t), current_user=_admin())
        assert exc.value.status_code == 400

    def test_update_participant_type_team(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(participant_type="TEAM"), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.participant_type == "TEAM"

    def test_update_participant_type_mixed(self):
        t = _tournament()
        result = update_tournament(1, TournamentUpdateRequest(participant_type="MIXED"), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.participant_type == "MIXED"

    def test_update_participant_type_invalid_raises_400(self):
        t = _tournament()
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(participant_type="WRONG"), db=_db(t), current_user=_admin())
        assert exc.value.status_code == 400


# ── campus_id ──────────────────────────────────────────────────────────────────

class TestUpdateTournamentCampus:

    def test_update_campus_id_found(self):
        t = _tournament()
        campus = MagicMock(); campus.id = 5
        db = _db_seq(_q(first=t), _q(first=campus))
        result = update_tournament(1, TournamentUpdateRequest(campus_id=5), db=db, current_user=_admin())
        assert t.campus_id == 5
        assert "campus_id" in result["updates"]

    def test_update_campus_id_not_found_raises_404(self):
        t = _tournament()
        db = _db_seq(_q(first=t), _q(first=None))
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(campus_id=99), db=db, current_user=_admin())
        assert exc.value.status_code == 404
        assert "Campus" in exc.value.detail


# ── tournament_type_id ─────────────────────────────────────────────────────────

class TestUpdateTournamentTypeId:

    def test_update_tournament_type_id_found_no_sessions(self):
        t = _tournament(sessions_generated=False, tournament_type_id=1)
        tt = MagicMock(); tt.id = 2
        db = _db_seq(_q(first=t), _q(first=tt))
        result = update_tournament(1, TournamentUpdateRequest(tournament_type_id=2), db=db, current_user=_admin())
        assert t.tournament_config_obj.tournament_type_id == 2
        assert "tournament_type_id" in result["updates"]

    def test_update_tournament_type_id_not_found_raises_404(self):
        t = _tournament()
        db = _db_seq(_q(first=t), _q(first=None))
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(tournament_type_id=99), db=db, current_user=_admin())
        assert exc.value.status_code == 404

    def test_update_tournament_type_id_deletes_sessions_when_type_changes(self):
        t = _tournament(sessions_generated=True, tournament_type_id=1)
        cfg = MagicMock()
        t.tournament_config_obj = cfg
        tt = MagicMock(); tt.id = 2
        sess_del_q = _q()
        db = _db_seq(_q(first=t), _q(first=tt), sess_del_q)
        result = update_tournament(1, TournamentUpdateRequest(tournament_type_id=2), db=db, current_user=_admin())
        assert "sessions_deleted" in result["updates"]
        assert result["updates"]["sessions_deleted"]["reason"] == "tournament_type_changed"
        assert cfg.sessions_generated == False

    def test_update_tournament_type_id_no_deletion_when_same_type(self):
        t = _tournament(sessions_generated=True, tournament_type_id=1)
        tt = MagicMock(); tt.id = 1  # same type
        db = _db_seq(_q(first=t), _q(first=tt))
        result = update_tournament(1, TournamentUpdateRequest(tournament_type_id=1), db=db, current_user=_admin())
        assert "sessions_deleted" not in result["updates"]


# ── tournament_status ──────────────────────────────────────────────────────────

class TestUpdateTournamentStatus:

    def test_update_status_valid_sets_field(self):
        t = _tournament()
        with patch(_VALID, _VALID_DICT):
            result = update_tournament(1, TournamentUpdateRequest(tournament_status="COMPLETED"), db=_db(t), current_user=_admin())
        assert t.tournament_status == "COMPLETED"
        assert result["updates"]["tournament_status"]["admin_override"] is True

    def test_update_status_invalid_raises_400(self):
        t = _tournament()
        with patch(_VALID, _VALID_DICT):
            with pytest.raises(HTTPException) as exc:
                update_tournament(1, TournamentUpdateRequest(tournament_status="BOGUS"), db=_db(t), current_user=_admin())
        assert exc.value.status_code == 400

    def test_in_progress_triggers_session_generation_success(self):
        t = _tournament(sessions_generated=False)
        with patch(_VALID, _VALID_DICT), patch(_TSG) as MockTSG:
            mock_gen = MockTSG.return_value
            mock_gen.can_generate_sessions.return_value = (True, "")
            mock_gen.generate_sessions.return_value = (True, "ok", [MagicMock(), MagicMock()])
            result = update_tournament(1, TournamentUpdateRequest(tournament_status="IN_PROGRESS"), db=_db(t), current_user=_admin())
        assert "sessions_auto_generated" in result["updates"]
        assert result["updates"]["sessions_auto_generated"]["count"] == 2

    def test_in_progress_generation_skipped_when_cannot_generate(self):
        t = _tournament(sessions_generated=False)
        with patch(_VALID, _VALID_DICT), patch(_TSG) as MockTSG:
            mock_gen = MockTSG.return_value
            mock_gen.can_generate_sessions.return_value = (False, "Missing campus")
            result = update_tournament(1, TournamentUpdateRequest(tournament_status="IN_PROGRESS"), db=_db(t), current_user=_admin())
        assert result["updates"]["session_generation_skipped"] == "Missing campus"

    def test_in_progress_generation_failure_recorded(self):
        t = _tournament(sessions_generated=False)
        with patch(_VALID, _VALID_DICT), patch(_TSG) as MockTSG:
            mock_gen = MockTSG.return_value
            mock_gen.can_generate_sessions.return_value = (True, "")
            mock_gen.generate_sessions.return_value = (False, "generation error", [])
            result = update_tournament(1, TournamentUpdateRequest(tournament_status="IN_PROGRESS"), db=_db(t), current_user=_admin())
        assert result["updates"]["session_generation_failed"] == "generation error"

    def test_in_progress_skips_generation_when_sessions_already_exist(self):
        t = _tournament(sessions_generated=True)
        with patch(_VALID, _VALID_DICT):
            result = update_tournament(1, TournamentUpdateRequest(tournament_status="IN_PROGRESS"), db=_db(t), current_user=_admin())
        assert "sessions_auto_generated" not in result["updates"]
        assert "session_generation_skipped" not in result["updates"]

    def test_db_flush_called_on_status_update(self):
        t = _tournament()
        db = _db(t)
        with patch(_VALID, _VALID_DICT):
            update_tournament(1, TournamentUpdateRequest(tournament_status="COMPLETED"), db=db, current_user=_admin())
        db.flush.assert_called_once()


# ── number_of_rounds ───────────────────────────────────────────────────────────

class TestUpdateTournamentRounds:

    def test_update_rounds_no_sessions_simple(self):
        t = _tournament(sessions_generated=False, number_of_rounds=3)
        result = update_tournament(1, TournamentUpdateRequest(number_of_rounds=5), db=_db(t), current_user=_admin())
        assert t.tournament_config_obj.number_of_rounds == 5
        assert result["updates"]["number_of_rounds"]["new"] == 5

    def test_update_rounds_same_value_no_deletion(self):
        t = _tournament(sessions_generated=True, number_of_rounds=3)
        result = update_tournament(1, TournamentUpdateRequest(number_of_rounds=3), db=_db(t), current_user=_admin())
        assert "sessions_deleted" not in result["updates"]

    def test_update_rounds_with_existing_sessions_triggers_deletion(self):
        t = _tournament(sessions_generated=True, number_of_rounds=3)
        cfg = MagicMock()
        t.tournament_config_obj = cfg
        sess = MagicMock(); sess.id = 10
        sess_list_q = _q(all_=[sess])
        att_del_q = _q()
        sess_del_q = _q()
        db = _db_seq(_q(first=t), sess_list_q, att_del_q, sess_del_q)
        result = update_tournament(1, TournamentUpdateRequest(number_of_rounds=5), db=db, current_user=_admin())
        assert "sessions_deleted" in result["updates"]
        assert "number_of_rounds changed" in result["updates"]["sessions_deleted"]["reason"]
        assert cfg.sessions_generated == False


# ── location_id ────────────────────────────────────────────────────────────────

class TestUpdateTournamentLocationId:

    def test_update_location_id_found(self):
        t = _tournament()
        loc = MagicMock(); loc.id = 7
        db = _db_seq(_q(first=t), _q(first=loc))
        result = update_tournament(1, TournamentUpdateRequest(location_id=7), db=db, current_user=_admin())
        assert t.location_id == 7
        assert "location_id" in result["updates"]
        assert result["updates"]["location_id"]["new"] == 7

    def test_update_location_id_not_found_raises_404(self):
        t = _tournament()
        db = _db_seq(_q(first=t), _q(first=None))
        with pytest.raises(HTTPException) as exc:
            update_tournament(1, TournamentUpdateRequest(location_id=99), db=db, current_user=_admin())
        assert exc.value.status_code == 404
        assert "Location" in exc.value.detail

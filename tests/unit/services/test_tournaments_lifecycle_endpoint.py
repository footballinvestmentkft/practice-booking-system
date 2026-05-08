"""Unit tests for app/api/api_v1/endpoints/tournaments/lifecycle.py
Sprint 25 — targeting 85%+ stmt, 60%+ branch coverage

Endpoints:
  record_status_change     — helper function (raw SQL insert)
  create_tournament        — POST /  (admin only)
  transition_tournament_status — PATCH /{id}/status  (admin/instructor)
  get_tournament_status_history — GET /{id}/status-history
"""
import json
from datetime import date, datetime
from unittest.mock import MagicMock, patch, call

import pytest

from app.api.api_v1.endpoints.tournaments.lifecycle import (
    create_tournament,
    transition_tournament_status,
    get_tournament_status_history,
    record_status_change,
    TournamentCreateRequest,
    StatusTransitionRequest,
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.tournaments.lifecycle"
_PATCH_VST = f"{_BASE}.validate_status_transition"
_PATCH_GNS = f"{_BASE}.get_next_allowed_statuses"
_PATCH_SEM = f"{_BASE}.Semester"
_PATCH_TSG = "app.services.tournament_session_generator.TournamentSessionGenerator"


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _user(role=UserRole.ADMIN, uid=42, email="admin@test.com"):
    u = MagicMock()
    u.id = uid
    u.role = role
    u.email = email
    return u


def _db():
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    return db


def _fq(first=None, all_=None, count=0):
    """Fluent ORM query mock that survives arbitrary chain calls."""
    q = MagicMock()
    q.options.return_value = q
    q.filter.return_value = q
    q.with_for_update.return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    q.count.return_value = count
    q.delete.return_value = count
    q.in_ = MagicMock(return_value=q)
    return q


def _seq_db(*qs):
    """Sequential db.query mock: n-th call returns qs[n]."""
    idx = [0]
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []

    def _side(m):
        i = idx[0]
        idx[0] += 1
        return qs[i] if i < len(qs) else _fq()

    db.query.side_effect = _side
    return db


def _tournament(**kw):
    t = MagicMock()
    t.id = 10
    t.name = "Test Cup"
    t.tournament_status = "DRAFT"
    t.specialization_type = "LFA_PLAYER"
    t.start_date = date(2026, 6, 1)
    t.end_date = date(2026, 6, 30)
    t.format = "INDIVIDUAL_RANKING"
    t.sessions_generated = False
    t.reward_config = {"template_name": "standard"}  # truthy; tests needing None pass it explicitly
    t.reward_policy_snapshot = None
    t.reward_config_obj = None
    t.tournament_config_obj = MagicMock()
    t.match_duration_minutes = 90
    t.break_duration_minutes = 15
    t.parallel_fields = 1
    t.number_of_rounds = 1
    t.tournament_type_id = 1
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _create_req(**kw):
    return TournamentCreateRequest(
        name=kw.get("name", "Test Cup"),
        specialization_type=kw.get("specialization_type", "LFA_PLAYER_PRO"),
        start_date=kw.get("start_date", "2026-06-01"),
        end_date=kw.get("end_date", "2026-06-30"),
        location_id=kw.get("location_id", None),
        campus_id=kw.get("campus_id", None),
        description=kw.get("description", None),
        age_group=kw.get("age_group", None),
    )


def _trans_req(**kw):
    return StatusTransitionRequest(
        new_status=kw.get("new_status", "OPEN"),
        reason=kw.get("reason", None),
        metadata=kw.get("metadata", None),
    )


# ─────────────────────────────────────────────────────────────────
# record_status_change
# ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRecordStatusChange:
    def test_without_metadata_passes_none(self):
        db = _db()
        record_status_change(db, 10, "DRAFT", "OPEN", 42)
        db.execute.assert_called_once()
        call_kwargs = db.execute.call_args[0][1]
        assert call_kwargs["extra_metadata"] is None

    def test_with_metadata_serialises_to_json(self):
        db = _db()
        meta = {"key": "value", "n": 1}
        record_status_change(db, 10, None, "DRAFT", 42, reason="Created", metadata=meta)
        call_kwargs = db.execute.call_args[0][1]
        assert call_kwargs["extra_metadata"] == json.dumps(meta)
        assert call_kwargs["reason"] == "Created"
        assert call_kwargs["old_status"] is None


# ─────────────────────────────────────────────────────────────────
# create_tournament
# ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestCreateTournament:
    def test_non_admin_raises_403(self):
        db = _db()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            create_tournament(_create_req(), db=db, current_user=_user(UserRole.STUDENT))
        assert exc.value.status_code == 403

    def test_invalid_date_format_raises_400(self):
        from fastapi import HTTPException
        db = _db()
        req = _create_req(start_date="not-a-date", end_date="2026-06-30")
        with pytest.raises(HTTPException) as exc:
            create_tournament(req, db=db, current_user=_user())
        assert exc.value.status_code == 400
        assert "Invalid date format" in exc.value.detail

    def test_end_before_start_raises_400(self):
        from fastapi import HTTPException
        db = _db()
        req = _create_req(start_date="2026-06-30", end_date="2026-06-01")
        with pytest.raises(HTTPException) as exc:
            create_tournament(req, db=db, current_user=_user())
        assert exc.value.status_code == 400
        assert "after start date" in exc.value.detail

    def test_happy_path_no_optional_fields(self):
        """location_id=None and campus_id=None → not added to tournament_data."""
        db = _db()
        mock_t = MagicMock()
        mock_t.id = 99
        mock_t.name = "Test Cup"
        mock_t.tournament_status = "DRAFT"
        mock_t.specialization_type = "LFA_PLAYER"
        mock_t.start_date = date(2026, 6, 1)
        mock_t.end_date = date(2026, 6, 30)

        with patch(_PATCH_SEM, return_value=mock_t) as MockSem:
            result = create_tournament(
                _create_req(),
                db=db,
                current_user=_user(),
            )
        assert result.tournament_id == 99
        assert result.status == "DRAFT"
        db.add.assert_called_once_with(mock_t)
        db.commit.assert_called_once()

    def test_happy_path_with_location_and_campus(self):
        """location_id and campus_id are set → added to tournament_data."""
        db = _db()
        mock_t = MagicMock()
        mock_t.id = 5
        mock_t.name = "Test Cup"
        mock_t.tournament_status = "DRAFT"
        mock_t.specialization_type = "LFA_PLAYER"
        mock_t.start_date = date(2026, 6, 1)
        mock_t.end_date = date(2026, 6, 30)

        with patch(_PATCH_SEM) as MockSem:
            MockSem.return_value = mock_t
            create_tournament(
                _create_req(location_id=3, campus_id=7),
                db=db,
                current_user=_user(),
            )
        # Verify Semester was called with location_id and campus_id
        call_kwargs = MockSem.call_args[1]
        assert call_kwargs.get("location_id") == 3
        assert call_kwargs.get("campus_id") == 7

    def test_instructor_cannot_create_tournament(self):
        """Instructors don't have ADMIN role → 403."""
        from fastapi import HTTPException
        db = _db()
        with pytest.raises(HTTPException) as exc:
            create_tournament(_create_req(), db=db, current_user=_user(UserRole.INSTRUCTOR))
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────
# transition_tournament_status
# ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestTransitionTournamentStatus:

    # ── role guards ──────────────────────────────────────────────

    def test_student_raises_403(self):
        from fastapi import HTTPException
        db = _db()
        db.query.return_value = _fq()
        with pytest.raises(HTTPException) as exc:
            transition_tournament_status(
                10, _trans_req(), db=db, current_user=_user(UserRole.STUDENT)
            )
        assert exc.value.status_code == 403

    def test_instructor_allowed(self):
        """Instructor role passes the role guard."""
        t = _tournament(tournament_status="DRAFT")
        q = _fq(first=t)
        db = _seq_db(q)
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=["OPEN"]):
            result = transition_tournament_status(
                10, _trans_req(new_status="OPEN"),
                db=db, current_user=_user(UserRole.INSTRUCTOR)
            )
        assert result.new_status == "OPEN"

    # ── tournament not found ─────────────────────────────────────

    def test_tournament_not_found_raises_404(self):
        from fastapi import HTTPException
        db = _seq_db(_fq(first=None))
        with pytest.raises(HTTPException) as exc:
            transition_tournament_status(
                99, _trans_req(), db=db, current_user=_user()
            )
        assert exc.value.status_code == 404

    # ── status validation ────────────────────────────────────────

    def test_invalid_status_transition_raises_400(self):
        from fastapi import HTTPException
        t = _tournament()
        db = _seq_db(_fq(first=t))
        with patch(_PATCH_VST, return_value=(False, "Cannot go to COMPLETED")):
            with pytest.raises(HTTPException) as exc:
                transition_tournament_status(
                    10, _trans_req(new_status="COMPLETED"),
                    db=db, current_user=_user()
                )
        assert exc.value.status_code == 400
        assert "Cannot go to COMPLETED" in exc.value.detail

    # ── simple happy path (no IN_PROGRESS logic) ─────────────────

    def test_happy_path_simple_transition(self):
        t = _tournament(tournament_status="DRAFT")
        db = _seq_db(_fq(first=t))
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=["OPEN", "CANCELLED"]):
            result = transition_tournament_status(
                10, _trans_req(new_status="OPEN", reason="Ready"),
                db=db, current_user=_user()
            )
        assert result.tournament_id == 10
        assert result.old_status == "DRAFT"
        assert result.reason == "Ready"
        assert "OPEN" in result.allowed_next_statuses
        db.commit.assert_called_once()

    def test_transition_with_metadata(self):
        t = _tournament(tournament_status="OPEN")
        db = _seq_db(_fq(first=t))
        meta = {"auto": True}
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="ENROLLMENT_OPEN", metadata=meta),
                db=db, current_user=_user()
            )
        assert result.new_status == "ENROLLMENT_OPEN"

    # ── IN_PROGRESS → ENROLLMENT_CLOSED (auto-delete) ───────────

    def test_in_progress_to_enrollment_closed_sessions_not_generated(self):
        """sessions_generated=False → no deletion."""
        t = _tournament(tournament_status="IN_PROGRESS", sessions_generated=False)
        db = _seq_db(_fq(first=t))
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="ENROLLMENT_CLOSED"),
                db=db, current_user=_user()
            )
        # No deletion — query only called once (the Semester query)
        assert result.new_status == "ENROLLMENT_CLOSED"

    def test_in_progress_to_enrollment_closed_no_auto_sessions(self):
        """sessions_generated=True but session_ids=[] → no attendance/session deletion."""
        t = _tournament(tournament_status="IN_PROGRESS", sessions_generated=True)
        q_semester = _fq(first=t)
        q_sessions = _fq(all_=[])       # no sessions
        db = _seq_db(q_semester, q_sessions)
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="ENROLLMENT_CLOSED"),
                db=db, current_user=_user()
            )
        assert result.new_status == "ENROLLMENT_CLOSED"

    def test_in_progress_to_enrollment_closed_deletes_sessions(self):
        """sessions_generated=True with sessions → delete attendance + sessions."""
        s1 = MagicMock(); s1.id = 101
        s2 = MagicMock(); s2.id = 102
        t = _tournament(tournament_status="IN_PROGRESS", sessions_generated=True)
        t.tournament_config_obj = MagicMock()

        q_semester = _fq(first=t)
        q_sessions_all = _fq(all_=[s1, s2])      # for [s.id for s in ...]
        q_attendance_del = _fq(count=2)           # Attendance.filter().delete()
        q_sessions_del = _fq(count=2)             # SessionModel.filter().delete()

        db = _seq_db(q_semester, q_sessions_all, q_attendance_del, q_sessions_del)

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="ENROLLMENT_CLOSED"),
                db=db, current_user=_user()
            )
        assert result.new_status == "ENROLLMENT_CLOSED"
        # config flags reset
        assert t.tournament_config_obj.sessions_generated is False

    def test_in_progress_to_enrollment_closed_no_config_obj(self):
        """sessions_generated=True, sessions exist, but no tournament_config_obj."""
        s1 = MagicMock(); s1.id = 101
        t = _tournament(tournament_status="IN_PROGRESS", sessions_generated=True)
        t.tournament_config_obj = None  # no config obj

        q_semester = _fq(first=t)
        q_sessions_all = _fq(all_=[s1])
        q_att_del = _fq(count=1)
        q_ses_del = _fq(count=1)

        db = _seq_db(q_semester, q_sessions_all, q_att_del, q_ses_del)
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            # Should complete without error even if config_obj is None
            result = transition_tournament_status(
                10, _trans_req(new_status="ENROLLMENT_CLOSED"),
                db=db, current_user=_user()
            )
        assert result.new_status == "ENROLLMENT_CLOSED"

    # ── new_status == IN_PROGRESS auto-generate ──────────────────

    def test_in_progress_without_reward_config_blocks(self):
        """reward_config=None → REWARD_CONFIG_MISSING blocks IN_PROGRESS with HTTP 400."""
        t = _tournament(reward_config=None, sessions_generated=True)
        q_semester = _fq(first=t)
        db = _seq_db(q_semester)  # no q_count — guard raises before session count query

        from fastapi import HTTPException
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            with pytest.raises(HTTPException) as exc:
                transition_tournament_status(
                    10, _trans_req(new_status="IN_PROGRESS"),
                    db=db, current_user=_user()
                )
        assert exc.value.status_code == 400
        assert "REWARD_CONFIG_MISSING" in exc.value.detail

    def test_in_progress_reward_snapshot_already_set_skips(self):
        """reward_config set but reward_policy_snapshot already set → skip snapshot."""
        t = _tournament(
            reward_config={"template_name": "standard"},
            reward_policy_snapshot={"template_name": "standard"},  # already saved
            sessions_generated=True,
        )
        q_semester = _fq(first=t)
        q_count = _fq(count=1)
        db = _seq_db(q_semester, q_count)

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="IN_PROGRESS"),
                db=db, current_user=_user()
            )
        assert result.new_status == "IN_PROGRESS"

    def test_in_progress_saves_reward_snapshot(self):
        """reward_config set, no snapshot yet, reward_config_obj present → save."""
        t = _tournament(
            reward_config={"template_name": "standard", "skill_mappings": []},
            reward_policy_snapshot=None,
            sessions_generated=True,
        )
        t.reward_config_obj = MagicMock()
        q_semester = _fq(first=t)
        q_count = _fq(count=1)   # INDIVIDUAL_RANKING, count=1 = expected → no regen
        db = _seq_db(q_semester, q_count)

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="IN_PROGRESS"),
                db=db, current_user=_user()
            )
        assert result.new_status == "IN_PROGRESS"
        assert t.reward_config_obj.reward_policy_snapshot == t.reward_config

    def test_in_progress_individual_ranking_no_regen_needed(self):
        """INDIVIDUAL_RANKING: sessions_generated=True AND count==1 → no regeneration."""
        t = _tournament(format="INDIVIDUAL_RANKING", sessions_generated=True)
        q_semester = _fq(first=t)
        q_count = _fq(count=1)    # matches expected_session_count=1
        db = _seq_db(q_semester, q_count)

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="IN_PROGRESS"),
                db=db, current_user=_user()
            )
        assert result.new_status == "IN_PROGRESS"

    def test_in_progress_head_to_head_already_generated_no_regen(self):
        """HEAD_TO_HEAD: sessions_generated=True → needs_regeneration=False."""
        t = _tournament(format="HEAD_TO_HEAD", sessions_generated=True)
        q_semester = _fq(first=t)
        q_count = _fq(count=3)   # count irrelevant for HEAD_TO_HEAD already generated
        db = _seq_db(q_semester, q_count)

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]):
            result = transition_tournament_status(
                10, _trans_req(new_status="IN_PROGRESS"),
                db=db, current_user=_user()
            )
        assert result.new_status == "IN_PROGRESS"

    def test_in_progress_needs_regen_no_existing_sessions_generator_called(self):
        """needs_regeneration=True, count=0 → skip deletion, call generator."""
        t = _tournament(format="INDIVIDUAL_RANKING", sessions_generated=False)
        t.tournament_config_obj = MagicMock()

        q_semester = _fq(first=t)
        q_count = _fq(count=0)         # no existing sessions
        q_enroll = _fq(all_=[])         # enrollment snapshot: no players

        db = _seq_db(q_semester, q_count, q_enroll)

        mock_gen = MagicMock()
        mock_gen.can_generate_sessions.return_value = (True, None)
        mock_gen.generate_sessions.return_value = (True, "OK", [MagicMock()])

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]), \
             patch(_PATCH_TSG, return_value=mock_gen):
            result = transition_tournament_status(
                10, _trans_req(new_status="IN_PROGRESS"),
                db=db, current_user=_user()
            )
        assert result.new_status == "IN_PROGRESS"
        mock_gen.generate_sessions.assert_called_once()

    def test_in_progress_needs_regen_deletes_old_sessions_first(self):
        """needs_regeneration=True, count>0 → delete old sessions then call can_generate (raises 400 if can't generate)."""
        s1 = MagicMock(); s1.id = 55
        t = _tournament(format="INDIVIDUAL_RANKING", sessions_generated=False)
        t.tournament_config_obj = MagicMock()

        q_semester = _fq(first=t)
        q_count = _fq(count=2)              # 2 existing → needs deletion
        q_sess_all = _fq(all_=[s1])         # for getting session ids
        q_att_del = _fq(count=1)
        q_sess_del = _fq(count=1)

        db = _seq_db(q_semester, q_count, q_sess_all, q_att_del, q_sess_del)

        mock_gen = MagicMock()
        mock_gen.can_generate_sessions.return_value = (False, "Not enough players")

        from fastapi import HTTPException
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]), \
             patch(_PATCH_TSG, return_value=mock_gen):
            with pytest.raises(HTTPException) as exc:
                transition_tournament_status(
                    10, _trans_req(new_status="IN_PROGRESS"),
                    db=db, current_user=_user()
                )
        assert exc.value.status_code == 400
        assert "Cannot regenerate" in exc.value.detail
        # deletion happened BEFORE can_generate_sessions was called
        mock_gen.can_generate_sessions.assert_called_once()

    def test_in_progress_generator_fails_raises_400(self):
        """can_generate=True but generate_sessions fails → HTTPException 400."""
        t = _tournament(format="INDIVIDUAL_RANKING", sessions_generated=False)
        t.tournament_config_obj = MagicMock()

        q_semester = _fq(first=t)
        q_count = _fq(count=0)

        db = _seq_db(q_semester, q_count)

        mock_gen = MagicMock()
        mock_gen.can_generate_sessions.return_value = (True, None)
        mock_gen.generate_sessions.return_value = (False, "Generation failed", [])

        from fastapi import HTTPException
        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]), \
             patch(_PATCH_TSG, return_value=mock_gen):
            with pytest.raises(HTTPException) as exc:
                transition_tournament_status(
                    10, _trans_req(new_status="IN_PROGRESS"),
                    db=db, current_user=_user()
                )
        assert exc.value.status_code == 400
        assert "Session regeneration failed" in exc.value.detail

    def test_in_progress_needs_regen_no_config_obj_still_works(self):
        """needs_regeneration=True, tournament_config_obj=None → skip config reset."""
        t = _tournament(format="INDIVIDUAL_RANKING", sessions_generated=False)
        t.tournament_config_obj = None  # no config obj

        q_semester = _fq(first=t)
        q_count = _fq(count=0)
        q_enroll = _fq(all_=[])

        db = _seq_db(q_semester, q_count, q_enroll)

        mock_gen = MagicMock()
        mock_gen.can_generate_sessions.return_value = (True, None)
        mock_gen.generate_sessions.return_value = (True, "OK", [])

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]), \
             patch(_PATCH_TSG, return_value=mock_gen):
            result = transition_tournament_status(
                10, _trans_req(new_status="IN_PROGRESS"),
                db=db, current_user=_user()
            )
        assert result.new_status == "IN_PROGRESS"

    def test_in_progress_regen_with_enrolled_players(self):
        """needs_regeneration=True (H2H, sessions not generated) → sessions generated at IN_PROGRESS.

        Architecture note: The enrollment snapshot is saved at CHECK_IN_OPEN (not IN_PROGRESS).
        IN_PROGRESS re-generates sessions using check-in-filtered participant lists.
        """
        enrolled = MagicMock()
        enrolled.user_id = 77
        enrolled.id = 200
        enrolled.payment_verified = True
        enrolled.created_at = datetime(2026, 5, 1)

        t = _tournament(format="HEAD_TO_HEAD", sessions_generated=False)
        t.tournament_config_obj = MagicMock()

        q_semester = _fq(first=t)
        q_count = _fq(count=0)
        q_enroll = _fq(all_=[enrolled])

        db = _seq_db(q_semester, q_count, q_enroll)

        mock_gen = MagicMock()
        mock_gen.can_generate_sessions.return_value = (True, None)
        mock_gen.generate_sessions.return_value = (True, "OK", [MagicMock()])

        with patch(_PATCH_VST, return_value=(True, None)), \
             patch(_PATCH_GNS, return_value=[]), \
             patch(_PATCH_TSG, return_value=mock_gen):
            result = transition_tournament_status(
                10, _trans_req(new_status="IN_PROGRESS"),
                db=db, current_user=_user()
            )
        assert result.new_status == "IN_PROGRESS"


# ─────────────────────────────────────────────────────────────────
# get_tournament_status_history
# ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestGetTournamentStatusHistory:
    def test_tournament_not_found_raises_404(self):
        from fastapi import HTTPException
        db = _seq_db(_fq(first=None))
        with pytest.raises(HTTPException) as exc:
            get_tournament_status_history(99, db=db, current_user=_user())
        assert exc.value.status_code == 404

    def test_happy_path_empty_history(self):
        t = _tournament()
        db = _seq_db(_fq(first=t))
        db.execute.return_value.fetchall.return_value = []

        result = get_tournament_status_history(10, db=db, current_user=_user())
        assert result.tournament_id == 10
        assert result.history == []

    def test_happy_path_with_history_rows(self):
        t = _tournament()
        t.name = "Cup Finals"
        t.tournament_status = "OPEN"

        row = MagicMock()
        row.id = 1
        row.old_status = None
        row.new_status = "DRAFT"
        row.changed_by = 42
        row.changed_by_name = "Admin User"
        row.created_at = datetime(2026, 6, 1, 10, 0, 0)
        row.reason = "Tournament created"
        row.extra_metadata = None

        db = _seq_db(_fq(first=t))
        db.execute.return_value.fetchall.return_value = [row]

        result = get_tournament_status_history(10, db=db, current_user=_user())
        assert result.tournament_name == "Cup Finals"
        assert result.current_status == "OPEN"
        assert len(result.history) == 1
        assert result.history[0].new_status == "DRAFT"
        assert result.history[0].changed_by_name == "Admin User"
        assert result.history[0].reason == "Tournament created"

    def test_history_entry_with_json_metadata(self):
        """extra_metadata dict from row is passed through."""
        t = _tournament()
        row = MagicMock()
        row.id = 2
        row.old_status = "DRAFT"
        row.new_status = "OPEN"
        row.changed_by = 42
        row.changed_by_name = "Admin"
        row.created_at = datetime(2026, 6, 2, 9, 0, 0)
        row.reason = None
        row.extra_metadata = {"auto": True}

        db = _seq_db(_fq(first=t))
        db.execute.return_value.fetchall.return_value = [row]

        result = get_tournament_status_history(10, db=db, current_user=_user())
        assert result.history[0].metadata == {"auto": True}

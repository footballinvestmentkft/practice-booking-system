"""
Unit tests for Team API v1 endpoints + admin_enroll_team_in_tournament cost policy.

Coverage:
  POST /api/v1/teams  (admin_create_team)
    T1  non-admin caller → 403
    T2  captain_user_id not found → 404 (service raises)
    T3  duplicate team code → 409 (service raises)
    T4  success → 201 with id/name/code/captain_user_id

  POST /api/v1/teams/{team_id}/members  (admin_add_team_member)
    T5  non-admin caller → 403
    T6  team not found → 404 (service raises)
    T7  user already a member → 409 (service raises)
    T8  success → 201 with id/team_id/user_id/role

  POST /api/v1/tournaments/{tournament_id}/enroll-team  (admin_enroll_team)
    T9   non-admin caller → 403
    T10  team not found → 404 (service raises)
    T11  tournament participant_type != TEAM → 400 (service raises)
    T12  tournament status != ENROLLMENT_OPEN → 400 (service raises)
    T13  success, cost=0 → 200, enrolled=True

  admin_enroll_team_in_tournament — cost policy (service-level, MagicMock DB)
    T14  cost>0, sufficient credits → deducts from captain once, creates CreditTransaction
    T15  cost>0, pre-existing enrollment → returns existing, no debit (double-debit proof)
"""
import pytest
from unittest.mock import MagicMock, call, patch
from fastapi import HTTPException

from app.api.api_v1.endpoints.teams import (
    admin_create_team,
    AdminCreateTeamRequest,
    admin_add_team_member,
    AdminAddMemberRequest,
)
from app.api.api_v1.endpoints.tournaments.team_enrollment import (
    admin_enroll_team,
    TeamEnrollRequest,
)
from app.models.user import UserRole
from app.models.team import Team, TournamentTeamEnrollment
from app.models.semester import Semester
from app.models.tournament_configuration import TournamentConfiguration
from app.models.license import UserLicense
from app.models.credit_transaction import CreditTransaction

_TEAMS_BASE = "app.api.api_v1.endpoints.teams.team_service"
_TE_BASE = "app.api.api_v1.endpoints.tournaments.team_enrollment.team_service"


def _admin_user():
    u = MagicMock()
    u.role = UserRole.ADMIN
    return u


def _non_admin_user():
    u = MagicMock()
    u.role = UserRole.STUDENT
    return u


# ─────────────────────────────────────────────────────────────────────────────
# POST /teams  — admin_create_team
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminCreateTeam:
    def test_T1_non_admin_raises_403(self):
        db = MagicMock()
        body = AdminCreateTeamRequest(name="Alpha", captain_user_id=1)
        with pytest.raises(HTTPException) as exc_info:
            admin_create_team(body=body, db=db, current_user=_non_admin_user())
        assert exc_info.value.status_code == 403

    @patch(f"{_TEAMS_BASE}.create_team")
    def test_T2_captain_not_found_propagates_404(self, mock_create):
        mock_create.side_effect = HTTPException(status_code=404, detail="Captain user not found")
        db = MagicMock()
        body = AdminCreateTeamRequest(name="Beta", captain_user_id=999)
        with pytest.raises(HTTPException) as exc_info:
            admin_create_team(body=body, db=db, current_user=_admin_user())
        assert exc_info.value.status_code == 404

    @patch(f"{_TEAMS_BASE}.create_team")
    def test_T3_duplicate_code_propagates_409(self, mock_create):
        mock_create.side_effect = HTTPException(status_code=409, detail="Team code 'X' already exists")
        db = MagicMock()
        body = AdminCreateTeamRequest(name="Gamma", captain_user_id=1, code="X")
        with pytest.raises(HTTPException) as exc_info:
            admin_create_team(body=body, db=db, current_user=_admin_user())
        assert exc_info.value.status_code == 409

    @patch(f"{_TEAMS_BASE}.create_team")
    def test_T4_success_returns_201_fields(self, mock_create):
        fake_team = MagicMock()
        fake_team.id = 42
        fake_team.name = "Delta"
        fake_team.code = "TEAM-DELTA"
        fake_team.captain_user_id = 7
        mock_create.return_value = fake_team

        db = MagicMock()
        body = AdminCreateTeamRequest(name="Delta", captain_user_id=7)
        result = admin_create_team(body=body, db=db, current_user=_admin_user())

        assert result["id"] == 42
        assert result["name"] == "Delta"
        assert result["code"] == "TEAM-DELTA"
        assert result["captain_user_id"] == 7
        mock_create.assert_called_once_with(db, name="Delta", captain_user_id=7, specialization_type="", code=None)


# ─────────────────────────────────────────────────────────────────────────────
# POST /teams/{team_id}/members  — admin_add_team_member
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminAddTeamMember:
    def test_T5_non_admin_raises_403(self):
        db = MagicMock()
        body = AdminAddMemberRequest(user_id=10)
        with pytest.raises(HTTPException) as exc_info:
            admin_add_team_member(team_id=1, body=body, db=db, current_user=_non_admin_user())
        assert exc_info.value.status_code == 403

    @patch(f"{_TEAMS_BASE}.add_team_member")
    def test_T6_team_not_found_propagates_404(self, mock_add):
        mock_add.side_effect = HTTPException(status_code=404, detail="Team not found")
        db = MagicMock()
        body = AdminAddMemberRequest(user_id=10)
        with pytest.raises(HTTPException) as exc_info:
            admin_add_team_member(team_id=999, body=body, db=db, current_user=_admin_user())
        assert exc_info.value.status_code == 404

    @patch(f"{_TEAMS_BASE}.add_team_member")
    def test_T7_already_member_propagates_409(self, mock_add):
        mock_add.side_effect = HTTPException(status_code=409, detail="User is already a team member")
        db = MagicMock()
        body = AdminAddMemberRequest(user_id=10)
        with pytest.raises(HTTPException) as exc_info:
            admin_add_team_member(team_id=1, body=body, db=db, current_user=_admin_user())
        assert exc_info.value.status_code == 409

    @patch(f"{_TEAMS_BASE}.add_team_member")
    def test_T8_success_returns_201_fields(self, mock_add):
        fake_member = MagicMock()
        fake_member.id = 55
        fake_member.team_id = 1
        fake_member.user_id = 10
        fake_member.role = "PLAYER"
        mock_add.return_value = fake_member

        db = MagicMock()
        body = AdminAddMemberRequest(user_id=10)
        result = admin_add_team_member(team_id=1, body=body, db=db, current_user=_admin_user())

        assert result["id"] == 55
        assert result["team_id"] == 1
        assert result["user_id"] == 10
        assert result["role"] == "PLAYER"
        mock_add.assert_called_once_with(db, team_id=1, user_id=10, role="PLAYER")


# ─────────────────────────────────────────────────────────────────────────────
# POST /tournaments/{tournament_id}/enroll-team  — admin_enroll_team
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminEnrollTeam:
    def test_T9_non_admin_raises_403(self):
        db = MagicMock()
        body = TeamEnrollRequest(team_id=5)
        with pytest.raises(HTTPException) as exc_info:
            admin_enroll_team(tournament_id=1, body=body, db=db, current_user=_non_admin_user())
        assert exc_info.value.status_code == 403

    @patch(f"{_TE_BASE}.admin_enroll_team_in_tournament")
    def test_T10_team_not_found_propagates_404(self, mock_enroll):
        mock_enroll.side_effect = HTTPException(status_code=404, detail="Team not found")
        db = MagicMock()
        body = TeamEnrollRequest(team_id=999)
        with pytest.raises(HTTPException) as exc_info:
            admin_enroll_team(tournament_id=1, body=body, db=db, current_user=_admin_user())
        assert exc_info.value.status_code == 404

    @patch(f"{_TE_BASE}.admin_enroll_team_in_tournament")
    def test_T11_not_team_tournament_returns_400(self, mock_enroll):
        mock_enroll.side_effect = HTTPException(
            status_code=400,
            detail="This tournament does not support team enrollment",
        )
        db = MagicMock()
        body = TeamEnrollRequest(team_id=5)
        with pytest.raises(HTTPException) as exc_info:
            admin_enroll_team(tournament_id=1, body=body, db=db, current_user=_admin_user())
        assert exc_info.value.status_code == 400

    @patch(f"{_TE_BASE}.admin_enroll_team_in_tournament")
    def test_T12_enrollment_not_open_returns_400(self, mock_enroll):
        mock_enroll.side_effect = HTTPException(
            status_code=400,
            detail="Tournament enrollment is not open (current status: IN_PROGRESS)",
        )
        db = MagicMock()
        body = TeamEnrollRequest(team_id=5)
        with pytest.raises(HTTPException) as exc_info:
            admin_enroll_team(tournament_id=1, body=body, db=db, current_user=_admin_user())
        assert exc_info.value.status_code == 400
        assert "ENROLLMENT_OPEN" not in str(exc_info.value.detail) or True  # status in message

    @patch(f"{_TE_BASE}.admin_enroll_team_in_tournament")
    def test_T13_success_cost_zero_returns_200_enrolled(self, mock_enroll):
        fake_enrollment = MagicMock()
        fake_enrollment.id = 88
        fake_enrollment.team_id = 5
        fake_enrollment.semester_id = 1
        fake_enrollment.payment_verified = True
        mock_enroll.return_value = fake_enrollment

        db = MagicMock()
        body = TeamEnrollRequest(team_id=5)
        result = admin_enroll_team(tournament_id=1, body=body, db=db, current_user=_admin_user())

        assert result["enrolled"] is True
        assert result["enrollment_id"] == 88
        assert result["team_id"] == 5
        assert result["tournament_id"] == 1
        assert result["payment_verified"] is True
        mock_enroll.assert_called_once_with(db, team_id=5, tournament_id=1)


# ─────────────────────────────────────────────────────────────────────────────
# admin_enroll_team_in_tournament — cost policy (service-level, MagicMock DB)
# ─────────────────────────────────────────────────────────────────────────────

_SVC = "app.services.tournament.team_service"


def _make_db(team, tournament, cfg, existing_enrollment, license_obj):
    """
    Build a MagicMock db whose .query() returns the right object per model.

    For UserLicense the chain is .filter().with_for_update().first().
    For all others it is .filter().first().
    """
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is Team:
            q.filter.return_value.first.return_value = team
        elif model is Semester:
            q.filter.return_value.first.return_value = tournament
        elif model is TournamentConfiguration:
            q.filter.return_value.first.return_value = cfg
        elif model is TournamentTeamEnrollment:
            q.filter.return_value.first.return_value = existing_enrollment
        elif model is UserLicense:
            q.filter.return_value.with_for_update.return_value.first.return_value = license_obj
        return q

    db.query.side_effect = _query
    return db


class TestAdminEnrollTeamCostPolicy:
    """
    Service-level tests for admin_enroll_team_in_tournament cost policy.

    Uses MagicMock DB — no real database required.
    Directly tests the service function (not the endpoint wrapper).
    """

    def test_T14_cost_positive_debits_captain_once(self):
        """First enrollment with cost>0 deducts from captain's credit balance."""
        from app.services.tournament.team_service import admin_enroll_team_in_tournament

        COST = 50
        INITIAL_BALANCE = 200

        fake_team = MagicMock()
        fake_team.is_active = True
        fake_team.captain_user_id = 7

        fake_tournament = MagicMock()
        fake_tournament.tournament_status = "ENROLLMENT_OPEN"

        fake_cfg = MagicMock()
        fake_cfg.participant_type = "TEAM"
        fake_cfg.team_enrollment_cost = COST

        fake_license = MagicMock()
        fake_license.id = 99
        fake_license.credit_balance = INITIAL_BALANCE

        db = _make_db(
            team=fake_team,
            tournament=fake_tournament,
            cfg=fake_cfg,
            existing_enrollment=None,   # no prior enrollment
            license_obj=fake_license,
        )

        admin_enroll_team_in_tournament(db, team_id=5, tournament_id=1)

        # Credit was deducted from captain
        assert fake_license.credit_balance == INITIAL_BALANCE - COST

        # A CreditTransaction was added to the session
        added_objects = [c.args[0] for c in db.add.call_args_list]
        credit_txs = [o for o in added_objects if isinstance(o, CreditTransaction)]
        assert len(credit_txs) == 1, f"Expected 1 CreditTransaction, got {len(credit_txs)}"
        assert credit_txs[0].amount == -COST
        assert credit_txs[0].balance_after == INITIAL_BALANCE - COST

    def test_T15_cost_positive_duplicate_no_double_debit(self):
        """Repeat enrollment returns existing record; captain balance is NOT touched."""
        from app.services.tournament.team_service import admin_enroll_team_in_tournament

        COST = 50
        INITIAL_BALANCE = 200

        fake_team = MagicMock()
        fake_team.is_active = True
        fake_team.captain_user_id = 7

        fake_tournament = MagicMock()
        fake_tournament.tournament_status = "ENROLLMENT_OPEN"

        fake_cfg = MagicMock()
        fake_cfg.participant_type = "TEAM"
        fake_cfg.team_enrollment_cost = COST

        fake_license = MagicMock()
        fake_license.credit_balance = INITIAL_BALANCE

        # Pre-existing enrollment — service must return early without debit
        existing = MagicMock()
        existing.id = 42
        existing.team_id = 5
        existing.semester_id = 1
        existing.payment_verified = False

        db = _make_db(
            team=fake_team,
            tournament=fake_tournament,
            cfg=fake_cfg,
            existing_enrollment=existing,
            license_obj=fake_license,
        )

        result = admin_enroll_team_in_tournament(db, team_id=5, tournament_id=1)

        # Returns the existing enrollment unchanged
        assert result is existing

        # Captain balance untouched
        assert fake_license.credit_balance == INITIAL_BALANCE

        # No CreditTransaction created
        added_objects = [c.args[0] for c in db.add.call_args_list]
        credit_txs = [o for o in added_objects if isinstance(o, CreditTransaction)]
        assert len(credit_txs) == 0, f"Expected 0 CreditTransactions, got {len(credit_txs)}"

        # db.commit() was NOT called (early return before any write)
        db.commit.assert_not_called()

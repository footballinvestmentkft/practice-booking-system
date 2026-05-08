"""
Unit tests for tournaments/instructor_assignment.py
Sprint 23 — coverage: 17% → ≥90%

8 endpoints:
1. accept_instructor_assignment
2. apply_to_tournament
3. approve_instructor_application
4. get_instructor_applications
5. get_my_tournament_application
6. get_my_instructor_applications
7. direct_assign_instructor
8. decline_instructor_application
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from datetime import datetime

from app.api.api_v1.endpoints.tournaments.instructor_assignment import (
    accept_instructor_assignment,
    apply_to_tournament,
    approve_instructor_application,
    get_instructor_applications,
    get_my_tournament_application,
    get_my_instructor_applications,
    direct_assign_instructor,
    decline_instructor_application,
    InstructorApplicationRequest,
    InstructorApplicationApprovalRequest,
    DirectAssignmentRequest,
    DeclineApplicationRequest,
)
from app.models.user import UserRole
from app.models.instructor_assignment import AssignmentRequestStatus

_BASE = "app.api.api_v1.endpoints.tournaments.instructor_assignment"
_NOTIF = "app.services.notification_service"


# ============================================================================
# Helpers
# ============================================================================

def _user(user_id=42, role=UserRole.INSTRUCTOR):
    u = MagicMock()
    u.id = user_id
    u.role = role
    u.name = "Test User"
    u.email = "user@test.com"
    u.nickname = None
    return u


def _instructor(user_id=42):
    return _user(user_id, UserRole.INSTRUCTOR)


def _admin(user_id=42):
    return _user(user_id, UserRole.ADMIN)


def _student(user_id=42):
    return _user(user_id, UserRole.STUDENT)


def _tournament(tid=1, status="SEEKING_INSTRUCTOR", assign_type="APPLICATION_BASED"):
    t = MagicMock()
    t.id = tid
    t.name = "Test Tournament"
    t.code = "TT-001"
    t.tournament_status = status
    t.assignment_type = assign_type
    t.age_group = "U18"
    t.master_instructor_id = None
    t.start_date = datetime(2026, 6, 1)
    return t


def _application(
    app_id=10,
    instructor_id=42,
    app_status=AssignmentRequestStatus.PENDING,
    requested_by=None,
    semester_id=1,
):
    a = MagicMock()
    a.id = app_id
    a.instructor_id = instructor_id
    a.status = app_status
    a.request_message = "I want to coach"
    a.response_message = None
    a.requested_by = requested_by
    a.semester_id = semester_id
    a.created_at = datetime(2026, 3, 1)
    a.responded_at = None
    return a


def _q(first=None, all_=None):
    q = MagicMock()
    q.filter.return_value = q
    q.filter_by.return_value = q
    q.order_by.return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    return q


def _seq_db(*qs):
    db = MagicMock()
    _n = [0]

    def _side(_model):
        idx = _n[0]
        _n[0] += 1
        if idx < len(qs):
            return qs[idx]
        return MagicMock()

    db.query.side_effect = _side
    return db


def _mock_repo(tournament):
    mock_repo = MagicMock()
    mock_repo.get_or_404.return_value = tournament
    return patch(f"{_BASE}.TournamentRepository", return_value=mock_repo)


# ============================================================================
# 1. accept_instructor_assignment
# ============================================================================

class TestAcceptInstructorAssignment:

    def test_non_instructor_raises_403(self):
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            accept_instructor_assignment(1, db=db, current_user=_student())
        assert exc.value.status_code == 403

    def test_wrong_status_raises_400(self):
        t = _tournament(status="COMPLETED")
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"):
            db = MagicMock()
            with pytest.raises(HTTPException) as exc:
                accept_instructor_assignment(1, db=db, current_user=_instructor())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_tournament_status"

    def test_success_seeking_instructor_updates_sessions(self):
        t = _tournament(status="SEEKING_INSTRUCTOR")
        s1, s2 = MagicMock(), MagicMock()
        db = _seq_db(_q(all_=[s1, s2]))
        _elig = "app.services.tournament.instructor_eligibility_service.is_eligible_master_instructor"
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"), \
                patch(_elig, return_value=(True, "")):
            result = accept_instructor_assignment(1, db=db, current_user=_instructor(42))
        assert result["message"] == "Tournament assignment accepted successfully"
        assert result["sessions_updated"] == 2
        assert s1.instructor_id == 42
        assert s2.instructor_id == 42
        assert t.tournament_status == "INSTRUCTOR_CONFIRMED"

    def test_success_pending_acceptance_no_sessions(self):
        t = _tournament(status="PENDING_INSTRUCTOR_ACCEPTANCE")
        db = _seq_db(_q(all_=[]))
        _elig = "app.services.tournament.instructor_eligibility_service.is_eligible_master_instructor"
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"), \
                patch(_elig, return_value=(True, "")):
            result = accept_instructor_assignment(1, db=db, current_user=_instructor())
        assert result["sessions_updated"] == 0

    def test_start_date_none_returns_none(self):
        t = _tournament(status="SEEKING_INSTRUCTOR")
        t.start_date = None
        db = _seq_db(_q(all_=[]))
        _elig = "app.services.tournament.instructor_eligibility_service.is_eligible_master_instructor"
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"), \
                patch(_elig, return_value=(True, "")):
            result = accept_instructor_assignment(1, db=db, current_user=_instructor())
        assert result["tournament_date"] is None


# ============================================================================
# 2. apply_to_tournament
# ============================================================================

class TestApplyToTournament:

    def test_non_instructor_raises_403(self):
        db = MagicMock()
        req = InstructorApplicationRequest()
        with pytest.raises(HTTPException) as exc:
            apply_to_tournament(1, req, db=db, current_user=_student())
        assert exc.value.status_code == 403

    def test_open_assignment_tournament_raises_400(self):
        t = _tournament(assign_type="OPEN_ASSIGNMENT", status="SEEKING_INSTRUCTOR")
        req = InstructorApplicationRequest()
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"):
            db = MagicMock()
            with pytest.raises(HTTPException) as exc:
                apply_to_tournament(1, req, db=db, current_user=_instructor())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "direct_assignment_only"

    def test_wrong_tournament_status_raises_400(self):
        t = _tournament(assign_type="APPLICATION_BASED", status="COMPLETED")
        req = InstructorApplicationRequest()
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"):
            db = _seq_db(_q(first=None))
            with pytest.raises(HTTPException) as exc:
                apply_to_tournament(1, req, db=db, current_user=_instructor())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_tournament_status"

    def test_duplicate_application_raises_400(self):
        t = _tournament(assign_type="APPLICATION_BASED", status="SEEKING_INSTRUCTOR")
        existing = _application()
        req = InstructorApplicationRequest()
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"):
            db = _seq_db(_q(first=existing))
            with pytest.raises(HTTPException) as exc:
                apply_to_tournament(1, req, db=db, current_user=_instructor())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "duplicate_application"

    def test_success_creates_application(self):
        t = _tournament(assign_type="APPLICATION_BASED", status="SEEKING_INSTRUCTOR")
        req = InstructorApplicationRequest(application_message="Hello coach!")
        mock_app = MagicMock()
        mock_app.id = 100
        mock_app.status = AssignmentRequestStatus.PENDING
        mock_app.request_message = "Hello coach!"
        mock_app.created_at = datetime(2026, 3, 1)
        with _mock_repo(t), patch(f"{_BASE}.LicenseValidator"):
            db = _seq_db(_q(first=None))
            with patch(f"{_BASE}.InstructorAssignmentRequest", return_value=mock_app):
                result = apply_to_tournament(1, req, db=db, current_user=_instructor())
        assert result["message"] == "Application submitted successfully"
        assert result["status"] == "PENDING"
        db.add.assert_called_once_with(mock_app)
        db.commit.assert_called_once()


# ============================================================================
# 3. approve_instructor_application
# ============================================================================

class TestApproveInstructorApplication:

    def test_non_admin_raises_403(self):
        db = MagicMock()
        approval = InstructorApplicationApprovalRequest()
        with pytest.raises(HTTPException) as exc:
            approve_instructor_application(1, 10, approval, db=db, current_user=_instructor())
        assert exc.value.status_code == 403

    def test_open_assignment_tournament_raises_400(self):
        t = _tournament(assign_type="OPEN_ASSIGNMENT")
        approval = InstructorApplicationApprovalRequest()
        with _mock_repo(t):
            db = MagicMock()
            with pytest.raises(HTTPException) as exc:
                approve_instructor_application(1, 10, approval, db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "direct_assignment_only"

    def test_application_not_found_raises_404(self):
        t = _tournament(assign_type="APPLICATION_BASED")
        approval = InstructorApplicationApprovalRequest()
        with _mock_repo(t):
            db = _seq_db(_q(first=None))
            with pytest.raises(HTTPException) as exc:
                approve_instructor_application(1, 10, approval, db=db, current_user=_admin())
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "application_not_found"

    def test_application_not_pending_raises_400(self):
        t = _tournament(assign_type="APPLICATION_BASED", status="SEEKING_INSTRUCTOR")
        app = _application(app_status=AssignmentRequestStatus.ACCEPTED)
        approval = InstructorApplicationApprovalRequest()
        with _mock_repo(t):
            db = _seq_db(_q(first=app))
            with pytest.raises(HTTPException) as exc:
                approve_instructor_application(1, 10, approval, db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_application_status"

    def test_tournament_not_seeking_raises_400(self):
        t = _tournament(assign_type="APPLICATION_BASED", status="COMPLETED")
        app = _application(app_status=AssignmentRequestStatus.PENDING)
        approval = InstructorApplicationApprovalRequest()
        with _mock_repo(t):
            db = _seq_db(_q(first=app))
            with pytest.raises(HTTPException) as exc:
                approve_instructor_application(1, 10, approval, db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_tournament_status"

    def test_success_approves_application(self):
        t = _tournament(assign_type="APPLICATION_BASED", status="SEEKING_INSTRUCTOR")
        app = _application(app_status=AssignmentRequestStatus.PENDING, instructor_id=99)
        # Set responded_at after approve sets it to utcnow()
        instructor = MagicMock()
        instructor.id = 99
        instructor.name = "Coach X"
        instructor.email = "coachx@test.com"
        approval = InstructorApplicationApprovalRequest(response_message="Welcome!")

        with _mock_repo(t):
            db = _seq_db(_q(first=app), _q(first=instructor))
            with patch(f"{_BASE}.StatusHistoryRecorder"):
                with patch(f"{_NOTIF}.create_tournament_application_approved_notification"):
                    result = approve_instructor_application(1, 10, approval, db=db, current_user=_admin())
        assert result["message"] == "Application approved successfully - Instructor automatically assigned"
        assert t.tournament_status == "INSTRUCTOR_CONFIRMED"
        assert app.status == AssignmentRequestStatus.ACCEPTED
        assert result["instructor_id"] == 99

    def test_notification_failure_raises_500(self):
        t = _tournament(assign_type="APPLICATION_BASED", status="SEEKING_INSTRUCTOR")
        app = _application(app_status=AssignmentRequestStatus.PENDING, instructor_id=99)
        instructor = MagicMock()
        instructor.id = 99
        instructor.name = "Coach X"
        instructor.email = "coachx@test.com"
        approval = InstructorApplicationApprovalRequest()

        with _mock_repo(t):
            db = _seq_db(_q(first=app), _q(first=instructor))
            with patch(f"{_BASE}.StatusHistoryRecorder"):
                with patch(
                    f"{_NOTIF}.create_tournament_application_approved_notification",
                    side_effect=Exception("DB failure"),
                ):
                    with pytest.raises(HTTPException) as exc:
                        approve_instructor_application(1, 10, approval, db=db, current_user=_admin())
        assert exc.value.status_code == 500
        assert exc.value.detail["error"] == "notification_creation_failed"


# ============================================================================
# 4. get_instructor_applications
# ============================================================================

class TestGetInstructorApplications:

    def test_non_admin_raises_403(self):
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_instructor_applications(1, db=db, current_user=_instructor())
        assert exc.value.status_code == 403

    def test_returns_empty_list(self):
        t = _tournament()
        with _mock_repo(t):
            db = _seq_db(_q(all_=[]))
            result = get_instructor_applications(1, db=db, current_user=_admin())
        assert result["total_applications"] == 0
        assert result["applications"] == []

    def test_returns_applications_with_instructor_details(self):
        t = _tournament()
        app1 = _application(app_id=10, instructor_id=99)
        app1.created_at = datetime(2026, 3, 1)
        app1.responded_at = None
        instructor = MagicMock()
        instructor.name = "Coach X"
        instructor.email = "coachx@test.com"

        with _mock_repo(t):
            db = _seq_db(_q(all_=[app1]), _q(first=instructor))
            result = get_instructor_applications(1, db=db, current_user=_admin())
        assert result["total_applications"] == 1
        assert result["applications"][0]["instructor_name"] == "Coach X"
        assert result["applications"][0]["instructor_email"] == "coachx@test.com"

    def test_instructor_not_found_shows_unknown(self):
        t = _tournament()
        app1 = _application(app_id=10, instructor_id=99)
        app1.created_at = datetime(2026, 3, 1)
        app1.responded_at = None

        with _mock_repo(t):
            db = _seq_db(_q(all_=[app1]), _q(first=None))
            result = get_instructor_applications(1, db=db, current_user=_admin())
        assert result["applications"][0]["instructor_name"] == "Unknown"
        assert result["applications"][0]["instructor_email"] == "N/A"

    def test_responded_at_isoformat_when_set(self):
        t = _tournament()
        app1 = _application(app_id=10)
        app1.created_at = datetime(2026, 3, 1)
        app1.responded_at = datetime(2026, 3, 5)
        instructor = MagicMock()
        instructor.name = "X"
        instructor.email = "x@test.com"

        with _mock_repo(t):
            db = _seq_db(_q(all_=[app1]), _q(first=instructor))
            result = get_instructor_applications(1, db=db, current_user=_admin())
        assert result["applications"][0]["responded_at"] == "2026-03-05T00:00:00"


# ============================================================================
# 5. get_my_tournament_application
# ============================================================================

class TestGetMyTournamentApplication:

    def test_non_instructor_raises_403(self):
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_my_tournament_application(1, db=db, current_user=_student())
        assert exc.value.status_code == 403

    def test_no_application_raises_404(self):
        t = _tournament()
        with _mock_repo(t):
            db = _seq_db(_q(first=None))
            with pytest.raises(HTTPException) as exc:
                get_my_tournament_application(1, db=db, current_user=_instructor())
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "application_not_found"

    def test_returns_pending_application(self):
        t = _tournament()
        app = _application(requested_by=None, app_status=AssignmentRequestStatus.PENDING)
        app.created_at = datetime(2026, 3, 1)
        app.responded_at = None
        with _mock_repo(t):
            db = _seq_db(_q(first=app))
            result = get_my_tournament_application(1, db=db, current_user=_instructor())
        assert result["status"] == "PENDING"
        assert result["requested_by"] is None

    def test_direct_assignment_accepted_shows_pending_acceptance(self):
        """requested_by != None + ACCEPTED → display PENDING_ACCEPTANCE"""
        t = _tournament()
        app = _application(
            requested_by=99,  # Admin assigned
            app_status=AssignmentRequestStatus.ACCEPTED,
        )
        app.created_at = datetime(2026, 3, 1)
        app.responded_at = datetime(2026, 3, 2)
        with _mock_repo(t):
            db = _seq_db(_q(first=app))
            result = get_my_tournament_application(1, db=db, current_user=_instructor())
        assert result["status"] == "PENDING_ACCEPTANCE"

    def test_responded_at_none_returns_none(self):
        t = _tournament()
        app = _application(requested_by=None, app_status=AssignmentRequestStatus.DECLINED)
        app.created_at = datetime(2026, 3, 1)
        app.responded_at = None
        with _mock_repo(t):
            db = _seq_db(_q(first=app))
            result = get_my_tournament_application(1, db=db, current_user=_instructor())
        assert result["responded_at"] is None


# ============================================================================
# 6. get_my_instructor_applications
# ============================================================================

class TestGetMyInstructorApplications:

    def test_non_instructor_raises_403(self):
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            get_my_instructor_applications(db=db, current_user=_student())
        assert exc.value.status_code == 403

    def test_returns_empty_list_no_applications(self):
        db = _seq_db(_q(all_=[]))
        result = get_my_instructor_applications(db=db, current_user=_instructor())
        assert result["total_applications"] == 0
        assert result["applications"] == []
        assert result["instructor_id"] == 42

    def test_returns_applications_with_tournament_details(self):
        app = _application(semester_id=5)
        app.created_at = datetime(2026, 3, 1)
        app.responded_at = None

        t = MagicMock()
        t.name = "My Tournament"
        t.start_date = datetime(2026, 6, 1)
        t.tournament_status = "SEEKING_INSTRUCTOR"

        # q0: InstructorAssignmentRequest.all(), q1: Semester.first()
        db = _seq_db(_q(all_=[app]), _q(first=t))
        result = get_my_instructor_applications(db=db, current_user=_instructor())
        assert result["total_applications"] == 1
        assert result["applications"][0]["tournament_name"] == "My Tournament"
        assert result["applications"][0]["tournament_status"] == "SEEKING_INSTRUCTOR"

    def test_tournament_not_found_shows_unknown(self):
        app = _application(semester_id=5)
        app.created_at = datetime(2026, 3, 1)
        app.responded_at = None

        db = _seq_db(_q(all_=[app]), _q(first=None))
        result = get_my_instructor_applications(db=db, current_user=_instructor())
        assert result["applications"][0]["tournament_name"] == "Unknown Tournament"
        assert result["applications"][0]["tournament_status"] == "UNKNOWN"

    def test_tournament_start_date_none_returns_none(self):
        app = _application(semester_id=5)
        app.created_at = datetime(2026, 3, 1)
        app.responded_at = None

        t = MagicMock()
        t.name = "X"
        t.start_date = None
        t.tournament_status = "DRAFT"

        db = _seq_db(_q(all_=[app]), _q(first=t))
        result = get_my_instructor_applications(db=db, current_user=_instructor())
        assert result["applications"][0]["tournament_start_date"] is None


# ============================================================================
# 7. direct_assign_instructor
# ============================================================================

class TestDirectAssignInstructor:

    def test_non_admin_raises_403(self):
        db = MagicMock()
        req = DirectAssignmentRequest(instructor_id=99)
        with pytest.raises(HTTPException) as exc:
            direct_assign_instructor(1, req, db=db, current_user=_instructor())
        assert exc.value.status_code == 403

    def test_wrong_tournament_status_raises_400(self):
        t = _tournament(status="COMPLETED")
        req = DirectAssignmentRequest(instructor_id=99)
        with _mock_repo(t):
            db = MagicMock()
            with pytest.raises(HTTPException) as exc:
                direct_assign_instructor(1, req, db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_tournament_status"

    def test_instructor_user_not_found_raises_404(self):
        t = _tournament(status="SEEKING_INSTRUCTOR")
        req = DirectAssignmentRequest(instructor_id=99)
        with _mock_repo(t):
            db = _seq_db(_q(first=None))
            with pytest.raises(HTTPException) as exc:
                direct_assign_instructor(1, req, db=db, current_user=_admin())
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "instructor_not_found"

    def test_user_not_instructor_role_raises_400(self):
        t = _tournament(status="SEEKING_INSTRUCTOR")
        student_user = MagicMock()
        student_user.id = 99
        student_user.role = UserRole.STUDENT
        req = DirectAssignmentRequest(instructor_id=99)
        with _mock_repo(t):
            db = _seq_db(_q(first=student_user))
            with pytest.raises(HTTPException) as exc:
                direct_assign_instructor(1, req, db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_instructor_role"

    def test_duplicate_accepted_assignment_raises_400(self):
        t = _tournament(status="SEEKING_INSTRUCTOR")
        instructor_user = MagicMock()
        instructor_user.id = 99
        instructor_user.role = UserRole.INSTRUCTOR
        instructor_user.email = "x@test.com"
        existing = _application(app_status=AssignmentRequestStatus.ACCEPTED)
        req = DirectAssignmentRequest(instructor_id=99)
        with _mock_repo(t):
            db = _seq_db(_q(first=instructor_user), _q(first=existing))
            with patch(f"{_BASE}.LicenseValidator"):
                with pytest.raises(HTTPException) as exc:
                    direct_assign_instructor(1, req, db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "duplicate_assignment"

    def test_success_creates_direct_assignment(self):
        t = _tournament(status="SEEKING_INSTRUCTOR")
        instructor_user = MagicMock()
        instructor_user.id = 99
        instructor_user.role = UserRole.INSTRUCTOR
        instructor_user.name = "Coach X"
        instructor_user.email = "coachx@test.com"
        req = DirectAssignmentRequest(instructor_id=99, assignment_message="Please join us")
        mock_assignment = MagicMock()
        mock_assignment.id = 50
        mock_assignment.status = AssignmentRequestStatus.ACCEPTED
        mock_assignment.request_message = "Please join us"
        mock_assignment.responded_at = datetime(2026, 3, 1)

        with _mock_repo(t):
            db = _seq_db(_q(first=instructor_user), _q(first=None))
            with patch(f"{_BASE}.LicenseValidator"):
                with patch(f"{_BASE}.StatusHistoryRecorder"):
                    with patch(f"{_BASE}.InstructorAssignmentRequest", return_value=mock_assignment):
                        with patch(
                            f"{_NOTIF}.create_tournament_direct_invitation_notification"
                        ):
                            result = direct_assign_instructor(1, req, db=db, current_user=_admin())
        assert result["message"] == "Instructor directly assigned successfully"
        assert t.tournament_status == "PENDING_INSTRUCTOR_ACCEPTANCE"
        assert result["instructor_id"] == 99


# ============================================================================
# 8. decline_instructor_application
# ============================================================================

class TestDeclineInstructorApplication:

    def test_non_admin_raises_403(self):
        db = MagicMock()
        decline = DeclineApplicationRequest()
        with pytest.raises(HTTPException) as exc:
            decline_instructor_application(1, 10, decline, db=db, current_user=_instructor())
        assert exc.value.status_code == 403

    def test_application_not_found_raises_404(self):
        t = _tournament()
        decline = DeclineApplicationRequest()
        with _mock_repo(t):
            db = _seq_db(_q(first=None))
            with pytest.raises(HTTPException) as exc:
                decline_instructor_application(1, 10, decline, db=db, current_user=_admin())
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "application_not_found"

    def test_application_not_pending_raises_400(self):
        t = _tournament()
        app = _application(app_status=AssignmentRequestStatus.DECLINED)
        decline = DeclineApplicationRequest()
        with _mock_repo(t):
            db = _seq_db(_q(first=app))
            with pytest.raises(HTTPException) as exc:
                decline_instructor_application(1, 10, decline, db=db, current_user=_admin())
        assert exc.value.status_code == 400
        assert exc.value.detail["error"] == "invalid_application_status"

    def test_success_declines_application(self):
        t = _tournament()
        app = _application(app_status=AssignmentRequestStatus.PENDING, instructor_id=99)
        instructor = MagicMock()
        instructor.id = 99
        instructor.name = "Coach Y"
        instructor.email = "coachy@test.com"
        decline = DeclineApplicationRequest(decline_message="Not qualified")

        with _mock_repo(t):
            db = _seq_db(_q(first=app), _q(first=instructor))
            with patch(f"{_NOTIF}.create_tournament_application_rejected_notification"):
                result = decline_instructor_application(1, 10, decline, db=db, current_user=_admin())
        assert result["message"] == "Application declined successfully"
        assert app.status == AssignmentRequestStatus.DECLINED
        assert result["instructor_id"] == 99
        assert result["instructor_name"] == "Coach Y"

    def test_no_decline_message_still_succeeds(self):
        """Decline without a message (None) completes successfully."""
        t = _tournament()
        app = _application(app_status=AssignmentRequestStatus.PENDING, instructor_id=99)
        instructor = MagicMock()
        instructor.id = 99
        instructor.name = "Coach Z"
        instructor.email = "coachz@test.com"
        decline = DeclineApplicationRequest()  # no decline_message

        with _mock_repo(t):
            db = _seq_db(_q(first=app), _q(first=instructor))
            with patch(f"{_NOTIF}.create_tournament_application_rejected_notification"):
                result = decline_instructor_application(1, 10, decline, db=db, current_user=_admin())
        assert result["message"] == "Application declined successfully"
        assert result["decline_message"] is None

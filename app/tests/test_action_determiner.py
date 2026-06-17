"""
Unit tests for ActionDeterminer service.

Tests all 34+ decision paths from the original _determine_action() method
to ensure behavior is identical after refactoring.
"""

import pytest
from unittest.mock import Mock
from app.services.action_determiner import (
    ActionDeterminer,
    AuthActionHandler,
    LicenseActionHandler,
    ProjectActionHandler,
    QuizActionHandler,
    CertificateActionHandler,
    TournamentActionHandler,
    JugglingActionHandler,
    DefaultActionHandler,
)
from app.models.audit_log import AuditAction


def create_mock_request(method: str, path: str):
    """Helper to create mock FastAPI Request."""
    request = Mock()
    request.method = method
    request.url.path = path
    return request


def create_mock_response(status_code: int = 200):
    """Helper to create mock FastAPI Response."""
    response = Mock()
    response.status_code = status_code
    return response


class TestAuthActionHandler:
    """Test authentication-related audit actions."""

    def setup_method(self):
        self.handler = AuthActionHandler()

    def test_can_handle_auth_paths(self):
        assert self.handler.can_handle("/api/v1/auth/login") is True
        assert self.handler.can_handle("/api/v1/auth/logout") is True
        assert self.handler.can_handle("/auth/change-password") is True
        assert self.handler.can_handle("/api/v1/projects") is False

    def test_login_success(self):
        action = self.handler.determine_action("POST", "/api/v1/auth/login", 200)
        assert action == AuditAction.LOGIN

    def test_login_failed(self):
        action = self.handler.determine_action("POST", "/api/v1/auth/login", 401)
        assert action == AuditAction.LOGIN_FAILED

    def test_logout(self):
        action = self.handler.determine_action("POST", "/api/v1/auth/logout", 200)
        assert action == AuditAction.LOGOUT

    def test_password_change(self):
        action = self.handler.determine_action("POST", "/auth/change-password", 200)
        assert action == AuditAction.PASSWORD_CHANGE

    def test_password_alternative_path(self):
        action = self.handler.determine_action("PUT", "/auth/password", 200)
        assert action == AuditAction.PASSWORD_CHANGE


class TestLicenseActionHandler:
    """Test license-related audit actions."""

    def setup_method(self):
        self.handler = LicenseActionHandler()

    def test_can_handle_license_paths(self):
        assert self.handler.can_handle("/api/v1/licenses") is True
        assert self.handler.can_handle("/licenses/123") is True
        assert self.handler.can_handle("/api/v1/projects") is False

    def test_license_download(self):
        action = self.handler.determine_action("GET", "/licenses/123/pdf", 200)
        assert action == AuditAction.LICENSE_DOWNLOADED

    def test_license_download_alternative(self):
        action = self.handler.determine_action("GET", "/licenses/123/download", 200)
        assert action == AuditAction.LICENSE_DOWNLOADED

    def test_license_verify(self):
        action = self.handler.determine_action("POST", "/licenses/verify", 200)
        assert action == AuditAction.LICENSE_VERIFIED

    def test_license_upgrade_request(self):
        action = self.handler.determine_action("POST", "/licenses/upgrade", 200)
        assert action == AuditAction.LICENSE_UPGRADE_REQUESTED

    def test_license_upgrade_approve(self):
        action = self.handler.determine_action("POST", "/licenses/upgrade/approve", 200)
        assert action == AuditAction.LICENSE_UPGRADE_APPROVED

    def test_license_upgrade_reject(self):
        action = self.handler.determine_action("POST", "/licenses/upgrade/reject", 200)
        assert action == AuditAction.LICENSE_UPGRADE_REJECTED

    def test_license_create(self):
        action = self.handler.determine_action("POST", "/licenses", 201)
        assert action == AuditAction.LICENSE_ISSUED

    def test_license_view(self):
        action = self.handler.determine_action("GET", "/licenses/123", 200)
        assert action == AuditAction.LICENSE_VIEWED

    def test_license_delete(self):
        action = self.handler.determine_action("DELETE", "/licenses/123", 200)
        assert action == AuditAction.LICENSE_REVOKED


class TestProjectActionHandler:
    """Test project-related audit actions."""

    def setup_method(self):
        self.handler = ProjectActionHandler()

    def test_can_handle_project_paths(self):
        assert self.handler.can_handle("/api/v1/projects") is True
        assert self.handler.can_handle("/projects/123") is True
        assert self.handler.can_handle("/api/v1/licenses") is False

    def test_project_enroll(self):
        action = self.handler.determine_action("POST", "/projects/123/enroll", 200)
        assert action == AuditAction.PROJECT_ENROLLED

    def test_project_unenroll(self):
        action = self.handler.determine_action("DELETE", "/projects/123/enroll", 200)
        assert action == AuditAction.PROJECT_UNENROLLED

    def test_project_create(self):
        action = self.handler.determine_action("POST", "/projects", 201)
        assert action == AuditAction.PROJECT_CREATED

    def test_project_update_put(self):
        action = self.handler.determine_action("PUT", "/projects/123", 200)
        assert action == AuditAction.PROJECT_UPDATED

    def test_project_update_patch(self):
        action = self.handler.determine_action("PATCH", "/projects/123", 200)
        assert action == AuditAction.PROJECT_UPDATED

    def test_project_delete(self):
        action = self.handler.determine_action("DELETE", "/projects/123", 200)
        assert action == AuditAction.PROJECT_DELETED


class TestQuizActionHandler:
    """Test quiz-related audit actions."""

    def setup_method(self):
        self.handler = QuizActionHandler()

    def test_can_handle_quiz_paths(self):
        assert self.handler.can_handle("/api/v1/quiz") is True
        assert self.handler.can_handle("/quizzes/123") is True
        assert self.handler.can_handle("/api/v1/projects") is False

    def test_quiz_start(self):
        action = self.handler.determine_action("POST", "/quiz/123/start", 200)
        assert action == AuditAction.QUIZ_STARTED

    def test_quiz_submit(self):
        action = self.handler.determine_action("POST", "/quiz/123/submit", 200)
        assert action == AuditAction.QUIZ_SUBMITTED

    def test_quiz_create(self):
        action = self.handler.determine_action("POST", "/quiz", 201)
        assert action == AuditAction.QUIZ_CREATED

    def test_quiz_update_put(self):
        action = self.handler.determine_action("PUT", "/quiz/123", 200)
        assert action == AuditAction.QUIZ_UPDATED

    def test_quiz_update_patch(self):
        action = self.handler.determine_action("PATCH", "/quiz/123", 200)
        assert action == AuditAction.QUIZ_UPDATED

    def test_quiz_delete(self):
        action = self.handler.determine_action("DELETE", "/quiz/123", 200)
        assert action == AuditAction.QUIZ_DELETED


class TestCertificateActionHandler:
    """Test certificate-related audit actions."""

    def setup_method(self):
        self.handler = CertificateActionHandler()

    def test_can_handle_certificate_paths(self):
        assert self.handler.can_handle("/api/v1/certificates") is True
        assert self.handler.can_handle("/certificate/123") is True
        assert self.handler.can_handle("/api/v1/projects") is False

    def test_certificate_download(self):
        action = self.handler.determine_action("GET", "/certificates/123/download", 200)
        assert action == AuditAction.CERTIFICATE_DOWNLOADED

    def test_certificate_download_pdf(self):
        action = self.handler.determine_action("GET", "/certificates/123/pdf", 200)
        assert action == AuditAction.CERTIFICATE_DOWNLOADED

    def test_certificate_create(self):
        action = self.handler.determine_action("POST", "/certificates", 201)
        assert action == AuditAction.CERTIFICATE_ISSUED

    def test_certificate_view(self):
        action = self.handler.determine_action("GET", "/certificates/123", 200)
        assert action == AuditAction.CERTIFICATE_VIEWED


class TestTournamentActionHandler:
    """Test tournament-related audit actions (added in P0)."""

    def setup_method(self):
        self.handler = TournamentActionHandler()

    def test_can_handle_tournament_paths(self):
        assert self.handler.can_handle("/api/v1/tournaments") is True
        assert self.handler.can_handle("/tournaments/123") is True
        assert self.handler.can_handle("/api/v1/projects") is False

    def test_tournament_enroll(self):
        action = self.handler.determine_action("POST", "/tournaments/123/enroll", 200)
        assert action == AuditAction.TOURNAMENT_ENROLLED

    def test_tournament_unenroll(self):
        action = self.handler.determine_action("DELETE", "/tournaments/123/enroll", 200)
        assert action == AuditAction.TOURNAMENT_UNENROLLED

    def test_tournament_fallback(self):
        """Future tournament CRUD operations fall back to generic format."""
        action = self.handler.determine_action("POST", "/tournaments", 201)
        assert action == "POST_/tournaments"


class TestJugglingActionHandler:
    """Test juggling annotation audit actions (AN-3B2A addition)."""

    _VIDEO  = "319eb833-f716-4f4e-8c93-b823c3781088"
    _EVENT  = "4d866695-e292-4819-b020-1d654ce11ee4"

    def setup_method(self):
        self.handler = JugglingActionHandler()

    def test_can_handle_juggling_paths(self):
        assert self.handler.can_handle(f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts") is True
        assert self.handler.can_handle(f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts/{self._EVENT}/pose-snapshot") is True
        assert self.handler.can_handle("/api/v1/projects") is False
        assert self.handler.can_handle("/api/v1/licenses") is False

    def test_contact_created(self):
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts"
        assert self.handler.determine_action("POST", path, 201) == AuditAction.JUGGLING_CONTACT_CREATED

    def test_contact_updated_patch(self):
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts/{self._EVENT}"
        assert self.handler.determine_action("PATCH", path, 200) == AuditAction.JUGGLING_CONTACT_UPDATED

    def test_contact_updated_put(self):
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts/{self._EVENT}"
        assert self.handler.determine_action("PUT", path, 200) == AuditAction.JUGGLING_CONTACT_UPDATED

    def test_contact_soft_deleted(self):
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts/{self._EVENT}"
        assert self.handler.determine_action("DELETE", path, 200) == AuditAction.JUGGLING_CONTACT_SOFT_DELETED

    def test_annotation_finished(self):
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/finish"
        assert self.handler.determine_action("POST", path, 200) == AuditAction.JUGGLING_ANNOTATION_FINISHED

    def test_pose_snapshot_created_201(self):
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts/{self._EVENT}/pose-snapshot"
        assert self.handler.determine_action("POST", path, 201) == AuditAction.JUGGLING_POSE_SNAPSHOT_CREATED

    def test_pose_snapshot_updated_200(self):
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts/{self._EVENT}/pose-snapshot"
        assert self.handler.determine_action("POST", path, 200) == AuditAction.JUGGLING_POSE_SNAPSHOT_UPDATED

    def test_pose_snapshot_action_fits_varchar_255(self):
        """Regression: action string must fit VARCHAR(255) — was causing StringDataRightTruncation."""
        path = f"/api/v1/users/me/juggling/videos/{self._VIDEO}/contacts/{self._EVENT}/pose-snapshot"
        action = self.handler.determine_action("POST", path, 201)
        assert len(action) <= 255, f"action too long for VARCHAR(255): {len(action)} chars"
        # Prove the OLD fallback would have exceeded VARCHAR(100)
        old_fallback = f"POST_{path}"
        assert len(old_fallback) > 100, "test assumption: old fallback was indeed > 100 chars"


class TestDefaultActionHandler:
    """Test fallback handler for unrecognized paths."""

    def setup_method(self):
        self.handler = DefaultActionHandler()

    def test_can_handle_any_path(self):
        assert self.handler.can_handle("/any/path") is True
        assert self.handler.can_handle("/unknown/endpoint") is True

    def test_generic_action_format(self):
        action = self.handler.determine_action("GET", "/api/v1/unknown", 200)
        assert action == "GET_/api/v1/unknown"

    def test_preserves_method_and_path(self):
        action = self.handler.determine_action("POST", "/custom/endpoint", 201)
        assert action == "POST_/custom/endpoint"


class TestActionDeterminer:
    """Integration tests for ActionDeterminer dispatcher."""

    def setup_method(self):
        self.determiner = ActionDeterminer()

    def test_auth_path_routed_to_auth_handler(self):
        request = create_mock_request("POST", "/api/v1/auth/login")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.LOGIN

    def test_license_path_routed_to_license_handler(self):
        request = create_mock_request("GET", "/api/v1/licenses/123/pdf")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.LICENSE_DOWNLOADED

    def test_project_path_routed_to_project_handler(self):
        request = create_mock_request("POST", "/api/v1/projects/123/enroll")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.PROJECT_ENROLLED

    def test_quiz_path_routed_to_quiz_handler(self):
        request = create_mock_request("POST", "/api/v1/quiz/123/start")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.QUIZ_STARTED

    def test_certificate_path_routed_to_certificate_handler(self):
        request = create_mock_request("GET", "/api/v1/certificates/123/download")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.CERTIFICATE_DOWNLOADED

    def test_tournament_path_routed_to_tournament_handler(self):
        request = create_mock_request("POST", "/api/v1/tournaments/123/enroll")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.TOURNAMENT_ENROLLED

    def test_juggling_contact_path_routed_to_juggling_handler(self):
        path = "/api/v1/users/me/juggling/videos/319eb833-f716-4f4e-8c93-b823c3781088/contacts"
        request = create_mock_request("POST", path)
        response = create_mock_response(201)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.JUGGLING_CONTACT_CREATED

    def test_juggling_pose_snapshot_path_routed_to_juggling_handler(self):
        """Regression: pose-snapshot path previously hit DefaultActionHandler → VARCHAR(100) overflow."""
        path = "/api/v1/users/me/juggling/videos/319eb833-f716-4f4e-8c93-b823c3781088/contacts/4d866695-e292-4819-b020-1d654ce11ee4/pose-snapshot"
        request = create_mock_request("POST", path)
        response = create_mock_response(201)
        action = self.determiner.determine_action(request, response)
        assert action == AuditAction.JUGGLING_POSE_SNAPSHOT_CREATED
        assert len(action) <= 100  # fits even the old VARCHAR(100) now

    def test_unknown_path_routed_to_default_handler(self):
        request = create_mock_request("GET", "/api/v1/unknown/endpoint")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        assert action == "GET_/api/v1/unknown/endpoint"

    def test_handler_priority_order_matters(self):
        """
        Ensure handlers are checked in order.
        If a path matches multiple handlers, the first one wins.
        """
        request = create_mock_request("GET", "/api/v1/licenses/123")
        response = create_mock_response(200)
        action = self.determiner.determine_action(request, response)
        # Should match LicenseActionHandler, not DefaultActionHandler
        assert action == AuditAction.LICENSE_VIEWED

    def test_status_code_affects_auth_actions(self):
        """Test that status code is properly propagated to handlers."""
        # Success
        request_success = create_mock_request("POST", "/api/v1/auth/login")
        response_success = create_mock_response(200)
        action_success = self.determiner.determine_action(request_success, response_success)
        assert action_success == AuditAction.LOGIN

        # Failure
        request_fail = create_mock_request("POST", "/api/v1/auth/login")
        response_fail = create_mock_response(401)
        action_fail = self.determiner.determine_action(request_fail, response_fail)
        assert action_fail == AuditAction.LOGIN_FAILED


# Parametrized tests for comprehensive coverage
@pytest.mark.parametrize("method,path,status_code,expected_action", [
    # Authentication
    ("POST", "/api/v1/auth/login", 200, AuditAction.LOGIN),
    ("POST", "/api/v1/auth/login", 401, AuditAction.LOGIN_FAILED),
    ("POST", "/api/v1/auth/logout", 200, AuditAction.LOGOUT),
    ("POST", "/auth/change-password", 200, AuditAction.PASSWORD_CHANGE),

    # Licenses
    ("GET", "/licenses/123/pdf", 200, AuditAction.LICENSE_DOWNLOADED),
    ("POST", "/licenses/verify", 200, AuditAction.LICENSE_VERIFIED),
    ("POST", "/licenses/upgrade", 200, AuditAction.LICENSE_UPGRADE_REQUESTED),
    ("POST", "/licenses/upgrade/approve", 200, AuditAction.LICENSE_UPGRADE_APPROVED),
    ("POST", "/licenses/upgrade/reject", 200, AuditAction.LICENSE_UPGRADE_REJECTED),
    ("POST", "/licenses", 201, AuditAction.LICENSE_ISSUED),
    ("GET", "/licenses/123", 200, AuditAction.LICENSE_VIEWED),
    ("DELETE", "/licenses/123", 200, AuditAction.LICENSE_REVOKED),

    # Projects
    ("POST", "/projects/123/enroll", 200, AuditAction.PROJECT_ENROLLED),
    ("DELETE", "/projects/123/enroll", 200, AuditAction.PROJECT_UNENROLLED),
    ("POST", "/projects", 201, AuditAction.PROJECT_CREATED),
    ("PUT", "/projects/123", 200, AuditAction.PROJECT_UPDATED),
    ("PATCH", "/projects/123", 200, AuditAction.PROJECT_UPDATED),
    ("DELETE", "/projects/123", 200, AuditAction.PROJECT_DELETED),

    # Quizzes
    ("POST", "/quiz/123/start", 200, AuditAction.QUIZ_STARTED),
    ("POST", "/quiz/123/submit", 200, AuditAction.QUIZ_SUBMITTED),
    ("POST", "/quiz", 201, AuditAction.QUIZ_CREATED),
    ("PUT", "/quiz/123", 200, AuditAction.QUIZ_UPDATED),
    ("PATCH", "/quiz/123", 200, AuditAction.QUIZ_UPDATED),
    ("DELETE", "/quiz/123", 200, AuditAction.QUIZ_DELETED),

    # Certificates
    ("GET", "/certificates/123/download", 200, AuditAction.CERTIFICATE_DOWNLOADED),
    ("POST", "/certificates", 201, AuditAction.CERTIFICATE_ISSUED),
    ("GET", "/certificates/123", 200, AuditAction.CERTIFICATE_VIEWED),

    # Tournaments (P0 addition)
    ("POST", "/tournaments/123/enroll", 200, AuditAction.TOURNAMENT_ENROLLED),
    ("DELETE", "/tournaments/123/enroll", 200, AuditAction.TOURNAMENT_UNENROLLED),

    # Juggling — contacts
    ("POST", "/api/v1/users/me/juggling/videos/vid-uuid/contacts", 201, AuditAction.JUGGLING_CONTACT_CREATED),
    ("PATCH", "/api/v1/users/me/juggling/videos/vid-uuid/contacts/evt-uuid", 200, AuditAction.JUGGLING_CONTACT_UPDATED),
    ("DELETE", "/api/v1/users/me/juggling/videos/vid-uuid/contacts/evt-uuid", 200, AuditAction.JUGGLING_CONTACT_SOFT_DELETED),
    ("POST", "/api/v1/users/me/juggling/videos/vid-uuid/finish", 200, AuditAction.JUGGLING_ANNOTATION_FINISHED),
    # Juggling — pose snapshot (201 = created, 200 = upsert)
    ("POST", "/api/v1/users/me/juggling/videos/319eb833-f716-4f4e-8c93-b823c3781088/contacts/4d866695-e292-4819-b020-1d654ce11ee4/pose-snapshot", 201, AuditAction.JUGGLING_POSE_SNAPSHOT_CREATED),
    ("POST", "/api/v1/users/me/juggling/videos/319eb833-f716-4f4e-8c93-b823c3781088/contacts/4d866695-e292-4819-b020-1d654ce11ee4/pose-snapshot", 200, AuditAction.JUGGLING_POSE_SNAPSHOT_UPDATED),

    # Fallback
    ("GET", "/unknown/endpoint", 200, "GET_/unknown/endpoint"),
])
def test_action_determiner_comprehensive(method, path, status_code, expected_action):
    """
    Comprehensive parametrized test covering all 34+ decision paths.

    Ensures behavior is identical to original _determine_action() method.
    """
    determiner = ActionDeterminer()
    request = create_mock_request(method, path)
    response = create_mock_response(status_code)
    action = determiner.determine_action(request, response)
    assert action == expected_action

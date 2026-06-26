"""Unit tests for app/services/action_determiner.py

Sprint P9 — Coverage target: ≥95% stmt+branch

Covers every handler's can_handle() + determine_action() branch,
and the ActionDeterminer dispatcher chain.

No DB mocks needed — pure Strategy Pattern with string inputs.
"""

from unittest.mock import MagicMock

from app.models.audit_log import AuditAction
from app.services.action_determiner import (
    ActionDeterminer,
    AuthActionHandler,
    CertificateActionHandler,
    DefaultActionHandler,
    LicenseActionHandler,
    MultiCameraActionHandler,
    ProjectActionHandler,
    QuizActionHandler,
    TournamentActionHandler,
)


def _req(method: str, path: str) -> MagicMock:
    req = MagicMock()
    req.method = method
    req.url.path = path
    return req


def _resp(status: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    return r


# ===========================================================================
# TestAuthActionHandler
# ===========================================================================

class TestAuthActionHandler:
    h = AuthActionHandler()

    def test_can_handle_auth_path(self):
        assert self.h.can_handle("/api/v1/auth/login") is True

    def test_cannot_handle_non_auth_path(self):
        assert self.h.can_handle("/api/v1/licenses") is False

    def test_login_success(self):
        assert self.h.determine_action("POST", "/api/v1/auth/login", 200) == AuditAction.LOGIN

    def test_login_failed_non_200(self):
        result = self.h.determine_action("POST", "/api/v1/auth/login", 401)
        assert result == AuditAction.LOGIN_FAILED

    def test_logout(self):
        assert self.h.determine_action("POST", "/api/v1/auth/logout", 200) == AuditAction.LOGOUT

    def test_change_password(self):
        result = self.h.determine_action("PUT", "/api/v1/auth/change-password", 200)
        assert result == AuditAction.PASSWORD_CHANGE

    def test_password_shorthand(self):
        result = self.h.determine_action("PUT", "/api/v1/auth/password", 200)
        assert result == AuditAction.PASSWORD_CHANGE

    def test_unknown_auth_path_returns_method_path(self):
        result = self.h.determine_action("GET", "/api/v1/auth/unknown", 200)
        assert result == "GET_/api/v1/auth/unknown"


# ===========================================================================
# TestLicenseActionHandler
# ===========================================================================

class TestLicenseActionHandler:
    h = LicenseActionHandler()

    def test_can_handle_licenses_path(self):
        assert self.h.can_handle("/api/v1/licenses") is True

    def test_cannot_handle_other_path(self):
        assert self.h.can_handle("/api/v1/auth/login") is False

    def test_pdf_download(self):
        result = self.h.determine_action("GET", "/api/v1/licenses/1/pdf", 200)
        assert result == AuditAction.LICENSE_DOWNLOADED

    def test_download_keyword(self):
        result = self.h.determine_action("GET", "/api/v1/licenses/1/download", 200)
        assert result == AuditAction.LICENSE_DOWNLOADED

    def test_verify(self):
        result = self.h.determine_action("GET", "/api/v1/licenses/verify", 200)
        assert result == AuditAction.LICENSE_VERIFIED

    def test_upgrade_approve(self):
        result = self.h.determine_action("POST", "/api/v1/licenses/upgrade/approve", 200)
        assert result == AuditAction.LICENSE_UPGRADE_APPROVED

    def test_upgrade_reject(self):
        result = self.h.determine_action("POST", "/api/v1/licenses/upgrade/reject", 200)
        assert result == AuditAction.LICENSE_UPGRADE_REJECTED

    def test_upgrade_base(self):
        result = self.h.determine_action("POST", "/api/v1/licenses/upgrade", 200)
        assert result == AuditAction.LICENSE_UPGRADE_REQUESTED

    def test_post_crud(self):
        result = self.h.determine_action("POST", "/api/v1/licenses", 201)
        assert result == AuditAction.LICENSE_ISSUED

    def test_get_crud(self):
        result = self.h.determine_action("GET", "/api/v1/licenses/1", 200)
        assert result == AuditAction.LICENSE_VIEWED

    def test_delete_crud(self):
        result = self.h.determine_action("DELETE", "/api/v1/licenses/1", 200)
        assert result == AuditAction.LICENSE_REVOKED

    def test_unknown_method_returns_method_path(self):
        result = self.h.determine_action("PATCH", "/api/v1/licenses/1", 200)
        assert result == "PATCH_/api/v1/licenses/1"


# ===========================================================================
# TestProjectActionHandler
# ===========================================================================

class TestProjectActionHandler:
    h = ProjectActionHandler()

    def test_can_handle_projects_path(self):
        assert self.h.can_handle("/api/v1/projects") is True

    def test_cannot_handle_other_path(self):
        assert self.h.can_handle("/api/v1/quiz") is False

    def test_enroll_post(self):
        result = self.h.determine_action("POST", "/api/v1/projects/1/enroll", 201)
        assert result == AuditAction.PROJECT_ENROLLED

    def test_enroll_delete(self):
        result = self.h.determine_action("DELETE", "/api/v1/projects/1/enroll", 200)
        assert result == AuditAction.PROJECT_UNENROLLED

    def test_post_crud(self):
        result = self.h.determine_action("POST", "/api/v1/projects", 201)
        assert result == AuditAction.PROJECT_CREATED

    def test_put_crud(self):
        result = self.h.determine_action("PUT", "/api/v1/projects/1", 200)
        assert result == AuditAction.PROJECT_UPDATED

    def test_patch_crud(self):
        result = self.h.determine_action("PATCH", "/api/v1/projects/1", 200)
        assert result == AuditAction.PROJECT_UPDATED

    def test_delete_crud(self):
        result = self.h.determine_action("DELETE", "/api/v1/projects/1", 200)
        assert result == AuditAction.PROJECT_DELETED

    def test_get_returns_method_path(self):
        result = self.h.determine_action("GET", "/api/v1/projects/1", 200)
        assert result == "GET_/api/v1/projects/1"


# ===========================================================================
# TestQuizActionHandler
# ===========================================================================

class TestQuizActionHandler:
    h = QuizActionHandler()

    def test_can_handle_quiz_path(self):
        assert self.h.can_handle("/api/v1/quiz/1") is True

    def test_can_handle_quizzes_path(self):
        assert self.h.can_handle("/api/v1/quizzes") is True

    def test_cannot_handle_other_path(self):
        assert self.h.can_handle("/api/v1/projects") is False

    def test_start(self):
        result = self.h.determine_action("POST", "/api/v1/quiz/1/start", 200)
        assert result == AuditAction.QUIZ_STARTED

    def test_submit(self):
        result = self.h.determine_action("POST", "/api/v1/quiz/1/submit", 200)
        assert result == AuditAction.QUIZ_SUBMITTED

    def test_post_crud(self):
        result = self.h.determine_action("POST", "/api/v1/quiz", 201)
        assert result == AuditAction.QUIZ_CREATED

    def test_put_crud(self):
        result = self.h.determine_action("PUT", "/api/v1/quiz/1", 200)
        assert result == AuditAction.QUIZ_UPDATED

    def test_patch_crud(self):
        result = self.h.determine_action("PATCH", "/api/v1/quiz/1", 200)
        assert result == AuditAction.QUIZ_UPDATED

    def test_delete_crud(self):
        result = self.h.determine_action("DELETE", "/api/v1/quiz/1", 200)
        assert result == AuditAction.QUIZ_DELETED

    def test_get_returns_method_path(self):
        result = self.h.determine_action("GET", "/api/v1/quiz/1", 200)
        assert result == "GET_/api/v1/quiz/1"


# ===========================================================================
# TestCertificateActionHandler
# ===========================================================================

class TestCertificateActionHandler:
    h = CertificateActionHandler()

    def test_can_handle_certificate_path(self):
        assert self.h.can_handle("/api/v1/certificates") is True

    def test_cannot_handle_other_path(self):
        assert self.h.can_handle("/api/v1/auth/login") is False

    def test_download(self):
        result = self.h.determine_action("GET", "/api/v1/certificates/1/download", 200)
        assert result == AuditAction.CERTIFICATE_DOWNLOADED

    def test_pdf(self):
        result = self.h.determine_action("GET", "/api/v1/certificates/1/pdf", 200)
        assert result == AuditAction.CERTIFICATE_DOWNLOADED

    def test_post_issued(self):
        result = self.h.determine_action("POST", "/api/v1/certificates", 201)
        assert result == AuditAction.CERTIFICATE_ISSUED

    def test_get_viewed(self):
        result = self.h.determine_action("GET", "/api/v1/certificates/1", 200)
        assert result == AuditAction.CERTIFICATE_VIEWED

    def test_other_method_returns_method_path(self):
        result = self.h.determine_action("DELETE", "/api/v1/certificates/1", 200)
        assert result == "DELETE_/api/v1/certificates/1"


# ===========================================================================
# TestTournamentActionHandler
# ===========================================================================

class TestTournamentActionHandler:
    h = TournamentActionHandler()

    def test_can_handle_tournaments_path(self):
        assert self.h.can_handle("/api/v1/tournaments") is True

    def test_cannot_handle_other_path(self):
        assert self.h.can_handle("/api/v1/auth/login") is False

    def test_enroll_post(self):
        result = self.h.determine_action("POST", "/api/v1/tournaments/1/enroll", 201)
        assert result == AuditAction.TOURNAMENT_ENROLLED

    def test_enroll_delete(self):
        result = self.h.determine_action("DELETE", "/api/v1/tournaments/1/enroll", 200)
        assert result == AuditAction.TOURNAMENT_UNENROLLED

    def test_non_enroll_returns_method_path(self):
        result = self.h.determine_action("GET", "/api/v1/tournaments/1", 200)
        assert result == "GET_/api/v1/tournaments/1"


# ===========================================================================
# TestDefaultActionHandler
# ===========================================================================

class TestDefaultActionHandler:
    h = DefaultActionHandler()

    def test_can_handle_any_path(self):
        assert self.h.can_handle("/any/path") is True
        assert self.h.can_handle("") is True

    def test_returns_method_path(self):
        result = self.h.determine_action("GET", "/api/v1/unknown", 200)
        assert result == "GET_/api/v1/unknown"

    def test_returns_method_path_for_post(self):
        result = self.h.determine_action("POST", "/api/v1/other", 201)
        assert result == "POST_/api/v1/other"


# ===========================================================================
# TestActionDeterminer (dispatcher)
# ===========================================================================

class TestActionDeterminer:
    d = ActionDeterminer()

    def test_routes_to_auth_handler(self):
        result = self.d.determine_action(_req("POST", "/api/v1/auth/login"), _resp(200))
        assert result == AuditAction.LOGIN

    def test_routes_to_license_handler(self):
        result = self.d.determine_action(_req("GET", "/api/v1/licenses/1"), _resp(200))
        assert result == AuditAction.LICENSE_VIEWED

    def test_routes_to_project_handler(self):
        result = self.d.determine_action(_req("POST", "/api/v1/projects"), _resp(201))
        assert result == AuditAction.PROJECT_CREATED

    def test_routes_to_quiz_handler(self):
        result = self.d.determine_action(_req("POST", "/api/v1/quiz/1/start"), _resp(200))
        assert result == AuditAction.QUIZ_STARTED

    def test_routes_to_certificate_handler(self):
        result = self.d.determine_action(_req("GET", "/api/v1/certificates/1"), _resp(200))
        assert result == AuditAction.CERTIFICATE_VIEWED

    def test_routes_to_tournament_handler(self):
        result = self.d.determine_action(_req("POST", "/api/v1/tournaments/1/enroll"), _resp(201))
        assert result == AuditAction.TOURNAMENT_ENROLLED

    def test_routes_to_default_handler_for_unknown_path(self):
        result = self.d.determine_action(_req("GET", "/api/v1/unknown/resource"), _resp(200))
        assert result == "GET_/api/v1/unknown/resource"

    def test_handler_chain_order_auth_before_default(self):
        # Auth path must not fall through to default
        result = self.d.determine_action(_req("POST", "/api/v1/auth/logout"), _resp(200))
        assert result == AuditAction.LOGOUT
        assert result != "POST_/api/v1/auth/logout"

    def test_login_failed_on_401(self):
        result = self.d.determine_action(_req("POST", "/api/v1/auth/login"), _resp(401))
        assert result == AuditAction.LOGIN_FAILED

    def test_determiner_has_eight_handlers(self):
        assert len(self.d.handlers) == 9

    def test_last_handler_is_default(self):
        assert isinstance(self.d.handlers[-1], DefaultActionHandler)


class TestMultiCameraActionHandler:
    h = MultiCameraActionHandler()

    def test_can_handle_multicamera_path(self):
        assert self.h.can_handle("/api/v1/multicamera/sessions/abc/cycles/1")

    def test_cannot_handle_unrelated_path(self):
        assert not self.h.can_handle("/api/v1/auth/login")

    def test_confirm_start(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/cycles/1/devices/2/confirm-start", 200)
        assert r == AuditAction.MC_CYCLE_CONFIRM_START

    def test_confirm_stop(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/cycles/1/devices/2/confirm-stop", 200)
        assert r == AuditAction.MC_CYCLE_CONFIRM_STOP

    def test_schedule(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/cycles/1/schedule", 200)
        assert r == AuditAction.MC_CYCLE_SCHEDULED

    def test_stop(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/cycles/1/stop", 200)
        assert r == AuditAction.MC_CYCLE_STOPPED

    def test_join(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/join", 200)
        assert r == AuditAction.MC_SESSION_JOINED

    def test_activate(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/activate", 200)
        assert r == AuditAction.MC_SESSION_ACTIVATED

    def test_create_session(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions", 201)
        assert r == AuditAction.MC_SESSION_CREATED

    def test_get_session(self):
        r = self.h.determine_action("GET", "/api/v1/multicamera/sessions/u", 200)
        assert r == AuditAction.MC_SESSION_QUERIED

    def test_register_device(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/devices", 201)
        assert r == AuditAction.MC_DEVICE_REGISTERED

    def test_patch_device_status(self):
        r = self.h.determine_action("PATCH", "/api/v1/multicamera/sessions/u/devices/1/status", 200)
        assert r == AuditAction.MC_DEVICE_STATUS_UPDATED

    def test_heartbeat(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/devices/1/heartbeat", 200)
        assert r == AuditAction.MC_DEVICE_HEARTBEAT

    def test_create_cycle(self):
        r = self.h.determine_action("POST", "/api/v1/multicamera/sessions/u/cycles", 201)
        assert r == AuditAction.MC_CYCLE_CREATED

    def test_list_cycles(self):
        r = self.h.determine_action("GET", "/api/v1/multicamera/sessions/u/cycles", 200)
        assert r == AuditAction.MC_CYCLE_QUERIED

    def test_all_action_constants_within_varchar100(self):
        from app.models.audit_log import AuditAction as A
        mc_actions = [
            A.MC_SESSION_CREATED, A.MC_SESSION_JOINED, A.MC_SESSION_ACTIVATED,
            A.MC_SESSION_QUERIED, A.MC_DEVICE_REGISTERED, A.MC_DEVICE_STATUS_UPDATED,
            A.MC_DEVICE_HEARTBEAT, A.MC_CYCLE_CREATED, A.MC_CYCLE_SCHEDULED,
            A.MC_CYCLE_STOPPED, A.MC_CYCLE_CONFIRM_START, A.MC_CYCLE_CONFIRM_STOP,
            A.MC_CYCLE_QUERIED, A.MC_SYSTEM_TIME_QUERIED,
        ]
        for action in mc_actions:
            assert len(action) <= 100, f"Action too long for VARCHAR(100): {action} ({len(action)} chars)"

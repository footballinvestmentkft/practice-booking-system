"""
ActionDeterminer Service

Responsibility:
    Determines the appropriate audit action type from HTTP request/response data.
    Uses a Strategy Pattern with handler chain to map request paths and methods
    to standardized AuditAction constants.

Architecture:
    - Protocol-based interface (ActionHandler) for extensibility
    - 7 specialized handlers for different resource types
    - Chain of responsibility pattern for path matching
    - Replaces the monolithic _determine_action() method in AuditMiddleware

Complexity Target: B (6-10) - orchestration only
"""

from typing import Protocol, List
from fastapi import Request, Response
from app.models.audit_log import AuditAction


class ActionHandler(Protocol):
    """
    Protocol for action determination handlers.

    Each handler is responsible for a specific resource type
    (e.g., authentication, licenses, projects) and determines
    the appropriate audit action from request data.
    """

    def can_handle(self, path: str) -> bool:
        """
        Check if this handler can process the given path.

        Args:
            path: HTTP request path (e.g., "/api/v1/auth/login")

        Returns:
            True if this handler should process this path
        """
        ...

    def determine_action(
        self,
        method: str,
        path: str,
        status_code: int
    ) -> str:
        """
        Determine the audit action for this request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: HTTP request path
            status_code: HTTP response status code

        Returns:
            AuditAction constant (e.g., "LOGIN", "PROJECT_ENROLLED")
        """
        ...


class AuthActionHandler:
    """
    Handles authentication-related audit actions.

    Paths: /auth/*
    Actions: LOGIN, LOGIN_FAILED, LOGOUT, PASSWORD_CHANGE
    """

    def can_handle(self, path: str) -> bool:
        return "/auth/" in path

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        if "/login" in path:
            return AuditAction.LOGIN if status_code == 200 else AuditAction.LOGIN_FAILED
        if "/logout" in path:
            return AuditAction.LOGOUT
        if "/change-password" in path or "/password" in path:
            return AuditAction.PASSWORD_CHANGE
        return f"{method}_{path}"


class LicenseActionHandler:
    """
    Handles license-related audit actions.

    Paths: /licenses/*
    Actions: LICENSE_ISSUED, LICENSE_VIEWED, LICENSE_DOWNLOADED,
             LICENSE_UPGRADE_REQUESTED, LICENSE_UPGRADE_APPROVED,
             LICENSE_UPGRADE_REJECTED, LICENSE_VERIFIED, LICENSE_REVOKED
    """

    def can_handle(self, path: str) -> bool:
        return "/licenses" in path

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        # Download actions
        if "pdf" in path or "download" in path:
            return AuditAction.LICENSE_DOWNLOADED

        # Verification
        if "verify" in path:
            return AuditAction.LICENSE_VERIFIED

        # Upgrade workflow (3-level nesting preserved from original)
        if "upgrade" in path:
            if "approve" in path:
                return AuditAction.LICENSE_UPGRADE_APPROVED
            if "reject" in path:
                return AuditAction.LICENSE_UPGRADE_REJECTED
            return AuditAction.LICENSE_UPGRADE_REQUESTED

        # CRUD operations
        if method == "POST":
            return AuditAction.LICENSE_ISSUED
        if method == "GET":
            return AuditAction.LICENSE_VIEWED
        if method == "DELETE":
            return AuditAction.LICENSE_REVOKED

        return f"{method}_{path}"


class ProjectActionHandler:
    """
    Handles project-related audit actions.

    Paths: /projects/*
    Actions: PROJECT_CREATED, PROJECT_UPDATED, PROJECT_DELETED,
             PROJECT_ENROLLED, PROJECT_UNENROLLED
    """

    def can_handle(self, path: str) -> bool:
        return "/projects" in path

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        # Enrollment actions
        if "/enroll" in path:
            if method == "POST":
                return AuditAction.PROJECT_ENROLLED
            elif method == "DELETE":
                return AuditAction.PROJECT_UNENROLLED

        # CRUD operations
        if method == "POST":
            return AuditAction.PROJECT_CREATED
        if method == "PUT" or method == "PATCH":
            return AuditAction.PROJECT_UPDATED
        if method == "DELETE":
            return AuditAction.PROJECT_DELETED

        return f"{method}_{path}"


class QuizActionHandler:
    """
    Handles quiz-related audit actions.

    Paths: /quiz/*, /quizzes/*
    Actions: QUIZ_STARTED, QUIZ_SUBMITTED,
             QUIZ_CREATED, QUIZ_UPDATED, QUIZ_DELETED
    """

    def can_handle(self, path: str) -> bool:
        return "/quiz" in path

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        # Quiz attempt actions
        if "/start" in path:
            return AuditAction.QUIZ_STARTED
        if "/submit" in path:
            return AuditAction.QUIZ_SUBMITTED

        # CRUD operations
        if method == "POST":
            return AuditAction.QUIZ_CREATED
        if method == "PUT" or method == "PATCH":
            return AuditAction.QUIZ_UPDATED
        if method == "DELETE":
            return AuditAction.QUIZ_DELETED

        return f"{method}_{path}"


class CertificateActionHandler:
    """
    Handles certificate-related audit actions.

    Paths: /certificates/*
    Actions: CERTIFICATE_ISSUED, CERTIFICATE_VIEWED, CERTIFICATE_DOWNLOADED
    """

    def can_handle(self, path: str) -> bool:
        return "/certificate" in path

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        # Download action
        if "download" in path or "pdf" in path:
            return AuditAction.CERTIFICATE_DOWNLOADED

        # CRUD operations
        if method == "POST":
            return AuditAction.CERTIFICATE_ISSUED
        if method == "GET":
            return AuditAction.CERTIFICATE_VIEWED

        return f"{method}_{path}"


class TournamentActionHandler:
    """
    Handles tournament-related audit actions.

    Paths: /tournaments/*
    Actions: TOURNAMENT_ENROLLED, TOURNAMENT_UNENROLLED

    Note: Added in P0 to close critical audit gap.
    """

    def can_handle(self, path: str) -> bool:
        return "/tournaments" in path

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        # Enrollment actions
        if "/enroll" in path:
            if method == "POST":
                return AuditAction.TOURNAMENT_ENROLLED
            elif method == "DELETE":
                return AuditAction.TOURNAMENT_UNENROLLED

        # Future: TOURNAMENT_CREATED, TOURNAMENT_UPDATED, etc.
        return f"{method}_{path}"


class JugglingActionHandler:
    """
    Handles juggling annotation audit actions.

    Paths: /juggling/*
    Actions: JUGGLING_CONTACT_CREATED, JUGGLING_CONTACT_UPDATED,
             JUGGLING_CONTACT_SOFT_DELETED, JUGGLING_ANNOTATION_FINISHED,
             JUGGLING_POSE_SNAPSHOT_CREATED, JUGGLING_POSE_SNAPSHOT_UPDATED
    """

    def can_handle(self, path: str) -> bool:
        return "/juggling/" in path

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        if "/pose-snapshot" in path:
            return (
                AuditAction.JUGGLING_POSE_SNAPSHOT_CREATED
                if status_code == 201
                else AuditAction.JUGGLING_POSE_SNAPSHOT_UPDATED
            )
        if "/finish" in path:
            return AuditAction.JUGGLING_ANNOTATION_FINISHED
        if method == "POST":
            return AuditAction.JUGGLING_CONTACT_CREATED
        if method in ("PUT", "PATCH"):
            return AuditAction.JUGGLING_CONTACT_UPDATED
        if method == "DELETE":
            return AuditAction.JUGGLING_CONTACT_SOFT_DELETED
        return f"{method}_{path}"


class DefaultActionHandler:
    """
    Fallback handler for unrecognized paths.

    Returns: "{METHOD}_{path}" format

    Note: Should always be last in handler chain.
    """

    def can_handle(self, path: str) -> bool:
        return True  # Always matches (fallback)

    def determine_action(self, method: str, path: str, status_code: int) -> str:
        return f"{method}_{path}"


class ActionDeterminer:
    """
    Main service class for determining audit actions.

    Uses a chain of responsibility pattern with specialized handlers
    to map HTTP requests to standardized audit action types.

    Usage:
        determiner = ActionDeterminer()
        action = determiner.determine_action(request, response)

    Complexity: B (8) - simple dispatcher logic
    """

    def __init__(self):
        """Initialize handler chain in priority order."""
        self.handlers: List[ActionHandler] = [
            AuthActionHandler(),
            LicenseActionHandler(),
            ProjectActionHandler(),
            QuizActionHandler(),
            CertificateActionHandler(),
            TournamentActionHandler(),
            JugglingActionHandler(),
            DefaultActionHandler(),  # Always last (fallback)
        ]

    def determine_action(self, request: Request, response: Response) -> str:
        """
        Determine audit action using handler chain.

        Iterates through handlers until one matches the request path,
        then delegates action determination to that handler.

        Args:
            request: FastAPI Request object
            response: FastAPI Response object

        Returns:
            AuditAction constant string (e.g., "LOGIN", "PROJECT_ENROLLED")
        """
        method = request.method
        path = request.url.path
        status_code = response.status_code

        for handler in self.handlers:
            if handler.can_handle(path):
                return handler.determine_action(method, path, status_code)

        # Should never reach here (DefaultActionHandler always matches)
        return f"{method}_{path}"

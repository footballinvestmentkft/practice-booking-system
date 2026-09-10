"""Fail-closed database target validation for test processes."""
from __future__ import annotations

from sqlalchemy.engine import make_url


class UnsafeTestDatabaseError(RuntimeError):
    """Raised before engine creation when a test target is not disposable."""


_SAFE_NAME_MARKERS = ("test", "disposable", "tmp", "ci")
_FORBIDDEN_NAME_MARKERS = ("prod", "production", "staging", "live")
_LOOPBACK_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}


def assert_safe_test_database_url(
    database_url: str,
    *,
    explicitly_configured: bool,
    disposable_confirmed: bool,
) -> None:
    """Reject implicit, production-like or unconfirmed remote test targets.

    This function does not connect. It is intentionally called before SQLAlchemy
    creates the application engine while a test process is importing the app.
    """
    if not explicitly_configured:
        raise UnsafeTestDatabaseError(
            "Test DATABASE_URL must be explicitly configured; application defaults are forbidden"
        )
    try:
        parsed = make_url(database_url)
    except Exception as exc:
        raise UnsafeTestDatabaseError("Test DATABASE_URL is invalid") from exc

    if not parsed.drivername.startswith("postgresql"):
        raise UnsafeTestDatabaseError("DB-backed tests require an explicit disposable PostgreSQL URL")

    database = (parsed.database or "").lower()
    if not database:
        raise UnsafeTestDatabaseError("Test DATABASE_URL must name a disposable database")
    if any(marker in database for marker in _FORBIDDEN_NAME_MARKERS):
        raise UnsafeTestDatabaseError("Production-like database name is forbidden in tests")
    if not any(marker in database for marker in _SAFE_NAME_MARKERS):
        raise UnsafeTestDatabaseError(
            "Test database name must contain test, disposable, tmp or ci"
        )
    if parsed.host not in _LOOPBACK_HOSTS and not disposable_confirmed:
        raise UnsafeTestDatabaseError(
            "Non-local test database requires explicit disposable confirmation"
        )

"""Framework core — re-exports most-used symbols for convenience."""
from .auth import AuthContext, login
from .client import make_client
from ._http import LifecycleError, PreflightError, require_ok

__all__ = [
    "AuthContext",
    "login",
    "make_client",
    "LifecycleError",
    "PreflightError",
    "require_ok",
]

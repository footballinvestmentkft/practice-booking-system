"""HTTP primitives — error types and the require_ok helper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class LifecycleError(Exception):
    """Raised when an HTTP step fails an assertion."""

    def __init__(self, step: str, http_code: int, detail: str) -> None:
        self.step = step
        self.http_code = http_code
        self.detail = detail
        super().__init__(f"[{step}] HTTP {http_code}: {detail}")


class PreflightError(Exception):
    """Raised when a preflight check fails before the scenario starts."""

    def __init__(self, check: str, reason: str) -> None:
        self.check = check
        self.reason = reason
        super().__init__(f"Preflight [{check}] failed: {reason}")


def require_ok(
    response: requests.Response,
    step: str,
    *,
    accepted: tuple[int, ...] = (200, 201),
) -> Any:
    """Assert HTTP status is acceptable and return parsed JSON.

    Raises LifecycleError with step context on failure.
    """
    if response.status_code not in accepted:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise LifecycleError(step, response.status_code, str(detail))
    try:
        return response.json()
    except Exception:
        return {}

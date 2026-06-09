"""
Biometric feature flag guard.

All biometric endpoints must depend on require_biometric_enabled().
When BIOMETRIC_FACE_MATCHING_ENABLED=false (default), every biometric
endpoint returns HTTP 503 — no data is accessed or written.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.config import settings


def is_biometric_enabled() -> bool:
    """Return True when the biometric feature flag is on."""
    return settings.BIOMETRIC_FACE_MATCHING_ENABLED


async def require_biometric_enabled() -> None:
    """
    FastAPI dependency — raises 503 when biometric feature is disabled.

    Usage::

        @router.post("/me/biometric-consent")
        async def grant_consent(
            _: None = Depends(require_biometric_enabled),
            ...
        ):
            ...
    """
    if not is_biometric_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Biometric face matching is not enabled on this server. "
                "Set BIOMETRIC_FACE_MATCHING_ENABLED=true after completing "
                "DPIA and obtaining legal approval."
            ),
        )

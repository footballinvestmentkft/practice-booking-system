"""
Juggling POC feature flag guard.

All juggling endpoints depend on require_juggling_enabled().
When JUGGLING_POC_ENABLED=False the endpoints return HTTP 503.
There are NO exceptions — every juggling endpoint uses this guard.
"""
from __future__ import annotations

from fastapi import HTTPException

from app.config import settings


def is_juggling_enabled() -> bool:
    return settings.JUGGLING_POC_ENABLED


async def require_juggling_enabled() -> None:
    """FastAPI dependency — raises 503 when juggling POC feature is disabled."""
    if not is_juggling_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "Juggling POC is not enabled on this server. "
                "Set JUGGLING_POC_ENABLED=true to activate the video intake pipeline."
            ),
        )
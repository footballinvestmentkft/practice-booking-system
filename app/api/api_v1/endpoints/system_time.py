"""MC1-BE-1 — Millisecond-precision server time for iOS clock sync.

Public endpoint (no auth). The iOS ClockSyncService uses RTT/2 offset
calculation against server_epoch_ms. This is app-level best-effort sync,
not NTP-grade precision. Wall-clock time may step backward on system
clock corrections (e.g. NTP adjustment); callers must not assume
monotonic ordering across calls.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Response
from pydantic import BaseModel

router = APIRouter()


class SystemTimeResponse(BaseModel):
    server_time_utc: str
    server_epoch_ms: int
    precision: str
    source: str


@router.get(
    "/time",
    response_model=SystemTimeResponse,
    summary="Server UTC time with millisecond precision",
)
def get_system_time(response: Response) -> SystemTimeResponse:
    epoch_ms = time.time_ns() // 1_000_000
    utc_now = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    iso = utc_now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_now.microsecond // 1000:03d}Z"

    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Server-Time-Ms"] = str(epoch_ms)

    return SystemTimeResponse(
        server_time_utc=iso,
        server_epoch_ms=epoch_ms,
        precision="milliseconds",
        source="backend_app_clock",
    )

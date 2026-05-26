"""
WebSocket endpoint for per-user challenge lifecycle events.

Endpoint: GET /ws/events?token=<JWT access token>

Auth: JWT Bearer in query param (same pattern as /ws/tournaments/{id}/live).
      Close code 4001 = invalid/expired token.
      Close code 4003 = user not found or inactive.

Each connected client receives events published to their personal Redis channel
``user:{user_id}:events`` by challenge services/routes.

Event envelope (JSON):
    { "type": "<event_type>", ...extra fields }

If Redis is unavailable the handler sends a single keepalive ping and exits
cleanly — the JS client falls back to polling in that case.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import verify_token
from app.core.redis_pubsub import subscribe_user_events
from app.core.ws_connection_manager import manager
from app.database import SessionLocal
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

_PING_INTERVAL = 30  # seconds between server-side keepalive pings


@router.get("/api/ws-token")
async def get_ws_token(request: Request) -> JSONResponse:
    """
    Return the raw JWT access token so ws_client.js can open /ws/events.

    Reads the HttpOnly access_token cookie server-side and returns the raw
    JWT string. Same-origin fetch only — no CORS exposure.
    The token is already the user's access credential; this endpoint changes
    nothing about the security posture (cookie is already trusted by all routes).
    Returns {"token": ""} when not logged in.
    """
    raw = request.cookies.get("access_token", "")
    token = raw[7:] if raw.startswith("Bearer ") else raw
    if not token:
        return JSONResponse({"token": ""})
    if verify_token(token, "access") is None:
        return JSONResponse({"token": ""})
    return JSONResponse({"token": token})


@router.websocket("/ws/events")
async def ws_user_events(websocket: WebSocket) -> None:
    """
    Personal event stream for a logged-in user.

    Publishes challenge lifecycle events (challenge_sent, challenge_accepted,
    challenge_declined, challenge_completed, challenge_forfeited,
    challenge_no_contest, challenge_card_phase_unlocked, notification_created).
    """
    token = websocket.query_params.get("token", "")

    # ── Auth ────────────────────────────────────────────────────────────────
    username = verify_token(token, "access")
    if username is None:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.email == username).first()
    finally:
        db.close()

    if user is None or not user.is_active:
        await websocket.close(code=4003, reason="Forbidden")
        return

    await websocket.accept()
    await manager.connect(user.id, websocket)
    logger.info("WS /ws/events connected user_id=%s", user.id)

    try:
        await _run_event_loop(websocket, user.id)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WS /ws/events error user_id=%s: %s", user.id, exc)
    finally:
        await manager.disconnect(user.id, websocket)
        logger.info("WS /ws/events disconnected user_id=%s", user.id)


async def _run_event_loop(websocket: WebSocket, user_id: int) -> None:
    """Drive the Redis subscribe + keepalive ping loops concurrently."""
    redis_task = asyncio.create_task(_redis_forward(websocket, user_id))
    ping_task = asyncio.create_task(_keepalive_ping(websocket))
    receive_task = asyncio.create_task(_drain_client_messages(websocket))

    done, pending = await asyncio.wait(
        [redis_task, ping_task, receive_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def _redis_forward(websocket: WebSocket, user_id: int) -> None:
    """Subscribe to user's Redis channel and forward every event to the WS."""
    redis_received = False
    async for raw in subscribe_user_events(user_id):
        redis_received = True
        try:
            await websocket.send_text(raw)
        except Exception:
            return

    if not redis_received:
        # Redis unavailable — tell client to fall back to polling
        try:
            await websocket.send_json({"type": "redis_unavailable"})
        except Exception:
            pass


async def _keepalive_ping(websocket: WebSocket) -> None:
    """Send a ping frame every _PING_INTERVAL seconds to keep the connection alive."""
    while True:
        await asyncio.sleep(_PING_INTERVAL)
        try:
            await websocket.send_json({"type": "ping"})
        except Exception:
            return


async def _drain_client_messages(websocket: WebSocket) -> None:
    """
    Consume (and discard) any messages the client sends.
    Returns when the client disconnects, which cancels the sibling tasks.
    """
    while True:
        try:
            await websocket.receive_text()
        except WebSocketDisconnect:
            return
        except Exception:
            return

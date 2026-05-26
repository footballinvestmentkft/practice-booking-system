"""Unit tests for GET /ws/events — per-user WebSocket event stream.

WS-01  Missing token → close 4001 before accept
WS-02  Invalid/expired token → close 4001 before accept
WS-03  Token for unknown user → close 4003 before accept
WS-04  Token for inactive user → close 4003 before accept
WS-05  Valid token + active user → 101 Upgrade (connection accepted)
WS-06  Redis unavailable → redis_unavailable frame sent, connection closed cleanly
WS-07  Redis delivers message → message forwarded verbatim to WS client
WS-08  Keepalive ping frame sent when no events arrive
WS-09  Client disconnect handled without exception
WS-10  ConnectionManager.connect() called on valid auth
WS-11  ConnectionManager.disconnect() called on close
WS-12  publish_challenge_event publishes to all provided user_ids
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

_BASE_WS   = "app.api.web_routes.ws_events"
_BASE_MGR  = "app.core.ws_connection_manager"
_BASE_REDIS = "app.core.redis_pubsub"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid=1, active=True):
    u = MagicMock()
    u.id        = uid
    u.email     = f"user{uid}@lfa.com"
    u.is_active = active
    return u


def _make_ws():
    """Return a mock WebSocket with async close/send/receive methods."""
    ws = MagicMock()
    ws.query_params = {"token": "good-token"}
    ws.accept  = AsyncMock()
    ws.close   = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=Exception("disconnect"))
    return ws


# ── WS-01  Missing token → 4001 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws01_missing_token_closes_4001():
    from app.api.web_routes.ws_events import ws_user_events
    ws = _make_ws()
    ws.query_params = {}

    with patch(f"{_BASE_WS}.verify_token", return_value=None):
        await ws_user_events(ws)

    ws.close.assert_awaited_once()
    args = ws.close.call_args
    assert args.kwargs.get("code") == 4001 or args.args[0] == 4001


# ── WS-02  Invalid token → 4001 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws02_invalid_token_closes_4001():
    from app.api.web_routes.ws_events import ws_user_events
    ws = _make_ws()

    with patch(f"{_BASE_WS}.verify_token", return_value=None):
        await ws_user_events(ws)

    ws.close.assert_awaited_once()
    close_args = ws.close.call_args
    code = close_args.kwargs.get("code") or (close_args.args[0] if close_args.args else None)
    assert code == 4001


# ── WS-03  Unknown user → 4003 ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws03_unknown_user_closes_4003():
    from app.api.web_routes.ws_events import ws_user_events
    ws = _make_ws()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch(f"{_BASE_WS}.verify_token", return_value="ghost@lfa.com"), \
         patch(f"{_BASE_WS}.SessionLocal", return_value=db):
        await ws_user_events(ws)

    ws.close.assert_awaited_once()
    close_args = ws.close.call_args
    code = close_args.kwargs.get("code") or (close_args.args[0] if close_args.args else None)
    assert code == 4003


# ── WS-04  Inactive user → 4003 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws04_inactive_user_closes_4003():
    from app.api.web_routes.ws_events import ws_user_events
    ws = _make_ws()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _user(active=False)

    with patch(f"{_BASE_WS}.verify_token", return_value="inactive@lfa.com"), \
         patch(f"{_BASE_WS}.SessionLocal", return_value=db):
        await ws_user_events(ws)

    ws.close.assert_awaited_once()
    close_args = ws.close.call_args
    code = close_args.kwargs.get("code") or (close_args.args[0] if close_args.args else None)
    assert code == 4003


# ── WS-05  Valid auth → 101 accepted ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws05_valid_auth_accepts_connection():
    from app.api.web_routes.ws_events import ws_user_events
    ws = _make_ws()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _user(uid=42)

    async def _no_events(_uid):
        return
        yield  # make it an async generator

    with patch(f"{_BASE_WS}.verify_token", return_value="u@lfa.com"), \
         patch(f"{_BASE_WS}.SessionLocal", return_value=db), \
         patch(f"{_BASE_WS}.manager") as mock_mgr, \
         patch(f"{_BASE_WS}.subscribe_user_events", side_effect=_no_events):
        mock_mgr.connect    = AsyncMock()
        mock_mgr.disconnect = AsyncMock()
        await ws_user_events(ws)

    ws.accept.assert_awaited_once()


# ── WS-06  Redis unavailable → redis_unavailable frame ────────────────────────

@pytest.mark.asyncio
async def test_ws06_redis_unavailable_sends_frame():
    from app.api.web_routes.ws_events import _redis_forward
    ws = _make_ws()

    async def _empty(_uid):
        return
        yield

    with patch(f"{_BASE_WS}.subscribe_user_events", side_effect=_empty):
        await _redis_forward(ws, user_id=1)

    ws.send_json.assert_awaited_once_with({"type": "redis_unavailable"})


# ── WS-07  Redis message forwarded ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws07_redis_message_forwarded():
    from app.api.web_routes.ws_events import _redis_forward
    ws = _make_ws()
    payload = json.dumps({"type": "challenge_sent", "challenge_id": 7})

    async def _one_msg(_uid):
        yield payload

    with patch(f"{_BASE_WS}.subscribe_user_events", side_effect=_one_msg):
        await _redis_forward(ws, user_id=1)

    ws.send_text.assert_awaited_once_with(payload)


# ── WS-08  Keepalive ping sent ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws08_keepalive_ping_sent():
    from app.api.web_routes.ws_events import _keepalive_ping
    ws = _make_ws()
    call_count = 0

    async def _fast_sleep(n):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            # First call lets send_json fire; second call exits the loop
            raise asyncio.CancelledError()

    # Patch asyncio.sleep inside the ws_events module namespace
    with patch(f"{_BASE_WS}.asyncio.sleep", side_effect=_fast_sleep):
        try:
            await _keepalive_ping(ws)
        except asyncio.CancelledError:
            pass

    ws.send_json.assert_awaited_once_with({"type": "ping"})


# ── WS-09  Client disconnect handled ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws09_client_disconnect_handled():
    from fastapi.websockets import WebSocketDisconnect
    from app.api.web_routes.ws_events import _drain_client_messages
    ws = _make_ws()
    ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())

    # Should return cleanly (no exception propagates)
    await _drain_client_messages(ws)


# ── WS-10  ConnectionManager.connect called ───────────────────────────────────

@pytest.mark.asyncio
async def test_ws10_connect_called_on_valid_auth():
    from app.api.web_routes.ws_events import ws_user_events
    ws = _make_ws()
    db = MagicMock()
    user = _user(uid=99)
    db.query.return_value.filter.return_value.first.return_value = user

    async def _no_events(_uid):
        return
        yield

    with patch(f"{_BASE_WS}.verify_token", return_value="u@lfa.com"), \
         patch(f"{_BASE_WS}.SessionLocal", return_value=db), \
         patch(f"{_BASE_WS}.manager") as mock_mgr, \
         patch(f"{_BASE_WS}.subscribe_user_events", side_effect=_no_events):
        mock_mgr.connect    = AsyncMock()
        mock_mgr.disconnect = AsyncMock()
        await ws_user_events(ws)

    mock_mgr.connect.assert_awaited_once_with(99, ws)


# ── WS-11  ConnectionManager.disconnect called on close ───────────────────────

@pytest.mark.asyncio
async def test_ws11_disconnect_called_on_close():
    from app.api.web_routes.ws_events import ws_user_events
    ws = _make_ws()
    db = MagicMock()
    user = _user(uid=55)
    db.query.return_value.filter.return_value.first.return_value = user

    async def _no_events(_uid):
        return
        yield

    with patch(f"{_BASE_WS}.verify_token", return_value="u@lfa.com"), \
         patch(f"{_BASE_WS}.SessionLocal", return_value=db), \
         patch(f"{_BASE_WS}.manager") as mock_mgr, \
         patch(f"{_BASE_WS}.subscribe_user_events", side_effect=_no_events):
        mock_mgr.connect    = AsyncMock()
        mock_mgr.disconnect = AsyncMock()
        await ws_user_events(ws)

    mock_mgr.disconnect.assert_awaited_once_with(55, ws)


# ── WS-12  publish_challenge_event publishes to all user_ids ──────────────────

def test_ws12_publish_challenge_event_all_user_ids():
    from app.core.redis_pubsub import publish_challenge_event
    mock_client = MagicMock()
    mock_client.publish = MagicMock()

    with patch(f"{_BASE_REDIS}._get_sync_client", return_value=mock_client):
        publish_challenge_event(
            [1, 2, 3],
            "challenge_sent",
            {"challenge_id": 7},
        )

    assert mock_client.publish.call_count == 3
    channels = [c.args[0] for c in mock_client.publish.call_args_list]
    assert set(channels) == {"user:1:events", "user:2:events", "user:3:events"}

    # All messages contain the event_type
    for c in mock_client.publish.call_args_list:
        data = json.loads(c.args[1])
        assert data["type"] == "challenge_sent"
        assert data["challenge_id"] == 7

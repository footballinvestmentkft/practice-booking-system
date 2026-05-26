"""
Per-user WebSocket connection registry.

Tracks all active WebSocket connections keyed by user_id (multi-tab safe).
Provides send_to_user / send_to_challenge_participants helpers.
Publish failure never propagates to callers — all sends are best-effort.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # user_id → set of active WebSocket connections (multi-tab)
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[user_id].add(ws)
        logger.debug("WS connect user_id=%s total_conns=%s", user_id, len(self._connections[user_id]))

    async def disconnect(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(user_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    del self._connections[user_id]
        logger.debug("WS disconnect user_id=%s", user_id)

    async def send_to_user(self, user_id: int, event: dict) -> None:
        """Send event JSON to all active tabs of user_id. Silently drops on error."""
        async with self._lock:
            sockets = set(self._connections.get(user_id, []))
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                conns = self._connections.get(user_id)
                if conns:
                    for ws in dead:
                        conns.discard(ws)
                    if not conns:
                        del self._connections[user_id]

    async def send_to_challenge_participants(
        self,
        challenger_id: int,
        challenged_id: int,
        event: dict,
    ) -> None:
        await asyncio.gather(
            self.send_to_user(challenger_id, event),
            self.send_to_user(challenged_id, event),
        )


manager = ConnectionManager()

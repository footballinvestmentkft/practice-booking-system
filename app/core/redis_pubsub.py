"""
Redis Pub/Sub for Tournament Live Monitoring
============================================

Provides:
  - publish_tournament_update(tournament_id, payload) — sync, safe to call from
    any synchronous FastAPI handler after db.commit().
  - subscribe_tournament_updates(tournament_id) — async generator that yields
    JSON strings; used by the WebSocket handler.
  - get_idle_pitches(tournament_id, threshold_s) — returns pitches with no
    recent activity; used by the WS idle-watcher background task.

Both I/O helpers fail silently when Redis is unavailable so the main app keeps
running even without a live monitoring backend.

──────────────────────────────────────────────────────────────────────────────
WebSocket event schema  (stable contract)
──────────────────────────────────────────────────────────────────────────────

Every message on ``tournament:{id}:updates`` and forwarded to WS clients:

.. code-block:: json

    {
      "type":            "session_result",
      "session_id":      1234,
      "campus_id":       2,
      "pitch_id":        5,
      "round_number":    7,
      "status":          "completed",
      "completed_count": 423,
      "total_count":     500500,
      "progress_pct":    0.0008,
      "completed_at":    "2026-03-24T10:00:00Z"
    }

Additional server-generated event types (not from Redis, injected by WS handler):

  type = "throttle_stats"   — sent every 30 s per WS connection
    { type, received, forwarded, dropped, drop_rate_pct }

  type = "pitch_idle_alert" — sent when a pitch has no activity > threshold
    { type, pitch_id, campus_id, idle_seconds, tournament_id }
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import AsyncIterator, Optional, TypedDict

import redis
import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)


# ── Stable event schema (TypedDict) ─────────────────────────────────────────

class TournamentUpdateEvent(TypedDict):
    """
    Canonical schema for a session-result event published on
    ``tournament:{id}:updates``.

    Publishers: ``app.api.api_v1.endpoints.sessions.results._publish_session_result``
    Consumers:  WebSocket handler in ``app.api.web_routes.tournament_live``
                (forwarded as-is to browser clients after throttle)
    """

    type: str              # discriminator — "session_result"
    session_id: int
    campus_id: Optional[int]
    pitch_id: Optional[int]
    round_number: Optional[int]
    status: str            # always "completed"
    completed_count: int
    total_count: int
    progress_pct: float    # 0.0 – 1.0
    completed_at: str      # ISO-8601 UTC
    tournament_phase: Optional[str]   # GROUP_STAGE / KNOCKOUT / None
    group_identifier: Optional[str]   # "A" / "B" / None
    game_type: Optional[str]          # "Semi-finals" / "Final" / None


# ── Per-pitch activity tracking (server-side idle detection) ─────────────────
# Single-dict per process; adequate for single-worker deployments.
# In multi-worker setups, replace with a Redis key.

_pitch_last_activity: dict[int, dict[int, float]] = defaultdict(dict)
# Structure: {tournament_id: {pitch_id: unix_timestamp_of_last_event}}


def _update_pitch_activity(tournament_id: int, pitch_id: Optional[int]) -> None:
    """Record that a pitch has just had activity."""
    if pitch_id:
        _pitch_last_activity[tournament_id][pitch_id] = time.time()


def get_idle_pitches(tournament_id: int, threshold_s: float) -> list[dict]:
    """
    Return pitches for ``tournament_id`` that have had no activity for at
    least ``threshold_s`` seconds.

    Returns a list of dicts:  [{"pitch_id": int, "idle_seconds": int}, …]
    """
    now = time.time()
    result = []
    for pid, last in _pitch_last_activity.get(tournament_id, {}).items():
        idle = now - last
        if idle >= threshold_s:
            result.append({"pitch_id": pid, "idle_seconds": int(idle)})
    return result


def reset_pitch_activity(tournament_id: int) -> None:
    """Clear tracking state for a tournament (used in tests)."""
    _pitch_last_activity.pop(tournament_id, None)


# ── Sync publish (called from HTTP handlers) ────────────────────────────────

_sync_client: redis.Redis | None = None


def _get_sync_client() -> redis.Redis | None:
    """Lazy singleton for the synchronous Redis client."""
    global _sync_client
    if _sync_client is None:
        try:
            _sync_client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            _sync_client.ping()
        except Exception as exc:
            logger.warning("Redis pub/sub unavailable (sync): %s", exc)
            _sync_client = None
    return _sync_client


def publish_tournament_update(tournament_id: int, payload: dict) -> None:
    """
    Publish a tournament event to Redis channel ``tournament:{id}:updates``.

    Also updates the server-side pitch activity tracker so the WS idle-watcher
    can detect pitches that have gone quiet.

    Called synchronously inside HTTP result-submission handlers after db.commit().
    Failures are logged and swallowed — live monitoring must never break the
    primary result flow.

    The ``payload`` dict should conform to :class:`TournamentUpdateEvent`.
    """
    # Always update pitch activity tracker (even when Redis is down)
    _update_pitch_activity(tournament_id, payload.get("pitch_id"))

    client = _get_sync_client()
    if client is None:
        return
    channel = f"tournament:{tournament_id}:updates"
    try:
        message = json.dumps(payload)
        client.publish(channel, message)
    except Exception as exc:
        logger.warning("Redis publish failed for channel %s: %s", channel, exc)
        # Reset client so next call re-tries the connection
        global _sync_client
        _sync_client = None


# ── Async subscribe (used by WebSocket handler) ─────────────────────────────

async def subscribe_tournament_updates(
    tournament_id: int,
) -> AsyncIterator[str]:
    """
    Async generator that subscribes to a tournament's update channel and
    yields raw JSON strings as they arrive.

    Each yielded string is a :class:`TournamentUpdateEvent` serialised to JSON.

    The generator returns (exhausts) when:
    - The Redis connection is lost.
    - The caller breaks out of the loop (generator is garbage-collected).

    Raises nothing — all errors are logged and the generator returns cleanly.
    """
    channel = f"tournament:{tournament_id}:updates"
    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        async with client.pubsub() as pubsub:
            await pubsub.subscribe(channel)
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    yield raw["data"]
    except Exception as exc:
        logger.warning("Redis async subscribe error on %s: %s", channel, exc)
        return


# ── Challenge event helpers ──────────────────────────────────────────────────

def publish_challenge_event(user_ids: list[int], event_type: str, payload: dict) -> None:
    """
    Publish a challenge lifecycle event to each user's personal Redis channel
    ``user:{user_id}:events``.

    Called synchronously from HTTP handlers and services after state changes.
    Never raises — failures are logged and swallowed so the primary challenge
    flow is never affected.

    Args:
        user_ids:   List of user IDs who should receive the event (typically
                    [challenger_id, challenged_id]).
        event_type: Discriminator string, e.g. "challenge_sent", "challenge_accepted".
        payload:    Extra fields merged into the event envelope (must be JSON-serializable).
    """
    try:
        client = _get_sync_client()
        if client is None:
            return
        message = json.dumps({"type": event_type, **payload})
        for uid in user_ids:
            channel = f"user:{uid}:events"
            try:
                client.publish(channel, message)
            except Exception as exc:
                logger.warning("Redis publish_challenge_event failed uid=%s: %s", uid, exc)
                global _sync_client
                _sync_client = None
                return
    except Exception as exc:
        logger.warning("publish_challenge_event error event=%s: %s", event_type, exc)


async def subscribe_user_events(user_id: int) -> AsyncIterator[str]:
    """
    Async generator that subscribes to ``user:{user_id}:events`` and yields
    raw JSON strings as they arrive.

    Returns cleanly (without raising) when Redis is unavailable or the connection
    is lost — the WS handler should fall back to polling in that case.
    """
    channel = f"user:{user_id}:events"
    try:
        client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        async with client.pubsub() as pubsub:
            await pubsub.subscribe(channel)
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    yield raw["data"]
    except Exception as exc:
        logger.warning("Redis subscribe_user_events error uid=%s: %s", user_id, exc)
        return

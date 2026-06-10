"""
Biometric rate limiter — PR-8.

Redis-backed sliding window rate limiter for biometric endpoints.
Falls back to an in-memory counter when Redis is unavailable (dev/test only).

Key schema (no plaintext PII in key or metrics):
  biometric_rl:user:{endpoint_group}:{user_id}
  biometric_rl:admin:{endpoint_group}:{actor_user_id}
  biometric_rl:ip:{endpoint_group}:{ip_hash}   (SHA-256 first 16 hex chars)

Design rules:
  1. Redis INCR + EXPIRE pattern — atomic, no race condition.
  2. In-memory fallback: allowed ONLY in dev/test (non-production).
  3. IP is NEVER stored plaintext — only SHA-256 hash prefix.
  4. No PII, no face_match_score, no embedding in key or log.
  5. 429 response on limit exceeded; EVT_RATE_LIMITED audit written by caller.

Production Redis unavailability behaviour (enforce_rate_limit only):
  Default (BIOMETRIC_RATE_LIMIT_FAIL_OPEN=false):
    HTTP 503 biometric_rate_limiter_unavailable — fail-closed.
    Biometric endpoints are unavailable until Redis is restored.
  Explicit opt-in (BIOMETRIC_RATE_LIMIT_FAIL_OPEN=true):
    CRITICAL log emitted; rate limiting disabled for that request — fail-open.
    This is a security risk and must only be used during a controlled outage
    with explicit operator acknowledgment.
  Transient Redis errors during a live request:
    Always fail-open with WARNING log (Redis was available at startup but
    a single check failed — safer than denying a legitimate user mid-flight).

Endpoint groups and limits:
  disclosure_post:    5 / 600s  (user)
  disclosure_delete:  3 / 600s  (user)
  disclosure_get:    30 /  60s  (user)
  liveness_submit:    3 / 600s  (user)
  verify:             5 / 900s  (user)
  admin_queue:       60 /  60s  (admin)
  admin_history:     60 /  60s  (admin)
  admin_override:    20 / 600s  (admin)
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Optional

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# ── Endpoint group constants ───────────────────────────────────────────────────

DISCLOSURE_POST    = "disclosure_post"
DISCLOSURE_DELETE  = "disclosure_delete"
DISCLOSURE_GET     = "disclosure_get"
LIVENESS_SUBMIT    = "liveness_submit"
VERIFY             = "verify"
ADMIN_QUEUE        = "admin_queue"
ADMIN_HISTORY      = "admin_history"
ADMIN_OVERRIDE     = "admin_override"

# Whitelisted endpoint groups (enum) — used as metric label values
ENDPOINT_GROUPS: frozenset[str] = frozenset({
    DISCLOSURE_POST, DISCLOSURE_DELETE, DISCLOSURE_GET,
    LIVENESS_SUBMIT, VERIFY,
    ADMIN_QUEUE, ADMIN_HISTORY, ADMIN_OVERRIDE,
})

# Limits: (max_requests, window_seconds)
_LIMITS: dict[str, tuple[int, int]] = {
    DISCLOSURE_POST:   (5,  600),
    DISCLOSURE_DELETE: (3,  600),
    DISCLOSURE_GET:    (30,  60),
    LIVENESS_SUBMIT:   (3,  600),
    VERIFY:            (5,  900),
    ADMIN_QUEUE:       (60,  60),
    ADMIN_HISTORY:     (60,  60),
    ADMIN_OVERRIDE:    (20, 600),
}


# ── IP hashing ─────────────────────────────────────────────────────────────────

def _hash_ip(ip: Optional[str]) -> str:
    """Return the first 16 hex chars of SHA-256(ip). Never stores plaintext IP."""
    if not ip:
        return "unknown"
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


# ── In-memory fallback (dev/test only) ────────────────────────────────────────

_mem_lock  = threading.Lock()
_mem_store: dict[str, tuple[int, float]] = {}   # key → (count, expires_at)


def _mem_check(key: str, limit: int, window: int) -> bool:
    """Thread-safe in-memory INCR+EXPIRE. Returns True if allowed."""
    now = time.monotonic()
    with _mem_lock:
        count, expires = _mem_store.get(key, (0, now + window))
        if now > expires:
            count, expires = 0, now + window
        count += 1
        _mem_store[key] = (count, expires)
        return count <= limit


# ── Redis client (lazy, singleton) ───────────────────────────────────────────

_redis_client = None
_redis_available: Optional[bool] = None


def _get_redis():
    """Lazy singleton Redis client. Returns None on connection failure."""
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis as _redis
        from app.config import settings
        client = _redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
        client.ping()
        _redis_client = client
        _redis_available = True
        logger.info("biometric_rate_limiter: Redis connected")
        return _redis_client
    except Exception as exc:
        _redis_available = False
        logger.warning("biometric_rate_limiter: Redis unavailable — %s", type(exc).__name__)
        return None


# ── Core check function ───────────────────────────────────────────────────────

def _is_production() -> bool:
    env = os.getenv("ENV", "development").lower()
    return env not in ("development", "dev", "test", "testing", "local")


def _is_fail_open() -> bool:
    """
    Read BIOMETRIC_RATE_LIMIT_FAIL_OPEN env var.

    Default: false (fail-closed — 503 when Redis is unavailable in production).
    Set to "true" only with explicit operator acknowledgment; this is a security risk.
    """
    return os.getenv("BIOMETRIC_RATE_LIMIT_FAIL_OPEN", "false").lower() in ("true", "1", "yes")


def check_rate_limit(key: str, endpoint_group: str) -> bool:
    """
    Check and increment rate limit counter.

    Returns True if request is allowed, False if rate limited.

    Redis path: INCR + EXPIRE (atomic sliding window).
    Fallback: in-memory (dev/test only).
    Production without Redis: fail-open with CRITICAL log.
    """
    limit, window = _LIMITS.get(endpoint_group, (30, 60))
    client = _get_redis()

    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window)
            count, _ = pipe.execute()
            return int(count) <= limit
        except Exception as exc:
            logger.warning(
                "biometric_rate_limiter: Redis check failed key=%s error=%s — fail-open",
                key, type(exc).__name__,
            )
            return True  # fail-open on transient error

    # No Redis available
    if _is_production():
        logger.critical(
            "biometric_rate_limiter: Redis unavailable in production — "
            "rate limiting DISABLED for endpoint_group=%s. "
            "Connect Redis immediately.",
            endpoint_group,
        )
        return True   # fail-open, but CRITICAL log

    # Dev/test fallback — allowed
    return _mem_check(key, limit, window)


# ── Public helpers for endpoint use ──────────────────────────────────────────

def user_key(endpoint_group: str, user_id: int) -> str:
    return f"biometric_rl:user:{endpoint_group}:{user_id}"


def admin_key(endpoint_group: str, actor_user_id: int) -> str:
    return f"biometric_rl:admin:{endpoint_group}:{actor_user_id}"


def ip_key(endpoint_group: str, ip: Optional[str]) -> str:
    return f"biometric_rl:ip:{endpoint_group}:{_hash_ip(ip)}"


def enforce_rate_limit(
    *,
    endpoint_group: str,
    user_id: Optional[int] = None,
    actor_user_id: Optional[int] = None,
    ip: Optional[str] = None,
    db=None,         # optional — used to write EVT_RATE_LIMITED if set
    audit_user_id: Optional[int] = None,
) -> None:
    """
    Enforce rate limit for the given endpoint group.

    Production Redis availability:
      - BIOMETRIC_RATE_LIMIT_FAIL_OPEN=false (default): raises HTTP 503
        biometric_rate_limiter_unavailable when Redis is unreachable.
      - BIOMETRIC_RATE_LIMIT_FAIL_OPEN=true: logs CRITICAL and bypasses
        rate limiting (fail-open, explicit operator opt-in only).

    Raises HTTPException 429 if any applicable limit is exceeded.
    Raises HTTPException 503 if Redis is unavailable in production (fail-closed).
    Writes EVT_RATE_LIMITED audit event if db is provided.
    No PII, no score, no embedding in response or log.
    """
    # ── Production Redis availability guard ──────────────────────────────────
    if _is_production() and _get_redis() is None:
        if _is_fail_open():
            logger.critical(
                "biometric_rate_limiter: Redis unavailable in production — "
                "rate limiting DISABLED (BIOMETRIC_RATE_LIMIT_FAIL_OPEN=true). "
                "endpoint_group=%s. Restore Redis immediately.",
                endpoint_group,
            )
            return  # fail-open by explicit operator config
        logger.critical(
            "biometric_rate_limiter: Redis unavailable in production — "
            "returning 503 fail-closed (BIOMETRIC_RATE_LIMIT_FAIL_OPEN=false). "
            "endpoint_group=%s. Restore Redis immediately.",
            endpoint_group,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="biometric_rate_limiter_unavailable",
        )

    keys_to_check: list[str] = []
    if user_id is not None:
        keys_to_check.append(user_key(endpoint_group, user_id))
    if actor_user_id is not None:
        keys_to_check.append(admin_key(endpoint_group, actor_user_id))
    if ip is not None:
        keys_to_check.append(ip_key(endpoint_group, ip))

    for key in keys_to_check:
        if not check_rate_limit(key, endpoint_group):
            logger.info(
                "biometric_rate_limit_exceeded endpoint_group=%s",
                endpoint_group,
                # No user_id, no IP in app log — only endpoint_group
            )
            if db is not None and audit_user_id is not None:
                _write_rate_limited_audit(db, audit_user_id, endpoint_group, ip)

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate_limited",
                headers={"Retry-After": str(_LIMITS.get(endpoint_group, (0, 60))[1])},
            )


def _write_rate_limited_audit(db, user_id: int, endpoint_group: str, ip: Optional[str]) -> None:
    """Write EVT_RATE_LIMITED to audit log. No score, no raw IP in error_message."""
    try:
        from app.services.biometric.audit_log import BiometricAuditLogger, EVT_RATE_LIMITED
        BiometricAuditLogger(db).log(
            user_id=user_id,
            event_type=EVT_RATE_LIMITED,
            event_result="rate_limited",
            actor_ip_address=ip,       # IP in audit DB is acceptable
            error_message=f"endpoint_group={endpoint_group}",
            # face_match_score intentionally omitted
        )
        db.flush()
    except Exception as exc:
        logger.warning("biometric_rate_limited_audit_failed: %s", type(exc).__name__)


# ── Verify abuse tracker ──────────────────────────────────────────────────────

_VERIFY_ABUSE_THRESHOLD = 3
_VERIFY_ABUSE_WINDOW    = 900   # 15 minutes
_VERIFY_ABUSE_KEY_PREFIX = "biometric_abuse:verify_reject"


def _verify_abuse_key(user_id: int) -> str:
    return f"{_VERIFY_ABUSE_KEY_PREFIX}:{user_id}"


def record_verify_outcome(
    *,
    user_id: int,
    outcome: str,       # "verified" | "manual_review_required" | "rejected"
    db=None,
    ip: Optional[str] = None,
) -> None:
    """
    Track consecutive verify rejections for abuse detection.

    Rejected: increment counter. If >= threshold → EVT_VERIFY_ABUSE_DETECTED audit.
    Verified / manual_review_required: reset the counter (success resets abuse window).

    Only audit event — no automatic ban.
    user_id-based (not IP-based) for consistency with the main rate limit.
    """
    key = _verify_abuse_key(user_id)

    if outcome in ("verified", "manual_review_required"):
        # Reset consecutive rejection counter on success/review
        client = _get_redis()
        if client is not None:
            try:
                client.delete(key)
            except Exception:
                pass
        else:
            with _mem_lock:
                _mem_store.pop(key, None)
        return

    if outcome == "rejected":
        client = _get_redis()
        count = 1
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, _VERIFY_ABUSE_WINDOW)
                count, _ = pipe.execute()
                count = int(count)
            except Exception:
                pass
        else:
            if _mem_check(key, 9999, _VERIFY_ABUSE_WINDOW):
                with _mem_lock:
                    count = _mem_store.get(key, (0, 0))[0]

        if count >= _VERIFY_ABUSE_THRESHOLD:
            logger.warning(
                "biometric_verify_abuse_detected endpoint_group=verify count=%d window=%ds",
                count, _VERIFY_ABUSE_WINDOW,
                # No user_id in app log
            )
            if db is not None:
                try:
                    from app.services.biometric.audit_log import (
                        BiometricAuditLogger, EVT_VERIFY_ABUSE_DETECTED
                    )
                    BiometricAuditLogger(db).log(
                        user_id=user_id,
                        event_type=EVT_VERIFY_ABUSE_DETECTED,
                        event_result="abuse_detected",
                        actor_ip_address=ip,
                        error_message=f"consecutive_rejected={count}/window={_VERIFY_ABUSE_WINDOW}s",
                    )
                    db.flush()
                except Exception as exc:
                    logger.warning("biometric_abuse_audit_failed: %s", type(exc).__name__)
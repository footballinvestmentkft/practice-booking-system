"""live_lobby_service — PR-L1 live challenge lobby + ready-state logic.

Live lobby lifecycle:
  LIVE_LOBBY      → LIVE_IN_PROGRESS  (both ready → live_start_at set)
  LIVE_LOBBY      → EXPIRED           (lobby_expires_at passed → no_show)
  LIVE_IN_PROGRESS → COMPLETED        (one played, other missed → forfeit win / no_show)
  LIVE_IN_PROGRESS → EXPIRED          (neither submitted in post-start window → no_contest)

All mutating functions do NOT flush/commit — caller is responsible.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..models.notification import NotificationType
from ..models.vt_challenge import (
    ChallengeStatus,
    LOBBY_TIMEOUT_SECONDS,
    POST_START_SUBMIT_WINDOW_SECONDS,
    VirtualTrainingChallenge,
)
from ..core.redis_pubsub import publish_challenge_event
from . import notification_service


# ── Ready state ────────────────────────────────────────────────────────────────

def set_ready(
    db: Session,
    challenge: VirtualTrainingChallenge,
    user_id: int,
    now: datetime,
) -> dict:
    """Mark a player as ready in the lobby. Returns lobby state dict.

    Transitions to LIVE_IN_PROGRESS if both sides are ready.
    No-ops if user already ready or challenge not in LIVE_LOBBY.
    Does NOT flush/commit.
    """
    if challenge.status != ChallengeStatus.LIVE_LOBBY:
        return get_lobby_state(challenge, now)

    if user_id == challenge.challenger_id and challenge.challenger_ready_at is None:
        challenge.challenger_ready_at = now
        challenge.updated_at = now
    elif user_id == challenge.challenged_id and challenge.challenged_ready_at is None:
        challenge.challenged_ready_at = now
        challenge.updated_at = now

    if challenge.challenger_ready_at and challenge.challenged_ready_at:
        challenge.status = ChallengeStatus.LIVE_IN_PROGRESS
        challenge.live_start_at = now
        challenge.updated_at = now
        _send_live_start_notifications(db, challenge)

    return get_lobby_state(challenge, now)


# ── Timeout sweeps ─────────────────────────────────────────────────────────────

def apply_lobby_timeout_if_expired(
    db: Session,
    challenge: VirtualTrainingChallenge,
    now: datetime,
) -> bool:
    """Expire a LIVE_LOBBY challenge if lobby_expires_at has passed.

    Returns True if state changed. Does NOT flush/commit.
    """
    if challenge.status != ChallengeStatus.LIVE_LOBBY:
        return False
    if challenge.lobby_expires_at is None or challenge.lobby_expires_at > now:
        return False

    challenge.status         = ChallengeStatus.EXPIRED
    challenge.forfeit_reason = "no_show"
    challenge.updated_at     = now
    _send_lobby_timeout_notifications(db, challenge)
    return True


def apply_post_start_timeout_if_expired(
    db: Session,
    challenge: VirtualTrainingChallenge,
    now: datetime,
) -> bool:
    """Apply post-start forfeit if POST_START_SUBMIT_WINDOW_SECONDS has passed.

    Outcome matrix (mirrors async forfeit logic):
      challenger submitted + challenged did not → challenger wins (no_show)
      challenged submitted + challenger did not → challenged wins (no_show)
      neither submitted                         → EXPIRED, no_contest
    Returns True if state changed. Does NOT flush/commit.
    """
    if challenge.status != ChallengeStatus.LIVE_IN_PROGRESS:
        return False
    if challenge.live_start_at is None:
        return False

    deadline = challenge.live_start_at + timedelta(seconds=POST_START_SUBMIT_WINDOW_SECONDS)
    if deadline > now:
        return False

    has_cr = challenge.challenger_attempt_id is not None
    has_cd = challenge.challenged_attempt_id is not None

    if has_cr and not has_cd:
        challenge.winner_id       = challenge.challenger_id
        challenge.is_draw         = False
        challenge.forfeit_user_id = challenge.challenged_id
        challenge.forfeit_reason  = "post_start_timeout"
        challenge.status          = ChallengeStatus.COMPLETED
        challenge.completed_at    = now
        challenge.updated_at      = now
        _send_forfeit_notifications(db, challenge)
    elif has_cd and not has_cr:
        challenge.winner_id       = challenge.challenged_id
        challenge.is_draw         = False
        challenge.forfeit_user_id = challenge.challenger_id
        challenge.forfeit_reason  = "post_start_timeout"
        challenge.status          = ChallengeStatus.COMPLETED
        challenge.completed_at    = now
        challenge.updated_at      = now
        _send_forfeit_notifications(db, challenge)
    else:
        challenge.status         = ChallengeStatus.EXPIRED
        challenge.forfeit_reason = "no_contest"
        challenge.updated_at     = now
        _send_no_contest_notifications(db, challenge)

    return True


def sweep_live_challenges(
    db: Session,
    challenges: list[VirtualTrainingChallenge],
) -> int:
    """Lazy sweep: apply lobby + post-start timeouts to all live challenges.

    Flushes to DB if any changes were made. Does NOT commit — caller commits.
    Returns count of modified challenges.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for ch in challenges:
        if apply_lobby_timeout_if_expired(db, ch, now):
            count += 1
        elif apply_post_start_timeout_if_expired(db, ch, now):
            count += 1
    if count:
        db.flush()
    return count


# ── State query ────────────────────────────────────────────────────────────────

def get_lobby_state(challenge: VirtualTrainingChallenge, now: datetime) -> dict:
    """Return a JSON-serialisable dict describing current lobby state.

    Used by the polling endpoint GET /challenges/{id}/lobby-state.
    """
    post_start_deadline = None
    if challenge.live_start_at is not None:
        post_start_deadline = (
            challenge.live_start_at + timedelta(seconds=POST_START_SUBMIT_WINDOW_SECONDS)
        ).isoformat()

    return {
        "status":                challenge.status.value,
        "challenger_ready":      challenge.challenger_ready_at is not None,
        "challenged_ready":      challenge.challenged_ready_at is not None,
        "live_start_at":         challenge.live_start_at.isoformat() if challenge.live_start_at else None,
        "lobby_expires_at":      challenge.lobby_expires_at.isoformat() if challenge.lobby_expires_at else None,
        "post_start_deadline":   post_start_deadline,
        "server_now":            now.isoformat(),
    }


# ── Notifications ──────────────────────────────────────────────────────────────

def _send_live_start_notifications(
    db: Session,
    challenge: VirtualTrainingChallenge,
) -> None:
    for uid in (challenge.challenger_id, challenge.challenged_id):
        notification_service.create_notification(
            db=db,
            user_id=uid,
            title="Live Challenge — Starting Now",
            message="Both players are ready. Your live challenge has started!",
            notification_type=NotificationType.VT_CHALLENGE_LIVE_LOBBY,
            link=f"/challenges/{challenge.id}/lobby",
        )
    publish_challenge_event(
        [challenge.challenger_id, challenge.challenged_id],
        "challenge_live_started",
        {"challenge_id": challenge.id},
    )


def _send_lobby_timeout_notifications(
    db: Session,
    challenge: VirtualTrainingChallenge,
) -> None:
    for uid in (challenge.challenger_id, challenge.challenged_id):
        notification_service.create_notification(
            db=db,
            user_id=uid,
            title="Live Challenge — Lobby Expired",
            message="Not all players were ready in time. The challenge was cancelled.",
            notification_type=NotificationType.VT_CHALLENGE_EXPIRED,
            link="/challenges",
        )
    publish_challenge_event(
        [challenge.challenger_id, challenge.challenged_id],
        "challenge_expired",
        {"challenge_id": challenge.id},
    )


def _send_forfeit_notifications(
    db: Session,
    challenge: VirtualTrainingChallenge,
) -> None:
    winner_id = challenge.winner_id

    def _msg(for_uid: int) -> str:
        if for_uid == winner_id:
            return "You won by forfeit — your opponent did not submit in time."
        return "You lost by forfeit — you did not submit in time."

    for uid in (challenge.challenger_id, challenge.challenged_id):
        notification_service.create_notification(
            db=db,
            user_id=uid,
            title="Live Challenge — Forfeit",
            message=_msg(uid),
            notification_type=NotificationType.VT_CHALLENGE_FORFEITED,
            link="/challenges",
        )
    publish_challenge_event(
        [challenge.challenger_id, challenge.challenged_id],
        "challenge_forfeited",
        {"challenge_id": challenge.id, "winner_id": winner_id},
    )


def _send_no_contest_notifications(
    db: Session,
    challenge: VirtualTrainingChallenge,
) -> None:
    for uid in (challenge.challenger_id, challenge.challenged_id):
        notification_service.create_notification(
            db=db,
            user_id=uid,
            title="Live Challenge — No Contest",
            message="Neither player submitted in time. The challenge expired.",
            notification_type=NotificationType.VT_CHALLENGE_FORFEITED,
            link="/challenges",
        )
    publish_challenge_event(
        [challenge.challenger_id, challenge.challenged_id],
        "challenge_no_contest",
        {"challenge_id": challenge.id},
    )

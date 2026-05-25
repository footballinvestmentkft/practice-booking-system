"""challenge_completion_service — PR-P1 async deadline + forfeit logic.

Forfeit rules (applied only when completion_deadline is not NULL):
  - Only attempts submitted BEFORE the deadline count toward forfeit determination.
  - A late submit (no prior attempt on that side) is BLOCKED at the route layer
    before this service is called.
  - apply_forfeit_if_deadline_passed() is idempotent: safe to call multiple times.

Outcome matrix:
  challenger played + challenged did not → challenger wins, challenged forfeits
  challenged played + challenger did not → challenged wins, challenger forfeits
  neither played                         → EXPIRED, no_contest
  both played (already COMPLETED)        → no-op (status not ACCEPTED)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models.notification import NotificationType
from ..models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from . import notification_service


def apply_forfeit_if_deadline_passed(
    db: Session,
    challenge: VirtualTrainingChallenge,
    now: datetime,
) -> bool:
    """Apply forfeit if completion_deadline has passed. Returns True if state changed.

    Only acts on ACCEPTED challenges with a non-NULL completion_deadline.
    Does NOT flush or commit — caller is responsible.
    """
    if challenge.status != ChallengeStatus.ACCEPTED:
        return False
    if challenge.completion_deadline is None:
        return False
    if challenge.completion_deadline > now:
        return False

    has_cr = challenge.challenger_attempt_id is not None
    has_cd = challenge.challenged_attempt_id is not None

    if has_cr and not has_cd:
        challenge.winner_id       = challenge.challenger_id
        challenge.is_draw         = False
        challenge.forfeit_user_id = challenge.challenged_id
        challenge.forfeit_reason  = "deadline_expired"
        challenge.status          = ChallengeStatus.COMPLETED
        challenge.completed_at    = now
        challenge.updated_at      = now
        _send_forfeit_notifications(db, challenge)
    elif has_cd and not has_cr:
        challenge.winner_id       = challenge.challenged_id
        challenge.is_draw         = False
        challenge.forfeit_user_id = challenge.challenger_id
        challenge.forfeit_reason  = "deadline_expired"
        challenge.status          = ChallengeStatus.COMPLETED
        challenge.completed_at    = now
        challenge.updated_at      = now
        _send_forfeit_notifications(db, challenge)
    else:
        # neither played (or already both — but COMPLETED check above guards that)
        challenge.status         = ChallengeStatus.EXPIRED
        challenge.forfeit_reason = "no_contest"
        challenge.updated_at     = now
        _send_no_contest_notifications(db, challenge)

    return True


def sweep_accepted_deadlines(
    db: Session,
    challenges: list[VirtualTrainingChallenge],
) -> int:
    """Lazy sweep: apply forfeit to all ACCEPTED challenges past their deadline.

    Flushes to DB if any changes were made. Does NOT commit — caller commits.
    Returns count of modified challenges.
    """
    now = datetime.now(timezone.utc)
    count = 0
    for ch in challenges:
        if apply_forfeit_if_deadline_passed(db, ch, now):
            count += 1
    if count:
        db.flush()
    return count


def _send_forfeit_notifications(
    db: Session,
    challenge: VirtualTrainingChallenge,
) -> None:
    winner_id   = challenge.winner_id
    forfeit_uid = challenge.forfeit_user_id

    def _msg(for_uid: int) -> str:
        if for_uid == winner_id:
            return "You won by forfeit — your opponent did not play in time."
        return "You lost by forfeit — you did not play in time."

    for uid in (challenge.challenger_id, challenge.challenged_id):
        notification_service.create_notification(
            db=db,
            user_id=uid,
            title="VT Challenge — Forfeit",
            message=_msg(uid),
            notification_type=NotificationType.VT_CHALLENGE_FORFEITED,
            link="/challenges",
        )


def _send_no_contest_notifications(
    db: Session,
    challenge: VirtualTrainingChallenge,
) -> None:
    for uid in (challenge.challenger_id, challenge.challenged_id):
        notification_service.create_notification(
            db=db,
            user_id=uid,
            title="VT Challenge — No Contest",
            message="The challenge expired — neither player completed it in time.",
            notification_type=NotificationType.VT_CHALLENGE_FORFEITED,
            link="/challenges",
        )

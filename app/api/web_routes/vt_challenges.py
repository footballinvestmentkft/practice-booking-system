"""
VT Challenge web routes — async friend-vs-friend challenge lifecycle (PR-C1).

Routes:
  POST /challenges/send               → send challenge to a friend
  POST /challenges/{id}/accept        → accept incoming challenge (challenged only)
  POST /challenges/{id}/decline       → decline incoming challenge (challenged only)
  POST /challenges/{id}/cancel        → cancel pending/accepted challenge (challenger only)

Guards (send):
  - self-challenge blocked
  - target must be active user
  - must be friends (is_friends)
  - game must exist
  - game must be in CHALLENGE_COMPATIBLE_GAMES
  - no duplicate active challenge between the pair on the same game

Guards (accept):
  - challenge must exist, current user must be challenged_id
  - challenge must not be expired (auto-marks EXPIRED if so)

Guards (decline/cancel):
  - challenge must exist, correct ownership
  - cancel allowed only for PENDING or ACCEPTED status

Notifications:
  - send   → VT_CHALLENGE_RECEIVED  to challenged_id
  - accept → VT_CHALLENGE_ACCEPTED  to challenger_id
  - decline→ VT_CHALLENGE_DECLINED  to challenger_id
  - cancel → VT_CHALLENGE_CANCELLED to challenged_id
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.friendship import is_friends
from ...models.notification import NotificationType
from ...models.user import User
from ...models.virtual_training import VirtualTrainingGame
from ...models.vt_challenge import (
    CHALLENGE_COMPATIBLE_GAMES,
    ChallengeStatus,
    VirtualTrainingChallenge,
    get_active_challenge,
    make_expires_at,
)
from ...services import notification_service

router = APIRouter()

_MAX_MSG = 500


def _trim_message(raw: str | None) -> str | None:
    if not raw:
        return None
    trimmed = raw.strip()[:_MAX_MSG]
    return trimmed or None


# ── Send ───────────────────────────────────────────────────────────────────────

@router.post("/challenges/send")
async def send_challenge(
    challenged_user_id: int = Form(...),
    game_id: int = Form(...),
    message: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    # Self-challenge guard
    if challenged_user_id == user.id:
        return RedirectResponse(url="/friends?error=self_challenge", status_code=303)

    # Target must be active
    target = db.query(User).filter(
        User.id == challenged_user_id, User.is_active == True
    ).first()
    if not target:
        return RedirectResponse(url="/friends?error=user_not_found", status_code=303)

    # Friendship guard
    if not is_friends(db, user.id, challenged_user_id):
        return RedirectResponse(url="/friends?error=not_friends", status_code=303)

    # Game existence guard
    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == game_id
    ).first()
    if not game:
        return RedirectResponse(url="/friends?error=game_not_found", status_code=303)

    # Compatible game guard
    if game.code not in CHALLENGE_COMPATIBLE_GAMES:
        return RedirectResponse(url="/friends?error=game_not_compatible", status_code=303)

    # Duplicate active challenge guard (bidirectional)
    if get_active_challenge(db, user.id, challenged_user_id, game_id):
        return RedirectResponse(url="/friends?error=challenge_active", status_code=303)

    now = datetime.now(timezone.utc)
    challenge = VirtualTrainingChallenge(
        challenger_id=user.id,
        challenged_id=challenged_user_id,
        game_id=game_id,
        status=ChallengeStatus.PENDING,
        message=_trim_message(message),
        expires_at=make_expires_at(now),
        created_at=now,
    )
    db.add(challenge)
    db.flush()

    notification_service.create_notification(
        db=db,
        user_id=challenged_user_id,
        title="Challenge Received",
        message=f"{user.nickname or user.email} challenged you to a VT game.",
        notification_type=NotificationType.VT_CHALLENGE_RECEIVED,
        link="/friends",
    )

    db.commit()
    return RedirectResponse(url="/friends?success=challenge_sent", status_code=303)


# ── Accept ─────────────────────────────────────────────────────────────────────

@router.post("/challenges/{challenge_id}/accept")
async def accept_challenge(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or challenge.challenged_id != user.id:
        return RedirectResponse(url="/friends?error=not_found", status_code=303)

    if challenge.status != ChallengeStatus.PENDING:
        return RedirectResponse(url="/friends?error=not_pending", status_code=303)

    # Expiry guard — auto-mark expired
    now = datetime.now(timezone.utc)
    if challenge.expires_at <= now:
        challenge.status     = ChallengeStatus.EXPIRED
        challenge.updated_at = now
        db.commit()
        return RedirectResponse(url="/friends?error=challenge_expired", status_code=303)

    challenge.status     = ChallengeStatus.ACCEPTED
    challenge.updated_at = now

    notification_service.create_notification(
        db=db,
        user_id=challenge.challenger_id,
        title="Challenge Accepted",
        message=f"{user.nickname or user.email} accepted your VT challenge.",
        notification_type=NotificationType.VT_CHALLENGE_ACCEPTED,
        link="/friends",
    )

    db.commit()
    return RedirectResponse(url="/friends?success=challenge_accepted", status_code=303)


# ── Decline ────────────────────────────────────────────────────────────────────

@router.post("/challenges/{challenge_id}/decline")
async def decline_challenge(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or challenge.challenged_id != user.id:
        return RedirectResponse(url="/friends?error=not_found", status_code=303)

    if challenge.status != ChallengeStatus.PENDING:
        return RedirectResponse(url="/friends?error=not_pending", status_code=303)

    now = datetime.now(timezone.utc)
    challenge.status     = ChallengeStatus.DECLINED
    challenge.updated_at = now

    notification_service.create_notification(
        db=db,
        user_id=challenge.challenger_id,
        title="Challenge Declined",
        message=f"{user.nickname or user.email} declined your VT challenge.",
        notification_type=NotificationType.VT_CHALLENGE_DECLINED,
        link="/friends",
    )

    db.commit()
    return RedirectResponse(url="/friends?success=challenge_declined", status_code=303)


# ── Cancel ─────────────────────────────────────────────────────────────────────

@router.post("/challenges/{challenge_id}/cancel")
async def cancel_challenge(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or challenge.challenger_id != user.id:
        return RedirectResponse(url="/friends?error=not_found", status_code=303)

    if challenge.status not in (ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED):
        return RedirectResponse(url="/friends?error=cannot_cancel", status_code=303)

    now = datetime.now(timezone.utc)
    challenge.status     = ChallengeStatus.CANCELLED
    challenge.updated_at = now

    notification_service.create_notification(
        db=db,
        user_id=challenge.challenged_id,
        title="Challenge Cancelled",
        message=f"{user.nickname or user.email} cancelled their VT challenge.",
        notification_type=NotificationType.VT_CHALLENGE_CANCELLED,
        link="/friends",
    )

    db.commit()
    return RedirectResponse(url="/friends?success=challenge_cancelled", status_code=303)

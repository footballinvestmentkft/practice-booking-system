"""
Friends web routes — minimal friendship system.

Routes:
  GET  /friends                    → friend list + friend request counts
  GET  /friends/requests           → incoming + outgoing pending requests
  POST /friends/request/{user_id}  → send friend request
  POST /friends/accept/{id}        → accept incoming request
  POST /friends/decline/{id}       → decline incoming request
  POST /friends/remove/{user_id}   → remove accepted friend

Guards:
  - Requester != addressee (self-request blocked)
  - No duplicate pending request
  - No request to inactive user
  - Accept/decline: addressee only
  - Remove: either participant

Notifications:
  - friend_request_received → sent to addressee on request
  - friend_request_accepted → sent to requester on accept
"""
from __future__ import annotations

import pathlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.friendship import (
    Friendship, FriendshipStatus, get_friendship, is_friends,
)
from typing import Optional
from ...models.notification import NotificationType
from ...models.user import User
from ...services import notification_service

BASE_DIR  = pathlib.Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

_NEXT_WHITELIST = ("/players/", "/friends")


def _safe_next(next_url: str | None, default: str = "/friends") -> str:
    """Return next_url if it matches the whitelist, otherwise the default."""
    if next_url and isinstance(next_url, str):
        for prefix in _NEXT_WHITELIST:
            if next_url.startswith(prefix):
                return next_url
    return default


# ── Helpers ────────────────────────────────────────────────────────────────────

def _friend_list(db: Session, user_id: int) -> list[User]:
    """All accepted friends of user_id (either direction)."""
    rows = (
        db.query(Friendship)
        .filter(
            Friendship.status == FriendshipStatus.ACCEPTED,
            (Friendship.requester_id == user_id) | (Friendship.addressee_id == user_id),
        )
        .all()
    )
    friends = []
    for row in rows:
        other_id = row.addressee_id if row.requester_id == user_id else row.requester_id
        u = db.query(User).filter(User.id == other_id).first()
        if u:
            friends.append(u)
    return friends


def _incoming_requests(db: Session, user_id: int) -> list[Friendship]:
    return (
        db.query(Friendship)
        .filter(
            Friendship.addressee_id == user_id,
            Friendship.status == FriendshipStatus.PENDING,
        )
        .order_by(Friendship.created_at.desc())
        .all()
    )


def _outgoing_requests(db: Session, user_id: int) -> list[Friendship]:
    return (
        db.query(Friendship)
        .filter(
            Friendship.requester_id == user_id,
            Friendship.status == FriendshipStatus.PENDING,
        )
        .order_by(Friendship.created_at.desc())
        .all()
    )


# ── Pages ──────────────────────────────────────────────────────────────────────

@router.get("/friends", response_class=HTMLResponse)
async def friends_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    friends         = _friend_list(db, user.id)
    incoming        = _incoming_requests(db, user.id)
    outgoing        = _outgoing_requests(db, user.id)
    return templates.TemplateResponse("friends.html", {
        "request":          request,
        "user":             user,
        "friends":          friends,
        "incoming":         incoming,
        "outgoing":         outgoing,
        "incoming_count":   len(incoming),
        "success":          request.query_params.get("success"),
        "error":            request.query_params.get("error"),
    })


@router.get("/friends/requests", response_class=HTMLResponse)
async def friends_requests_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    incoming = _incoming_requests(db, user.id)
    outgoing = _outgoing_requests(db, user.id)
    return templates.TemplateResponse("friends.html", {
        "request":        request,
        "user":           user,
        "friends":        _friend_list(db, user.id),
        "incoming":       incoming,
        "outgoing":       outgoing,
        "incoming_count": len(incoming),
        "active_tab":     "requests",
        "success":        request.query_params.get("success"),
        "error":          request.query_params.get("error"),
    })


# ── Actions (POST) ─────────────────────────────────────────────────────────────

@router.post("/friends/send")
async def send_friend_request_by_identifier(
    request: Request,
    identifier: str = Form(default=""),
    next: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Add Friend form — looks up target by email or nickname, then delegates."""
    identifier = identifier.strip()
    if not identifier:
        return RedirectResponse(url="/friends?error=user_not_found", status_code=303)

    target = (
        db.query(User)
        .filter(
            User.is_active == True,
            (User.email == identifier) | (User.nickname == identifier),
        )
        .first()
    )
    if not target:
        return RedirectResponse(url="/friends?error=user_not_found", status_code=303)

    if target.id == user.id:
        return RedirectResponse(url="/friends?error=self_request", status_code=303)

    existing = get_friendship(db, user.id, target.id)
    if existing:
        if existing.status == FriendshipStatus.ACCEPTED:
            return RedirectResponse(url="/friends?error=already_friends", status_code=303)
        if existing.status == FriendshipStatus.PENDING:
            return RedirectResponse(url="/friends?error=request_pending", status_code=303)
        if existing.status == FriendshipStatus.BLOCKED:
            return RedirectResponse(url="/friends?error=blocked", status_code=303)
        db.delete(existing)
        db.flush()

    row = Friendship(
        requester_id=user.id,
        addressee_id=target.id,
        status=FriendshipStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()

    notification_service.create_notification(
        db=db,
        user_id=target.id,
        title="New Friend Request",
        message=f"{user.nickname or user.email} sent you a friend request.",
        notification_type=NotificationType.FRIEND_REQUEST_RECEIVED,
        link="/friends/requests",
    )

    db.commit()
    return RedirectResponse(url=_safe_next(next, "/friends?success=request_sent"), status_code=303)


@router.post("/friends/request/{user_id}")
async def send_friend_request(
    user_id: int,
    next: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    # Self-request guard
    if user_id == user.id:
        return RedirectResponse(url="/friends?error=self_request", status_code=303)

    # Target must be active
    target = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not target:
        return RedirectResponse(url="/friends?error=user_not_found", status_code=303)

    # Duplicate / existing friendship guard
    existing = get_friendship(db, user.id, user_id)
    if existing:
        if existing.status == FriendshipStatus.ACCEPTED:
            return RedirectResponse(url="/friends?error=already_friends", status_code=303)
        if existing.status == FriendshipStatus.PENDING:
            return RedirectResponse(url="/friends?error=request_pending", status_code=303)
        if existing.status == FriendshipStatus.BLOCKED:
            return RedirectResponse(url="/friends?error=blocked", status_code=303)
        # DECLINED: allow re-request by deleting old row
        db.delete(existing)
        db.flush()

    row = Friendship(
        requester_id=user.id,
        addressee_id=user_id,
        status=FriendshipStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()

    notification_service.create_notification(
        db=db,
        user_id=user_id,
        title="New Friend Request",
        message=f"{user.nickname or user.email} sent you a friend request.",
        notification_type=NotificationType.FRIEND_REQUEST_RECEIVED,
        link="/friends/requests",
    )

    db.commit()
    return RedirectResponse(url=_safe_next(next, "/friends?success=request_sent"), status_code=303)


@router.post("/friends/accept/{friendship_id}")
async def accept_friend_request(
    friendship_id: int,
    next: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    row = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not row or row.addressee_id != user.id:
        return RedirectResponse(url="/friends/requests?error=not_found", status_code=303)
    if row.status != FriendshipStatus.PENDING:
        return RedirectResponse(url="/friends/requests?error=not_pending", status_code=303)

    row.status     = FriendshipStatus.ACCEPTED
    row.updated_at = datetime.now(timezone.utc)

    notification_service.create_notification(
        db=db,
        user_id=row.requester_id,
        title="Friend Request Accepted",
        message=f"{user.nickname or user.email} accepted your friend request.",
        notification_type=NotificationType.FRIEND_REQUEST_ACCEPTED,
        link="/friends",
    )

    db.commit()
    return RedirectResponse(url=_safe_next(next, "/friends?success=request_accepted"), status_code=303)


@router.post("/friends/decline/{friendship_id}")
async def decline_friend_request(
    friendship_id: int,
    next: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    row = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not row or row.addressee_id != user.id:
        return RedirectResponse(url="/friends/requests?error=not_found", status_code=303)
    if row.status != FriendshipStatus.PENDING:
        return RedirectResponse(url="/friends/requests?error=not_pending", status_code=303)

    row.status     = FriendshipStatus.DECLINED
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url=_safe_next(next, "/friends/requests?success=request_declined"), status_code=303)


@router.post("/friends/remove/{user_id}")
async def remove_friend(
    user_id: int,
    next: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    row = get_friendship(db, user.id, user_id)
    if not row or row.status != FriendshipStatus.ACCEPTED:
        return RedirectResponse(url="/friends?error=not_friends", status_code=303)
    if row.requester_id != user.id and row.addressee_id != user.id:
        return RedirectResponse(url="/friends?error=not_participant", status_code=303)

    db.delete(row)
    db.commit()
    return RedirectResponse(url=_safe_next(next, "/friends?success=friend_removed"), status_code=303)


@router.post("/friends/cancel/{friendship_id}")
async def cancel_friend_request(
    friendship_id: int,
    next: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Cancel an outgoing PENDING friend request (requester only)."""
    row = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not row or row.requester_id != user.id:
        return RedirectResponse(url="/friends?error=not_found", status_code=303)
    if row.status != FriendshipStatus.PENDING:
        return RedirectResponse(url="/friends?error=not_pending", status_code=303)

    db.delete(row)
    db.commit()
    return RedirectResponse(url=_safe_next(next, "/friends?success=request_cancelled"), status_code=303)

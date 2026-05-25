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

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from .student_features import _spec_ctx
from ...utils.football_positions import (
    normalize_position as _norm_pos,
    position_label as _raw_pos_label,
    position_short as _pos_short,
)
from ...models.friendship import (
    Friendship, FriendshipStatus, get_friendship, is_friends,
)
from typing import Optional
from ...models.license import UserLicense
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

def _format_pos(raw: str | None) -> str:
    """Convert raw position string to human-readable label. Safe for unknown values."""
    if not raw:
        return ""
    canonical = _norm_pos(raw) or raw
    label = _raw_pos_label(canonical)
    return label.replace("_", " ").title() if "_" in label else label


def _extract_pos_badges(lic, user: "User") -> list[dict]:
    """Return position badge list [{short, label}, ...] from license + user, max 4.

    Source priority: motivation_scores["positions"] (plural) →
                     motivation_scores["position"] (singular) → User.position.
    """
    ms = (lic.motivation_scores or {}) if lic else {}
    raw_list = ms.get("positions")
    if not raw_list or not isinstance(raw_list, list):
        single = ms.get("position") or (user.position if user else None)
        raw_list = [single] if single else []
    badges: list[dict] = []
    seen: set[str] = set()
    for raw in raw_list[:4]:
        if not raw:
            continue
        canonical = _norm_pos(raw) or raw
        if canonical in seen:
            continue
        seen.add(canonical)
        short = _pos_short(canonical)
        label = _raw_pos_label(canonical)
        label = label.replace("_", " ").title() if "_" in label else label
        badges.append({"short": short, "label": label})
    return badges


def _friend_list(db: Session, user_id: int) -> list[User]:
    """All accepted friends of user_id (either direction). 2-query, no N+1."""
    rows = (
        db.query(Friendship)
        .filter(
            Friendship.status == FriendshipStatus.ACCEPTED,
            (Friendship.requester_id == user_id) | (Friendship.addressee_id == user_id),
        )
        .all()
    )
    if not rows:
        return []
    other_ids = [
        row.addressee_id if row.requester_id == user_id else row.requester_id
        for row in rows
    ]
    users = db.query(User).filter(User.id.in_(other_ids)).all()
    user_map = {u.id: u for u in users}
    return [user_map[uid] for uid in other_ids if uid in user_map]


def _friend_data_map(db: Session, friends: list[User]) -> dict[int, dict]:
    """Return per-friend enriched data from a single UserLicense IN query.

    {user_id: {"level": int|None, "photo_url": str|None,
               "positions": [{"short": str, "label": str}, ...], "initials": str}}

    Photo priority: card_photo_portrait_url → player_card_photo_url → wc_photo_url.
    Position source: motivation_scores["positions"] → ["position"] → User.position.
    Initials derived from User.name, falling back to email.
    Zero extra queries beyond the one UserLicense IN fetch.
    """
    if not friends:
        return {}
    friend_ids = [f.id for f in friends]
    user_map   = {f.id: f for f in friends}

    rows = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id.in_(friend_ids),
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            UserLicense.is_active == True,
        )
        .all()
    )
    license_map = {r.user_id: r for r in rows}

    result = {}
    for uid in friend_ids:
        u        = user_map[uid]
        parts    = (u.name or u.email or "").split()
        initials = "".join(p[0].upper() for p in parts[:2]) if parts else "?"

        lic = license_map.get(uid)
        if lic:
            result[uid] = {
                "level":    lic.current_level,
                "photo_url": (
                    lic.card_photo_portrait_url
                    or lic.player_card_photo_url
                    or lic.wc_photo_url
                ),
                "positions": _extract_pos_badges(lic, u),
                "initials":  initials,
            }
        else:
            result[uid] = {
                "level":    None,
                "photo_url": None,
                "positions": _extract_pos_badges(None, u),
                "initials":  initials,
            }
    return result


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


def _friendship_state(
    db: Session, viewer_id: int, target_id: int
) -> tuple[str, int | None]:
    """Return (state_str, friendship_id_or_None) for the viewer's relation to target.

    States: none | accepted | pending_sent | pending_received | blocked
    DECLINED is treated as "none" (re-request allowed, old row deleted by send route).
    """
    row = get_friendship(db, viewer_id, target_id)
    if row is None:
        return "none", None
    if row.status == FriendshipStatus.ACCEPTED:
        return "accepted", row.id
    if row.status == FriendshipStatus.PENDING:
        if row.requester_id == viewer_id:
            return "pending_sent", row.id
        return "pending_received", row.id
    if row.status == FriendshipStatus.BLOCKED:
        return "blocked", row.id
    # DECLINED → allow re-request
    return "none", None


# ── Pages ──────────────────────────────────────────────────────────────────────

@router.get("/friends/search")
async def friends_search(
    request: Request,
    q: str = Query(min_length=2, max_length=50),
    limit: int = Query(10, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Live search for add-friend autocomplete.

    Returns JSON list of users matching q (name / nickname / email ilike),
    each decorated with the current friendship state. No CSRF needed (GET, read-only).
    """
    results = (
        db.query(User)
        .filter(
            User.is_active == True,
            User.id != user.id,
            or_(
                User.name.ilike(f"%{q}%"),
                User.nickname.ilike(f"%{q}%"),
                User.email.ilike(f"%{q}%"),
            ),
        )
        .limit(limit)
        .all()
    )

    items = []
    for u in results:
        display_name = u.nickname or u.name
        state, friendship_id = _friendship_state(db, user.id, u.id)
        items.append({
            "id": u.id,
            "display_name": display_name,
            "email": u.email,
            "state": state,
            "friendship_id": friendship_id,
        })

    return JSONResponse(items)


@router.get("/friends", response_class=HTMLResponse)
async def friends_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    friends     = _friend_list(db, user.id)
    incoming    = _incoming_requests(db, user.id)
    outgoing    = _outgoing_requests(db, user.id)
    friend_data = _friend_data_map(db, friends)
    return templates.TemplateResponse("friends.html", {
        "request":        request,
        "user":           user,
        "friends":        friends,
        "incoming":       incoming,
        "outgoing":       outgoing,
        "incoming_count": len(incoming),
        "friend_data":    friend_data,
        "success":        request.query_params.get("success"),
        "error":          request.query_params.get("error"),
        **_spec_ctx(user, db),
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
        "friend_data":    {},
        "success":        request.query_params.get("success"),
        "error":          request.query_params.get("error"),
        **_spec_ctx(user, db),
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

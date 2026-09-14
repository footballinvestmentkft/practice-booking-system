"""Canonical public Player identity policy.

`User.id` remains the stable identity key. `User.nickname` is the only value
allowed at public/challenge display boundaries.
"""
from __future__ import annotations

import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User


_NICKNAME_RE = re.compile(r"^[a-zA-Z0-9_áéíóöőúüűÁÉÍÓÖŐÚÜŰ]+$")
_RESERVED = frozenset({"admin", "moderator", "system", "support", "help", "info", "test"})


class PublicIdentityPolicyError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_public_nickname(value: str | None) -> str:
    nickname = (value or "").strip()
    if len(nickname) < 3 or len(nickname) > 30 or not _NICKNAME_RE.fullmatch(nickname):
        raise PublicIdentityPolicyError("PUBLIC_DISPLAY_IDENTITY_INVALID")
    if nickname.lower() in _RESERVED:
        raise PublicIdentityPolicyError("PUBLIC_DISPLAY_IDENTITY_RESERVED")
    return nickname


def nickname_available(
    db: Session, *, nickname: str, current_user_id: int | None = None,
) -> bool:
    normalized = normalize_public_nickname(nickname)
    query = db.query(User.id).filter(func.lower(User.nickname) == normalized.lower())
    if current_user_id is not None:
        query = query.filter(User.id != current_user_id)
    return query.first() is None


def require_public_display_identity(db: Session, user: User) -> str:
    try:
        nickname = normalize_public_nickname(user.nickname)
    except PublicIdentityPolicyError as exc:
        raise PublicIdentityPolicyError("PUBLIC_DISPLAY_IDENTITY_REQUIRED") from exc
    owners = db.query(User.id).filter(func.lower(User.nickname) == nickname.lower()).limit(2).all()
    if len(owners) != 1 or owners[0][0] != user.id:
        raise PublicIdentityPolicyError("PUBLIC_DISPLAY_IDENTITY_CONFLICT")
    return nickname


def safe_public_display_identity(user: User | None) -> str | None:
    """Presentation helper that never falls back to personal/account data."""
    if user is None:
        return None
    try:
        return normalize_public_nickname(user.nickname)
    except PublicIdentityPolicyError:
        return None

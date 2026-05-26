from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from pydantic import BaseModel

from ..config import settings


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT refresh token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def verify_token(token: str, token_type: str = "access") -> Optional[str]:
    """Verify JWT token and return username"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str = payload.get("sub")
        token_type_claim: str = payload.get("type")
        if username is None or token_type_claim != token_type:
            return None
        return username
    except JWTError:
        return None


def create_render_token(user_id: int, expires_seconds: int = 60) -> str:
    """Short-lived JWT for internal Playwright render auth (Welcome Card export).

    Claims: sub=str(user_id), purpose="wc_render", exp=now+expires_seconds.
    Only accepted by the Welcome Card render route when purpose=="wc_render".
    """
    return create_access_token(
        data={"sub": str(user_id), "purpose": "wc_render"},
        expires_delta=timedelta(seconds=expires_seconds),
    )


def create_challenge_render_token(user_id: int, challenge_id: int, expires_seconds: int = 60) -> str:
    """Short-lived JWT for internal Playwright render auth (Challenge Card export).

    Claims: sub=str(user_id), cid=challenge_id, purpose="vt_card_render", exp=now+expires_seconds.
    Only accepted by the challenge card preview route when purpose=="vt_card_render"
    and cid matches the requested challenge_id.
    """
    return create_access_token(
        data={"sub": str(user_id), "cid": challenge_id, "purpose": "vt_card_render"},
        expires_delta=timedelta(seconds=expires_seconds),
    )
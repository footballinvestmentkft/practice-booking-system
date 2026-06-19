import logging
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db
from .core.auth import verify_token
from .models.user import User, UserRole

logger = logging.getLogger(__name__)

security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


def get_current_user(
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """Get current authenticated user"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = credentials.credentials
    username = verify_token(token, "access")
    
    if username is None:
        raise credentials_exception
    
    user = db.query(User).filter(User.email == username).first()
    if user is None:
        raise credentials_exception
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    
    return user


def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current user if they are admin"""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user


def get_current_admin_or_instructor_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current user if they are admin or instructor"""
    if current_user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )
    return current_user


def get_current_sport_director_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current user if they are admin or sport director"""
    if current_user.role not in {UserRole.ADMIN, UserRole.SPORT_DIRECTOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sport Director role required"
        )
    return current_user


def get_current_pitch_manager_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current user if they can manage pitch assignments (admin, sport director, or instructor)."""
    if current_user.role not in {UserRole.ADMIN, UserRole.SPORT_DIRECTOR, UserRole.INSTRUCTOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to manage pitch assignments"
        )
    return current_user


def get_ball_training_poc_user(current_user: User = Depends(get_current_user)) -> User:
    """ADMIN users or explicitly allowlisted user IDs may access the Ball Training Hub.

    Allowlist is read from BALL_TRAINING_ALLOWED_USER_IDS (comma-separated integers).
    Empty string → only ADMIN can access.
    """
    from .config import settings
    if current_user.role == UserRole.ADMIN:
        return current_user
    raw = settings.BALL_TRAINING_ALLOWED_USER_IDS.strip()
    if raw:
        try:
            allowed = {int(x.strip()) for x in raw.split(",") if x.strip()}
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="BALL_TRAINING_ALLOWED_USER_IDS is misconfigured (non-integer value)",
            )
        if current_user.id in allowed:
            return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access to the ball training hub is not enabled for this account",
    )


async def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Get current user from cookie (optional, for web pages)"""
    # Try to get token from cookie
    token_cookie = request.cookies.get("access_token")

    if not token_cookie:
        return None

    # Extract token from "Bearer <token>" format
    try:
        token = token_cookie.replace("Bearer ", "")
        username = verify_token(token, "access")

        if username is None:
            return None

        user = db.query(User).filter(User.email == username).first()
        if user and user.is_active:
            return user
    except Exception as e:
        logger.error(f"Error verifying optional user token: {e}")

    return None


async def get_current_user_web(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    """Get current user from cookie (required, for web pages)"""
    user = await get_current_user_optional(request, db)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    return user


async def get_current_admin_user_web(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    """Get current admin user from cookie (for web-based API calls)"""
    user = await get_current_user_web(request, db)

    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )

    return user


async def get_current_sport_director_user_web(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Cookie-auth Sport Director dependency (for web pages / browser form submissions).

    Accepts session cookie (same as get_current_user_web).
    Raises 401 if not authenticated, 403 if authenticated but not ADMIN or SPORT_DIRECTOR.
    """
    user = await get_current_user_web(request, db)
    if user.role not in {UserRole.ADMIN, UserRole.SPORT_DIRECTOR}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sport Director role required",
        )
    return user


async def get_current_admin_user_hybrid(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> User:
    """Accept both Bearer (API clients) and cookie (browser JS fetches) admin auth.

    Try Bearer token first so existing API tests and curl clients are unaffected.
    Fall back to cookie so the admin web UI JS fetches (credentials:'include') work.
    """
    # 1. Bearer token path (API clients, existing tests)
    if credentials:
        token = credentials.credentials
        username = verify_token(token, "access")
        if username:
            user = db.query(User).filter(User.email == username).first()
            if user and user.is_active:
                if user.role != UserRole.ADMIN:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not enough permissions",
                    )
                return user

    # 2. Cookie path (browser JS fetch from admin pages)
    user = await get_current_user_optional(request, db)
    if user:
        if user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_admin_or_instructor_user_hybrid(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> User:
    """Accept both Bearer (API clients) and cookie (browser JS fetches) for ADMIN or INSTRUCTOR.

    Same pattern as get_current_admin_user_hybrid but permits INSTRUCTOR role too.
    """
    _allowed = {UserRole.ADMIN, UserRole.INSTRUCTOR}

    # 1. Bearer token path
    if credentials:
        token = credentials.credentials
        username = verify_token(token, "access")
        if username:
            user = db.query(User).filter(User.email == username).first()
            if user and user.is_active:
                if user.role not in _allowed:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Not enough permissions",
                    )
                return user

    # 2. Cookie path (browser JS fetch from admin pages)
    user = await get_current_user_optional(request, db)
    if user:
        if user.role not in _allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions",
            )
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user_media(
    request: Request,
    db: Session = Depends(get_db),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> User:
    """Dual-auth for juggling media endpoints: Bearer (native iOS app / API) OR cookie (Safari/WKWebView).

    Try Bearer first so existing API tests and iOS native app clients are unaffected.
    Fall back to access_token cookie so Safari <video src> and WKWebView same-site requests work.
    No role restriction — any active authenticated user is permitted.
    """
    # 1. Bearer token path (native iOS app, API clients, fetch() calls)
    if credentials:
        token = credentials.credentials
        username = verify_token(token, "access")
        if username:
            user = db.query(User).filter(User.email == username).first()
            if user and user.is_active:
                return user

    # 2. Cookie path (Safari <video src>, WKWebView same-site context)
    user = await get_current_user_optional(request, db)
    if user:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
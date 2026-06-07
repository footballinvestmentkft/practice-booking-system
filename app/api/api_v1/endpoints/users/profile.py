"""
User profile management endpoints
Self-service profile updates, password reset, and profile photo management.
"""
from typing import Any
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json

from .....database import get_db
from .....dependencies import get_current_user, get_current_admin_user
from .....core.security import get_password_hash
from .....models.user import User
from .....models.license import UserLicense
from .....schemas.user import User as UserSchema, UserUpdateSelf
from .....schemas.auth import ResetPassword
from .....services.profile_photo_service import (
    save_profile_photo,
    delete_profile_photo,
    trigger_bg_removal,
    run_bg_removal,
    STATUS_NONE,
)
from .....config import settings as _settings
from .helpers import validate_email_unique, validate_nickname

router = APIRouter()


@router.get("/me", response_model=UserSchema)
def get_current_user_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get current user profile with licenses
    """
    # ✅ CRITICAL FIX: Return ACTIVE licenses first, sorted by is_active DESC
    # This ensures frontend always receives active licenses before inactive ones
    # Without proper ordering, frontend may select inactive license first
    licenses = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == current_user.id)
        .order_by(UserLicense.is_active.desc(), UserLicense.id.asc())
        .all()
    )
    current_user.licenses = licenses
    return current_user


@router.patch("/me", response_model=UserSchema)
def update_own_profile(
    user_update: UserUpdateSelf,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Update own profile
    """
    # Check email uniqueness if email is being updated
    if user_update.email and user_update.email != current_user.email:
        if not validate_email_unique(db, user_update.email, current_user.id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )

    # Validate that emergency phone is different from user phone
    update_data = user_update.model_dump(exclude_unset=True)
    user_phone = update_data.get('phone', current_user.phone)
    emergency_phone = update_data.get('emergency_phone', current_user.emergency_phone)

    if user_phone and emergency_phone and user_phone == emergency_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A vészhelyzeti telefonszám nem lehet ugyanaz, mint a saját telefonszámod"
        )

    # Handle NDA acceptance with timestamp
    if 'nda_accepted' in update_data and update_data['nda_accepted']:
        setattr(current_user, 'nda_accepted_at', datetime.now(timezone.utc))

    # Update fields
    for field, value in update_data.items():
        if field == 'interests' and isinstance(value, list):
            # Convert interests list to JSON string for database storage
            setattr(current_user, field, json.dumps(value))
        else:
            setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)

    # Keep interests as JSON string for schema compatibility
    user_data = current_user.__dict__.copy()
    # Ensure interests is a string (not parsed to list)
    if user_data.get('interests') is None:
        user_data['interests'] = None

    return user_data


@router.post("/{user_id:int}/reset-password")
def reset_user_password(
    user_id: int,
    password_data: ResetPassword,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Reset user password (Admin only)
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    user.password_hash = get_password_hash(password_data.new_password)
    db.commit()
    
    return {"message": "Password reset successfully"}


@router.get("/check-nickname/{nickname}")
def check_nickname_availability(
    nickname: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Check if a nickname is available for use
    """
    is_valid, message = validate_nickname(nickname, db, current_user.id)

    return {
        "available": is_valid,
        "message": message
    }


# ── Profile Photo — Academy ID Phase 1 ───────────────────────────────────────

@router.post("/me/profile-photo", status_code=status.HTTP_201_CREATED)
async def upload_profile_photo(
    background_tasks: BackgroundTasks,
    photo:            UploadFile   = File(...),
    db:               Session      = Depends(get_db),
    current_user:     User         = Depends(get_current_user),
) -> Any:
    """
    Upload or replace the current user's profile photo.

    - Accepts JPEG, PNG, WEBP (max 5 MB).
    - Resizes to max 2048 px, saves as PNG.
    - Auto-triggers background removal if BG_REMOVAL_PROCESSOR != "null".
    - Returns { profile_photo_url, status }.
    """
    file_bytes   = await photo.read()
    content_type = photo.content_type or ""

    try:
        updated = save_profile_photo(file_bytes, content_type, current_user, db)
        db.commit()
        db.refresh(updated)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Auto-trigger BG removal only when a real processor is configured.
    # With BG_REMOVAL_PROCESSOR="null" (default) we skip — no status change.
    if _settings.BG_REMOVAL_PROCESSOR != "null" and trigger_bg_removal(updated, db):
        db.commit()
        background_tasks.add_task(
            run_bg_removal, updated.id, updated.profile_photo_url, db
        )

    return {
        "profile_photo_url": updated.profile_photo_url,
        "status":            updated.profile_photo_status,
    }


@router.get("/me/profile-photo/status")
def get_profile_photo_status(
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
) -> Any:
    """
    Return current profile photo status and URLs.

    status values: none / uploaded / processing / ready / failed
    NULL DB status is returned as "none".
    """
    return {
        "status":                       current_user.profile_photo_status or STATUS_NONE,
        "profile_photo_url":            current_user.profile_photo_url,
        "profile_photo_processed_url":  current_user.profile_photo_processed_url,
    }


@router.delete("/me/profile-photo", status_code=status.HTTP_204_NO_CONTENT)
def delete_current_profile_photo(
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
) -> None:
    """
    Delete the current user's profile photo.

    Removes disk files and sets all three photo columns to NULL.  Idempotent.
    """
    delete_profile_photo(current_user, db)
    db.commit()

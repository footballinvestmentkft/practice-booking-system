"""
User CRUD endpoints
Create, Read, Update, Delete operations for users (Admin only)
"""
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload

from .....database import get_db
from .....dependencies import get_current_admin_user, get_current_user
from .....core.security import get_password_hash
from .....models.user import User, UserRole
from .....schemas.user import (
    User as UserSchema, UserCreate, UserUpdate, UserWithStats, UserList
)
from .helpers import calculate_pagination, validate_email_unique, get_user_statistics
from .....services.canonical_policy import evaluate_profile_age_policy
from .....services.program_eligibility_service import record_guardian_consent

router = APIRouter()


@router.post("/", response_model=UserSchema)
def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Create new user (Admin only)
    """
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )

    profile_decision = evaluate_profile_age_policy(
        user_data.date_of_birth,
        bool(user_data.parental_consent and user_data.parental_consent_by),
    )
    if not profile_decision.usable:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=profile_decision.reason)
    
    # Create new user
    from .....models.specialization import SpecializationType

    # Convert specialization string to enum if provided
    specialization_enum = None
    if user_data.specialization:
        try:
            specialization_enum = SpecializationType[user_data.specialization]
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid specialization: {user_data.specialization}"
            )

    user = User(
        name=user_data.name,
        email=user_data.email,
        nickname=user_data.nickname,
        password_hash=get_password_hash(user_data.password),
        role=user_data.role,
        is_active=user_data.is_active,
        phone=user_data.phone,
        emergency_contact=user_data.emergency_contact,
        emergency_phone=user_data.emergency_phone,
        date_of_birth=user_data.date_of_birth,
        medical_notes=user_data.medical_notes,
        position=user_data.position,
        specialization=specialization_enum,
        onboarding_completed=user_data.onboarding_completed if hasattr(user_data, 'onboarding_completed') else False,
        payment_verified=user_data.payment_verified if hasattr(user_data, 'payment_verified') else False,
        parental_consent=user_data.parental_consent if hasattr(user_data, 'parental_consent') else False,
        parental_consent_by=user_data.parental_consent_by if hasattr(user_data, 'parental_consent_by') else None,
        created_by=current_user.id
    )

    db.add(user)
    db.flush()
    if profile_decision.age is not None and profile_decision.age < 18:
        record_guardian_consent(
            db,
            user=user,
            guardian_name=user_data.parental_consent_by,
            granted_by_user_id=current_user.id,
            evidence_reference="ADMIN_USER_CREATE",
        )
    db.commit()
    db.refresh(user)

    return user


@router.get("/", response_model=UserList)
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),  # FIX: Allow admin AND instructor
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    skip: Optional[int] = Query(default=None, ge=0),  # Backward compatibility
    limit: Optional[int] = Query(default=None, ge=1, le=100),  # Backward compatibility
    role: Optional[UserRole] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None
) -> Any:
    """
    List users with pagination and filtering (Admin and Instructor)

    - Admin: Can see all users
    - Instructor: Can see all students (for teaching purposes)

    Supports two pagination modes:
    - page/size: ?page=1&size=50
    - skip/limit: ?skip=0&limit=10 (backward compatibility)
    """
    # Check permissions
    if current_user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin and instructor can list users"
        )

    # IMPORTANT: Eager-load licenses to avoid N+1 queries
    query = db.query(User).options(joinedload(User.licenses))

    # Instructor can only see students
    if current_user.role == UserRole.INSTRUCTOR:
        query = query.filter(User.role == UserRole.STUDENT)

    # Apply filters
    if role:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    if search:
        query = query.filter(
            User.name.contains(search) | User.email.contains(search)
        )

    # Get total count
    total = query.count()

    # Apply pagination
    offset, page_size, current_page = calculate_pagination(page, size, skip, limit)
    users = query.offset(offset).limit(page_size).all()

    return UserList(
        users=users,
        total=total,
        page=current_page,
        size=page_size
    )


@router.get("/{user_id:int}", response_model=UserWithStats)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Get user by ID with statistics (Admin only)
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Get user statistics
    stats = get_user_statistics(db, user_id)
    
    return UserWithStats(
        **user.__dict__,
        **stats
    )


@router.patch("/{user_id:int}", response_model=UserSchema)
def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Update user (Admin only)
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check email uniqueness if email is being updated
    if user_update.email and user_update.email != user.email:
        if not validate_email_unique(db, user_update.email, user_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )
    
    # Update fields
    update_data = user_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)
    
    db.commit()
    db.refresh(user)
    
    return user


@router.delete("/{user_id:int}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Delete user (Admin only)
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Don't allow deleting self
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself"
        )
    
    # Soft delete by deactivating the user instead of hard delete
    # to preserve referential integrity
    user.is_active = False
    db.commit()
    
    return {"message": "User deactivated successfully"}

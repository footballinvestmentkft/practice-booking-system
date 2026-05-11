from datetime import timedelta, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from ....database import get_db
from ....dependencies import get_current_user
from ....core.auth import create_access_token, create_refresh_token, verify_token
from ....core.security import verify_password, get_password_hash
from ....models.user import User, UserRole
from ....models.invitation_code import InvitationCode
from ....models.credit_transaction import CreditTransaction, TransactionType
from ....schemas.auth import Login, Token, RefreshToken, ChangePassword
from ....schemas.user import User as UserSchema
from ....config import settings
from ....services.audit_service import AuditService
from ....models.audit_log import AuditAction
from ....utils.validators import validate_phone_number, validate_address, validate_name

router = APIRouter()


@router.post("/login", response_model=Token)
def login(
    user_credentials: Login,
    db: Session = Depends(get_db)
) -> Any:
    """
    OAuth2 compatible token login, get an access token for future requests
    """
    print(f"🔍 LOGIN ATTEMPT - Email: {user_credentials.email}")
    print(f"🔍 Password received: '{user_credentials.password}' (length: {len(user_credentials.password)})")

    print(f"🔍 STEP 1: About to query database for user...")
    user = db.query(User).filter(User.email == user_credentials.email).first()
    print(f"🔍 STEP 2: Database query completed!")
    print(f"🔍 User found: {user is not None}")
    
    if user:
        print(f"🔍 User active: {user.is_active}")
        password_check = verify_password(user_credentials.password, user.password_hash)
        print(f"🔍 Password valid: {password_check}")
        print(f"🔍 Password hash: {user.password_hash[:30]}...")
        # Test with expected password
        expected_check = verify_password("password123", user.password_hash)
        print(f"🔍 Expected password123 works: {expected_check}")
    
    if not user or not verify_password(user_credentials.password, user.password_hash):
        print(f"❌ LOGIN FAILED - User: {user is not None}, Password: {verify_password(user_credentials.password, user.password_hash) if user else False}")

        # 🔍 AUDIT: Log failed login
        audit_service = AuditService(db)
        audit_service.log(
            action=AuditAction.LOGIN_FAILED,
            user_id=user.id if user else None,
            details={
                "email": user_credentials.email,
                "reason": "invalid_password" if user else "user_not_found"
            }
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_token_expires = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={"sub": user.email}, expires_delta=refresh_token_expires
    )

    # 🔍 AUDIT: Log successful login
    audit_service = AuditService(db)
    audit_service.log(
        action=AuditAction.LOGIN,
        user_id=user.id,
        details={
            "email": user.email,
            "role": user.role.value if user.role else None,
            "success": True
        }
    )

    # 🏆 GAMIFICATION: Check for achievement unlocks
    from app.services.gamification import GamificationService
    gamification_service = GamificationService(db)
    try:
        unlocked = gamification_service.check_and_unlock_achievements(
            user_id=user.id,
            trigger_action="login"
        )
        if unlocked:
            print(f"🎉 Unlocked {len(unlocked)} achievement(s) for user {user.id}")
    except Exception as e:
        # Don't fail login if achievement check fails
        print(f"⚠️  Achievement check failed: {e}")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/login/form", response_model=Token)
def login_form(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
) -> Any:
    """
    OAuth2 compatible token login with form data
    """
    user = db.query(User).filter(User.email == form_data.username).first()
    
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_token_expires = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={"sub": user.email}, expires_delta=refresh_token_expires
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh", response_model=Token)
def refresh_token(
    token_data: RefreshToken,
    db: Session = Depends(get_db)
) -> Any:
    """
    Refresh access token using refresh token
    """
    username = verify_token(token_data.refresh_token, "refresh")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = db.query(User).filter(User.email == username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_token_expires = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    new_refresh_token = create_refresh_token(
        data={"sub": user.email}, expires_delta=refresh_token_expires
    )
    
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@router.post("/logout")
def logout() -> Any:
    """
    Logout (in a stateless JWT system, this is mostly symbolic)
    """
    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserSchema)
def read_users_me(
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get current user
    """
    return current_user


@router.post("/change-password")
def change_password(
    password_data: ChangePassword,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Change current user's password
    """
    if not verify_password(password_data.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect old password"
        )
    
    current_user.password_hash = get_password_hash(password_data.new_password)
    db.commit()

    return {"message": "Password updated successfully"}


# ==================== REGISTRATION ====================

class RegisterWithInvitation(BaseModel):
    """Registration request with invitation code"""
    email: EmailStr
    password: str
    name: str  # Keep for backward compatibility
    first_name: str
    last_name: str
    nickname: str
    phone: str
    date_of_birth: datetime
    nationality: str
    gender: str
    street_address: str
    city: str
    postal_code: str
    country: str
    invitation_code: str


@router.post("/register-with-invitation", response_model=Token)
def register_with_invitation(
    registration_data: RegisterWithInvitation,
    db: Session = Depends(get_db)
) -> Any:
    """
    Register a new user with an invitation code
    - Validates invitation code
    - Creates new user with STUDENT role
    - Marks invitation code as used
    - Adds bonus credits to user
    - Returns access token for immediate login
    """
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == registration_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Find and validate invitation code
    invitation_code = db.query(InvitationCode).filter(
        InvitationCode.code == registration_data.invitation_code.upper().strip()
    ).first()

    if not invitation_code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invitation code"
        )

    # Check if code is valid
    if not invitation_code.is_valid():
        if invitation_code.is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This invitation code has already been used"
            )
        if invitation_code.expires_at and invitation_code.expires_at < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This invitation code has expired"
            )

    # Check email restriction (if code is restricted to specific email)
    if not invitation_code.can_be_used_by_email(registration_data.email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This invitation code is restricted to {invitation_code.invited_email}"
        )

    # Validate password
    if len(registration_data.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters long"
        )

    # Validate first name
    is_valid, error = validate_name(registration_data.first_name, "First name")
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    # Validate last name
    is_valid, error = validate_name(registration_data.last_name, "Last name")
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    # Validate nickname
    is_valid, error = validate_name(registration_data.nickname, "Nickname")
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    # Validate and format phone number
    is_valid, formatted_phone, error = validate_phone_number(registration_data.phone)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    # Validate address
    is_valid, error = validate_address(
        registration_data.street_address,
        registration_data.city,
        registration_data.postal_code,
        registration_data.country
    )
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error
        )

    # Create new user
    new_user = User(
        email=registration_data.email,
        password_hash=get_password_hash(registration_data.password),
        name=registration_data.name,  # Keep for backward compatibility
        first_name=registration_data.first_name,
        last_name=registration_data.last_name,
        nickname=registration_data.nickname,
        phone=formatted_phone,  # Use validated and formatted international phone number
        role=UserRole.STUDENT,
        is_active=True,
        payment_verified=False,
        nda_accepted=False,
        parental_consent=False,
        credit_balance=invitation_code.bonus_credits,  # Add bonus credits immediately
        credit_purchased=0,
        date_of_birth=registration_data.date_of_birth,
        nationality=registration_data.nationality,
        gender=registration_data.gender,
        street_address=registration_data.street_address,
        city=registration_data.city,
        postal_code=registration_data.postal_code,
        country=registration_data.country
    )

    db.add(new_user)
    db.flush()  # Get user ID without committing

    # Log invitation bonus credit transaction (if any)
    if invitation_code.bonus_credits > 0:
        bonus_tx = CreditTransaction(
            user_id=new_user.id,
            amount=invitation_code.bonus_credits,
            transaction_type=TransactionType.INVITATION_BONUS.value,
            description=f"Registration bonus via invitation code {invitation_code.code}",
            balance_after=new_user.credit_balance,
            idempotency_key=f"invite_bonus:{invitation_code.id}:{new_user.id}",
            created_at=datetime.now(timezone.utc),
        )
        db.add(bonus_tx)

    # Mark invitation code as used
    invitation_code.is_used = True
    invitation_code.used_by_user_id = new_user.id
    invitation_code.used_at = datetime.now(timezone.utc)

    # Commit transaction
    db.commit()
    db.refresh(new_user)
    db.refresh(invitation_code)

    print(f"✅ New user registered: {new_user.email} (ID: {new_user.id}) with invitation code {invitation_code.code}")
    print(f"🎁 {invitation_code.bonus_credits} bonus credits added to {new_user.name}")

    # 🔍 AUDIT: Log successful registration
    audit_service = AuditService(db)
    audit_service.log(
        action=AuditAction.USER_CREATED,
        user_id=new_user.id,
        details={
            "email": new_user.email,
            "name": new_user.name,
            "invitation_code": invitation_code.code,
            "bonus_credits": invitation_code.bonus_credits,
            "registration_type": "invitation_code"
        },
        ip_address=None
    )

    # Create access token for immediate login
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_token_expires = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    access_token = create_access_token(
        data={"sub": new_user.email}, expires_delta=access_token_expires
    )
    refresh_token = create_refresh_token(
        data={"sub": new_user.email}, expires_delta=refresh_token_expires
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }
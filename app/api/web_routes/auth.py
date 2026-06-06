"""
Authentication routes for web interface
Handles login, logout, and age verification
"""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import timedelta, datetime, date, timezone
import asyncio
import logging
import traceback

from ...database import get_db
from ...dependencies import get_current_user_web, get_current_user_optional
from ...models.user import User, UserRole
from ...models.invitation_code import InvitationCode
from ...models.credit_transaction import CreditTransaction, TransactionType
from ...core.auth import create_access_token
from ...core.security import verify_password, get_password_hash
from ...config import settings
from ...utils.country_codes import COUNTRY_CODES, COUNTRY_OPTIONS, register_filters

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
register_filters(templates.env)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    """Home page - redirects to login or dashboard"""
    try:
        user = await get_current_user_optional(request, db)
        if user:
            return RedirectResponse(url="/dashboard", status_code=303)
    except:
        pass
    return RedirectResponse(url="/login", status_code=303)


def _safe_next(url: str) -> str:
    """Validate next-URL to prevent open-redirect attacks.
    Only relative paths starting with '/' (not '//') are allowed.
    """
    if url and url.startswith("/") and not url.startswith("//") and url not in ("/login", "/logout"):
        return url
    return "/dashboard"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "", registered: str = ""):
    """Display login page"""
    return templates.TemplateResponse("login.html", {"request": request, "next_url": next, "registered": registered == "1"})


@router.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db)
):
    """Process login form"""
    # Find user
    user = db.query(User).filter(User.email == email).first()

    # asyncio.to_thread: bcrypt.checkpw (~60ms) is CPU-bound + blocking.
    # Calling it directly in async def blocks the entire event loop for the
    # duration — at 125 VU/worker this serialises logins into a 7.5s queue.
    # Running in a thread lets other requests proceed concurrently. (Phase 8)
    pwd_ok = user is not None and await asyncio.to_thread(
        verify_password, password, user.password_hash
    )
    if not user or not pwd_ok:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid email or password", "next_url": next}
        )

    if not user.is_active:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Account is inactive", "next_url": next}
        )

    # Create access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    # Determine post-login redirect (age-verification takes priority over next)
    redirect_url = _safe_next(next) if next else "/dashboard"

    if user.role == UserRole.STUDENT and user.date_of_birth is None:
        # First time login - redirect to age verification (next preserved for after verification)
        redirect_url = "/age-verification"
        logger.info("first_time_student_login_redirect", extra={"user": user.email})

    # Redirect with token in cookie (SECURITY: SameSite + Secure flags)
    response = RedirectResponse(url=redirect_url, status_code=303)
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=settings.COOKIE_HTTPONLY,  # ✅ SECURITY: Prevents XSS cookie theft
        max_age=settings.COOKIE_MAX_AGE,  # ✅ SECURITY: Explicit expiry (1 hour)
        secure=settings.COOKIE_SECURE,  # ✅ SECURITY: HTTPS only in production
        samesite=settings.COOKIE_SAMESITE,  # ✅ SECURITY FIX: "strict" prevents CSRF
        path="/"  # Make cookie available across all paths
    )
    return response


@router.get("/logout")
async def logout():
    """Logout user"""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(key="access_token")
    return response


# Age Verification Routes

@router.get("/age-verification", response_class=HTMLResponse)
async def age_verification_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Age verification page for first-time students"""
    if user.role != UserRole.STUDENT:
        return RedirectResponse(url="/dashboard", status_code=303)

    # If already verified, redirect to dashboard
    if user.date_of_birth is not None:
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(
        "age_verification.html",
        {
            "request": request,
            "user": user,
            "today": datetime.now().date().isoformat()
        }
    )


@router.post("/age-verification")
async def age_verification_submit(
    request: Request,
    date_of_birth: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Process age verification form"""
    if user.role != UserRole.STUDENT:
        return RedirectResponse(url="/dashboard", status_code=303)

    try:
        # Parse date
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()

        # Validate date (not in future, reasonable age)
        today = date.today()
        if dob > today:
            return templates.TemplateResponse(
                "age_verification.html",
                {
                    "request": request,
                    "user": user,
                    "today": today.isoformat(),
                    "error": "Date of birth cannot be in the future",
                    "date_of_birth": date_of_birth
                }
            )

        # Calculate age
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        if age < 5:
            return templates.TemplateResponse(
                "age_verification.html",
                {
                    "request": request,
                    "user": user,
                    "today": today.isoformat(),
                    "error": "You must be at least 5 years old to use this platform",
                    "date_of_birth": date_of_birth
                }
            )

        if age > 120:
            return templates.TemplateResponse(
                "age_verification.html",
                {
                    "request": request,
                    "user": user,
                    "today": today.isoformat(),
                    "error": "Please enter a valid date of birth",
                    "date_of_birth": date_of_birth
                }
            )

        # Save date of birth
        user.date_of_birth = dob
        db.commit()
        db.refresh(user)

        logger.info("age_verified", extra={"user": user.email, "age": age})

        # Redirect to dashboard
        return RedirectResponse(url="/dashboard", status_code=303)

    except ValueError as e:
        return templates.TemplateResponse(
            "age_verification.html",
            {
                "request": request,
                "user": user,
                "today": date.today().isoformat(),
                "error": "Invalid date format. Please use the date picker.",
                "date_of_birth": date_of_birth
            }
        )
    except Exception as e:
        logger.error("age_verification_error", exc_info=True)
        traceback.print_exc()
        return templates.TemplateResponse(
            "age_verification.html",
            {
                "request": request,
                "user": user,
                "today": date.today().isoformat(),
                "error": f"An error occurred: {str(e)}"
            }
        )


# Registration Routes

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, db: Session = Depends(get_db)):
    """Display registration page (unauthenticated users only)"""
    try:
        user = await get_current_user_optional(request, db)
        if user:
            return RedirectResponse(url="/dashboard", status_code=303)
    except:
        pass
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "today": date.today().isoformat(), "country_list": COUNTRY_OPTIONS}
    )


@router.post("/register")
async def register_submit(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    nickname: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    phone: str = Form(...),
    date_of_birth: str = Form(...),
    nationality: str = Form(...),
    secondary_nationality: str = Form(None),
    gender: str = Form(...),
    street_address: str = Form(...),
    city: str = Form(...),
    postal_code: str = Form(...),
    country: str = Form(...),
    invitation_code: str = Form(...),
    db: Session = Depends(get_db)
):
    """Process registration form"""
    form_data = {
        "first_name": first_name,
        "last_name": last_name,
        "nickname": nickname,
        "email": email,
        "phone": phone,
        "date_of_birth": date_of_birth,
        "nationality": nationality,
        "secondary_nationality": secondary_nationality or "",
        "gender": gender,
        "street_address": street_address,
        "city": city,
        "postal_code": postal_code,
        "country": country,
        "invitation_code": invitation_code,
    }

    def error(msg: str):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": msg, "today": date.today().isoformat(), "country_list": COUNTRY_OPTIONS, **form_data}
        )

    try:
        # Basic field validation
        if len(password) < 6:
            return error("Password must be at least 6 characters.")
        if len(first_name.strip()) < 2:
            return error("First name must be at least 2 characters.")
        if len(last_name.strip()) < 2:
            return error("Last name must be at least 2 characters.")
        if len(nickname.strip()) < 2:
            return error("Nickname must be at least 2 characters.")
        if gender not in ("Male", "Female", "Non-binary", "Other"):
            return error("Please select a valid gender.")
        if nationality not in COUNTRY_CODES:
            return error("Please select a valid nationality from the list.")
        if secondary_nationality:
            if secondary_nationality not in COUNTRY_CODES:
                return error("Please select a valid secondary nationality from the list.")
            if secondary_nationality == nationality:
                return error("Secondary nationality must be different from primary nationality.")

        # Parse DOB
        try:
            dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        except ValueError:
            return error("Invalid date of birth format.")
        today = date.today()
        if dob > today:
            return error("Date of birth cannot be in the future.")
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 5:
            return error("You must be at least 5 years old to register.")
        if age > 120:
            return error("Please enter a valid date of birth.")

        # Check email uniqueness
        existing = db.query(User).filter(User.email == email.lower().strip()).first()
        if existing:
            return error("An account with this email already exists.")

        # Validate invitation code
        code_str = invitation_code.strip().upper()
        inv_code = db.query(InvitationCode).filter(
            InvitationCode.code == code_str
        ).first()

        if inv_code is None:
            return error("Invalid invitation code. Please check for typos and try again.")
        if inv_code.is_used:
            return error("This invitation code has already been used.")
        if inv_code.expires_at and inv_code.expires_at < datetime.now(timezone.utc):
            return error("This invitation code has expired. Please request a new one from your administrator.")
        if not inv_code.can_be_used_by_email(email):
            return error(
                "This invitation code was issued for a specific email address. "
                "Please register using the email address you received the invitation on."
            )

        # Create user
        new_user = User(
            name=f"{first_name.strip()} {last_name.strip()}",
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            nickname=nickname.strip(),
            email=email.lower().strip(),
            password_hash=get_password_hash(password),
            phone=phone.strip(),
            date_of_birth=dob,
            nationality=nationality.strip(),
            secondary_nationality=secondary_nationality.strip() if secondary_nationality else None,
            gender=gender,
            street_address=street_address.strip(),
            city=city.strip(),
            postal_code=postal_code.strip(),
            country=country.strip(),
            role=UserRole.STUDENT,
            is_active=True,
            credit_balance=inv_code.bonus_credits,
        )
        db.add(new_user)
        db.flush()  # get new_user.id

        # Log invitation bonus credit transaction (if any)
        from datetime import timezone as _tz
        if inv_code.bonus_credits > 0:
            bonus_tx = CreditTransaction(
                user_id=new_user.id,
                amount=inv_code.bonus_credits,
                transaction_type=TransactionType.INVITATION_BONUS.value,
                description=f"Registration bonus via invitation code {inv_code.code}",
                balance_after=new_user.credit_balance,
                idempotency_key=f"invite_bonus:{inv_code.id}:{new_user.id}",
                created_at=datetime.now(_tz.utc),
            )
            db.add(bonus_tx)

        # Mark invitation code as used
        inv_code.is_used = True
        inv_code.used_by_user_id = new_user.id
        inv_code.used_at = datetime.now(_tz.utc)
        db.commit()
        db.refresh(new_user)

        logger.info("new_registration", extra={"user": new_user.email, "credits": new_user.credit_balance})

    except Exception as e:
        logger.error("registration_error", exc_info=True)
        traceback.print_exc()
        return error(f"Registration failed: {str(e)}")

    # Post-commit: user + invite code are now permanently saved.
    # If session creation fails here, send the user to login instead of
    # showing a generic error that implies registration itself failed.
    try:
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": new_user.email}, expires_delta=access_token_expires
        )
        response = RedirectResponse(url="/dashboard", status_code=303)
        response.set_cookie(
            key="access_token",
            value=f"Bearer {access_token}",
            httponly=settings.COOKIE_HTTPONLY,
            max_age=settings.COOKIE_MAX_AGE,
            secure=settings.COOKIE_SECURE,
            samesite=settings.COOKIE_SAMESITE,
            path="/"
        )
        return response
    except Exception:
        logger.error("post_registration_session_error", exc_info=True)
        return RedirectResponse(
            url="/login?registered=1",
            status_code=303
        )

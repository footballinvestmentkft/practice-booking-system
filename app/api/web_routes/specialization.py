"""
Specialization-related routes (unlock, motivation, switch)
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pathlib import Path
from datetime import datetime, timezone
import logging
import traceback

from ...database import get_db
from ...dependencies import get_current_user_web, get_current_user
from ...models.user import User
from ...models.license import UserLicense
from ...models.credit_transaction import TransactionType
from ...models.specialization import SpecializationType
from ...services.canonical_policy import PROGRAM_MINIMUM_AGES, resolve_license_program, resolve_program_id
from ...services.program_eligibility_service import is_user_eligible_for_program
from ...services.licence_package import (
    ALLOWED_DURATIONS,
    DEFAULT_DURATION_MONTHS,
    validate_duration_months,
    cost_for_duration,
    calculate_expires_at,
)
from ...services.credit_service import (
    CreditService,
    InsufficientCreditsError as CreditInsufficientCreditsError,
)
from ...services.player_identity_service import (
    PlayerEntitlementExistsError,
    PlayerIdentityError,
    activate_football_player,
    unlock_football_player,
)

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/specialization/unlock")
async def specialization_unlock(
    specialization:  str = Form(...),
    duration_months: int = Form(DEFAULT_DURATION_MONTHS),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Unlock a specialization (Bearer token auth — iOS native).

    duration_months controls both the credit cost and the licence expiry:
      1  month  → 100 CR
      3  months → 250 CR
      6  months → 450 CR
      12 months → 800 CR

    expires_at is always set (never NULL) on new licences.
    Expiry uses calendar-month arithmetic (dateutil.relativedelta).
    """
    # Validate duration
    try:
        validate_duration_months(duration_months)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    cost = cost_for_duration(duration_months)

    resolution = resolve_program_id(specialization)
    if not resolution.usable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Program identity requires manual review or is invalid",
        )
    spec_type = SpecializationType(resolution.canonical_program.value)

    if resolution.canonical_program.value == "LFA_FOOTBALL_PLAYER":
        try:
            result = unlock_football_player(
                db,
                user=current_user,
                duration_months=duration_months,
            )
            db.commit()
        except PlayerEntitlementExistsError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.code,
            ) from exc
        except CreditInsufficientCreditsError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Insufficient credits. You have {exc.available} CR "
                    f"but need {exc.required} CR for a {duration_months}-month licence."
                ),
            ) from exc
        except PlayerIdentityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=exc.code,
            ) from exc
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="FOOTBALL_ENTITLEMENT_CONCURRENT_CONFLICT",
            ) from exc

        logger.info(
            "specialization_unlocked",
            extra={
                "user": current_user.email,
                "spec": result.license.canonical_program_id,
                "duration_months": duration_months,
                "cost": result.cost,
                "expires_at": result.license.expires_at.isoformat(),
            },
        )
        return {
            "success": True,
            "message": "Specialization unlocked successfully",
            "canonical_program_id": result.license.canonical_program_id,
            "new_balance": current_user.credit_balance,
            "license_id": result.license.id,
            "duration_months": duration_months,
            "cost": result.cost,
            "expires_at": result.license.expires_at.isoformat(),
            "season_start": result.assignment.season_start.isoformat(),
            "season_end": result.assignment.season_end.isoformat(),
            "season_base_category": result.assignment.season_base_category,
            "effective_category": result.assignment.effective_category,
            "base_participation_retained": result.assignment.base_participation_retained,
        }

    # Age requirement validation
    eligible, denial_reason = is_user_eligible_for_program(db, current_user, spec_type)
    if not eligible:
        required_age = PROGRAM_MINIMUM_AGES[resolution.canonical_program]
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{denial_reason}; minimum program age is {required_age}",
        )

    # Lock user row to prevent concurrent unlock race conditions
    current_user = db.query(User).with_for_update().filter(
        User.id == current_user.id
    ).first()
    db.refresh(current_user, with_for_update=True)

    if current_user.credit_balance < cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient credits. You have {current_user.credit_balance} CR "
                f"but need {cost} CR for a {duration_months}-month licence."
            ),
        )

    # Re-check after acquiring the lock
    existing_license = next(
        (
            row for row in db.query(UserLicense).filter(UserLicense.user_id == current_user.id).all()
            if resolve_license_program(row.canonical_program_id, row.specialization_type).canonical_program
            is resolution.canonical_program
        ),
        None,
    )
    if existing_license:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a license for {spec_type.value}",
        )

    # All validations passed — deduct credits and create licence
    now = datetime.now(timezone.utc)
    expires_at = calculate_expires_at(now, duration_months)

    new_license = UserLicense(
        user_id=current_user.id,
        specialization_type=spec_type.value,
        canonical_program_id=spec_type.value,
        current_level=1,
        max_achieved_level=1,
        started_at=now,
        payment_verified=True,
        payment_verified_at=now,
        onboarding_completed=False,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(new_license)
    db.flush()

    current_user.specialization = spec_type.value

    try:
        CreditService(db).deduct(
            user=current_user,
            amount=cost,
            transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
            description=(
                f"Unlocked specialization: {spec_type.value} "
                f"({duration_months} month{'s' if duration_months > 1 else ''})"
            ),
            idempotency_key=f"license_unlock_{new_license.id}",
        )
        db.commit()
    except CreditInsufficientCreditsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient credits. You have {exc.available} CR "
                f"but need {exc.required} CR for a {duration_months}-month licence."
            ),
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"License for {spec_type.value} already exists (concurrent request)",
        )

    logger.info(
        "specialization_unlocked",
        extra={
            "user": current_user.email,
            "spec": spec_type.value,
            "duration_months": duration_months,
            "cost": cost,
            "expires_at": expires_at.isoformat(),
        },
    )

    return {
        "success":        True,
        "message":        "Specialization unlocked successfully",
        "new_balance":    current_user.credit_balance,
        "license_id":     new_license.id,
        "duration_months": duration_months,
        "cost":           cost,
        "expires_at":     expires_at.isoformat(),
    }


@router.get("/specialization/motivation", response_class=HTMLResponse)
async def student_motivation_questionnaire_page(
    request: Request,
    spec: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Student self-assessment motivation questionnaire (part of onboarding)"""
    try:
        spec_type = SpecializationType(spec)
    except ValueError:
        return RedirectResponse(url="/specialization/select", status_code=303)

    # Create display name
    spec_display_map = {
        SpecializationType.GANCUJU_PLAYER: "GānCuju Player",
        SpecializationType.LFA_FOOTBALL_PLAYER: "LFA Football Player",
        SpecializationType.LFA_COACH: "LFA Coach",
        SpecializationType.INTERNSHIP: "Internship"
    }
    specialization_display = spec_display_map.get(spec_type, spec_type.value.replace('_', ' '))

    logger.info("motivation_questionnaire_access", extra={"user": user.email, "spec": spec_type.value})

    return templates.TemplateResponse(
        "student_motivation_questionnaire.html",
        {
            "request": request,
            "user": user,
            "specialization": spec_type.value,
            "specialization_display": specialization_display
        }
    )


@router.post("/specialization/motivation-submit")
async def student_motivation_questionnaire_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Process student's motivation self-assessment and complete onboarding"""
    try:
        # Parse form data
        form = await request.form()
        specialization = form.get("specialization")

        # Validate specialization
        try:
            spec_type = SpecializationType(specialization)
        except ValueError:
            return RedirectResponse(url="/specialization/select", status_code=303)

        # Get the 5 motivation scores
        goal_clarity = int(form.get("goal_clarity", 0))
        commitment_level = int(form.get("commitment_level", 0))
        engagement = int(form.get("engagement", 0))
        progress_mindset = int(form.get("progress_mindset", 0))
        initiative = int(form.get("initiative", 0))
        notes = form.get("notes", "").strip()

        # Validate scores (must be 1-5)
        scores = [goal_clarity, commitment_level, engagement, progress_mindset, initiative]
        if any(score < 1 or score > 5 for score in scores):
            return templates.TemplateResponse(
                "student_motivation_questionnaire.html",
                {
                    "request": request,
                    "user": user,
                    "specialization": spec_type.value,
                    "specialization_display": spec_type.value.replace('_', ' '),
                    "error": "All scores must be between 1 and 5"
                }
            )

        # Calculate average
        average_score = sum(scores) / len(scores)

        # Create motivation data object (student self-assessment)
        motivation_data = {
            "self_assessment": {
                "goal_clarity": goal_clarity,
                "commitment_level": commitment_level,
                "engagement": engagement,
                "progress_mindset": progress_mindset,
                "initiative": initiative,
                "average": round(average_score, 2),
                "notes": notes,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "assessed_by": "student"
            }
        }

        # Find or create UserLicense for this specialization
        license = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == spec_type.value
        ).first()

        if not license:
            if spec_type is SpecializationType.LFA_FOOTBALL_PLAYER:
                return templates.TemplateResponse(
                    "student_motivation_questionnaire.html",
                    {
                        "request": request,
                        "user": user,
                        "specialization": spec_type.value,
                        "specialization_display": spec_type.value.replace('_', ' '),
                        "error": "ACTIVE_FOOTBALL_ENTITLEMENT_REQUIRED",
                    },
                    status_code=409,
                )
            # Non-Player legacy onboarding retains its compatibility fallback.
            license = UserLicense(
                user_id=user.id,
                specialization_type=spec_type.value,
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc)
            )
            db.add(license)

        # Update motivation scores
        license.motivation_scores = motivation_data
        license.average_motivation_score = average_score
        license.motivation_last_assessed_at = datetime.now(timezone.utc)
        license.motivation_assessed_by = user.id  # Student self-assessment

        # Mark onboarding as completed via unified service
        from ...services.onboarding_service import complete_motivation_onboarding
        complete_motivation_onboarding(db, user, license)

        db.commit()
        db.refresh(user)
        db.refresh(license)

        logger.info("motivation_questionnaire_complete", extra={"user": user.email, "spec": spec_type.value, "avg_score": round(average_score, 2)})

        # Redirect directly to the spec dashboard — onboarding complete
        spec_slug = spec_type.value.lower().replace("_", "-")
        return RedirectResponse(url=f"/dashboard/{spec_slug}", status_code=303)

    except Exception:
        db.rollback()
        logger.error("motivation_questionnaire_error", extra={"user": user.email}, exc_info=True)
        return templates.TemplateResponse(
            "student_motivation_questionnaire.html",
            {
                "request": request,
                "user": user,
                "specialization": specialization if 'specialization' in locals() else "",
                "specialization_display": "",
                "error": "Unable to save motivation questionnaire",
            },
            status_code=500,
        )


# lfa_player_onboarding_page and lfa_player_onboarding_cancel are defined in
# onboarding.py (registered first in __init__.py). Duplicates removed from here.


@router.post("/specialization/switch")
async def specialization_switch(
    request: Request,
    specialization: str = Form(...),
    return_url: str = Form(None),  # 🔄 NEW: Optional return URL
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Switch student's active specialization (with onboarding check for new specs)"""
    redirect_url = return_url if return_url else "/dashboard"

    try:
        # Validate specialization type
        try:
            spec_type = SpecializationType(specialization)
        except ValueError:
            return RedirectResponse(url=redirect_url, status_code=303)

        if spec_type is SpecializationType.LFA_FOOTBALL_PLAYER:
            try:
                activate_football_player(db, user=user)
                db.commit()
            except PlayerIdentityError:
                db.rollback()
            return RedirectResponse(url=redirect_url, status_code=303)

        # SECURITY: Check if user has a license for this specialization
        license = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == spec_type.value
        ).first()

        if not license:
            logger.warning("specialization_switch_unauthorized", extra={"user": user.email, "spec": spec_type.value})
            return RedirectResponse(url=redirect_url, status_code=303)

        logger.info("specialization_switch", extra={"user": user.email, "spec": spec_type.value})

        # Update user's current specialization
        user.specialization = spec_type
        db.commit()
        db.refresh(user)

        # Redirect back to the page they came from (or dashboard)
        logger.info("specialization_switch_complete", extra={"user": user.email, "spec": spec_type.value})
        return RedirectResponse(url=redirect_url, status_code=303)

    except Exception as e:
        db.rollback()
        logger.error("specialization_switch_error", extra={"user": user.email}, exc_info=True)
        return RedirectResponse(url=redirect_url, status_code=303)

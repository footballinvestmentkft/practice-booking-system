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
from ...models.credit_transaction import CreditTransaction, TransactionType
from ...models.specialization import SpecializationType
from ...utils.age_requirements import validate_specialization_for_age

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/specialization/unlock")
async def specialization_unlock(
    specialization: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Unlock a specialization from Streamlit (costs 100 credits)
    Uses Bearer token authentication for Streamlit frontend

    BUSINESS LOGIC:
    - All specializations are VISIBLE to all users (for motivation/future planning)
    - Age requirement is ONLY enforced at UNLOCK time
    """
    if current_user.credit_balance < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient credits. You have {current_user.credit_balance} credits, but need 100."
        )

    # Map specialization enum
    spec_mapping = {
        "LFA_PLAYER": SpecializationType.LFA_FOOTBALL_PLAYER,
        "LFA_COACH": SpecializationType.LFA_COACH,
        "INTERNSHIP": SpecializationType.INTERNSHIP,
        "GANCUJU_PLAYER": SpecializationType.GANCUJU_PLAYER
    }

    spec_type = spec_mapping.get(specialization)
    if not spec_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid specialization: {specialization}"
        )

    # ✅ AGE REQUIREMENT VALIDATION (Business requirement)
    # Check if user meets age requirement for this specialization
    if not validate_specialization_for_age(specialization, current_user.age):
        # Get age requirement text for error message
        age_requirements = {
            "INTERNSHIP": "18+",
            "LFA_COACH": "14+",
            "GANCUJU_PLAYER": "5+",
            "LFA_PLAYER": "5+"
        }
        required_age = age_requirements.get(specialization, "unknown")

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Age requirement not met. This specialization requires age {required_age}. Your current age: {current_user.age or 'not set'}."
        )

    # Lock user row to prevent concurrent unlock race conditions
    current_user = db.query(User).with_for_update().filter(User.id == current_user.id).first()

    # Check for existing license (re-check AFTER acquiring the lock)
    existing_license = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id,
        UserLicense.specialization_type == spec_type.value
    ).first()
    if existing_license:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You already have a license for {spec_type.value}"
        )

    # Deduct credits (only after all validations pass)
    current_user.credit_balance -= 100

    # Create user license
    now = datetime.now(timezone.utc)
    new_license = UserLicense(
        user_id=current_user.id,
        specialization_type=spec_type.value,
        current_level=1,
        max_achieved_level=1,
        started_at=now,
        payment_verified=True,
        payment_verified_at=now,
        onboarding_completed=False,
        is_active=True
    )
    db.add(new_license)
    db.flush()  # Get the ID

    # Create credit transaction
    import uuid
    idempotency_key = f"unlock_{current_user.id}_{spec_type.value}_{now.timestamp()}"

    credit_transaction = CreditTransaction(
        user_license_id=new_license.id,
        amount=-100,
        transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
        description=f"Unlocked specialization: {spec_type.value}",
        balance_after=current_user.credit_balance,
        idempotency_key=idempotency_key,
        created_at=now,
    )
    db.add(credit_transaction)

    # Update user specialization
    current_user.specialization = spec_type.value

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"License for {spec_type.value} already exists (concurrent request)"
        )

    return {
        "success": True,
        "message": "Specialization unlocked successfully",
        "new_balance": current_user.credit_balance,
        "license_id": new_license.id
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
            # Should not happen if admin verified payment properly, but create if missing
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

    except Exception as e:
        db.rollback()
        logger.error("motivation_questionnaire_error", extra={"user": user.email}, exc_info=True)
        return templates.TemplateResponse(
            "student_motivation_questionnaire.html",
            {
                "request": request,
                "user": user,
                "specialization": specialization if 'specialization' in locals() else "",
                "specialization_display": "",
                "error": f"An error occurred: {str(e)}"
            }
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


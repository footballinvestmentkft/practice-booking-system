"""
Onboarding routes for student specialization selection and questionnaires
"""
from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pathlib import Path
from datetime import datetime, timezone, date
import traceback
import uuid

from ...database import get_db
from ...dependencies import get_current_user_web, get_current_user
from ...models.user import User
from ...models.license import UserLicense
from ...models.specialization import SpecializationType
from ...models.credit_transaction import CreditTransaction, TransactionType
from ...utils.age_requirements import get_available_specializations
from ...utils.football_positions import normalize_position, normalize_positions, VALID_POSITION_VALUES
from ...skills_config import SKILL_CATEGORIES, get_all_skill_keys
from ...services.skill_progression import SYSTEM_BASELINE
from ...services.licence_package import DEFAULT_DURATION_MONTHS, cost_for_duration, calculate_expires_at
import logging

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/specialization/select", response_class=HTMLResponse)
async def specialization_select_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Display specialization selection page - only show active specializations"""
    active_specializations = {
        "INTERNSHIP": {"has_instructor": True, "max_students": 30},
        "LFA_FOOTBALL_PLAYER": {"has_instructor": True, "max_students": 25},
        "LFA_COACH": {"has_instructor": True, "max_students": 20},
        "GANCUJU_PLAYER": {"has_instructor": True, "max_students": 25}
    }

    # Get user's existing licenses
    user_licenses = db.query(UserLicense).filter(UserLicense.user_id == user.id).all()
    user_specialization_types = [license.specialization_type for license in user_licenses]

    return templates.TemplateResponse(
        "specialization_select.html",
        {
            "request": request,
            "user": user,
            "active_specializations": active_specializations,
            "user_specialization_types": user_specialization_types
        }
    )


@router.post("/specialization/select")
async def specialization_select_submit(
    request: Request,
    specialization: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Process specialization selection and complete onboarding"""
    try:
        # Validate specialization type
        try:
            spec_type = SpecializationType[specialization]
        except KeyError:
            logger.warning("onboarding_invalid_specialization", extra={"value": specialization})
            return templates.TemplateResponse(
                "specialization_select.html",
                {"request": request, "user": user, "error": f"Invalid specialization: {specialization}"}
            )

        # Lock user row to prevent concurrent unlock race conditions
        user = db.query(User).with_for_update().filter(User.id == user.id).first()

        # Check if user already has a license (AFTER acquiring the lock)
        user_license = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == spec_type.value
        ).first()

        # If NO license exists, this is a NEW unlock.
        # Web path defaults to DEFAULT_DURATION_MONTHS (1 month) — no UI picker yet.
        # expires_at is always set; perpetual licences are no longer created here.
        if not user_license:
            unlock_duration = DEFAULT_DURATION_MONTHS
            unlock_cost     = cost_for_duration(unlock_duration)

            if user.credit_balance < unlock_cost:
                logger.warning("onboarding_insufficient_credits", extra={"user": user.email, "balance": user.credit_balance, "required": unlock_cost, "spec": spec_type.value})
                error_msg = f"Insufficient credits! Unlocking {spec_type.value.replace('_', ' ')} requires {unlock_cost} credits. You have {user.credit_balance} credits."
                return RedirectResponse(url=f"/dashboard?error={error_msg}", status_code=303)

            now        = datetime.now(timezone.utc)
            expires_at = calculate_expires_at(now, unlock_duration)

            logger.info("onboarding_credits_deducted", extra={"user": user.email, "cost": unlock_cost, "new_balance": user.credit_balance - unlock_cost})
            user.credit_balance -= unlock_cost

            user_license = UserLicense(
                user_id=user.id,
                specialization_type=spec_type.value,
                current_level=1,
                started_at=now,
                payment_verified=True,
                payment_verified_at=now,
                onboarding_completed=False,
                is_active=True,
                expires_at=expires_at,
            )
            db.add(user_license)
            db.flush()

            credit_transaction = CreditTransaction(
                user_license_id=user_license.id,
                amount=-unlock_cost,
                transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
                description=(
                    f"Unlocked specialization: {spec_type.value.replace('_', ' ')} "
                    f"({unlock_duration} month)"
                ),
                balance_after=user.credit_balance,
                idempotency_key=str(uuid.uuid4()),
                created_at=now,
            )
            db.add(credit_transaction)

            logger.info("onboarding_spec_unlocked", extra={"user": user.email, "spec": spec_type.value, "cost": unlock_cost, "duration_months": unlock_duration, "expires_at": expires_at.isoformat()})

        logger.info("onboarding_spec_set", extra={"user": user.email, "spec": str(spec_type)})

        # Update user's specialization BUT DO NOT mark onboarding as completed yet
        # Student needs to fill out motivation questionnaire first
        user.specialization = spec_type
        # onboarding_completed will be set to True AFTER motivation questionnaire

        db.flush()  # Flush to catch any DB errors before commit
        db.commit()
        db.refresh(user)  # Refresh to get updated values

        logger.info("onboarding_spec_selected", extra={"user": user.email, "spec": spec_type.value})

        # Redirect based on specialization type
        if spec_type == SpecializationType.LFA_FOOTBALL_PLAYER:
            # LFA Player gets specialized onboarding questionnaire
            return RedirectResponse(url=f"/specialization/lfa-player/onboarding", status_code=303)
        else:
            # Other specializations get standard motivation questionnaire
            return RedirectResponse(url=f"/specialization/motivation?spec={spec_type.value}", status_code=303)

    except IntegrityError:
        db.rollback()
        logger.warning("onboarding_duplicate_license_attempt", extra={"user": user.email})
        return RedirectResponse(url="/dashboard", status_code=303)
    except Exception as e:
        db.rollback()
        logger.error("onboarding_spec_selection_error", extra={"user": user.email}, exc_info=True)
        return RedirectResponse(url=f"/dashboard?error={str(e)}", status_code=303)


@router.get("/specialization/lfa-player/onboarding", response_class=HTMLResponse)
async def lfa_player_onboarding_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """
    LFA Player specialized onboarding questionnaire
    Multi-step: Position -> Self-Assessment -> Motivation
    """
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER"
    ).first()

    if not license:
        logger.warning("onboarding_no_license", extra={"user": user.email})
        return RedirectResponse(url="/dashboard", status_code=303)

    # If already completed onboarding, redirect to dashboard
    if license.onboarding_completed:
        logger.info("onboarding_already_complete", extra={"user": user.email})
        return RedirectResponse(url="/dashboard", status_code=303)

    logger.info("onboarding_started", extra={"user": user.email})

    return templates.TemplateResponse(
        "lfa_player_onboarding.html",
        {
            "request": request,
            "user": user,
            "license": license,
            "skill_categories": SKILL_CATEGORIES,   # 44 skills across 4 categories
        }
    )


@router.post("/specialization/lfa-player/onboarding-web")
async def lfa_player_onboarding_web_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)   # cookie auth — HTML frontend
):
    """
    Web-compatible LFA Player onboarding submit.
    Called via AJAX fetch from lfa_player_onboarding.html (cookie session auth).

    Accepts the same JSON body as /specialization/lfa-player/onboarding-submit
    (Bearer JWT endpoint for Streamlit) but uses get_current_user_web so that
    browser-session users can submit the 6-step onboarding form.

    Request body (JSON):
        position   : str  — STRIKER | MIDFIELDER | DEFENDER | GOALKEEPER
        goals      : str  — dropdown value
        motivation : str  — free text
        skills     : dict — all 44 skill keys (0-99 scale, step=1)

    Returns JSON {"success": true} on success; {"error": "..."} on failure.
    """
    try:
        body = await request.json()

        raw_position   = body.get("position", "")
        raw_positions  = body.get("positions", [])   # full list: [primary, ...secondaries]
        goals          = body.get("goals", "")
        motivation     = body.get("motivation", "")
        skills         = body.get("skills", {})
        # foot_dominance: 0 = fully left, 50 = balanced, 100 = fully right. Default: 50.
        foot_dominance = body.get("foot_dominance", 50)
        height_cm      = body.get("height_cm")
        weight_kg      = body.get("weight_kg")
        preferred_foot = body.get("preferred_foot")

        logger.info("onboarding_submit_received", extra={"user": user.email, "skill_count": len(skills)})

        # Validate primary position
        position = normalize_position(raw_position)
        if not position:
            return JSONResponse(status_code=400, content={"error": f"Invalid position: {raw_position!r}"})

        # Validate positions list (primary + secondaries)
        if not raw_positions:
            raw_positions = [raw_position]
        if not isinstance(raw_positions, list) or len(raw_positions) > 4:
            return JSONResponse(status_code=400, content={"error": "positions must be a list of 1–4 values"})
        positions = normalize_positions(raw_positions)
        if positions is None:
            return JSONResponse(status_code=400, content={"error": f"Invalid value in positions list: {raw_positions}"})
        if positions[0] != position:
            return JSONResponse(status_code=400, content={"error": "positions[0] must match the primary position field"})

        # Validate height_cm
        if height_cm is None:
            return JSONResponse(status_code=422, content={"error": "height_cm is required"})
        try:
            height_cm = int(height_cm)
        except (TypeError, ValueError):
            return JSONResponse(status_code=422, content={"error": "height_cm must be an integer"})
        if not (120 <= height_cm <= 230):
            return JSONResponse(status_code=422, content={"error": "height_cm must be 120–230"})

        # Validate weight_kg
        if weight_kg is None:
            return JSONResponse(status_code=422, content={"error": "weight_kg is required"})
        try:
            weight_kg = int(weight_kg)
        except (TypeError, ValueError):
            return JSONResponse(status_code=422, content={"error": "weight_kg must be an integer"})
        if not (35 <= weight_kg <= 160):
            return JSONResponse(status_code=422, content={"error": "weight_kg must be 35–160"})

        # Validate preferred_foot
        if preferred_foot is None:
            return JSONResponse(status_code=422, content={"error": "preferred_foot is required"})
        if preferred_foot not in ("left", "right", "both"):
            return JSONResponse(status_code=422, content={"error": "preferred_foot must be left|right|both"})

        # Validate foot_dominance
        try:
            foot_dominance = float(foot_dominance)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"error": "foot_dominance must be a number 0–100"})
        if not (0.0 <= foot_dominance <= 100.0):
            return JSONResponse(status_code=400, content={"error": "foot_dominance out of range (0–100)"})

        # Validate all 44 skills present
        expected_skills = set(get_all_skill_keys())
        received_skills = set(skills.keys())
        missing = expected_skills - received_skills
        if missing:
            return JSONResponse(status_code=400, content={"error": f"Missing skills: {sorted(missing)}"})

        # Validate skill values 0-99
        for key, val in skills.items():
            if not (0 <= float(val) <= 99):
                return JSONResponse(status_code=400, content={"error": f"Skill value out of range: {key}={val}"})

        # Get LFA Player license
        license = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER"
        ).first()

        if not license:
            return JSONResponse(status_code=400, content={"error": "LFA Player license not found. Unlock the specialization first."})

        # Write skills to football_skills JSONB column (engine-compatible format).
        # Business rule: visible starting level is always SYSTEM_BASELINE (60.0).
        # Self-assessment is stored separately and must not become current_level.
        football_skills = {}
        for skill_key, baseline_value in skills.items():
            football_skills[skill_key] = {
                "system_baseline":  SYSTEM_BASELINE,          # 60.0 — fixed for all new players
                "self_assessment":  float(baseline_value),    # stored; not the visible level
                "baseline":         SYSTEM_BASELINE,          # 60.0 — EMA anchor (backward compat)
                "current_level":    SYSTEM_BASELINE,          # 60.0 — visible starting point
                "total_delta":      0.0,
                "tournament_delta": 0.0,
                "assessment_delta": 0.0,
                "last_updated":     datetime.now(timezone.utc).isoformat(),
                "assessment_count": 0,
                "tournament_count": 0,
            }

        average_skill = sum(float(v) for v in skills.values()) / len(skills)

        license.football_skills      = football_skills
        # Dominant foot: canonical storage in UserLicense columns (not motivation_scores).
        # right_foot_score = slider value; left_foot_score = complement.
        license.right_foot_score     = foot_dominance
        license.left_foot_score      = 100.0 - foot_dominance
        license.motivation_scores    = {
            "position":              position,           # primary (snake_case)
            "positions":             positions,          # [primary, ...secondaries]
            "goals":                 goals,
            "motivation":            motivation,
            "average_skill_level":   round(average_skill, 1),
            "onboarding_completed_at": datetime.now(timezone.utc).isoformat(),
            "height_cm":             height_cm,
            "weight_kg":             weight_kg,
            "preferred_foot":        preferred_foot,
        }
        license.average_motivation_score  = average_skill
        license.motivation_last_assessed_at = datetime.now(timezone.utc)
        license.motivation_assessed_by    = user.id

        # Update user's primary position (snake_case canonical value)
        user.position = position

        # Mark onboarding complete via unified service
        from ...services.onboarding_service import complete_lfa_player_onboarding
        complete_lfa_player_onboarding(db, user, license, football_skills)

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(license, "football_skills")
        flag_modified(license, "motivation_scores")

        db.commit()
        db.refresh(user)
        db.refresh(license)

        logger.info("onboarding_complete", extra={"user": user.email, "position": position, "positions": positions, "skill_count": len(skills), "avg_skill": round(average_skill, 1), "foot_dominance": foot_dominance})

        return {
            "success":               True,
            "redirect":              "/dashboard/lfa-football-player",
            "user_id":               user.id,
            "welcome_card_url":      "/profile/onboarding-card",
            "welcome_card_export_url": "/profile/onboarding-card/export",
        }

    except Exception as e:
        db.rollback()
        logger.error("onboarding_submit_error", extra={"user": user.email}, exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/specialization/lfa-player/onboarding-cancel")
async def lfa_player_onboarding_cancel(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """
    Cancel LFA Player onboarding and refund credits
    """
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.onboarding_completed == False  # Only incomplete onboarding
    ).first()

    if license:
        REFUND_AMOUNT = 100

        # Refund the credits
        user.credit_balance += REFUND_AMOUNT

        # Log the refund transaction — must use user_id, NOT user_license_id,
        # because db.delete(license) below triggers CASCADE on user_license_id FK.
        refund_transaction = CreditTransaction(
            user_id=user.id,
            amount=REFUND_AMOUNT,
            transaction_type=TransactionType.REFUND.value,
            description="Refund for cancelled LFA Football Player onboarding",
            balance_after=user.credit_balance,
            idempotency_key=str(uuid.uuid4()),
            created_at=datetime.now()
        )
        db.add(refund_transaction)

        # Delete the license
        db.delete(license)

        # Reset user's specialization
        user.specialization = None

        db.commit()

        logger.info("onboarding_cancelled_refund", extra={"user": user.email, "refund": REFUND_AMOUNT})
        return RedirectResponse(url="/dashboard?success=Onboarding cancelled. 100 credits refunded.", status_code=303)
    else:
        return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/specialization/lfa-player/onboarding-submit")
async def lfa_player_onboarding_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)  # Uses Bearer token from Streamlit API call
):
    """
    Process LFA Player onboarding questionnaire
    NEW: Accepts 44 skills on 0-99 scale, writes directly to football_skills
    Saves: position, skills (44 skills, 0-99 scale), goals
    """
    try:
        # Parse JSON body
        body = await request.json()

        # Get form data
        raw_position   = body.get("position")
        raw_positions  = body.get("positions", [])  # full list; legacy callers may omit this
        goals          = body.get("goals", "")
        motivation     = body.get("motivation", "")
        skills         = body.get("skills", {})  # All skills, 0-99 scale
        # foot_dominance: 0 = fully left, 50 = balanced, 100 = fully right. Default: 50.
        foot_dominance = body.get("foot_dominance", 50)
        # Optional physical fields — accepted here for parity with web handler.
        # Defaults used for legacy API callers that pre-date P1.
        height_cm      = body.get("height_cm", 175)
        weight_kg      = body.get("weight_kg", 72)
        preferred_foot = body.get("preferred_foot", "right")

        logger.info("onboarding_full_submit_received", extra={"user": user.email, "skill_count": len(skills)})

        # Validate primary position (accepts legacy UPPERCASE or canonical snake_case)
        position = normalize_position(raw_position or "")
        if not position:
            raise ValueError(f"Invalid position: {raw_position!r}")

        # Validate positions list — legacy callers may only send `position`
        if not raw_positions:
            raw_positions = [raw_position]
        if not isinstance(raw_positions, list) or len(raw_positions) > 4:
            raise ValueError("positions must be a list of 1–4 values")
        positions = normalize_positions(raw_positions)
        if positions is None:
            raise ValueError(f"Invalid value in positions list: {raw_positions}")
        if positions[0] != position:
            raise ValueError("positions[0] must match the primary position field")

        # Validate foot_dominance
        try:
            foot_dominance = float(foot_dominance)
        except (TypeError, ValueError):
            raise ValueError("foot_dominance must be a number 0–100")
        if not (0.0 <= foot_dominance <= 100.0):
            raise ValueError(f"foot_dominance out of range: {foot_dominance} (must be 0–100)")

        # Validate skills (must have all 36 skills)
        from app.skills_config import get_all_skill_keys
        expected_skills = set(get_all_skill_keys())
        received_skills = set(skills.keys())

        if received_skills != expected_skills:
            missing = expected_skills - received_skills
            extra = received_skills - expected_skills
            logger.warning("onboarding_skill_mismatch", extra={"user": user.email, "missing": list(missing), "extra": list(extra)})
            # Don't fail, just log - allow submission with whatever skills we have

        # Get user's LFA Player license
        license = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER"
        ).first()

        if not license:
            raise ValueError("LFA Player license not found")

        # Write skills to football_skills in engine-compatible format.
        # Business rule: visible starting level is always SYSTEM_BASELINE (60.0).
        # Self-assessment is stored separately and must not become current_level.
        football_skills = {}
        for skill_key, baseline_value in skills.items():
            football_skills[skill_key] = {
                "system_baseline":  SYSTEM_BASELINE,          # 60.0 — fixed for all new players
                "self_assessment":  float(baseline_value),    # stored; not the visible level
                "baseline":         SYSTEM_BASELINE,          # 60.0 — EMA anchor (backward compat)
                "current_level":    SYSTEM_BASELINE,          # 60.0 — visible starting point
                "total_delta":      0.0,
                "tournament_delta": 0.0,
                "assessment_delta": 0.0,
                "last_updated":     datetime.now(timezone.utc).isoformat(),
                "assessment_count": 0,
                "tournament_count": 0,
            }

        license.football_skills = football_skills
        # Dominant foot: canonical storage in UserLicense columns (not motivation_scores).
        license.right_foot_score = foot_dominance
        license.left_foot_score  = 100.0 - foot_dominance

        average_skill = sum(skills.values()) / len(skills) if skills else SYSTEM_BASELINE
        license.motivation_scores = {
            "position":              position,           # primary (snake_case)
            "positions":             positions,          # [primary, ...secondaries]
            "goals":                 goals,
            "motivation":            motivation,
            "average_skill_level":   round(average_skill, 1),
            "onboarding_completed_at": datetime.now(timezone.utc).isoformat(),
            "height_cm":             height_cm,
            "weight_kg":             weight_kg,
            "preferred_foot":        preferred_foot,
        }
        license.average_motivation_score = average_skill
        license.motivation_last_assessed_at = datetime.now(timezone.utc)
        license.motivation_assessed_by = user.id

        # Update user's primary position (snake_case canonical value)
        user.position = position

        # Mark onboarding as completed via unified service
        from ...services.onboarding_service import complete_lfa_player_onboarding
        complete_lfa_player_onboarding(db, user, license, football_skills)

        # Flag JSONB field as modified
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(license, 'football_skills')
        flag_modified(license, 'motivation_scores')

        db.commit()
        db.refresh(user)
        db.refresh(license)

        logger.info("onboarding_full_complete", extra={"user": user.email, "position": position, "positions": positions, "skill_count": len(skills), "avg_skill": round(average_skill, 1)})

        # Return JSON response for Streamlit API call
        return {"success": True, "message": "Onboarding completed successfully"}

    except Exception as e:
        db.rollback()
        logger.error("onboarding_full_error", extra={"user": user.email}, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/onboarding/start", response_class=HTMLResponse)
async def onboarding_start(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """
    New onboarding flow:
    1. Collect date of birth (if not set)
    2. Show age-filtered specializations
    3. Student selects spec(s)
    4. Auto-create UserLicense(s)
    5. Show payment info
    """
    today = date.today().isoformat()

    # Get available specializations based on age
    available_specs = []
    if user.age is not None:
        available_specs = get_available_specializations(user.age)

    return templates.TemplateResponse(
        "student/onboarding_new.html",
        {
            "request": request,
            "user": user,
            "today": today,
            "available_specs": available_specs
        }
    )


@router.post("/onboarding/set-birthdate")
async def onboarding_set_birthdate(
    request: Request,
    date_of_birth: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Set user's date of birth and continue onboarding"""
    try:
        # Parse date
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()

        # Validate age (must be at least 5 years old)
        today = datetime.now().date()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        if age < 5:
            raise HTTPException(status_code=400, detail="You must be at least 5 years old to register")

        # Update user
        user.date_of_birth = dob
        db.commit()

        logger.info("onboarding_dob_set", extra={"user": user.email, "age": age})

        # Redirect back to onboarding to show spec selection
        return RedirectResponse(url="/onboarding/start", status_code=303)

    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

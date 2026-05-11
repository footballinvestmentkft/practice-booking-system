"""
User profile routes
"""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime, timezone, date

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.license import UserLicense
from ...models.semester_enrollment import SemesterEnrollment
from ...models.semester import Semester, SemesterStatus
from ...utils.age_requirements import validate_specialization_for_age
from ...utils.country_codes import COUNTRY_CODES, COUNTRY_OPTIONS, register_filters
from ...skills_config import SKILL_CATEGORIES
import logging
import traceback

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
register_filters(templates.env)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Display user profile page"""
    user_licenses = []
    active_license = None
    active_enrollment = None
    current_semester = None
    available_semesters = []
    enrollment_map = {}
    credit_balance = 0
    credit_purchased = 0

    if user.role == UserRole.STUDENT:
        user_licenses = db.query(UserLicense).filter(UserLicense.user_id == user.id).all()

        # Get ACTIVE semester enrollment for CURRENT specialization
        if user.specialization:
            active_license = db.query(UserLicense).filter(
                UserLicense.user_id == user.id,
                UserLicense.specialization_type == user.specialization.value
            ).first()

            if active_license:
                # Get active enrollment for this spec
                active_enrollment = db.query(SemesterEnrollment).filter(
                    SemesterEnrollment.user_id == user.id,
                    SemesterEnrollment.user_license_id == active_license.id,
                    SemesterEnrollment.is_active == True,
                    SemesterEnrollment.payment_verified == True
                ).first()

                if active_enrollment:
                    current_semester = db.query(Semester).filter(
                        Semester.id == active_enrollment.semester_id
                    ).first()

                # Get available semesters for enrollment request (SPEC-SPECIFIC)
                # Map specialization to semester code prefix
                semester_code_prefix = {
                    'LFA_PLAYER_PRE': 'LFA_PLAYER_PRE',
                    'LFA_PLAYER_YOUTH': 'LFA_PLAYER_YOUTH',
                    'LFA_PLAYER_AMATEUR': 'LFA_PLAYER_AMATEUR',
                    'LFA_PLAYER_PRO': 'LFA_PLAYER_PRO',
                    'GANCUJU_PLAYER': 'GANCUJU',  # GANCUJU_PLAYER -> GANCUJU_*
                    'LFA_COACH': 'LFA_COACH',
                    'INTERNSHIP': 'INTERNSHIP'
                }.get(user.specialization.value, user.specialization.value)

                # Show NEXT 6 semesters for advance booking (payment verified = 1 semester enrollment right)
                today = date.today()
                available_semesters = db.query(Semester).filter(
                    Semester.code.like(f'{semester_code_prefix}_%'),
                    Semester.status != SemesterStatus.CANCELLED,
                    Semester.start_date >= today  # Only future semesters
                ).order_by(Semester.start_date).limit(6).all()  # Show max 6 upcoming semesters

                # Get all enrollments for current license
                enrollments = db.query(SemesterEnrollment).filter(
                    SemesterEnrollment.user_id == user.id,
                    SemesterEnrollment.user_license_id == active_license.id
                ).all()

                # Build enrollment status map
                enrollment_map = {e.semester_id: e for e in enrollments}

                # Get credit balance from User (centralized, spec-independent)
                credit_balance = user.credit_balance
                credit_purchased = user.credit_purchased

    # Get specialization color
    specialization_color = None
    if user.specialization:
        if user.specialization.value == 'INTERNSHIP':
            specialization_color = '#e74c3c'
        elif user.specialization.value == 'GANCUJU_PLAYER':
            specialization_color = '#8e44ad'
        elif user.specialization.value == 'LFA_FOOTBALL_PLAYER':
            specialization_color = '#f1c40f'
        elif user.specialization.value == 'LFA_COACH':
            specialization_color = '#27ae60'

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "user": user,
            "user_licenses": user_licenses,
            "active_license": active_license,
            "specialization_color": specialization_color,
            "active_enrollment": active_enrollment,
            "current_semester": current_semester,
            "available_semesters": available_semesters,
            "enrollment_map": enrollment_map,
            "credit_balance": credit_balance,
            "credit_purchased": credit_purchased,
            "today": datetime.now(timezone.utc).date(),
            "spec_header_class": "hdr-hub",
            "show_spec_nav": False,
        }
    )


@router.get("/profile/edit", response_class=HTMLResponse)
async def profile_edit_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Display profile edit page"""
    # Calculate user age
    user_age = None
    if user.date_of_birth:
        today = date.today()
        user_age = today.year - user.date_of_birth.year - ((today.month, today.day) < (user.date_of_birth.month, user.date_of_birth.day))

    return templates.TemplateResponse(
        "profile_edit.html",
        {
            "request": request,
            "user": user,
            "user_age": user_age,
            "country_list": COUNTRY_OPTIONS,
            "spec_header_class": "hdr-hub",
            "show_spec_nav": False,
        }
    )


@router.post("/profile/edit")
async def profile_edit_submit(
    request: Request,
    name: str = Form(...),
    nickname: str = Form(None),
    date_of_birth: str = Form(...),
    phone: str = Form(None),
    nationality: str = Form(None),
    secondary_nationality: str = Form(None),
    gender: str = Form(None),
    current_location: str = Form(None),
    emergency_contact: str = Form(None),
    emergency_phone: str = Form(None),
    medical_notes: str = Form(None),
    interests: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Update user profile"""
    try:
        # Parse date of birth
        try:
            dob = datetime.strptime(date_of_birth, '%Y-%m-%d').date()
        except ValueError:
            return templates.TemplateResponse(
                "profile_edit.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Invalid date format. Please use YYYY-MM-DD format.",
                    "spec_header_class": "hdr-hub",
                    "show_spec_nav": False,
                }
            )

        # Validate age (5-120 years)
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

        if age < 5:
            return templates.TemplateResponse(
                "profile_edit.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Warning: You must be at least 5 years old to use this platform.",
                    "spec_header_class": "hdr-hub",
                    "show_spec_nav": False,
                }
            )

        if age > 120:
            return templates.TemplateResponse(
                "profile_edit.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Warning: Please enter a valid date of birth.",
                    "spec_header_class": "hdr-hub",
                    "show_spec_nav": False,
                }
            )

        # Check if age change affects existing specializations
        old_dob = user.date_of_birth
        age_changed = old_dob != dob

        if age_changed:
            # Check if user has any unlocked specializations that are no longer valid for new age
            user_licenses = db.query(UserLicense).filter(UserLicense.user_id == user.id).all()

            blocked_specs = []
            for license in user_licenses:
                if not validate_specialization_for_age(license.specialization_type, age):
                    blocked_specs.append(license.specialization_type)

            if blocked_specs:
                spec_names = []
                for spec in blocked_specs:
                    if spec == "INTERNSHIP":
                        spec_names.append("Internship")
                    elif spec == "GANCUJU_PLAYER":
                        spec_names.append("GanCuju Player")
                    elif spec == "LFA_FOOTBALL_PLAYER":
                        spec_names.append("LFA Football Player")
                    elif spec == "LFA_COACH":
                        spec_names.append("LFA Coach")
                    else:
                        spec_names.append(spec.replace('_', ' ').title())

                return templates.TemplateResponse(
                    "profile_edit.html",
                    {
                        "request": request,
                        "user": user,
                        "user_age": age,
                        "error": f"Warning: Cannot change age: You currently have specializations that require a different age. Affected: {', '.join(spec_names)}. Please contact support if you need to update your age.",
                        "spec_header_class": "hdr-hub",
                        "show_spec_nav": False,
                    }
                )

        # Validate gender and nationality
        if gender and gender not in ("Male", "Female", "Non-binary", "Other"):
            return templates.TemplateResponse(
                "profile_edit.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Please select a valid gender.",
                    "country_list": COUNTRY_OPTIONS,
                    "spec_header_class": "hdr-hub",
                    "show_spec_nav": False,
                }
            )
        if nationality and nationality not in COUNTRY_CODES:
            return templates.TemplateResponse(
                "profile_edit.html",
                {
                    "request": request,
                    "user": user,
                    "error": "Please select a valid nationality from the list.",
                    "country_list": COUNTRY_OPTIONS,
                    "spec_header_class": "hdr-hub",
                    "show_spec_nav": False,
                }
            )
        if secondary_nationality:
            if secondary_nationality not in COUNTRY_CODES:
                return templates.TemplateResponse(
                    "profile_edit.html",
                    {
                        "request": request,
                        "user": user,
                        "error": "Please select a valid secondary nationality from the list.",
                        "country_list": COUNTRY_OPTIONS,
                        "spec_header_class": "hdr-hub",
                        "show_spec_nav": False,
                    }
                )
            if secondary_nationality == nationality:
                return templates.TemplateResponse(
                    "profile_edit.html",
                    {
                        "request": request,
                        "user": user,
                        "error": "Secondary nationality must be different from primary nationality.",
                        "country_list": COUNTRY_OPTIONS,
                        "spec_header_class": "hdr-hub",
                        "show_spec_nav": False,
                    }
                )

        # Update user profile
        user.name = name
        user.nickname = nickname if nickname else None
        user.date_of_birth = dob
        user.phone = phone if phone else None
        user.nationality = nationality if nationality else None
        user.secondary_nationality = secondary_nationality if secondary_nationality else None
        user.gender = gender if gender else None
        user.current_location = current_location if current_location else None
        user.emergency_contact = emergency_contact if emergency_contact else None
        user.emergency_phone = emergency_phone if emergency_phone else None
        user.medical_notes = medical_notes if medical_notes else None
        user.interests = interests if interests else None

        db.commit()
        db.refresh(user)

        logger.info("profile_updated", extra={"user": user.email, "age": age})

        # Redirect to profile page with success message
        return RedirectResponse(url="/profile?updated=true", status_code=303)

    except Exception as e:
        db.rollback()
        logger.error("profile_update_error", extra={"user": user.email}, exc_info=True)

        return templates.TemplateResponse(
            "profile_edit.html",
            {
                "request": request,
                "user": user,
                "error": f"Failed to update profile: {str(e)}",
                "country_list": COUNTRY_OPTIONS,
                "spec_header_class": "hdr-hub",
                "show_spec_nav": False,
            }
        )


@router.get("/profile/onboarding-card", response_class=HTMLResponse)
async def onboarding_welcome_card(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """
    Welcome Card — private self-assessment preview (Phase C).

    Data source: football_skills[*].self_assessment ONLY.
    NEVER reads current_level, baseline, system_baseline, tournament_delta,
    assessment_delta, or any EMA output.

    Auth: own card only (get_current_user_web enforces login; ownership is
    implicit because we query by user.id).
    Visibility: private, no-index (meta tag in template).
    """
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()

    if not license:
        logger.info("welcome_card_no_license", extra={"user": user.email})
        return RedirectResponse(
            url="/dashboard?info=complete_lfa_onboarding_first", status_code=303
        )

    if not license.onboarding_completed:
        logger.info("welcome_card_onboarding_incomplete", extra={"user": user.email})
        return RedirectResponse(
            url="/specialization/lfa-player/onboarding", status_code=303
        )

    # ── Extract self_assessment values ONLY ───────────────────────────────────
    football_skills = license.football_skills or {}
    skill_categories_data = []
    all_sa_values: list[float] = []

    for cat in SKILL_CATEGORIES:
        cat_skills = []
        for skill_def in cat["skills"]:
            key = skill_def["key"]
            raw = football_skills.get(key)
            if isinstance(raw, dict):
                sa_value = float(raw.get("self_assessment", 60.0))
            else:
                sa_value = 60.0
            cat_skills.append({
                "key":     key,
                "name_en": skill_def["name_en"],
                "name_hu": skill_def.get("name_hu", skill_def["name_en"]),
                "value":   round(sa_value, 1),
            })
            all_sa_values.append(sa_value)
        cat_avg = round(sum(s["value"] for s in cat_skills) / len(cat_skills), 1) if cat_skills else 0.0
        skill_categories_data.append({
            "key":     cat["key"],
            "name_en": cat["name_en"],
            "name_hu": cat.get("name_hu", cat["name_en"]),
            "emoji":   cat.get("emoji", ""),
            "skills":  cat_skills,
            "avg":     cat_avg,
        })

    overall_sa = round(sum(all_sa_values) / len(all_sa_values), 1) if all_sa_values else 60.0

    # Top 5 self-assessed skills (highest values)
    flat_skills = [s for cat in skill_categories_data for s in cat["skills"]]
    top_skills = sorted(flat_skills, key=lambda s: s["value"], reverse=True)[:5]

    # ── Physical / personal data from motivation_scores ───────────────────────
    ms = license.motivation_scores or {}

    # ── Display name + initials fallback ─────────────────────────────────────
    display_name = user.name or user.email or ""
    parts = display_name.split()
    initials = "".join(p[0].upper() for p in parts[:2]) if parts else "?"

    logger.info("welcome_card_rendered", extra={"user": user.email, "overall_sa": overall_sa})

    return templates.TemplateResponse(
        "public/welcome_card.html",
        {
            "request":          request,
            "user":             user,
            "license":          license,
            "display_name":     display_name,
            "initials":         initials,
            "skill_categories": skill_categories_data,
            "overall_sa":       overall_sa,
            "top_skills":       top_skills,
            "position":         ms.get("position", ""),
            "positions":        ms.get("positions", []),
            "height_cm":        ms.get("height_cm"),
            "weight_kg":        ms.get("weight_kg"),
            "preferred_foot":   ms.get("preferred_foot"),
            "goals":            ms.get("goals", ""),
            "right_foot_score": license.right_foot_score,
            "left_foot_score":  license.left_foot_score,
        },
    )

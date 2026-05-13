"""
User profile routes
"""
import asyncio
import logging
import os
import traceback
import types
from pathlib import Path
from datetime import datetime, timezone, date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.license import UserLicense
from ...models.semester_enrollment import SemesterEnrollment
from ...models.semester import Semester, SemesterStatus
from ...utils.age_requirements import validate_specialization_for_age
from ...utils.country_codes import COUNTRY_CODES, COUNTRY_OPTIONS, register_filters
from ...utils.dominant_foot import calculate_dominant_badge
from ...utils.football_positions import POSITIONS_21, VALID_POSITION_VALUES, positions_grouped
from ...skills_config import SKILL_CATEGORIES
from ...services.card_theme_service import get_theme as _get_theme
from ...services.card_platform_service import get_preset as _get_platform_preset
import app.services.card_export_service as _export_svc

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
    lfa_license = None
    active_enrollment = None
    current_semester = None
    available_semesters = []
    enrollment_map = {}
    credit_balance = 0
    credit_purchased = 0

    if user.role == UserRole.STUDENT:
        user_licenses = db.query(UserLicense).filter(UserLicense.user_id == user.id).all()

        # LFA Football Player license — independent of active specialization.
        # Used by the Welcome Card section which must remain visible after spec-switch.
        lfa_license = next(
            (l for l in user_licenses
             if l.specialization_type == "LFA_FOOTBALL_PLAYER" and l.onboarding_completed),
            None
        )

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
            "lfa_license": lfa_license,
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


# ── LFA Football Player profile constants ─────────────────────────────────────
# Derived from football_positions.py — single source of truth.
# Adding a new position to POSITIONS_21 automatically propagates here.

_VALID_POSITIONS: frozenset[str] = VALID_POSITION_VALUES

_VALID_GOALS: frozenset[str] = frozenset({
    "improve_skills", "play_higher_level", "become_professional",
    "team_football", "fitness_health", "enjoy_game",
})

_VALID_PREFERRED_FOOT: frozenset[str] = frozenset({"right", "left", "both"})

_GOAL_LABELS: dict[str, str] = {
    "improve_skills":      "Improve technical skills",
    "play_higher_level":   "Play at a higher competitive level",
    "become_professional": "Become a professional player",
    "team_football":       "Join a football team",
    "fitness_health":      "Stay fit through football",
    "enjoy_game":          "Enjoy the game",
}

_POSITION_LABELS: dict[str, str] = {
    p["value"]: f"{p['label']} ({p['short']})" for p in POSITIONS_21
}

_POSITION_GROUPS: list[dict] = positions_grouped()


def _lfa_license_or_redirect(
    user_id: int, db: Session
) -> "tuple[UserLicense, None] | tuple[None, RedirectResponse]":
    """Return (license, None) or (None, redirect) for LFA Football Player guard."""
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if not lic:
        return None, RedirectResponse(url="/dashboard?info=no_lfa_license", status_code=303)
    if not lic.onboarding_completed:
        return None, RedirectResponse(url="/specialization/lfa-player/onboarding", status_code=303)
    return lic, None


def _lfa_profile_ctx(request, user, license, error=None) -> dict:
    """Shared context for lfa_player_profile_edit.html GET and POST (error re-render)."""
    ms           = license.motivation_scores or {}
    primary_pos  = ms.get("position", "")
    all_pos      = ms.get("positions", [primary_pos] if primary_pos else [])
    secondary_pos = [p for p in all_pos if p != primary_pos]
    return {
        "request":            request,
        "user":               user,
        "license":            license,
        "ms":                 ms,
        "primary_pos":        primary_pos,
        "secondary_pos":      secondary_pos,
        "position_labels":    _POSITION_LABELS,
        "position_groups":    _POSITION_GROUPS,
        "goal_labels":        _GOAL_LABELS,
        "valid_preferred_foot": sorted(_VALID_PREFERRED_FOOT),
        "error":              error,
        "spec_dashboard_url":  "/dashboard/lfa-football-player",
        "spec_dashboard_icon": "⚽",
        "show_spec_nav":      True,
    }


@router.get("/profile/lfa-football-player", response_class=HTMLResponse)
async def lfa_player_profile_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """
    LFA Football Player specialization profile hub.

    Requires a completed LFA_FOOTBALL_PLAYER license.
    Displays spec-specific data from motivation_scores and UserLicense fields.
    football_skills (EMA-computed) are not exposed or editable here.
    """
    license, redirect = _lfa_license_or_redirect(user.id, db)
    if redirect:
        return redirect

    ms            = license.motivation_scores or {}
    primary_pos   = ms.get("position", "")
    all_pos       = ms.get("positions", [primary_pos] if primary_pos else [])
    secondary_pos = [p for p in all_pos if p != primary_pos]

    return templates.TemplateResponse(
        "lfa_player_profile.html",
        {
            "request":                request,
            "user":                   user,
            "license":                license,
            "ms":                     ms,
            "primary_pos":            primary_pos,
            "secondary_pos":          secondary_pos,
            "player_positions":       all_pos,
            "position_labels":        _POSITION_LABELS,
            "goal_label":             _GOAL_LABELS.get(ms.get("goals", ""), ms.get("goals", "")),
            "average_skill_level":    ms.get("average_skill_level"),
            "onboarding_completed_at":ms.get("onboarding_completed_at"),
            "pos_updated":            request.query_params.get("updated") == "positions",
            "pos_error":              request.query_params.get("pos_error", ""),
            "spec_dashboard_url":     "/dashboard/lfa-football-player",
            "spec_dashboard_icon":    "⚽",
            "show_spec_nav":          True,
        },
    )


@router.post("/profile/lfa-football-player/positions", response_class=HTMLResponse)
async def lfa_player_profile_positions_submit(
    request: Request,
    position: str     = Form(default=""),
    positions_raw: str = Form(default="[]"),
    db: Session       = Depends(get_db),
    user: User        = Depends(get_current_user_web),
):
    """Update only the player's positions (primary + secondaries, max 4 total).

    Accepts:
      position      — canonical primary position (snake_case)
      positions_raw — JSON array of all positions including primary, e.g. '["striker","left_wing"]'

    Validates: canonical values, 1–4 count, primary is first element.
    Never touches foot scores, goals, height, weight, or any other field.
    """
    import json as _json

    license, redirect = _lfa_license_or_redirect(user.id, db)
    if redirect:
        return redirect

    _base_url = "/profile/lfa-football-player"

    # ── Parse positions JSON ──────────────────────────────────────────────────
    try:
        all_positions: list = _json.loads(positions_raw)
        if not isinstance(all_positions, list):
            raise ValueError
    except Exception:
        return RedirectResponse(url=f"{_base_url}?pos_error=invalid_format", status_code=303)

    # ── Validate primary ──────────────────────────────────────────────────────
    position = position.strip()
    if not position or position not in _VALID_POSITIONS:
        return RedirectResponse(url=f"{_base_url}?pos_error=invalid_primary", status_code=303)

    # ── Validate count (1–4 total) ────────────────────────────────────────────
    if not (1 <= len(all_positions) <= 4):
        return RedirectResponse(url=f"{_base_url}?pos_error=invalid_count", status_code=303)

    # ── Validate each canonical value ─────────────────────────────────────────
    for p in all_positions:
        if p not in _VALID_POSITIONS:
            return RedirectResponse(url=f"{_base_url}?pos_error=invalid_position", status_code=303)

    # ── Primary must be first in the list (enforced by JS, verified server-side) ──
    if all_positions[0] != position:
        return RedirectResponse(url=f"{_base_url}?pos_error=primary_not_first", status_code=303)

    # ── Save — only positions keys touched ───────────────────────────────────
    ms = dict(license.motivation_scores or {})
    ms["position"]  = position
    ms["positions"] = all_positions
    license.motivation_scores = ms
    user.position = position          # backward-compat: User.position global field
    db.commit()

    return RedirectResponse(url=f"{_base_url}?updated=positions", status_code=303)


@router.get("/profile/lfa-football-player/edit", response_class=HTMLResponse)
async def lfa_player_profile_edit_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Show the LFA Football Player spec-profile edit form."""
    license, redirect = _lfa_license_or_redirect(user.id, db)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "lfa_player_profile_edit.html",
        _lfa_profile_ctx(request, user, license),
    )


@router.post("/profile/lfa-football-player/edit")
async def lfa_player_profile_edit_submit(
    request: Request,
    position: str          = Form(...),
    secondary_positions: list[str] = Form(default=[]),
    preferred_foot: str    = Form(...),
    goals: str             = Form(...),
    height_cm_raw: str     = Form(default=""),
    weight_kg_raw: str     = Form(default=""),
    db: Session            = Depends(get_db),
    user: User             = Depends(get_current_user_web),
):
    """Validate and save LFA Football Player spec-profile fields (motivation_scores only)."""
    license, redirect = _lfa_license_or_redirect(user.id, db)
    if redirect:
        return redirect

    errors: list[str] = []

    # ── Validate categorical fields ───────────────────────────────────────────
    if position not in _VALID_POSITIONS:
        errors.append(f"Invalid primary position: {position!r}")
    clean_secondary = []
    for sp in secondary_positions:
        if not sp:
            continue
        if sp not in _VALID_POSITIONS:
            errors.append(f"Invalid secondary position: {sp!r}")
        elif sp != position:
            clean_secondary.append(sp)
    if len(clean_secondary) > 3:
        errors.append("Maximum 3 secondary positions allowed (excluding primary)")
    if preferred_foot not in _VALID_PREFERRED_FOOT:
        errors.append(
            f"Preferred foot must be one of: {', '.join(sorted(_VALID_PREFERRED_FOOT))}"
        )
    if goals not in _VALID_GOALS:
        errors.append(f"Invalid goal value: {goals!r}")

    # ── Validate optional numeric fields ──────────────────────────────────────
    height_cm: int | None = None
    if height_cm_raw.strip():
        try:
            height_cm = int(height_cm_raw.strip())
            if not (100 <= height_cm <= 250):
                errors.append("Height must be between 100 and 250 cm")
        except ValueError:
            errors.append("Height must be a whole number")

    weight_kg: int | None = None
    if weight_kg_raw.strip():
        try:
            weight_kg = int(weight_kg_raw.strip())
            if not (30 <= weight_kg <= 200):
                errors.append("Weight must be between 30 and 200 kg")
        except ValueError:
            errors.append("Weight must be a whole number")

    # ── Re-render form on validation error (no DB write) ──────────────────────
    if errors:
        return templates.TemplateResponse(
            "lfa_player_profile_edit.html",
            _lfa_profile_ctx(request, user, license, error="; ".join(errors)),
            status_code=422,
        )

    ms = dict(license.motivation_scores or {})
    ms["position"]       = position
    ms["positions"]      = [position] + clean_secondary
    ms["preferred_foot"] = preferred_foot
    ms["goals"]          = goals
    if height_cm is not None:
        ms["height_cm"] = height_cm
    if weight_kg is not None:
        ms["weight_kg"] = weight_kg
    license.motivation_scores = ms

    # Backward-compat: sync primary position to User.position global field
    user.position = position

    db.commit()
    return RedirectResponse(url="/profile/lfa-football-player?updated=true", status_code=303)


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


# Export format buckets — sourced from the authoritative card_constants module.
from ...services.card_constants import (
    CANVAS_SIZES as _WC_CANVAS_SIZES,
    EXPORT_FORMAT_BUCKETS as _WC_EXPORT_BUCKETS,
    WC_GALLERY_PLATFORM_IDS as _WC_GALLERY_PLATFORM_IDS,
)
from ...services.card_platform_service import build_platform_list as _build_platform_list

# Platforms exposed in the Welcome Card gallery — derived from authoritative sources.
_WC_GALLERY_PLATFORMS: list[dict] = _build_platform_list(_WC_GALLERY_PLATFORM_IDS)

# JSON-serialisable canvas_sizes dict passed to the gallery template so the
# JS CANVAS_SIZES object is server-rendered instead of hardcoded.
_WC_CANVAS_SIZES_JSON: dict = {
    pid: {"w": w, "h": h} for pid, (w, h) in _WC_CANVAS_SIZES.items()
}

_WC_APP_LOGO_URL = "/static/images/logo-dark.png"
_TEMPLATES_DIR   = str(BASE_DIR / "templates")


def _build_welcome_card_context(
    request: Request,
    user: User,
    license: UserLicense,
    platform: str | None,
    export: bool,
) -> dict:
    """
    Build the FIFA template context for the Welcome Card.

    Self-assessment adapter:
    FIFA Classic templates read `current_level` as the displayed skill number.
    For Welcome Card only, this field is populated from self_assessment.
    This must never be written back to football_skills JSONB and must never
    be used by calculation services.
    """
    football_skills = license.football_skills or {}
    ms              = license.motivation_scores or {}

    # ── Build skills dict: current_level = self_assessment (adapter only) ──────
    skills_for_fifa: dict[str, dict] = {}
    all_sa_values: list[float] = []
    for cat in SKILL_CATEGORIES:
        for skill_def in cat["skills"]:
            key = skill_def["key"]
            raw = football_skills.get(key)
            sa_val = float(raw.get("self_assessment", 60.0)) if isinstance(raw, dict) else 60.0
            # Welcome Card template adapter:
            # FIFA Classic templates read `current_level` as the displayed number.
            # For Welcome Card only, this field is populated from self_assessment.
            # This must never be written back to football_skills JSONB and must
            # never be used by calculation services.
            skills_for_fifa[key] = {"current_level": sa_val, "self_assessment": sa_val}
            all_sa_values.append(sa_val)

    overall_sa = round(sum(all_sa_values) / len(all_sa_values), 1) if all_sa_values else 60.0

    display_name      = user.name or user.email or ""
    parts             = display_name.split()
    initials          = "".join(p[0].upper() for p in parts[:2]) if parts else "?"
    position          = ms.get("position", "")
    player_height_cm  = ms.get("height_cm")
    player_weight_kg  = ms.get("weight_kg")
    dominant_badge    = calculate_dominant_badge(
        license.right_foot_score, license.left_foot_score
    )

    # ── Player namespace: satisfies all `player.*` references in FIFA template ──
    player = types.SimpleNamespace(
        skills               = skills_for_fifa,
        name                 = display_name,
        position             = position,
        positions            = ms.get("positions", []),
        nationality          = getattr(user, "country", None) or "",
        secondary_nationality= None,
        age_group            = None,
        total_tournaments    = 0,
        photo_url            = license.player_card_photo_url,
    )

    platform_preset = _get_platform_preset(platform)
    theme           = _get_theme("midnight")  # dark FIFA Classic default

    return {
        "request":               request,
        "player":                player,
        "overall":               overall_sa,
        "tier_label":            "Self-Assessment",
        "tier_color":            "#f59e0b",
        "avatar_bg":             "#1e3a5f",
        "initials":              initials,
        "pos_color":             "#667eea",
        "skill_categories":      SKILL_CATEGORIES,
        "teams_info":            [],
        "animated_mode":         False,
        "last_skill_delta":      {},
        "participations_history":[],
        "theme":                 theme,
        "card_theme_id":         theme.id,
        "card_theme":            theme.id,
        "card_variant_id":       "fifa",
        "platform_class":        platform_preset.css_class,
        "platform_id":           platform_preset.id,
        "export_mode":           export,
        "photo_url":             license.player_card_photo_url,
        "portrait_photo_url":    license.card_photo_portrait_url or license.player_card_photo_url,
        "landscape_photo_url":   license.card_photo_landscape_url or license.player_card_photo_url,
        "compact_bg_url":        None,
        "showcase_bg_url":       None,
        # sponsor_logo is always None on Welcome Card — enforced at context build time
        "sponsor_logo_url":      None,
        # Fixed app logo shown on Welcome Card (logo-dark.png for dark FIFA background)
        "app_logo_url":          _WC_APP_LOGO_URL,
        "compact_photo_position":"left",
        "player_height_cm":      player_height_cm,
        "player_weight_kg":      player_weight_kg,
        "dominant_badge":        dominant_badge,
        "display_name":          display_name,
        "welcome_card_mode":     True,
    }


def _select_welcome_card_template(platform: str | None, export: bool) -> str:
    """Return the FIFA template path appropriate for this platform + render mode."""
    if platform and platform in _WC_EXPORT_BUCKETS:
        bucket   = _WC_EXPORT_BUCKETS[platform]
        exp_path = f"public/export/{bucket}/fifa.html"
        if os.path.isfile(os.path.join(_TEMPLATES_DIR, exp_path)):
            return exp_path
    return "public/player_card_fifa.html"


def _check_welcome_card_auth(
    license: UserLicense | None, user_email: str
) -> RedirectResponse | None:
    """Return a redirect if the user is not eligible to view the Welcome Card."""
    if not license:
        logger.info("welcome_card_no_license", extra={"user": user_email})
        return RedirectResponse(
            url="/dashboard?info=complete_lfa_onboarding_first", status_code=303
        )
    if not license.onboarding_completed:
        logger.info("welcome_card_onboarding_incomplete", extra={"user": user_email})
        return RedirectResponse(
            url="/specialization/lfa-player/onboarding", status_code=303
        )
    return None


@router.get("/profile/onboarding-card", response_class=HTMLResponse)
async def onboarding_welcome_card(
    request: Request,
    platform: str | None = Query(default=None),
    export: bool         = Query(default=False),
    db: Session          = Depends(get_db),
    user: User           = Depends(get_current_user_web),
):
    """
    Welcome Card preview — private self-assessment view.

    Data source: football_skills[*].self_assessment ONLY.
    NEVER reads current_level, baseline, system_baseline, tournament_delta,
    assessment_delta, or any EMA output.

    Without ?platform=: renders the gallery hub (iframe + download buttons).
    With ?platform=X:   renders the FIFA Classic card for that platform size.
    With ?export=1:     switches the FIFA template to export-mode (Playwright use).

    Auth: own card only (get_current_user_web enforces login; ownership is
    implicit because we query by user.id).
    Visibility: private, no-index (meta tag in template).
    """
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()

    redirect = _check_welcome_card_auth(license, user.email)
    if redirect:
        return redirect

    if not platform:
        # Gallery hub: iframe preview of default platform + per-platform download buttons
        display_name = user.name or user.email or ""
        logger.info("welcome_card_gallery_rendered", extra={"user": user.email})
        return templates.TemplateResponse(
            "public/welcome_card.html",
            {
                "request":          request,
                "user":             user,
                "display_name":     display_name,
                "platforms":        _WC_GALLERY_PLATFORMS,
                "default_platform": "instagram_square",
                "photo_url":        license.player_card_photo_url,
                "canvas_sizes":     _WC_CANVAS_SIZES_JSON,
            },
        )

    logger.info("welcome_card_rendered", extra={"user": user.email, "platform": platform, "export": export})
    ctx  = _build_welcome_card_context(request, user, license, platform, export)
    tmpl = _select_welcome_card_template(platform, export)
    return templates.TemplateResponse(tmpl, ctx)


@router.get("/profile/onboarding-card/export")
async def export_onboarding_welcome_card(
    request: Request,
    platform: str = Query(default="instagram_square"),
    db: Session   = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """
    Export the Welcome Card as a PNG at a social-media canvas size.

    Auth: own card only. Rate limit: 5 exports per 60 s per user+IP.
    Data source: self_assessment only (same contract as the preview route).
    """
    from app.config import settings

    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()

    redirect = _check_welcome_card_auth(license, user.email)
    if redirect:
        return redirect

    if platform not in _export_svc.CANVAS_SIZES:
        valid = list(_export_svc.CANVAS_SIZES)
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported export platform: {platform!r}. Valid values: {valid}",
        )

    client_ip = request.client.host if request.client else "unknown"
    rate_key  = f"wc:{user.id}:{client_ip}"
    if not _export_svc.check_export_rate_limit(rate_key):
        raise HTTPException(
            status_code=429,
            detail="Export rate limit exceeded (5 per minute). Please wait before exporting again.",
        )

    render_url = (
        f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
        f"/profile/onboarding-card?platform={platform}&export=1"
    )

    logger.info("welcome_card_export", extra={"user": user.email, "platform": platform})
    try:
        png_bytes = await asyncio.to_thread(
            _export_svc._sync_take_screenshot, render_url, platform
        )
    except _export_svc.CardExportTimeoutError:
        raise HTTPException(status_code=504, detail="Card render timed out")

    filename = f"welcome_card_{platform}.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control":       "no-store",
            "X-Export-Platform":   platform,
        },
    )

"""
Dashboard routes for student, instructor, and admin dashboards
"""
from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import date
import logging
import re

from sqlalchemy import func as sqlfunc

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
UserModel = User  # alias used in some template context sections
from ...models.semester import Semester, SemesterStatus, SemesterCategory
from ...models.license import UserLicense
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.session import Session as SessionModel
from ...models.invoice_request import InvoiceRequest, InvoiceRequestStatus
from ...models.system_event import SystemEvent
from ...models.audit_log import AuditLog
from ...models.coupon import Coupon
from ...utils.age_requirements import get_available_specializations
from .helpers import get_lfa_age_category

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/dashboard-fresh", response_class=HTMLResponse)  # CACHE BYPASS ROUTE
async def dashboard(
    request: Request,
    spec: str = None,  # Query param for spec switching
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Dashboard page with multi-spec support"""
    if user.role == UserRole.STUDENT:
        user_age = None
        if user.date_of_birth:
            today = date.today()
            user_age = today.year - user.date_of_birth.year - ((today.month, today.day) < (user.date_of_birth.month, user.date_of_birth.day))

        # Get user's existing licenses (unlocked specializations)
        user_licenses = db.query(UserLicense).filter(UserLicense.user_id == user.id).all()
        license_map = {lic.specialization_type: lic for lic in user_licenses}

        # Pre-fetch which license IDs have at least one SemesterEnrollment (legacy compat)
        license_ids = [lic.id for lic in user_licenses]
        enrolled_license_ids: set = set()
        if license_ids:
            enrolled_license_ids = {
                row[0] for row in
                db.query(SemesterEnrollment.user_license_id)
                .filter(SemesterEnrollment.user_license_id.in_(license_ids))
                .distinct()
                .all()
            }

        # Get age-appropriate specializations
        available_specs_list = get_available_specializations(user_age)

        # Build specialization data with unlock status (ALWAYS SHOW ALL)
        specializations_data = []
        for spec_item in available_specs_list:
            is_unlocked = spec_item["type"] in license_map
            lic = license_map.get(spec_item["type"])
            # Effective onboarding: explicit flag OR skills data present OR legacy enrollment
            effective_onboarding = bool(
                lic and (
                    lic.onboarding_completed
                    or lic.football_skills is not None
                    or lic.id in enrolled_license_ids
                )
            )
            specializations_data.append({
                "type": spec_item["type"],
                "name": spec_item["name"],
                "icon": spec_item["icon"],
                "color": spec_item["color"],
                "description": spec_item["description"],
                "age_requirement": spec_item["age_requirement"],
                "is_unlocked": is_unlocked,
                "onboarding_completed": effective_onboarding,
                "is_available": True
            })

        logger.info("dashboard_loaded", extra={"user": user.email, "unlocked_specs": len(license_map), "total_specs": len(specializations_data)})

        # ALWAYS show specialization hub (no auto-redirect)
        return templates.TemplateResponse(
            "hub_specializations.html",  # SPECIALIZATIONS HUB
            {
                "request": request,
                "user": user,
                "user_age": user_age or "N/A",
                "available_specializations": specializations_data,
                "unlocked_count": len(license_map)
            }
        )
    else:
        # Not a student or no special multi-spec handling needed
        specialization = None

    # 📅 Get active semesters (for ADMIN dashboard)
    active_semesters = []
    if user.role == UserRole.ADMIN:
        today = date.today()
        active_semesters = db.query(Semester).filter(
            Semester.status != SemesterStatus.CANCELLED,
            Semester.start_date <= today,
            Semester.end_date >= today
        ).order_by(Semester.code, Semester.start_date.desc()).all()

        # Add specialization_type and extract location from code
        for semester in active_semesters:
            code = semester.code

            # Extract location suffix (BUDA, PEST, BUDAPEST, city names)
            location_match = re.search(r'_(BUDA|PEST|BUDAPEST|DEBRECEN|SZEGED|MISKOLC|GYOR)$', code, re.IGNORECASE)
            if location_match:
                # Remove location suffix for specialization extraction
                code_without_location = code[:location_match.start()]
            else:
                code_without_location = code

            # Remove year patterns
            code_clean = re.sub(r'_\d{4}(-\d{2})?(_[A-Z]{3,6})?$', '', code_without_location)
            code_clean = re.sub(r'_\d{4}_Q\d$', '', code_clean)

            # Special case: GANCUJU should become GANCUJU_PLAYER
            if code_clean.startswith('GANCUJU'):
                semester.specialization_type = 'GANCUJU_PLAYER'
            else:
                semester.specialization_type = code_clean if code_clean else None

    # Get user's specialization (if not already set by multi-spec logic above)
    # pragma: no branch — student always returns early above; this condition is always True here
    if user.role != UserRole.STUDENT or not specialization:  # pragma: no branch
        specialization = user.specialization if hasattr(user, 'specialization') and user.specialization else None

    # Check if user is instructor
    is_instructor = user.role == UserRole.INSTRUCTOR

    # Get instructor teaching qualifications
    teaching_specializations = []
    all_teaching_specializations = []
    if is_instructor:
        teaching_specializations = user.get_teaching_specializations()  # Active only
        all_teaching_specializations = user.get_all_teaching_specializations()  # Active + Inactive

    # Initialize dashboard data variables (defaults for non-student roles and fallback else branch)
    xp_data = {
        "total_xp": 0,
        "level": 1,
        "level_progress": 0
    }
    user_licenses = []
    specialization_color = None
    pending_enrollments = []
    has_active_enrollment = False
    current_license = None
    available_semesters = []
    current_semester = None
    next_semester = None
    upcoming_sessions = []
    credit_balance = user.credit_balance if hasattr(user, 'credit_balance') else 0
    credit_purchased = user.credit_purchased if hasattr(user, 'credit_purchased') else 0
    football_skills = None
    skills_updated_by_name = None

    # ========================================
    # ROLE-BASED TEMPLATE ROUTING (3 Separate Templates)
    # ========================================

    if user.role == UserRole.ADMIN:
        # ADMIN Dashboard — operational 4-layer layout
        logger.debug("dashboard_routing", extra={"user": user.email, "template": "dashboard_admin.html"})
        _today = date.today()

        # ── Layer 1: Primary KPI ──────────────────────────────────────────────
        _active_sessions = db.query(SessionModel).filter(
            sqlfunc.date(SessionModel.date_start) >= _today,
            SessionModel.session_status != 'cancelled'
        ).count()
        _active_tournaments = db.query(Semester).filter(
            Semester.semester_category == SemesterCategory.TOURNAMENT,
            Semester.status.in_([SemesterStatus.READY_FOR_ENROLLMENT, SemesterStatus.ONGOING])
        ).count()
        _pending_revenue = db.query(
            sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.amount_eur), 0)
        ).filter(InvoiceRequest.status == InvoiceRequestStatus.PENDING.value).scalar() or 0.0

        kpi = {
            "total_users": db.query(User).count(),
            "active_sessions": _active_sessions,
            "active_tournaments": _active_tournaments,
            "pending_revenue_eur": round(float(_pending_revenue), 2),
        }

        # ── Layer 2: Operational Queue ────────────────────────────────────────
        _pending_enrollments = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.request_status == EnrollmentStatus.PENDING
        ).count()
        _todays_sessions = db.query(SessionModel).filter(
            sqlfunc.date(SessionModel.date_start) == _today,
            SessionModel.session_status != 'cancelled'
        ).count()
        _pending_payments = db.query(InvoiceRequest).filter(
            InvoiceRequest.status == InvoiceRequestStatus.PENDING.value
        ).count()
        _unresolved_events = db.query(SystemEvent).filter(
            SystemEvent.resolved == False  # noqa: E712
        ).count()

        queue = {
            "pending_enrollments": _pending_enrollments,
            "todays_sessions": _todays_sessions,
            "pending_payments": _pending_payments,
            "unresolved_events": _unresolved_events,
        }

        # ── Layer 0: Alert condition ──────────────────────────────────────────
        show_alert = _pending_enrollments > 0 or _pending_payments > 0 or _unresolved_events > 0

        # ── Layer 3A: Recent activity feed ────────────────────────────────────
        _recent_logs = (
            db.query(AuditLog)
            .filter(AuditLog.user_id.isnot(None))
            .order_by(AuditLog.timestamp.desc())
            .limit(10)
            .all()
        )
        _actor_ids = list({log.user_id for log in _recent_logs if log.user_id})
        actor_map = (
            {u.id: u for u in db.query(User).filter(User.id.in_(_actor_ids)).all()}
            if _actor_ids else {}
        )

        # ── Layer 3B: Quick stats ─────────────────────────────────────────────
        _month_start = _today.replace(day=1)
        _revenue_mtd = db.query(
            sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.amount_eur), 0)
        ).filter(
            InvoiceRequest.status.in_([
                InvoiceRequestStatus.PAID.value, InvoiceRequestStatus.VERIFIED.value
            ]),
            sqlfunc.date(InvoiceRequest.created_at) >= _month_start
        ).scalar() or 0.0

        quick_stats = {
            "active_semesters": db.query(Semester).filter(
                Semester.status == SemesterStatus.ONGOING,
                Semester.semester_category.notin_([
                    SemesterCategory.TOURNAMENT,
                    SemesterCategory.PROMOTION_EVENT,
                ])
            ).count(),
            "enrolled_students": db.query(SemesterEnrollment).filter(
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
            ).count(),
            "active_coupons": db.query(Coupon).filter(Coupon.is_active == True).count(),  # noqa: E712
            "revenue_mtd": round(float(_revenue_mtd), 2),
            "total_students": db.query(User).filter(User.role == UserRole.STUDENT).count(),
            "total_instructors": db.query(User).filter(User.role == UserRole.INSTRUCTOR).count(),
        }

        response = templates.TemplateResponse(
            "dashboard_admin.html",
            {
                "request": request,
                "user": user,
                "active_semesters": active_semesters,  # kept for backward compat
                "kpi": kpi,
                "queue": queue,
                "show_alert": show_alert,
                "recent_activity": _recent_logs,
                "actor_map": actor_map,
                "quick_stats": quick_stats,
                "today_display": _today.strftime('%A, %B %d, %Y'),
            }
        )
    elif user.role == UserRole.INSTRUCTOR:
        # INSTRUCTOR Dashboard
        logger.debug("dashboard_routing", extra={"user": user.email, "template": "dashboard_instructor.html"})
        response = templates.TemplateResponse(
            "dashboard_instructor.html",
            {
                "request": request,
                "user": user,
                "teaching_specializations": teaching_specializations,
                "all_teaching_specializations": all_teaching_specializations,
            }
        )
    else:  # pragma: no cover
        # Unreachable: UserRole enum has exactly 3 values (STUDENT/INSTRUCTOR/ADMIN).
        # Student early-returns above; INSTRUCTOR and ADMIN are handled above.
        raise HTTPException(status_code=403, detail="Unknown role")
    # Disable caching to ensure fresh data
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def get_lfa_age_category(date_of_birth):
    """
    Determine LFA Player age category based on date of birth.

    Returns tuple: (category_code, category_name, age_range, description)

    Categories:
    - PRE (5-13 years): Foundation Years - Monthly semesters
    - YOUTH (14-18 years): Technical Development - Quarterly semesters
    - AMATEUR (14+ years): Competitive Play - Bi-annual semesters (instructor assigned)
    - PRO (14+ years): Professional Track - Annual semesters (instructor assigned)
    """
    if not date_of_birth:
        return None, None, None, "Date of birth not set"

    today = date.today()
    age = today.year - date_of_birth.year - ((today.month, today.day) < (date_of_birth.month, date_of_birth.day))

    if 5 <= age <= 13:
        return "PRE", "PRE (Foundation Years)", "5-13 years", f"Age {age} - Monthly training blocks"
    elif 14 <= age <= 18:
        return "YOUTH", "YOUTH (Technical Development)", "14-18 years", f"Age {age} - Quarterly programs"
    elif age > 18:
        # For 18+ students, category must be assigned by instructor (AMATEUR or PRO)
        return None, None, None, f"Age {age} - Category assigned by instructor (AMATEUR or PRO)"
    else:
        return None, None, None, f"Age {age} - Below minimum age requirement (5 years)"


@router.get("/dashboard/lfa-football-player/card-editor", response_class=HTMLResponse)
async def lfa_player_card_editor(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Dedicated sub-page for LFA Football Player card editing (Phase 1 extraction)."""
    spec_enum = "LFA_FOOTBALL_PLAYER"

    # Same license guard as spec_dashboard
    user_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == spec_enum,
    ).first()

    if not user_license:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to LFA Football Player. Please unlock it first.",
        )

    # Same onboarding guard as spec_dashboard
    has_enrollment = db.query(SemesterEnrollment.id).filter(
        SemesterEnrollment.user_license_id == user_license.id
    ).first() is not None
    effective_onboarding = (
        user_license.onboarding_completed
        or user_license.football_skills is not None
        or has_enrollment
    )
    if not effective_onboarding:
        return RedirectResponse(url="/specialization/lfa-player/onboarding", status_code=303)

    credit_balance = user.credit_balance if hasattr(user, "credit_balance") else 0

    # Load (or create) the singleton card draft — single source of truth after 4D-2
    card_draft = _CardDraftService.get_player_card_draft(db, user.id)

    # Card theme picker data — identical logic to spec_dashboard
    from ...services.card_theme_service import get_all_themes as _get_all_themes, is_unlocked as _is_theme_unlocked
    card_themes = [
        {
            "id": t.id,
            "label": t.label,
            "dot_color": t.dot_color,
            "is_premium": t.is_premium,
            "credit_cost": t.credit_cost,
            "unlocked": _is_theme_unlocked(user_license, t.id),
        }
        for t in _get_all_themes()
    ]
    active_card_theme = card_draft.draft_theme

    # Published public card state (read-only in the editor — shown as indicator)
    published_card_theme    = card_draft.published_theme    or "default"
    published_card_variant  = card_draft.published_variant  or "fifa"
    published_card_platform = card_draft.published_platform or "default"

    # Card variant picker data — identical logic to spec_dashboard
    from ...services.card_variant_service import (  # noqa: E402
        get_all_variants as _get_all_variants,
        is_variant_unlocked as _is_variant_unlocked,
    )
    card_variants = [
        {
            "id": v.id,
            "label": v.label,
            "description": v.description,
            "is_premium": v.is_premium,
            "credit_cost": v.credit_cost,
            "available": v.available,
            "unlocked": _is_variant_unlocked(user_license, v.id),
        }
        for v in _get_all_variants()
    ]
    active_card_variant = card_draft.draft_variant

    # Animated video export capability: list of platforms supported for the
    # current variant. Used by the card editor to show/hide the video button.
    from ...services.card_constants import (
        ANIMATED_EXPORT_CAPABLE,
        CANVAS_SIZES as _editor_canvas_sizes,
        CARD_EDITOR_PLATFORM_IDS,
    )
    from ...services.card_platform_service import build_platform_list as _build_platform_list
    animated_capable_platforms = [
        p for (v, p) in ANIMATED_EXPORT_CAPABLE if v == active_card_variant
    ]

    # Platform list for the Jinja2 picker loop (all non-default export platforms).
    editor_platforms = _build_platform_list(CARD_EDITOR_PLATFORM_IDS)

    # JSON-serialisable canvas sizes for server-rendered JS const in template.
    canvas_sizes = {
        pid: {"w": w, "h": h} for pid, (w, h) in _editor_canvas_sizes.items()
    }

    return templates.TemplateResponse(
        "dashboard_card_editor.html",
        {
            "request": request,
            "user": user,
            "user_license": user_license,
            "credit_balance": credit_balance,
            "card_themes": card_themes,
            "active_card_theme": active_card_theme,
            "card_variants": card_variants,
            "active_card_variant": active_card_variant,
            "active_card_platform": card_draft.draft_platform or "default",
            "show_variant_picker": True,  # page is LFA Football Player only
            "animated_capable_platforms": animated_capable_platforms,
            "platforms": editor_platforms,
            "canvas_sizes": canvas_sizes,
            # Published state — used for "Unpublished changes" indicator + View Public Card CTA
            "published_card_theme":    published_card_theme,
            "published_card_variant":  published_card_variant,
            "published_card_platform": published_card_platform,
        },
    )


@router.get("/dashboard/{spec_type}", response_class=HTMLResponse)
async def spec_dashboard(
    request: Request,
    spec_type: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Spec-specific dashboard for unlocked specializations"""
    spec_enum = spec_type.upper().replace("-", "_")

    # Verify user has access to this specialization
    user_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == spec_enum
    ).first()

    if not user_license:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You don't have access to {spec_type}. Please unlock it first."
        )

    # Onboarding guard: effective_onboarding = flag OR skills OR legacy enrollment
    has_enrollment = db.query(SemesterEnrollment.id).filter(
        SemesterEnrollment.user_license_id == user_license.id
    ).first() is not None
    effective_onboarding = (
        user_license.onboarding_completed
        or user_license.football_skills is not None
        or has_enrollment
    )
    if not effective_onboarding:
        onboarding_url = (
            "/specialization/lfa-player/onboarding"
            if spec_enum == "LFA_FOOTBALL_PLAYER"
            else f"/specialization/motivation?spec={spec_enum}"
        )
        return RedirectResponse(url=onboarding_url, status_code=303)

    # Simple spec config (no external config file needed)
    spec_configs = {
        "LFA_FOOTBALL_PLAYER": {"name": "LFA Football Player", "icon": "⚽", "color": "#2ecc71"},
        "GANCUJU_PLAYER": {"name": "GanCuju Player", "icon": "🥋", "color": "#e74c3c"},
        "JUNIOR_INTERNSHIP": {"name": "Junior Internship", "icon": "💼", "color": "#3498db"},
        "SENIOR_INTERNSHIP": {"name": "Senior Internship", "icon": "🎓", "color": "#9b59b6"},
    }

    spec_config = spec_configs.get(spec_enum, {
        "name": spec_type.replace("-", " ").title(),
        "icon": "🎓",
        "color": "#667eea"
    })

    # Get active enrollment for this spec
    has_active_enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.user_license_id == user_license.id,
        SemesterEnrollment.is_active == True
    ).first() is not None

    # Get available semesters for this spec
    today = date.today()

    # For LFA_FOOTBALL_PLAYER, determine age-based category
    age_category = None
    age_category_name = None
    age_range = None
    age_description = None
    user_age = None

    if spec_enum == 'LFA_FOOTBALL_PLAYER':
        age_category, age_category_name, age_range, age_description = get_lfa_age_category(user.date_of_birth)

        # Calculate user_age for template display
        if user.date_of_birth:
            user_age = today.year - user.date_of_birth.year - ((today.month, today.day) < (user.date_of_birth.month, user.date_of_birth.day))

        logger.debug("lfa_age_check", extra={"user": user.email, "age_category": age_category, "user_age": user_age})

        if not age_category:
            # 18+ student — instructor must assign AMATEUR or PRO.
            # Until assignment is stored, default to AMATEUR so the page renders.
            age_category = "AMATEUR"
            age_category_name = "AMATEUR (Adult)"
            age_range = "18+ years"

    # Map specialization to semester code prefix
    semester_code_prefix = {
        'LFA_PLAYER_PRE': 'LFA_PLAYER_PRE',
        'LFA_PLAYER_YOUTH': 'LFA_PLAYER_YOUTH',
        'LFA_PLAYER_AMATEUR': 'LFA_PLAYER_AMATEUR',
        'LFA_PLAYER_PRO': 'LFA_PLAYER_PRO',
        'LFA_FOOTBALL_PLAYER': f'LFA_PLAYER_{age_category}' if age_category else 'LFA_PLAYER',  # Age-based filtering!
        'GANCUJU_PLAYER': 'GANCUJU',  # GANCUJU_PLAYER → GANCUJU_*
        'LFA_COACH': 'LFA_COACH',
        'INTERNSHIP': 'INTERNSHIP'
    }.get(spec_enum, spec_enum)

    logger.debug("semester_prefix_search", extra={"prefix": semester_code_prefix})

    # Get all track semesters
    track_semesters = db.query(Semester).filter(
        Semester.code.like(f'{semester_code_prefix}_%'),
        Semester.status != SemesterStatus.CANCELLED
    ).order_by(Semester.start_date).all()

    logger.debug("track_semesters_found", extra={"prefix": semester_code_prefix, "count": len(track_semesters)})

    # Check which semesters user already enrolled in
    existing_enrollments = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id
    ).all()
    enrolled_semester_ids = {e.semester_id for e in existing_enrollments}

    # Available semesters = all semesters not yet enrolled (show upcoming and current, max 6)
    # Filter: not enrolled AND (current semester OR future semester)
    available_semesters = [
        sem for sem in track_semesters
        if sem.id not in enrolled_semester_ids and sem.end_date >= today
    ][:6]

    logger.debug("available_semesters", extra={"count": len(available_semesters)})

    # Get current semester if enrolled
    current_semester = None
    if has_active_enrollment:
        enrollment = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.user_license_id == user_license.id,
            SemesterEnrollment.is_active == True
        ).first()
        if enrollment:
            current_semester = enrollment.semester

    # Get all pending enrollments for this user
    pending_enrollments = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id
    ).order_by(SemesterEnrollment.requested_at.desc()).all()

    # Get user credit balance
    credit_balance = user.credit_balance if hasattr(user, 'credit_balance') else 0

    # Map spec type to header gradient class
    _spec_header_map = {
        "LFA_FOOTBALL_PLAYER": "hdr-football",
        "LFA_COACH": "hdr-coach",
        "INTERNSHIP": "hdr-intern",
        "JUNIOR_INTERNSHIP": "hdr-intern",
        "SENIOR_INTERNSHIP": "hdr-intern",
        "GANCUJU_PLAYER": "hdr-gancuju",
    }
    spec_header_class = _spec_header_map.get(spec_enum, "hdr-football")

    return templates.TemplateResponse(
        "dashboard_student_new.html",
        {
            "request": request,
            "user": user,
            "specialization": spec_enum,
            "spec_config": spec_config,
            "user_license": user_license,
            "current_license": user_license,  # For payment_verified check
            "active_card_platform": (user_license.public_card_platform or "default") if spec_enum == "LFA_FOOTBALL_PLAYER" else "default",
            "has_active_enrollment": has_active_enrollment,
            "available_semesters": available_semesters,
            "current_semester": current_semester,
            "specialization_color": spec_config.get("color", "#667eea"),
            "pending_enrollments": pending_enrollments,
            "credit_balance": credit_balance,
            "credit_purchased": user.credit_purchased if hasattr(user, 'credit_purchased') else 0,
            # LFA Player age category info
            "age_category": age_category,
            "age_category_name": age_category_name,
            "age_range": age_range,
            "age_description": age_description,
            "user_age": user_age,
            "spec_header_class": spec_header_class,
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# LFA PLAYER CARD PHOTO  (student self-upload)
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import UploadFile, File  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from ...services.player_photo_service import (  # noqa: E402
    save_player_photo,
    delete_player_photo,
    save_portrait_photo,
    delete_portrait_photo,
    save_landscape_photo,
    delete_landscape_photo,
    save_compact_bg_photo,
    delete_compact_bg_photo,
    save_showcase_bg_photo,
    delete_showcase_bg_photo,
    save_sponsor_logo,
    delete_sponsor_logo,
    save_wc_photo,
    delete_wc_photo,
    save_wc_portrait_photo,
    delete_wc_portrait_photo,
    save_wc_landscape_photo,
    delete_wc_landscape_photo,
    save_initial_player_photo,
)


@router.post("/dashboard/lfa-player-photo")
async def student_upload_player_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_player_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.player_card_photo_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/lfa-player-photo/delete")
async def student_delete_player_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_player_photo(user.id)
        lfa_license.player_card_photo_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/lfa-player-photo-portrait")
async def student_upload_portrait_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_portrait_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.card_photo_portrait_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/lfa-player-photo-portrait/delete")
async def student_delete_portrait_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_portrait_photo(user.id)
        lfa_license.card_photo_portrait_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/lfa-player-photo-landscape")
async def student_upload_landscape_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_landscape_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.card_photo_landscape_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/lfa-player-photo-landscape/delete")
async def student_delete_landscape_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_landscape_photo(user.id)
        lfa_license.card_photo_landscape_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/lfa-player-photo-compact-bg")
async def student_upload_compact_bg_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_compact_bg_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.card_bg_compact_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/lfa-player-photo-compact-bg/delete")
async def student_delete_compact_bg_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_compact_bg_photo(user.id)
        lfa_license.card_bg_compact_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/lfa-player-photo-showcase-bg")
async def student_upload_showcase_bg_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_showcase_bg_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.card_bg_showcase_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/lfa-player-photo-showcase-bg/delete")
async def student_delete_showcase_bg_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_showcase_bg_photo(user.id)
        lfa_license.card_bg_showcase_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/lfa-player-sponsor-logo")
async def student_upload_sponsor_logo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_sponsor_logo(await file.read(), file.content_type or "", user.id)
        lfa_license.sponsor_logo_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ══════════════════════════════════════════════════════════════════════════════
# ONBOARDING INITIAL PHOTO  (atomic dual-write — PC + WC in one transaction)
# Used exclusively by onboarding Step 7.  Writes player_card_photo_url AND
# wc_photo_url to the same URL in one db.commit() so the two fields start life
# as independent copies with no fallback dependency between them.
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/dashboard/initial-player-photo")
async def student_upload_initial_player_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_initial_player_photo(await file.read(), file.content_type or "", user.id)
        # Atomic dual-write: both fields set in a single commit.
        # If commit fails, neither field is updated — no partial state.
        lfa_license.player_card_photo_url = url
        lfa_license.wc_photo_url          = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ══════════════════════════════════════════════════════════════════════════════
# WELCOME CARD PHOTOS  (student self-upload — fully separate from Player Card)
# Route naming: /dashboard/wc-photo* mirrors /dashboard/lfa-player-photo* but
# writes to the independent wc_photo_* fields on UserLicense.
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/dashboard/wc-photo")
async def student_upload_wc_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_wc_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.wc_photo_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/wc-photo/delete")
async def student_delete_wc_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_wc_photo(user.id)
        lfa_license.wc_photo_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/wc-photo-portrait")
async def student_upload_wc_portrait_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_wc_portrait_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.wc_photo_portrait_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/wc-photo-portrait/delete")
async def student_delete_wc_portrait_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_wc_portrait_photo(user.id)
        lfa_license.wc_photo_portrait_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/wc-photo-landscape")
async def student_upload_wc_landscape_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_wc_landscape_photo(await file.read(), file.content_type or "", user.id)
        lfa_license.wc_photo_landscape_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/wc-photo-landscape/delete")
async def student_delete_wc_landscape_photo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_wc_landscape_photo(user.id)
        lfa_license.wc_photo_landscape_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/lfa-player-sponsor-logo/delete")
async def student_delete_sponsor_logo(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if lfa_license:
        delete_sponsor_logo(user.id)
        lfa_license.sponsor_logo_url = None
        db.commit()
    return JSONResponse({"ok": True})


@router.post("/dashboard/card-photo-focus")
async def student_set_card_photo_focus(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Save photo focus point (X%, Y%) for a specific card variant."""
    try:
        body = await request.json()
        variant = body.get("variant")
        x = int(body.get("x", 50))
        y = int(body.get("y", 50))
    except Exception:
        return JSONResponse({"ok": False, "error": "Invalid request body"}, status_code=400)
    if variant not in ("compact", "showcase"):
        return JSONResponse({"ok": False, "error": "Invalid variant"}, status_code=400)
    x = max(0, min(100, x))
    y = max(0, min(100, y))
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    if variant == "compact":
        lfa_license.card_compact_focus_x = x
        lfa_license.card_compact_focus_y = y
    else:
        lfa_license.card_showcase_focus_x = x
        lfa_license.card_showcase_focus_y = y
    db.commit()
    return JSONResponse({"ok": True, "x": x, "y": y})


# ══════════════════════════════════════════════════════════════════════════════
# PLAYER CARD THEME  (student self-service)
# ══════════════════════════════════════════════════════════════════════════════

from ...services.card_theme_service import apply_theme as _apply_theme, unlock_theme as _unlock_theme  # noqa: E402
from ...services.card_variant_service import apply_variant as _apply_variant, unlock_variant as _unlock_variant  # noqa: E402
from ...services.card_platform_service import PLATFORM_PRESETS as _PLATFORM_PRESETS  # noqa: E402
from ...services.card_draft_service import CardDraftService as _CardDraftService  # noqa: E402
from pydantic import BaseModel as _BaseModel  # noqa: E402

_VALID_PLATFORM_IDS: frozenset = frozenset(_PLATFORM_PRESETS.keys())


class _CardThemeRequest(_BaseModel):
    theme: str


class _CardVariantRequest(_BaseModel):
    variant: str


class _CardPlatformRequest(_BaseModel):
    platform: str


def _get_lfa_license(db, user_id: int):
    """Return the active LFA Football Player license, or None."""
    return db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()


@router.post("/dashboard/card-theme")
async def student_set_card_theme(
    payload: _CardThemeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Update the active colour theme for the authenticated student's player card."""
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    try:
        _apply_theme(db, lfa_license, payload.theme)
        return JSONResponse({"ok": True, "theme": payload.theme})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/unlock-theme")
async def student_unlock_theme(
    payload: _CardThemeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Unlock a premium card theme by spending credits."""
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    try:
        _unlock_theme(db, user, lfa_license, payload.theme)
        return JSONResponse({"ok": True, "theme": payload.theme,
                             "new_balance": user.credit_balance})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/card-variant")
async def student_set_card_variant(
    payload: _CardVariantRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Apply a card layout variant (must already be unlocked for premium variants)."""
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    try:
        _apply_variant(db, lfa_license, payload.variant)
        return JSONResponse({"ok": True, "variant": payload.variant})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/unlock-variant")
async def student_unlock_variant(
    payload: _CardVariantRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Unlock a premium card layout variant by spending credits."""
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    try:
        _unlock_variant(db, user, lfa_license, payload.variant)
        return JSONResponse({"ok": True, "variant": payload.variant,
                             "new_balance": user.credit_balance})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/card-platform")
async def student_set_card_platform(
    payload: _CardPlatformRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Save the player's preferred public card platform."""
    if payload.platform not in _VALID_PLATFORM_IDS:
        return JSONResponse(
            {"ok": False, "error": f"Unknown platform: {payload.platform!r}"},
            status_code=422,
        )
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    draft = _CardDraftService.get_player_card_draft(db, user.id)
    # "default" stored as NULL (NULL == platform default everywhere)
    _CardDraftService.update_draft_platform(
        db, draft, None if payload.platform == "default" else payload.platform
    )
    return JSONResponse({"ok": True, "platform": payload.platform})


@router.post("/dashboard/publish-card")
async def student_publish_card(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Copy the current editor draft state to the published public card state.

    Idempotent: calling it multiple times with the same draft produces the same result.
    The public card route (/players/{id}/card) reads published_card_* fields only,
    so a user's public card is frozen until they explicitly call this endpoint.
    """
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    draft = _CardDraftService.get_player_card_draft(db, user.id)
    _CardDraftService.publish_draft(db, draft)
    return JSONResponse({
        "ok": True,
        "published": {
            "theme":    draft.published_theme,
            "variant":  draft.published_variant,
            "platform": draft.published_platform or "default",
        },
    })

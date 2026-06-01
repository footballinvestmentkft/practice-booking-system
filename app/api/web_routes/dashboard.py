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

import httpx as _httpx

from sqlalchemy import func as sqlfunc, or_, and_
from ...models.friendship import Friendship, FriendshipStatus
from ...models.vt_challenge import VirtualTrainingChallenge, ChallengeStatus

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
from ...services.skill_progression import get_all_skill_keys
from ...models.user_mood_photos import MOOD_PHOTO_SLOTS, UserMoodPhoto

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


@router.get("/card-editor/player", response_class=HTMLResponse)
async def lfa_player_card_editor(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Player Card editor — canonical route (CE-1).  Old URL redirects here."""
    spec_enum = "LFA_FOOTBALL_PLAYER"
    _FAMILY   = "player_card"           # CE-3.1: consolidated for family-aware prep

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
    card_draft = _CardDraftService.get_draft(db, user.id, _FAMILY)

    # Card color picker data — family-aware ownership (TS-1, card_color_service)
    from ...services.card_color_service import (  # noqa: E402
        get_colors_for_family as _get_colors_for_family,
        get_owned_color_ids as _get_owned_color_ids,
    )
    _raw_colors   = _get_colors_for_family(_FAMILY)
    _owned_colors = _get_owned_color_ids(db, user.id, _FAMILY)
    card_themes = [
        {
            "id":          c.id,
            "label":       c.label,
            "dot_color":   c.dot_color,
            "is_premium":  c.is_premium,
            "credit_cost": c.credit_cost,
            "unlocked":    (not c.is_premium) or (c.id in _owned_colors),
        }
        for c in _raw_colors
    ]
    # CE-2: owned-only — free colors always pass; premium only if ownership row exists
    card_themes = [t for t in card_themes if t["unlocked"]]
    active_card_theme = card_draft.draft_theme
    # Render-time theme fallback — if draft theme was filtered out, fall back to "default"
    if not any(t["id"] == active_card_theme for t in card_themes):
        active_card_theme = "default"

    # Published public card state (read-only in the editor — shown as indicator)
    published_card_theme    = card_draft.published_theme    or "default"
    published_card_variant  = card_draft.published_variant  or "fclassic"
    published_card_platform = card_draft.published_platform or "default"

    # Card variant picker data — CDO-based ownership check
    from ...services.card_variant_service import get_all_variants as _get_all_variants  # noqa: E402
    from ...services.card_design_service import is_design_accessible as _is_design_accessible  # noqa: E402
    card_variants = [
        {
            "id": v.id,
            "label": v.label,
            "description": v.description,
            "is_premium": v.is_premium,
            "credit_cost": v.credit_cost,
            "available": v.available,
            "unlocked": _is_design_accessible(db, user.id, _FAMILY, v.id),
        }
        for v in _get_all_variants()
    ]
    # CE-2: owned-only — no purchase affordance in editor (CE-2 policy)
    card_variants = [v for v in card_variants if v["unlocked"]]
    active_card_variant  = card_draft.draft_variant
    active_variant_owned = _is_design_accessible(db, user.id, _FAMILY, active_card_variant)
    # CE-2: render-time fallback — if draft points to unowned design, use first owned
    # Does NOT write to DB; user explicit tile-click will persist the change.
    if not active_variant_owned and card_variants:
        active_card_variant = card_variants[0]["id"]
        active_variant_owned = True

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

    # Highlight video state — draft and published snapshots for Media tab UI.
    from ...services.highlight_video_service import build_youtube_embed_url as _build_yt_embed
    _dd = card_draft.draft_data or {}
    _pd = card_draft.published_data or {}
    _draft_hv_raw = _dd.get("highlight_video")
    _pub_hv_raw   = _pd.get("highlight_video")

    def _hv_ctx(raw: dict | None) -> dict | None:
        if not raw or not isinstance(raw, dict):
            return None
        vid = raw.get("video_id")
        provider = raw.get("provider", "youtube")
        if not vid:
            return None
        embed_url = _build_yt_embed(vid) if provider == "youtube" else None
        return {
            "provider":   provider,
            "video_id":   vid,
            "embed_url":  embed_url,
            "source_url": raw.get("source_url", ""),
        }

    draft_highlight_video     = _hv_ctx(_draft_hv_raw)
    published_highlight_video = _hv_ctx(_pub_hv_raw)
    # unpublished when video_id OR provider diverges (includes remove case)
    _draft_vid  = (_draft_hv_raw or {}).get("video_id")
    _pub_vid    = (_pub_hv_raw   or {}).get("video_id")
    _draft_prov = (_draft_hv_raw or {}).get("provider")
    _pub_prov   = (_pub_hv_raw   or {}).get("provider")
    highlight_video_unpublished = (_draft_vid != _pub_vid or _draft_prov != _pub_prov)

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
            "active_variant_owned": active_variant_owned,
            "active_card_platform": card_draft.draft_platform or "default",
            "show_variant_picker": True,  # page is LFA Football Player only
            "animated_capable_platforms": animated_capable_platforms,
            "platforms": editor_platforms,
            "canvas_sizes": canvas_sizes,
            # Published state — used for "Unpublished changes" indicator + View Public Card CTA
            "published_card_theme":    published_card_theme,
            "published_card_variant":  published_card_variant,
            "published_card_platform": published_card_platform,
            # Highlight video state — Media tab
            "draft_highlight_video":       draft_highlight_video,
            "published_highlight_video":   published_highlight_video,
            "highlight_video_unpublished": highlight_video_unpublished,
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

    # Social counts (all spec types)
    social_pending_friends = db.query(sqlfunc.count(Friendship.id)).filter(
        Friendship.addressee_id == user.id,
        Friendship.status == FriendshipStatus.PENDING
    ).scalar() or 0

    social_pending_challenges = db.query(sqlfunc.count(VirtualTrainingChallenge.id)).filter(
        VirtualTrainingChallenge.challenged_id == user.id,
        VirtualTrainingChallenge.status == ChallengeStatus.PENDING
    ).scalar() or 0

    social_active_challenges = db.query(sqlfunc.count(VirtualTrainingChallenge.id)).filter(
        or_(
            VirtualTrainingChallenge.challenger_id == user.id,
            VirtualTrainingChallenge.challenged_id == user.id,
        ),
        VirtualTrainingChallenge.status == ChallengeStatus.ACCEPTED
    ).scalar() or 0

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

    # Public profile context (LFA_FOOTBALL_PLAYER only)
    public_profile_url = None
    grid_editor_url = None
    is_profile_published = False
    profile_grid_filled_slots = 0
    profile_grid_total_slots = _MAX_SLOTS
    has_published_highlight_video = False

    if spec_enum == "LFA_FOOTBALL_PLAYER":
        card_draft = _CardDraftService.get_player_card_draft(db, user.id)
        public_profile_url = f"/players/{user.id}"
        grid_editor_url = "/dashboard/lfa-football-player/public-profile-editor"
        is_profile_published = _CardDraftService.is_published(card_draft)
        _pub_grid = _build_published_grid_state(card_draft)
        profile_grid_filled_slots = len(_pub_grid) if _pub_grid else 0
        has_published_highlight_video = bool(
            (card_draft.published_data or {}).get("highlight_video")
            if card_draft else False
        )

    skill_count = len(get_all_skill_keys())
    has_welcome_card = bool(
        user_license.onboarding_completed
        or user_license.football_skills is not None
    )

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
            # Public profile entry point (LFA_FOOTBALL_PLAYER only)
            "public_profile_url": public_profile_url,
            "grid_editor_url": grid_editor_url,
            "is_profile_published": is_profile_published,
            "profile_grid_filled_slots": profile_grid_filled_slots,
            "profile_grid_total_slots": profile_grid_total_slots,
            "has_published_highlight_video": has_published_highlight_video,
            # Social counts
            "social_pending_friends": social_pending_friends,
            "social_pending_challenges": social_pending_challenges,
            "social_active_challenges": social_active_challenges,
            # Explicit LFA spec context for spec_subpage_hdr.html quicknav
            "spec_dashboard_url": "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
            "spec_profile_url": "/profile/lfa-football-player",
            "spec_profile_icon": "🪪",
            # MVP dashboard context
            "skill_count": skill_count,
            "has_welcome_card": has_welcome_card,
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
from ...services.card_variant_service import unlock_variant as _unlock_variant  # noqa: E402
from ...services.card_platform_service import PLATFORM_PRESETS as _PLATFORM_PRESETS  # noqa: E402
from ...services.card_draft_service import CardDraftService as _CardDraftService  # noqa: E402
from ...services.card_color_service import (  # noqa: E402
    unlock_color as _unlock_color,
    InsufficientCreditsError as _InsufficientCreditsError,
)
from pydantic import BaseModel as _BaseModel  # noqa: E402

_VALID_PLATFORM_IDS: frozenset = frozenset(_PLATFORM_PRESETS.keys())


class _CardThemeRequest(_BaseModel):
    theme: str


class _CardVariantRequest(_BaseModel):
    variant: str


class _CardColorUnlockRequest(_BaseModel):
    card_type_id: str
    color_id: str


class _CardPlatformRequest(_BaseModel):
    platform: str


class _HighlightVideoRequest(_BaseModel):
    video_url: str


class _WcFromMoodRequest(_BaseModel):
    mood_slot: str


def _get_lfa_license(db, user_id: int):
    """Return the active LFA Football Player license, or None."""
    return db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()


# ── WC PHOTO — ASSIGN FROM MOOD LIBRARY (CE-3.8) ────────────────────────────
# IMG-FIX-1: Priority: processed_png_url (background-removed, when available) →
# fallback: original_url (raw upload). No FK reference stored; no new file
# created. When background removal activates (future), processed asset is used
# automatically without further changes.

def _mood_photo_asset_url(mood_photo) -> str:
    """Return the best available asset URL for a mood photo.
    Priority: processed_png_url (bg-removed) > original_url (raw).
    """
    return mood_photo.processed_png_url or mood_photo.original_url

@router.post("/dashboard/wc-photo/from-mood")
async def student_assign_wc_photo_from_mood(
    payload: _WcFromMoodRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    if payload.mood_slot not in MOOD_PHOTO_SLOTS:
        return JSONResponse({"ok": False, "error": "Unknown mood slot"}, status_code=422)
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    mood_photo = db.query(UserMoodPhoto).filter_by(user_id=user.id, slot=payload.mood_slot).first()
    if not mood_photo:
        return JSONResponse({"ok": False, "error": "Mood photo not found"}, status_code=404)
    photo_url = _mood_photo_asset_url(mood_photo)
    lfa_license.wc_photo_url = photo_url
    db.commit()
    return JSONResponse({"ok": True, "photo_url": photo_url})


@router.post("/dashboard/wc-photo-portrait/from-mood")
async def student_assign_wc_portrait_photo_from_mood(
    payload: _WcFromMoodRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    if payload.mood_slot not in MOOD_PHOTO_SLOTS:
        return JSONResponse({"ok": False, "error": "Unknown mood slot"}, status_code=422)
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    mood_photo = db.query(UserMoodPhoto).filter_by(user_id=user.id, slot=payload.mood_slot).first()
    if not mood_photo:
        return JSONResponse({"ok": False, "error": "Mood photo not found"}, status_code=404)
    photo_url = _mood_photo_asset_url(mood_photo)
    lfa_license.wc_photo_portrait_url = photo_url
    db.commit()
    return JSONResponse({"ok": True, "photo_url": photo_url})


@router.post("/dashboard/wc-photo-landscape/from-mood")
async def student_assign_wc_landscape_photo_from_mood(
    payload: _WcFromMoodRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    if payload.mood_slot not in MOOD_PHOTO_SLOTS:
        return JSONResponse({"ok": False, "error": "Unknown mood slot"}, status_code=422)
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    mood_photo = db.query(UserMoodPhoto).filter_by(user_id=user.id, slot=payload.mood_slot).first()
    if not mood_photo:
        return JSONResponse({"ok": False, "error": "Mood photo not found"}, status_code=404)
    photo_url = _mood_photo_asset_url(mood_photo)
    lfa_license.wc_photo_landscape_url = photo_url
    db.commit()
    return JSONResponse({"ok": True, "photo_url": photo_url})


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


@router.post("/dashboard/wc-card-theme")
async def student_set_wc_card_theme(
    payload: _CardThemeRequest,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """CS-COLOR-1: Set the active colour theme for the Welcome Card Studio.

    Writes to CardDraft(card_type_id='welcome_card').draft_theme.
    Only free themes are valid in CS-COLOR-1AA (no premium unlock scope).
    Requires active LFA Football Player license + onboarding completed.
    """
    from ...services.card_theme_service import get_all_themes as _get_wc_themes
    from ...services.card_draft_service import CardDraftService as _WCDraftService

    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    if not lfa_license.onboarding_completed:
        return JSONResponse({"ok": False, "error": "Onboarding not completed"}, status_code=403)

    # Validate: theme must exist and be free (CS-COLOR-1: no premium unlock)
    free_theme_ids = {t.id for t in _get_wc_themes(db) if not t.is_premium}
    if payload.theme not in free_theme_ids:
        return JSONResponse(
            {"ok": False, "error": f"Unknown or locked theme: {payload.theme!r}"},
            status_code=400,
        )

    draft = _WCDraftService.get_draft(db, user.id, "welcome_card")
    _WCDraftService.update_draft_theme(db, draft, payload.theme)
    return JSONResponse({"ok": True, "theme": payload.theme})


@router.post("/dashboard/unlock-theme")
async def student_unlock_theme(
    payload: _CardThemeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Unlock a premium card theme by spending credits.

    LEGACY endpoint — writes to user_licenses.unlocked_card_themes JSON.
    Kept for backward compatibility. New purchases use /dashboard/unlock-color.
    """
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    try:
        _unlock_theme(db, user, lfa_license, payload.theme)
        return JSONResponse({"ok": True, "theme": payload.theme,
                             "new_balance": user.credit_balance})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/dashboard/unlock-color")
async def student_unlock_color(
    payload: _CardColorUnlockRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Unlock a premium card color pack for a specific card family (TS-1).

    Writes to card_color_ownership (family-aware, new ownership model).
    Supports card_type_id='player_card' only in TS-1; other families return 422.
    Idempotent: already-owned colors return ok=True with already_owned=True and
    credits_charged=0 — no double deduction possible.
    """
    try:
        result = _unlock_color(db, user, payload.card_type_id, payload.color_id)
        return JSONResponse({
            "ok":             True,
            "already_owned":  result.already_owned,
            "credits_charged": result.credits_charged,
            "credit_balance": result.credit_balance,
            "color_id":       result.color_id,
            "card_type_id":   result.card_type_id,
        })
    except ValueError as e:
        error_key = str(e)
        if error_key == "unsupported_family":
            return JSONResponse(
                {"ok": False, "error": f"Unsupported card family: {payload.card_type_id!r}"},
                status_code=422,
            )
        if error_key == "color_not_found":
            return JSONResponse(
                {"ok": False, "error": f"Unknown color: {payload.color_id!r}"},
                status_code=422,
            )
        return JSONResponse({"ok": False, "error": error_key}, status_code=400)
    except _InsufficientCreditsError as e:
        return JSONResponse(
            {"ok": False, "error": "insufficient_credits",
             "required": e.required, "balance": e.available},
            status_code=402,
        )


@router.post("/dashboard/card-variant")
async def student_set_card_variant(
    payload: _CardVariantRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Apply a card layout variant (requires CardDesignOwnership entitlement)."""
    from ...services.card_design_service import is_design_accessible as _is_da  # noqa: E402
    from ...services.card_draft_service import CardDraftService as _CDS  # noqa: E402
    from ...services.card_variant_service import VARIANTS as _VARIANTS  # noqa: E402

    if payload.variant not in _VARIANTS:
        return JSONResponse({"ok": False, "error": f"Unknown variant: {payload.variant!r}"}, status_code=400)
    if not _VARIANTS[payload.variant].available:
        return JSONResponse({"ok": False, "error": f"Variant '{_VARIANTS[payload.variant].label}' is not yet available"}, status_code=400)
    if not _is_da(db, user.id, "player_card", payload.variant):
        return JSONResponse({"ok": False, "error": f"Design '{_VARIANTS[payload.variant].label}' not owned"}, status_code=403)

    draft = _CDS.get_player_card_draft(db, user_id=user.id)
    _CDS.update_draft_variant(db, draft, payload.variant)
    return JSONResponse({"ok": True, "variant": payload.variant})


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
    # CDO guard — user must own the draft variant before it can be published.
    from ...services.card_design_service import is_design_accessible as _is_da_pub  # noqa: E402
    if not _is_da_pub(db, user.id, "player_card", draft.draft_variant):
        return JSONResponse(
            {"ok": False, "error": "Design not owned — purchase it first to publish"},
            status_code=403,
        )
    _CardDraftService.publish_draft(db, draft)
    return JSONResponse({
        "ok": True,
        "published": {
            "theme":    draft.published_theme,
            "variant":  draft.published_variant,
            "platform": draft.published_platform or "default",
        },
    })


# ── CE-1: legacy redirect ─────────────────────────────────────────────────────
# The old URL stays alive as a 303 so bookmarks and cached links still work.
# 303 (not 301) prevents browsers caching the redirect — allows future path
# changes without stale-cache issues on authenticated pages.
@router.get("/dashboard/lfa-football-player/card-editor", response_class=HTMLResponse)
async def lfa_player_card_editor_legacy(
    user: User = Depends(get_current_user_web),
) -> RedirectResponse:
    return RedirectResponse(url="/card-editor/player", status_code=303)


@router.post("/dashboard/lfa-football-player/card-editor/media/highlight-video")
async def student_save_highlight_video(
    payload: _HighlightVideoRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Save a YouTube or TikTok highlight video URL to the player card draft.

    Validates URL, extracts provider + video_id, writes draft_data.highlight_video.
    The video is NOT visible on the public profile until the card is published.
    CSRF protection is enforced by the global CSRF middleware.
    """
    from ...services.highlight_video_service import build_youtube_embed_url as _build_yt_embed
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    draft = _CardDraftService.get_player_card_draft(db, user.id)
    try:
        _CardDraftService.update_draft_highlight_video(db, draft, payload.video_url)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    hv = (draft.draft_data or {}).get("highlight_video", {})
    video_id = hv.get("video_id", "")
    provider = hv.get("provider", "youtube")
    embed_url = _build_yt_embed(video_id) if provider == "youtube" else None
    return JSONResponse({
        "ok":        True,
        "provider":  provider,
        "video_id":  video_id,
        "embed_url": embed_url,
        "status":    "draft",
    })


@router.delete("/dashboard/lfa-football-player/card-editor/media/highlight-video")
async def student_remove_highlight_video(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Remove the highlight video from the player card draft.

    Removal only affects draft state. The published profile still shows the
    previous video until the card is published again.
    CSRF protection is enforced by the global CSRF middleware.
    """
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)
    draft = _CardDraftService.get_player_card_draft(db, user.id)
    _CardDraftService.remove_draft_highlight_video(db, draft)
    pub_hv = (draft.published_data or {}).get("highlight_video")
    return JSONResponse({
        "ok":     True,
        "status": "removed_from_draft",
        "published_video_still_live": pub_hv is not None,
    })


# ── Public Profile Grid Designer routes ───────────────────────────────────────

from app.services.profile_grid_service import (  # noqa: E402
    build_draft_grid_state     as _build_draft_grid_state,
    build_published_grid_state as _build_published_grid_state,
    SLOT_REGISTRY              as _SLOT_REGISTRY,
    MAX_SLOTS                  as _MAX_SLOTS,
    VALID_ZONES                as _VALID_ZONES,
    VALID_WIDGET_TYPES         as _VALID_WIDGET_TYPES,
    zone_slot_ids              as _zone_slot_ids,
)


class _SlotWidgetRequest(_BaseModel):
    # Widget type — None triggers backward-compat video path when video_url present.
    widget_type: str | None = None
    # Video fields (video_youtube / video_tiktok)
    video_url:     str | None = None
    title:         str = ""
    # TikTok-only: optional custom thumbnail HTTPS URL
    thumbnail_url: str | None = None
    # text_bio fields
    content:   str | None = None
    heading:   str = ""
    # image_url fields
    url:       str | None = None
    alt_text:  str | None = None
    caption:   str = ""
    # weather_current fields (browser geolocation payload)
    lat:        float | None = None
    lon:        float | None = None
    accuracy_m: float | None = None
    units:      str = "metric"


class _ReorderRequest(_BaseModel):
    zone:     str
    slot_ids: list[str]


class _MoveRequest(_BaseModel):
    source_slot_id: str
    target_slot_id: str
    on_conflict:    str = "swap"


@router.get(
    "/dashboard/lfa-football-player/public-profile-editor",
    response_class=HTMLResponse,
)
async def lfa_public_profile_editor(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Render the visual Public Profile Grid Designer page."""
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/dashboard", status_code=302)

    draft = _CardDraftService.get_player_card_draft(db, user.id)
    draft_slots     = _build_draft_grid_state(draft)
    published_slots = _build_published_grid_state(draft)
    is_pub          = _CardDraftService.is_published(draft)

    return templates.TemplateResponse(
        request,
        "dashboard/lfa_public_profile_editor.html",
        {
            "user":             user,
            "draft_slots":      draft_slots,
            "published_slots":  published_slots,
            "is_published":     is_pub,
            "profile_url":      f"/players/{user.id}",
            "card_editor_url":  "/card-studio",
        },
    )


@router.post(
    "/dashboard/lfa-football-player/public-profile-editor/slots/{slot_id}",
)
async def lfa_profile_editor_set_slot(
    slot_id: str,
    payload: _SlotWidgetRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Save a widget module to a draft profile grid slot.

    Accepts widget_type (text_bio, image_url, video_youtube, video_tiktok).
    Backward-compat: omit widget_type and pass video_url for video modules.
    CSRF protection enforced by global middleware.
    """
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)

    wtype = payload.widget_type

    # Validate widget_type when explicitly provided.
    if wtype is not None and wtype not in _VALID_WIDGET_TYPES:
        return JSONResponse(
            {"ok": False, "error": f"Unknown widget_type: {wtype!r}. Valid: {sorted(_VALID_WIDGET_TYPES)}"},
            status_code=422,
        )

    # Validate thumbnail_url — HTTPS only, ignored for non-TikTok types.
    if payload.thumbnail_url:
        from urllib.parse import urlparse as _urlparse
        _pt = _urlparse(payload.thumbnail_url)
        if _pt.scheme != "https" or not _pt.netloc:
            return JSONResponse(
                {"ok": False, "error": "thumbnail_url must be a valid HTTPS URL."},
                status_code=422,
            )

    # Require either widget_type or video_url (backward-compat video path).
    if wtype is None and not payload.video_url:
        return JSONResponse(
            {"ok": False, "error": "widget_type or video_url is required."},
            status_code=422,
        )

    # Build per-type payload dict for the service.
    if wtype is None or wtype in ("video_youtube", "video_tiktok"):
        if not payload.video_url:
            return JSONResponse(
                {"ok": False, "error": "video_url is required for video widgets."},
                status_code=422,
            )
        svc_payload: dict | None = None  # service handles legacy path via video_url positional
    elif wtype == "text_bio":
        if not payload.content:
            return JSONResponse(
                {"ok": False, "error": "content is required for text_bio widget."},
                status_code=422,
            )
        svc_payload = {"content": payload.content, "heading": payload.heading or ""}
    elif wtype == "image_url":
        if not payload.url or not payload.alt_text:
            return JSONResponse(
                {"ok": False, "error": "url and alt_text are required for image_url widget."},
                status_code=422,
            )
        svc_payload = {
            "url":      payload.url,
            "alt_text": payload.alt_text,
            "caption":  payload.caption or "",
        }
    elif wtype == "weather_current":
        if payload.lat is None or payload.lon is None or payload.accuracy_m is None:
            return JSONResponse(
                {"ok": False, "error": "lat, lon, and accuracy_m are required for weather_current widget."},
                status_code=422,
            )
        svc_payload = {
            "lat":        payload.lat,
            "lon":        payload.lon,
            "accuracy_m": payload.accuracy_m,
            "units":      payload.units or "metric",
        }
    else:
        svc_payload = None

    draft = _CardDraftService.get_player_card_draft(db, user.id)
    try:
        _CardDraftService.set_draft_slot(
            db, draft, slot_id,
            payload.video_url,
            payload.title,
            widget_type=wtype,
            payload=svc_payload,
            thumbnail_url=payload.thumbnail_url,
        )
    except (ValueError, KeyError) as exc:
        http_status = 404 if "Unknown slot_id" in str(exc) else 422
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=http_status)
    except _httpx.TimeoutException:
        return JSONResponse(
            {"ok": False, "error": "Weather service timed out. Please try again."},
            status_code=503,
        )
    except _httpx.HTTPError:
        return JSONResponse(
            {"ok": False, "error": "Weather service error. Please try again."},
            status_code=503,
        )

    pg = (draft.draft_data or {}).get("profile_grid", {})
    slot_entry = next(
        (s for s in pg.get("slots", []) if s["slot_id"] == slot_id), {}
    )
    mod = slot_entry.get("module", {})
    return JSONResponse({
        "ok":             True,
        "slot_id":        slot_id,
        "widget_type":    mod.get("type"),
        "provider":       mod.get("provider"),
        "video_id":       mod.get("video_id"),
        "title":          mod.get("title", ""),
        "thumbnail_url":  mod.get("custom_thumbnail_url"),
        # weather_current fields:
        "location_label": mod.get("location_label"),
        "cached_at":      mod.get("cached_at"),
        "fetch_error":    mod.get("fetch_error"),
        "weather":        mod.get("weather"),
        "status":         "draft",
    })


@router.delete(
    "/dashboard/lfa-football-player/public-profile-editor/slots/{slot_id}",
)
async def lfa_profile_editor_remove_slot(
    slot_id: str,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Remove a module from a draft profile grid slot.

    Publish is required for the removal to be reflected on the public profile.
    CSRF protection enforced by global middleware.
    """
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)

    draft = _CardDraftService.get_player_card_draft(db, user.id)
    try:
        _CardDraftService.remove_draft_slot(db, draft, slot_id)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    pub_pg = (draft.published_data or {}).get("profile_grid")
    pub_has_slot = any(
        s["slot_id"] == slot_id
        for s in (pub_pg or {}).get("slots", [])
    )
    return JSONResponse({
        "ok":                        True,
        "slot_id":                   slot_id,
        "status":                    "removed_from_draft",
        "published_slot_still_live": pub_has_slot,
    })


@router.post(
    "/dashboard/lfa-football-player/public-profile-editor/reorder",
)
async def lfa_profile_editor_reorder_zone(
    payload: _ReorderRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Reorder filled modules within a zone in the draft profile grid.

    Returns {"ok": true, "status": "noop"} when ≤1 filled slot or order unchanged — no DB write.
    Returns {"ok": true, "status": "reordered"} on successful reorder.
    CSRF protection enforced by global middleware.
    """
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)

    draft = _CardDraftService.get_player_card_draft(db, user.id)

    # Pre-compute noop using same positional logic as the service (for response status).
    existing_pg = (draft.draft_data or {}).get("profile_grid")
    _occupied = {
        s["slot_id"]: s.get("module")
        for s in (existing_pg or {}).get("slots", [])
        if isinstance(s.get("slot_id"), str)
    }
    _canon = _zone_slot_ids(payload.zone)  # [] for invalid zone — service will raise ValueError
    _n = min(len(payload.slot_ids), len(_canon))
    is_noop = all(
        _occupied.get(payload.slot_ids[i]) is None or payload.slot_ids[i] == _canon[i]
        for i in range(_n)
    )

    try:
        _CardDraftService.reorder_draft_zone(db, draft, payload.zone, payload.slot_ids)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    return JSONResponse({
        "ok":     True,
        "zone":   payload.zone,
        "status": "noop" if is_noop else "reordered",
    })


@router.post(
    "/dashboard/lfa-football-player/public-profile-editor/move",
)
async def lfa_profile_editor_move_slot(
    payload: _MoveRequest,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Move a slot's module to another slot (cross-zone or same-zone).

    on_conflict: "swap" (default) | "overwrite" | "reject"
    Returns {"ok": true, "status": "noop"} when source is empty — no DB write.
    Returns {"ok": true, "status": "moved", ...} on success.
    CSRF protection enforced by global middleware.
    """
    lfa_license = _get_lfa_license(db, user.id)
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "No active LFA Football Player license"}, status_code=404)

    draft = _CardDraftService.get_player_card_draft(db, user.id)

    # Pre-detect noop (source empty) before calling service, for response status.
    existing_pg = (draft.draft_data or {}).get("profile_grid")
    _occupied_move = {
        s["slot_id"]: s.get("module")
        for s in (existing_pg or {}).get("slots", [])
        if isinstance(s.get("slot_id"), str)
    }
    is_noop = _occupied_move.get(payload.source_slot_id) is None

    try:
        _CardDraftService.move_draft_slot(
            db, draft,
            payload.source_slot_id,
            payload.target_slot_id,
            on_conflict=payload.on_conflict,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    if is_noop:
        return JSONResponse({"ok": True, "status": "noop"})

    return JSONResponse({
        "ok":            True,
        "status":        "moved",
        "source_slot_id": payload.source_slot_id,
        "target_slot_id": payload.target_slot_id,
    })

"""
Admin panel routes
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime, timezone, date
import logging

import re
from collections import defaultdict

from sqlalchemy.orm import joinedload
from sqlalchemy import func as sqlfunc, or_, case

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.semester import Semester, SemesterStatus, SemesterCategory
from ...models.license import UserLicense, LicenseProgression
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.specialization import SpecializationType
from ...models.invoice_request import InvoiceRequest, InvoiceRequestStatus
from ...models.credit_transaction import CreditTransaction, TransactionType
from ...core.security import get_password_hash
import uuid as _uuid
from ...models.coupon import Coupon
from ...models.invitation_code import InvitationCode
from ...models.session import Session as SessionModel, EventCategory
from ...models.booking import Booking, BookingStatus
from ...models.attendance import Attendance, AttendanceStatus
from ...services.audit_service import AuditService
from ...models.audit_log import AuditAction
from ...services.tournament.session_generation import get_tournament_venue
from ...models.location import Location, LocationType
from ...models.campus import Campus
from ...models.system_event import SystemEvent, SystemEventLevel
from ...models.game_preset import GamePreset
from ...skills_config import SKILL_CATEGORIES
from ...services.location_validation_service import LocationValidationService
from ...models.event_reward_log import EventRewardLog
from ...models.football_skill_assessment import FootballSkillAssessment
from ...models.notification import Notification, NotificationType
from ...models.instructor_assignment import InstructorAssignment
from ...models.pitch import Pitch
from ...models.semester_schedule_config import SemesterScheduleConfig
from ...services.scheduling.mini_season_generator import MiniSeasonSessionGenerator, PitchConflictError
from datetime import timedelta

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


_USERS_PAGE_SIZE = 100


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    role_filter: str = "",
    status_filter: str = "",
    search: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: User management page with filters and pagination"""
    _admin_guard(user)

    q = db.query(User)
    if role_filter:
        try:
            q = q.filter(User.role == UserRole(role_filter))
        except ValueError:
            pass
    if status_filter == "active":
        q = q.filter(User.is_active == True)
    elif status_filter == "inactive":
        q = q.filter(User.is_active == False)
    if search:
        like = f"%{search}%"
        q = q.filter((User.name.ilike(like)) | (User.email.ilike(like)))

    total_filtered = q.count()
    page = max(1, page)
    total_pages = max(1, (total_filtered + _USERS_PAGE_SIZE - 1) // _USERS_PAGE_SIZE)
    page = min(page, total_pages)
    offset = (page - 1) * _USERS_PAGE_SIZE

    page_users = q.order_by(User.id).offset(offset).limit(_USERS_PAGE_SIZE).all()

    # Stats (always from full DB — filter-independent; single aggregation query)
    _stats = db.query(
        sqlfunc.count().label("total_all"),
        sqlfunc.sum(case((User.role == UserRole.STUDENT, 1), else_=0)).label("total_students"),
        sqlfunc.sum(case((User.role == UserRole.INSTRUCTOR, 1), else_=0)).label("total_instructors"),
        sqlfunc.sum(case((User.is_active == True, 1), else_=0)).label("total_active"),
    ).first()
    total_all = _stats.total_all or 0
    total_students = _stats.total_students or 0
    total_instructors = _stats.total_instructors or 0
    total_active = _stats.total_active or 0

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "user": user,
            "all_users": page_users,
            "role_filter": role_filter,
            "status_filter": status_filter,
            "search": search,
            "stat_total": total_all,
            "stat_students": total_students,
            "stat_instructors": total_instructors,
            "stat_active": total_active,
            "current_page": page,
            "total_pages": total_pages,
            "total_filtered": total_filtered,
            "page_start": offset + 1,
            "page_end": min(offset + _USERS_PAGE_SIZE, total_filtered),
        }
    )


@router.get("/admin/semesters", response_class=HTMLResponse)
async def admin_semesters_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Semester management page"""
    _admin_guard(user)
    
    # Get all semesters
    semesters = db.query(Semester).order_by(Semester.start_date.desc()).all()
    
    return templates.TemplateResponse(
        "admin/semesters.html",
        {
            "request": request,
            "user": user,
            "semesters": semesters
        }
    )


@router.get("/admin/enrollments", response_class=HTMLResponse)
async def admin_enrollments_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Unified Semester Enrollment Management page (replaces /admin/payments)"""
    _admin_guard(user)

    # Get all semesters (for dropdown)
    semesters = db.query(Semester).order_by(Semester.start_date.desc()).all()

    # Get all students with their licenses
    students = db.query(User).filter(User.role == UserRole.STUDENT).order_by(User.name).all()

    # Attach user licenses to each student (batch load — avoids N+1)
    student_ids = [s.id for s in students]
    all_license_rows = (
        db.query(UserLicense)
        .filter(UserLicense.user_id.in_(student_ids))
        .all()
    ) if student_ids else []

    license_map: dict = {}
    for lic in all_license_rows:
        license_map.setdefault(lic.user_id, []).append(lic)

    for student in students:
        student.all_licenses = license_map.get(student.id, [])
        # For specialization management section (moved from /admin/payments)
        student.active_specializations = student.all_licenses

    # Get ALL truly active semesters (running TODAY - between start_date and end_date)
    today = date.today()

    active_semesters = (
        db.query(Semester)
        .options(joinedload(Semester.location), joinedload(Semester.campus))
        .filter(
            Semester.status != SemesterStatus.CANCELLED,
            Semester.start_date <= today,
            Semester.end_date >= today,
        )
        .order_by(Semester.code, Semester.start_date.desc())
        .all()
    )

    # Add specialization_type and extract location from code
    for semester in active_semesters:
        # Extract specialization and location from code
        # Examples:
        #   "LFA_PLAYER_PRE_2025_JAN_BUDA" -> spec: "LFA_PLAYER_PRE", location: "Buda"
        #   "LFA_PLAYER_PRO_2025-26_PEST" -> spec: "LFA_PLAYER_PRO", location: "Pest"
        #   "GANCUJU_WINTER_2025_BUDAPEST" -> spec: "GANCUJU_PLAYER", location: "Budapest"
        #   "INTERNSHIP_FALL_2025_BUDAPEST" -> spec: "INTERNSHIP", location: "Budapest"

        code = semester.code

        # Extract location suffix (BUDA, PEST, BUDAPEST, city names)
        location_match = re.search(r'_(BUDA|PEST|BUDAPEST|DEBRECEN|SZEGED|MISKOLC|GYOR)$', code, re.IGNORECASE)
        if location_match:
            # Remove location suffix for specialization extraction
            code_without_location = code[:location_match.start()]
        else:
            code_without_location = code

        # Remove year patterns (4 digits, or YYYY-YY format, or Q1/Q2/Q3/Q4, or month names)
        code_clean = re.sub(r'_\d{4}(-\d{2})?(_[A-Z]{3,6})?$', '', code_without_location)
        code_clean = re.sub(r'_\d{4}_Q\d$', '', code_clean)

        # Special case: GANCUJU should become GANCUJU_PLAYER
        if code_clean.startswith('GANCUJU'):
            semester.specialization_type = 'GANCUJU_PLAYER'
        else:
            semester.specialization_type = code_clean if code_clean else None

    # Get ALL enrollments for ALL active semesters
    all_enrollments = []
    if active_semesters:
        semester_ids = [s.id for s in active_semesters]
        all_enrollments = (
            db.query(SemesterEnrollment)
            .options(
                joinedload(SemesterEnrollment.user),
                joinedload(SemesterEnrollment.user_license),
                joinedload(SemesterEnrollment.semester)
            )
            .filter(SemesterEnrollment.semester_id.in_(semester_ids))
            .order_by(SemesterEnrollment.requested_at.desc())
            .all()
        )

    # Group enrollments by specialization + location
    specialization_groups = {}
    for spec_type in SpecializationType:
        spec_enrollments = [e for e in all_enrollments if e.user_license.specialization_type == spec_type.value]

        # Get all active semesters for this specialization type
        spec_semesters = [s for s in active_semesters if s.specialization_type == spec_type.value]

        # Group by venue within this specialization (using helper function)
        location_groups = defaultdict(list)

        # First, add enrollments to their locations
        for enrollment in spec_enrollments:
            # get_tournament_venue reads location/campus loaded via joinedload above
            location_key = get_tournament_venue(enrollment.semester)
            location_groups[location_key].append(enrollment)

        # Then, ensure EVERY active semester location has a group (even if empty)
        for semester in spec_semesters:
            location_key = get_tournament_venue(semester)
            if location_key not in location_groups:
                location_groups[location_key] = []

        # Create a group for each location
        spec_location_groups = {}
        for location_venue, enrollments in location_groups.items():
            # Separate pending and active
            pending = [e for e in enrollments if e.request_status == EnrollmentStatus.PENDING]
            active = [e for e in enrollments if e.request_status != EnrollmentStatus.PENDING]

            spec_location_groups[location_venue] = {
                'pending': pending,
                'active': active,
                'total_count': len(enrollments),
                'location_venue': location_venue
            }

        specialization_groups[spec_type.value] = spec_location_groups

    # 💳 NEW: Get all UserLicenses that DON'T have any SemesterEnrollment yet
    # These are "newcomers" who selected specializations but haven't enrolled yet
    # Admin needs to see these to verify payment BEFORE student can request enrollment
    enrollment_license_ids = [e.user_license_id for e in all_enrollments]

    newcomer_licenses = (
        db.query(UserLicense)
        .options(joinedload(UserLicense.user))
        .filter(
            UserLicense.id.notin_(enrollment_license_ids) if enrollment_license_ids else True,
            UserLicense.payment_reference_code.isnot(None)
        )
        .order_by(UserLicense.started_at.desc())
        .all()
    )

    # Group newcomer licenses by specialization
    newcomer_groups = {}
    for spec_type in SpecializationType:
        newcomer_groups[spec_type.value] = [
            lic for lic in newcomer_licenses
            if lic.specialization_type == spec_type.value
        ]

    logger.info("admin_enrollments_loaded", extra={"enrollments": len(all_enrollments), "newcomers": len(newcomer_licenses)})

    return templates.TemplateResponse(
        "admin/enrollments.html",
        {
            "request": request,
            "user": user,
            "semesters": semesters,
            "students": students,
            "active_semesters": active_semesters,  # CHANGED: Multiple active semesters
            "specialization_groups": specialization_groups,  # NEW: Grouped by spec
            "newcomer_groups": newcomer_groups,  # NEW: Licenses awaiting first payment verification
            "SpecializationType": SpecializationType  # For template iteration
        }
    )


def _build_financial_kpi(db) -> dict:
    """Build the 8-metric financial KPI dict used by both payments and analytics pages."""
    paid_statuses = [InvoiceRequestStatus.PAID.value, InvoiceRequestStatus.VERIFIED.value]
    total_eur = db.query(sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.amount_eur), 0)).filter(
        InvoiceRequest.status.in_(paid_statuses)).scalar() or 0
    pending_eur = db.query(sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.amount_eur), 0)).filter(
        InvoiceRequest.status == InvoiceRequestStatus.PENDING.value).scalar() or 0
    issued_credits = db.query(sqlfunc.coalesce(sqlfunc.sum(InvoiceRequest.credit_amount), 0)).filter(
        InvoiceRequest.status.in_(paid_statuses)).scalar() or 0
    active_balance = db.query(sqlfunc.coalesce(sqlfunc.sum(User.credit_balance), 0)).scalar() or 0
    total_invoices = db.query(InvoiceRequest).count()
    open_invoices = db.query(InvoiceRequest).filter(
        InvoiceRequest.status == InvoiceRequestStatus.PENDING.value).count()
    verified_invoices = db.query(InvoiceRequest).filter(
        InvoiceRequest.status == InvoiceRequestStatus.VERIFIED.value).count()
    users_with_credits = db.query(User).filter(User.credit_balance > 0).count()
    return {
        "total_eur": round(float(total_eur), 2),
        "pending_eur": round(float(pending_eur), 2),
        "issued_credits": int(issued_credits),
        "active_balance": int(active_balance),
        "total_invoices": total_invoices,
        "open_invoices": open_invoices,
        "verified_invoices": verified_invoices,
        "users_with_credits": users_with_credits,
    }


@router.get("/admin/payments", response_class=HTMLResponse)
async def admin_payments_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Payment Management page (invoice requests + license payment verification)"""
    _admin_guard(user)

    # 8-metric financial KPI
    fin_kpi = _build_financial_kpi(db)

    # Get all invoice requests (ordered by most recent first)
    invoice_requests = (
        db.query(InvoiceRequest)
        .options(joinedload(InvoiceRequest.user))
        .order_by(InvoiceRequest.created_at.desc())
        .all()
    )

    # Get all UserLicenses that DON'T have any SemesterEnrollment yet (subquery avoids full table scan)
    enrolled_license_ids_subq = db.query(SemesterEnrollment.user_license_id)

    newcomer_licenses = (
        db.query(UserLicense)
        .options(joinedload(UserLicense.user))
        .filter(
            UserLicense.id.notin_(enrolled_license_ids_subq),
            UserLicense.payment_reference_code.isnot(None)
        )
        .order_by(UserLicense.started_at.desc())
        .all()
    )

    newcomer_groups = {}
    for spec_type in SpecializationType:
        newcomer_groups[spec_type.value] = [
            lic for lic in newcomer_licenses
            if lic.specialization_type == spec_type.value
        ]

    return templates.TemplateResponse(
        "admin/payments.html",
        {
            "request": request,
            "user": user,
            "invoice_requests": invoice_requests,
            "newcomer_groups": newcomer_groups,
            "SpecializationType": SpecializationType,
            "fin_kpi": fin_kpi,
        }
    )


@router.get("/admin/coupons", response_class=HTMLResponse)
async def admin_coupons_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Coupon Management page"""
    _admin_guard(user)

    # Get all coupons (ordered by creation date)
    coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).all()

    # Add validity status
    for coupon in coupons:
        coupon.is_currently_valid = coupon.is_valid()

    logger.info("admin_coupons_loaded", extra={"count": len(coupons)})

    return templates.TemplateResponse(
        "admin/coupons.html",
        {
            "request": request,
            "user": user,
            "coupons": coupons,
            "today": datetime.now(timezone.utc)
        }
    )


@router.get("/admin/invitation-codes", response_class=HTMLResponse)
async def admin_invitation_codes_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Partner Invitation Codes Management page"""
    _admin_guard(user)

    # Get all invitation codes with creator/redeemer in one query
    codes = db.query(InvitationCode).order_by(InvitationCode.created_at.desc()).all()

    # Bulk-load user names in two queries (avoid N+1)
    used_ids = {c.used_by_user_id for c in codes if c.used_by_user_id}
    admin_ids = {c.created_by_admin_id for c in codes if c.created_by_admin_id}
    all_ids = used_ids | admin_ids
    if all_ids:
        users_map = {u.id: u.name for u in db.query(User.id, User.name).filter(User.id.in_(all_ids)).all()}
    else:
        users_map = {}

    for code in codes:
        code.used_by_name = users_map.get(code.used_by_user_id) if code.used_by_user_id else None
        code.created_by_name = users_map.get(code.created_by_admin_id) if code.created_by_admin_id else None

    logger.info("admin_invitation_codes_loaded", extra={"count": len(codes)})

    return templates.TemplateResponse(
        "admin/invitation_codes.html",
        {
            "request": request,
            "user": user,
            "codes": codes,
            "now": datetime.now(timezone.utc)
        }
    )


# ============================================================================
# ADMIN USER CRUD
# ============================================================================

@router.post("/admin/users/create")
async def admin_create_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    password: str = Form(...),
    credit_balance: int = Form(default=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin-only: Create a new user account directly from the admin panel."""
    _admin_guard(user)

    # Validate role
    try:
        new_role = UserRole(role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    new_email = email.lower().strip()
    if db.query(User).filter(User.email == new_email).first():
        return RedirectResponse(
            url=f"/admin/users?create_error=Email+{new_email}+is+already+in+use",
            status_code=303,
        )

    new_user = User(
        email=new_email,
        name=name.strip(),
        password_hash=get_password_hash(password),
        role=new_role,
        is_active=True,
        credit_balance=max(0, credit_balance),
        credit_purchased=max(0, credit_balance),
        onboarding_completed=False,
        payment_verified=(credit_balance > 0),
    )
    db.add(new_user)
    db.flush()

    if credit_balance > 0:
        db.add(CreditTransaction(
            user_id=new_user.id,
            transaction_type=TransactionType.ADMIN_ADJUSTMENT,
            amount=credit_balance,
            balance_after=credit_balance,
            description="Initial credit balance set by admin on account creation",
            created_by_admin_id=user.id,
        ))

    db.commit()
    logger.info(
        "admin_user_created",
        extra={"admin": user.email, "new_user": new_user.email, "role": str(new_role)},
    )
    return RedirectResponse(url=f"/admin/users/{new_user.id}/edit", status_code=303)


@router.post("/admin/users/{user_id}/toggle-status")
async def admin_toggle_user_status(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Toggle a user's is_active status"""
    _admin_guard(user)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Safety: admin cannot deactivate themselves
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot toggle your own account status")

    target.is_active = not target.is_active
    db.commit()
    logger.info("admin_user_toggled", extra={"admin": user.email, "target": target.email, "is_active": target.is_active})
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
async def admin_edit_user_page(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Edit user form"""
    _admin_guard(user)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Credit history (last 20 user-level transactions)
    credit_history = (
        db.query(CreditTransaction)
        .filter(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(20)
        .all()
    )

    # Licenses with progression history
    licenses = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == user_id)
        .order_by(UserLicense.is_active.desc(), UserLicense.id.asc())
        .all()
    )
    license_ids = [lic.id for lic in licenses]
    progressions = (
        db.query(LicenseProgression)
        .filter(LicenseProgression.user_license_id.in_(license_ids))
        .order_by(LicenseProgression.advanced_at.desc())
        .all()
    ) if license_ids else []
    progression_map = defaultdict(list)
    for p in progressions:
        progression_map[p.user_license_id].append(p)

    # Admin users map for credit history performer names
    performer_ids = {ct.performed_by_user_id for ct in credit_history if ct.performed_by_user_id}
    performers = db.query(User).filter(User.id.in_(performer_ids)).all() if performer_ids else []
    performer_map = {u.id: u for u in performers}

    # Set of specialization_type strings that already have an active license
    active_spec_types = {lic.specialization_type for lic in licenses if lic.is_active}

    # Expired licenses: is_active=True but expires_at in the past
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expired_license_ids = {
        lic.id
        for lic in licenses
        if lic.is_active and lic.expires_at is not None and lic.expires_at < now_naive
    }

    # ── FÁZIS 5: Skill Progression data (LFA_FOOTBALL_PLAYER only) ──────────────
    has_lfa_player = any(
        lic.specialization_type == "LFA_FOOTBALL_PLAYER"
        for lic in licenses if lic.is_active
    )
    xp_logs: list = []
    xp_total: int = 0
    skill_assessments: list = []
    skill_tier_milestones: list = []
    if has_lfa_player:
        xp_logs = (
            db.query(EventRewardLog)
            .filter(EventRewardLog.user_id == user_id)
            .order_by(EventRewardLog.created_at.desc())
            .limit(20)
            .all()
        )
        xp_total = db.query(sqlfunc.sum(EventRewardLog.xp_earned)).filter(
            EventRewardLog.user_id == user_id
        ).scalar() or 0
        if license_ids:
            skill_assessments = (
                db.query(FootballSkillAssessment)
                .filter(
                    FootballSkillAssessment.user_license_id.in_(license_ids),
                    FootballSkillAssessment.status != "ARCHIVED",
                )
                .order_by(FootballSkillAssessment.assessed_at.desc())
                .limit(20)
                .all()
            )
        skill_tier_milestones = (
            db.query(Notification)
            .filter(
                Notification.user_id == user_id,
                Notification.type == NotificationType.SKILL_TIER_REACHED,
            )
            .order_by(Notification.created_at.desc())
            .all()
        )

    return templates.TemplateResponse(
        "admin/user_edit.html",
        {
            "request": request,
            "user": user,
            "target": target,
            "UserRole": UserRole,
            "SpecializationType": SpecializationType,
            "credit_history": credit_history,
            "licenses": licenses,
            "progression_map": progression_map,
            "performer_map": performer_map,
            "active_spec_types": active_spec_types,
            "expired_license_ids": expired_license_ids,
            "today_iso": date.today().isoformat(),
            "msg": request.query_params.get("msg", ""),
            "error": request.query_params.get("error", ""),
            "error_detail": request.query_params.get("error_detail", ""),
            "has_lfa_player": has_lfa_player,
            "xp_logs": xp_logs,
            "xp_total": xp_total,
            "skill_assessments": skill_assessments,
            "skill_tier_milestones": skill_tier_milestones,
        }
    )


@router.post("/admin/users/{user_id}/edit")
async def admin_edit_user_submit(
    user_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Save user edits"""
    _admin_guard(user)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate role
    try:
        new_role = UserRole(role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")

    # Check email uniqueness (if changed)
    new_email = email.lower().strip()
    if new_email != target.email:
        existing = db.query(User).filter(User.email == new_email).first()
        if existing:
            logger.warning("admin_user_edit_duplicate_email", extra={"admin": user.email, "duplicate_email": new_email})
            return templates.TemplateResponse(
                "admin/user_edit.html",
                {
                    "request": request, "user": user, "target": target,
                    "UserRole": UserRole,
                    "error": f"Email {new_email} is already in use."
                }
            )

    target.name = name.strip()
    target.email = new_email
    target.role = new_role
    db.commit()
    logger.info("admin_user_edited", extra={"admin": user.email, "target": target.email, "role": str(target.role)})
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/reset-password")
async def admin_reset_user_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: reset a user's password."""
    _admin_guard(user)
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.password_hash = get_password_hash(new_password)
    db.commit()
    logger.info("admin_password_reset", extra={"admin": user.email, "target": target.email})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit?msg=password_reset", status_code=303)


@router.post("/admin/users/{user_id}/grant-credit")
async def admin_grant_credit(
    user_id: int,
    request: Request,
    amount: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: grant credits to a user (creates CreditTransaction audit record)."""
    _admin_guard(user)
    if amount < 1 or amount > 50000:
        raise HTTPException(status_code=400, detail="Amount must be between 1 and 50000")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.credit_balance = (target.credit_balance or 0) + amount
    target.credit_purchased = (target.credit_purchased or 0) + amount
    ct = CreditTransaction(
        user_id=target.id,
        transaction_type=TransactionType.ADMIN_ADJUSTMENT.value,
        amount=amount,
        balance_after=target.credit_balance,
        description=reason[:500],
        idempotency_key=f"admin-grant-{target.id}-{_uuid.uuid4()}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    logger.info("admin_credit_granted", extra={"admin": user.email, "target": target.email, "amount": amount})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#credits", status_code=303)


@router.post("/admin/users/{user_id}/deduct-credit")
async def admin_deduct_credit(
    user_id: int,
    request: Request,
    amount: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: deduct credits from a user (creates CreditTransaction audit record)."""
    _admin_guard(user)
    if amount < 1 or amount > 50000:
        raise HTTPException(status_code=400, detail="Amount must be between 1 and 50000")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if amount > (target.credit_balance or 0):
        raise HTTPException(status_code=400, detail="Cannot deduct more than current balance")
    target.credit_balance = (target.credit_balance or 0) - amount
    ct = CreditTransaction(
        user_id=target.id,
        transaction_type=TransactionType.ADMIN_ADJUSTMENT.value,
        amount=-amount,
        balance_after=target.credit_balance,
        description=reason[:500],
        idempotency_key=f"admin-deduct-{target.id}-{_uuid.uuid4()}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    logger.info("admin_credit_deducted", extra={"admin": user.email, "target": target.email, "amount": amount})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#credits", status_code=303)


@router.post("/admin/users/{user_id}/grant-license")
async def admin_grant_license(
    user_id: int,
    request: Request,
    specialization_type: str = Form(...),
    reason: str = Form(...),
    expires_at: str = Form(default=""),  # optional "YYYY-MM-DD"; empty = perpetual
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: grant a new license to a user (creates LicenseProgression audit record)."""
    _admin_guard(user)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate specialization type
    try:
        spec = SpecializationType(specialization_type)
    except ValueError:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=invalid_spec&error_detail={specialization_type}#licenses",
            status_code=303,
        )

    # Check for existing active license — redirect with user-friendly error instead of
    # returning JSON 400 (which breaks the admin_base.html CSRF JS handler)
    existing = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == user_id, UserLicense.specialization_type == spec.value, UserLicense.is_active == True)
        .first()
    )
    if existing:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=duplicate_license&error_detail={spec.value}#licenses",
            status_code=303,
        )

    # Parse optional expiry date (blank = perpetual license)
    expires_at_dt = None
    if expires_at and expires_at.strip():
        try:
            expires_at_dt = datetime.strptime(expires_at.strip(), "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid expires_at format (expected YYYY-MM-DD)")
        if expires_at_dt <= datetime.now(timezone.utc).replace(tzinfo=None):
            raise HTTPException(status_code=400, detail="expires_at must be in the future")

    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    new_license = UserLicense(
        user_id=user_id,
        specialization_type=spec.value,
        started_at=now_naive,
        issued_at=now_naive,
        is_active=True,
        expires_at=expires_at_dt,
    )
    db.add(new_license)
    db.flush()  # get new_license.id

    progression = LicenseProgression(
        user_license_id=new_license.id,
        from_level=0,
        to_level=0,
        advanced_by=user.id,
        advancement_reason=reason[:500],
        requirements_met="INITIAL_GRANT",
        advanced_at=now,
    )
    db.add(progression)
    db.commit()
    logger.info("admin_license_granted", extra={"admin": user.email, "target": target.email, "spec": spec.value})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#licenses", status_code=303)


@router.post("/admin/users/{user_id}/revoke-license/{license_id}")
async def admin_revoke_license(
    user_id: int,
    license_id: int,
    request: Request,
    reason: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: revoke a user's license (creates LicenseProgression audit record)."""
    _admin_guard(user)
    license = (
        db.query(UserLicense)
        .filter(UserLicense.id == license_id, UserLicense.user_id == user_id)
        .first()
    )
    if not license:
        raise HTTPException(status_code=404, detail="License not found")

    if not license.is_active:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=already_revoked&error_detail={license_id}#licenses",
            status_code=303,
        )

    now = datetime.now(timezone.utc)
    license.is_active = False
    progression = LicenseProgression(
        user_license_id=license.id,
        from_level=license.current_level or 0,
        to_level=-1,
        advanced_by=user.id,
        advancement_reason=reason[:500],
        requirements_met="REVOKED",
        advanced_at=now,
    )
    db.add(progression)
    db.commit()
    logger.info("admin_license_revoked", extra={"admin": user.email, "license_id": license_id})
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#licenses", status_code=303)


@router.post("/admin/users/{user_id}/renew-license/{license_id}")
async def admin_renew_license(
    user_id: int,
    license_id: int,
    request: Request,
    new_expires_at: str = Form(...),  # "YYYY-MM-DD" required
    reason: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: renew a license — set new expires_at + record LicenseProgression('RENEWED')."""
    _admin_guard(user)
    license = (
        db.query(UserLicense)
        .filter(UserLicense.id == license_id, UserLicense.user_id == user_id)
        .first()
    )
    if not license:
        raise HTTPException(status_code=404, detail="License not found")

    if not license.is_active:
        return RedirectResponse(
            url=f"/admin/users/{user_id}/edit?error=cannot_renew_revoked&error_detail={license_id}#licenses",
            status_code=303,
        )

    try:
        new_expires_dt = datetime.strptime(new_expires_at.strip(), "%Y-%m-%d").replace(
            hour=23, minute=59, second=59
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format (expected YYYY-MM-DD)")

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if new_expires_dt <= now_naive:
        raise HTTPException(status_code=400, detail="New expiry date must be in the future")

    now = datetime.now(timezone.utc)
    license.expires_at = new_expires_dt
    license.last_renewed_at = now_naive

    progression = LicenseProgression(
        user_license_id=license.id,
        from_level=license.current_level or 0,
        to_level=license.current_level or 0,
        advanced_by=user.id,
        advancement_reason=(reason[:500] if reason else "License renewed by admin"),
        requirements_met="RENEWED",
        advanced_at=now,
    )
    db.add(progression)
    db.commit()
    logger.info(
        "admin_license_renewed",
        extra={"admin": user.email, "license_id": license_id, "new_expires": new_expires_at}
    )
    return RedirectResponse(url=f"/admin/users/{user_id}/edit#licenses", status_code=303)


# ============================================================================
# ADMIN SEMESTER CRUD
# ============================================================================

@router.get("/admin/semesters/new", response_class=HTMLResponse)
async def admin_new_semester_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Create semester form"""
    _admin_guard(user)

    instructors = db.query(User).filter(User.role == UserRole.INSTRUCTOR, User.is_active == True).all()
    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    return templates.TemplateResponse(
        "admin/semester_new.html",
        {
            "request": request, "user": user,
            "instructors": instructors,
            "locations": locations,
            "today": date.today().isoformat()
        }
    )


@router.post("/admin/semesters/new")
async def admin_new_semester_submit(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    enrollment_cost: int = Form(500),
    specialization_type: str = Form(""),
    master_instructor_id: str = Form(""),
    location_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Create new semester"""
    _admin_guard(user)

    instructors = db.query(User).filter(User.role == UserRole.INSTRUCTOR, User.is_active == True).all()
    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()

    def form_error(msg: str):
        return templates.TemplateResponse(
            "admin/semester_new.html",
            {
                "request": request, "user": user,
                "error": msg, "instructors": instructors,
                "locations": locations,
                "today": date.today().isoformat(),
                "form": {
                    "code": code, "name": name, "start_date": start_date,
                    "end_date": end_date, "enrollment_cost": enrollment_cost,
                    "specialization_type": specialization_type,
                    "location_id": location_id,
                }
            }
        )

    # Validate dates
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return form_error("Invalid date format.")

    if ed <= sd:
        return form_error("End date must be after start date.")

    # Validate CENTER vs PARTNER rule
    _ACADEMY_TYPES = {
        SpecializationType.LFA_PLAYER_PRE_ACADEMY,
        SpecializationType.LFA_PLAYER_YOUTH_ACADEMY,
        SpecializationType.LFA_PLAYER_AMATEUR_ACADEMY,
        SpecializationType.LFA_PLAYER_PRO_ACADEMY,
    }
    parsed_location_id = int(location_id) if location_id.strip() else None
    spec_str = specialization_type.strip()
    try:
        spec_enum = SpecializationType(spec_str) if spec_str else None
    except ValueError:
        spec_enum = None

    # Academy Season requires a location (so the CENTER rule can be evaluated)
    if spec_enum in _ACADEMY_TYPES and not parsed_location_id:
        return form_error(
            "Academy Season típus létrehozásához kötelező helyszínt kiválasztani, "
            "hogy a CENTER / PARTNER szabályt ellenőrizni lehessen."
        )

    # If location is selected, check the CENTER / PARTNER capability rule
    if parsed_location_id and spec_enum:
        result = LocationValidationService.can_create_semester_at_location(
            parsed_location_id, spec_enum, db
        )
        if not result["allowed"]:
            return form_error(result["reason"])

    # Check code uniqueness
    existing = db.query(Semester).filter(Semester.code == code.strip()).first()
    if existing:
        return form_error(f"Semester code '{code}' already exists.")

    instructor_id = int(master_instructor_id) if master_instructor_id.strip() else None

    new_sem = Semester(
        code=code.strip(),
        name=name.strip(),
        start_date=sd,
        end_date=ed,
        enrollment_cost=enrollment_cost,
        specialization_type=specialization_type.strip() or None,
        master_instructor_id=instructor_id,
    )
    db.add(new_sem)
    db.commit()
    logger.info("admin_semester_created", extra={"admin": user.email, "code": new_sem.code})
    return RedirectResponse(url="/admin/semesters", status_code=303)


@router.post("/admin/semesters/{semester_id}/delete")
async def admin_delete_semester(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Cancel a semester (status=CANCELLED) or hard-delete if no enrollments."""
    _admin_guard(user)

    sem = db.query(Semester).filter(Semester.id == semester_id).first()
    if not sem:
        raise HTTPException(status_code=404, detail="Semester not found")

    # Check if semester has active enrollments
    active_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == semester_id,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
    ).count()

    if active_count > 0:
        # Don't delete — cancel the semester
        sem.status = SemesterStatus.CANCELLED
        db.commit()
        logger.info("admin_semester_cancelled", extra={"admin": user.email, "code": sem.code, "active_enrollments": active_count})
    else:
        db.delete(sem)
        db.commit()
        logger.info("admin_semester_deleted", extra={"admin": user.email, "code": sem.code})

    return RedirectResponse(url="/admin/semesters", status_code=303)


@router.get("/admin/semesters/{semester_id}/edit", response_class=HTMLResponse)
async def admin_semester_edit_dispatch(
    semester_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Dispatch: redirect to the appropriate edit page based on semester category."""
    _admin_guard(user)
    sem = db.query(Semester).filter(Semester.id == semester_id).first()
    if not sem:
        raise HTTPException(status_code=404, detail="Semester not found")
    is_tournament = (
        sem.semester_category == SemesterCategory.TOURNAMENT
        or (sem.code or "").startswith("TOURN-")
        or (sem.code or "").startswith("OPS-")
    )
    if is_tournament:
        return RedirectResponse(f"/admin/tournaments/{semester_id}/edit", status_code=303)
    if sem.semester_category == SemesterCategory.CAMP:
        return RedirectResponse(f"/admin/camps/{semester_id}/edit", status_code=303)
    return RedirectResponse(f"/admin/semesters", status_code=303)


@router.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics_page(
    request: Request,
    location_id: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Analytics and reports page"""
    _admin_guard(user)

    # Platform stats
    total_users = db.query(User).count()
    total_students = db.query(User).filter(User.role == UserRole.STUDENT).count()
    total_instructors = db.query(User).filter(User.role == UserRole.INSTRUCTOR).count()
    total_sessions = db.query(SessionModel).count()
    total_bookings = db.query(Booking).count()

    stats = {
        "total_users": total_users,
        "total_students": total_students,
        "total_instructors": total_instructors,
        "total_sessions": total_sessions,
        "total_bookings": total_bookings,
    }

    # Financial snapshot (all 8 metrics)
    fin = _build_financial_kpi(db)

    # Locations for filter selector
    all_locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    selected_location = db.query(Location).filter(Location.id == location_id).first() if location_id else None

    # Semesters grouped by specialization — eager-load location + campus for table columns
    all_semesters = (
        db.query(Semester)
        .filter(Semester.status != SemesterStatus.CANCELLED)
        .options(joinedload(Semester.location), joinedload(Semester.campus))
        .order_by(Semester.start_date.desc())
        .all()
    )
    spec_semesters = defaultdict(list)
    for sem in all_semesters:
        spec = sem.specialization_type if sem.specialization_type else "Unknown"
        spec_semesters[spec].append(sem)

    # Campuses + session counts for selected location
    location_campuses = []
    if selected_location:
        campuses = db.query(Campus).filter(
            Campus.location_id == selected_location.id,
            Campus.is_active == True
        ).all()
        now = datetime.now(timezone.utc)
        # Build campus session counts in two queries (no per-campus loop)
        campus_names = [c.name for c in campuses]
        if campus_names:
            from sqlalchemy import or_
            all_sessions = db.query(SessionModel.location, SessionModel.date_start).filter(
                or_(*[SessionModel.location.ilike(f"%{n}%") for n in campus_names])
            ).all()
            for campus in campuses:
                matching = [s for s in all_sessions if campus.name.lower() in (s.location or "").lower()]
                upcoming = sum(
                    1 for s in matching
                    if s.date_start and s.date_start.replace(
                        tzinfo=timezone.utc if s.date_start.tzinfo is None else s.date_start.tzinfo
                    ) > now
                )
                location_campuses.append({
                    "campus": campus,
                    "total": len(matching),
                    "upcoming": upcoming,
                    "past": len(matching) - upcoming,
                })

    # ── FÁZIS 5: Skill tier distribution ─────────────────────────────────────────
    all_active_assessments = (
        db.query(FootballSkillAssessment)
        .filter(FootballSkillAssessment.status != "ARCHIVED")
        .all()
    )
    from collections import defaultdict as _dd
    _by_skill: dict = _dd(list)
    for _a in all_active_assessments:
        _by_skill[_a.skill_name].append(_a.percentage)
    skill_dist = [
        {
            "skill": sn,
            "beginner": sum(1 for p in pcts if p < 60),
            "intermediate": sum(1 for p in pcts if 60 <= p < 75),
            "advanced": sum(1 for p in pcts if 75 <= p < 90),
            "expert": sum(1 for p in pcts if p >= 90),
        }
        for sn, pcts in sorted(_by_skill.items())
    ]
    tier_milestone_count = db.query(Notification).filter(
        Notification.type == NotificationType.SKILL_TIER_REACHED
    ).count()

    return templates.TemplateResponse(
        "admin/analytics.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "fin": fin,
            "all_locations": all_locations,
            "selected_location": selected_location,
            "selected_location_id": location_id,
            "spec_semesters": dict(spec_semesters),
            "location_campuses": location_campuses,
            "skill_dist": skill_dist,
            "tier_milestone_count": tier_milestone_count,
        }
    )


# ============================================================================
# MOTIVATION ASSESSMENT ROUTES - Admin/Instructor evaluate student motivation
# ============================================================================

@router.get("/admin/students/{student_id}/motivation/{specialization}", response_class=HTMLResponse)
async def motivation_assessment_page(
    request: Request,
    student_id: int,
    specialization: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin/Instructor-only: Motivation assessment page for a student's specialization"""
    if user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(status_code=403, detail="Admin or Instructor access required")

    # Get student
    student = db.query(User).filter(User.id == student_id, User.role == UserRole.STUDENT).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Get student's license for this specialization
    license = db.query(UserLicense).filter(
        UserLicense.user_id == student_id,
        UserLicense.specialization_type == specialization
    ).first()

    if not license:
        raise HTTPException(status_code=404, detail=f"Student does not have {specialization} license")

    # Format specialization name for display
    specialization_display = specialization.replace('_', ' ').title()

    # Check if there are existing scores
    existing_scores = license.motivation_scores is not None

    return templates.TemplateResponse(
        "admin/motivation_assessment.html",
        {
            "request": request,
            "user": user,
            "student": student,
            "license": license,
            "specialization": specialization,
            "specialization_display": specialization_display,
            "existing_scores": existing_scores
        }
    )


@router.post("/admin/students/{student_id}/motivation/{specialization}")
async def motivation_assessment_submit(
    request: Request,
    student_id: int,
    specialization: str,
    goal_clarity: int = Form(...),
    commitment_level: int = Form(...),
    engagement: int = Form(...),
    progress_mindset: int = Form(...),
    initiative: int = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin/Instructor-only: Save motivation assessment"""
    if user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(status_code=403, detail="Admin or Instructor access required")

    # Validate scores (1-5)
    scores = [goal_clarity, commitment_level, engagement, progress_mindset, initiative]
    for score in scores:
        if score < 1 or score > 5:
            raise HTTPException(status_code=400, detail="Scores must be between 1 and 5")

    # Get student's license
    license = db.query(UserLicense).filter(
        UserLicense.user_id == student_id,
        UserLicense.specialization_type == specialization
    ).first()

    if not license:
        raise HTTPException(status_code=404, detail=f"Student does not have {specialization} license")

    # Create motivation scores JSON
    motivation_data = {
        "goal_clarity": goal_clarity,
        "commitment_level": commitment_level,
        "engagement": engagement,
        "progress_mindset": progress_mindset,
        "initiative": initiative,
        "notes": notes,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "assessed_by_id": user.id,
        "assessed_by_name": user.name
    }

    # Calculate average
    average_score = sum(scores) / len(scores)

    # Update license
    license.motivation_scores = motivation_data
    license.average_motivation_score = average_score
    license.motivation_last_assessed_at = datetime.now(timezone.utc)
    license.motivation_assessed_by = user.id

    db.commit()

    logger.info("admin_motivation_assessed", extra={"assessor": user.email, "student_id": student_id, "spec": specialization, "avg_score": round(average_score, 1)})

    # Redirect back to user management page
    return RedirectResponse(url="/admin/users", status_code=303)


# ============================================================================
# 🎓 SEMESTER ENROLLMENT REQUEST WORKFLOW (Student-initiated, Admin-approved)
# ============================================================================


# ============================================================================
# ADMIN LOCATIONS + CAMPUSES
# ============================================================================

def _admin_guard(user: User):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/admin/programs", response_class=HTMLResponse)
async def admin_programs_hub_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Programs operational hub (Semesters + Enrollments)"""
    _admin_guard(user)

    active_semesters_count = db.query(Semester).filter(
        Semester.status == SemesterStatus.ONGOING
    ).count()
    pending_enrollments_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.request_status == EnrollmentStatus.PENDING
    ).count()
    today = date.today()
    upcoming_semesters = (
        db.query(Semester)
        .filter(Semester.start_date >= today, Semester.status != SemesterStatus.CANCELLED)
        .options(joinedload(Semester.location))
        .order_by(Semester.start_date.asc())
        .limit(5)
        .all()
    )
    total_semesters = db.query(Semester).filter(Semester.status != SemesterStatus.CANCELLED).count()
    total_enrollments = db.query(SemesterEnrollment).count()

    return templates.TemplateResponse(
        "admin/programs_hub.html",
        {
            "request": request,
            "user": user,
            "active_semesters_count": active_semesters_count,
            "pending_enrollments_count": pending_enrollments_count,
            "upcoming_semesters": upcoming_semesters,
            "total_semesters": total_semesters,
            "total_enrollments": total_enrollments,
        }
    )


@router.get("/admin/config", response_class=HTMLResponse)
async def admin_config_hub_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Game Config hub (Game Presets only; Locations moved to top-level nav)"""
    _admin_guard(user)

    game_presets_count = db.query(GamePreset).count()

    return templates.TemplateResponse(
        "admin/config_hub.html",
        {
            "request": request,
            "user": user,
            "game_presets_count": game_presets_count,
        }
    )


@router.get("/admin/events", response_class=HTMLResponse)
async def admin_events_hub_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Event Management hub — tournaments, camps, training sessions, match sessions."""
    _admin_guard(user)

    today = date.today()
    two_weeks = today + timedelta(days=14)

    tournament_count = db.query(sqlfunc.count(Semester.id)).filter(
        or_(
            Semester.code.like("TOURN-%"),
            Semester.code.like("OPS-%"),
            Semester.semester_category == SemesterCategory.TOURNAMENT,
        ),
        Semester.status != SemesterStatus.CANCELLED,
    ).scalar() or 0

    camp_count = db.query(sqlfunc.count(Semester.id)).filter(
        Semester.semester_category == SemesterCategory.CAMP,
        Semester.status != SemesterStatus.CANCELLED,
    ).scalar() or 0

    training_count = db.query(sqlfunc.count(SessionModel.id)).filter(
        SessionModel.event_category == EventCategory.TRAINING,
        sqlfunc.date(SessionModel.date_start) >= today,
        sqlfunc.date(SessionModel.date_start) <= two_weeks,
        SessionModel.session_status != "cancelled",
    ).scalar() or 0

    match_count = db.query(sqlfunc.count(SessionModel.id)).filter(
        SessionModel.event_category == EventCategory.MATCH,
        sqlfunc.date(SessionModel.date_start) >= today,
        sqlfunc.date(SessionModel.date_start) <= two_weeks,
        SessionModel.session_status != "cancelled",
    ).scalar() or 0

    upcoming_events = db.query(Semester).filter(
        or_(
            Semester.semester_category.in_([SemesterCategory.TOURNAMENT, SemesterCategory.CAMP]),
            Semester.code.like("TOURN-%"),
            Semester.code.like("OPS-%"),
        ),
        Semester.start_date >= today,
        Semester.status != SemesterStatus.CANCELLED,
    ).order_by(Semester.start_date).limit(5).all()

    # Batch-load locations and campuses for upcoming events table
    ev_loc_ids = list({e.location_id for e in upcoming_events if e.location_id})
    ev_cam_ids = list({e.campus_id for e in upcoming_events if e.campus_id})
    ev_loc_map = {l.id: l for l in db.query(Location).filter(Location.id.in_(ev_loc_ids)).all()} if ev_loc_ids else {}
    ev_cam_map = {c.id: c for c in db.query(Campus).filter(Campus.id.in_(ev_cam_ids)).all()} if ev_cam_ids else {}

    # Location cards — aggregate semester + session counts per location
    all_locations = (
        db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    )
    all_loc_ids = [l.id for l in all_locations]
    sem_by_loc = (
        dict(
            db.query(Semester.location_id, sqlfunc.count(Semester.id))
            .filter(
                Semester.location_id.in_(all_loc_ids),
                Semester.status != SemesterStatus.CANCELLED,
            )
            .group_by(Semester.location_id)
            .all()
        )
        if all_loc_ids
        else {}
    )
    # Session has campus_id (no location_id) — count via Campus join
    if all_loc_ids:
        all_campuses = (
            db.query(Campus).filter(Campus.location_id.in_(all_loc_ids)).all()
        )
        campus_to_loc = {c.id: c.location_id for c in all_campuses}
        all_campus_ids = [c.id for c in all_campuses]
        sess_by_campus = (
            dict(
                db.query(SessionModel.campus_id, sqlfunc.count(SessionModel.id))
                .filter(
                    SessionModel.campus_id.in_(all_campus_ids),
                    SessionModel.session_status != "cancelled",
                )
                .group_by(SessionModel.campus_id)
                .all()
            )
            if all_campus_ids
            else {}
        )
        sess_by_loc: dict = {}
        for cam_id, cnt in sess_by_campus.items():
            loc_id = campus_to_loc.get(cam_id)
            if loc_id:
                sess_by_loc[loc_id] = sess_by_loc.get(loc_id, 0) + cnt
    else:
        sess_by_loc = {}

    return templates.TemplateResponse(
        "admin/events_hub.html",
        {
            "request": request,
            "user": user,
            "tournament_count": tournament_count,
            "camp_count": camp_count,
            "training_count": training_count,
            "match_count": match_count,
            "upcoming_events": upcoming_events,
            "ev_loc_map": ev_loc_map,
            "ev_cam_map": ev_cam_map,
            "SemesterCategory": SemesterCategory,
            "all_locations": all_locations,
            "sem_by_loc": sem_by_loc,
            "sess_by_loc": sess_by_loc,
        }
    )


@router.get("/admin/camps", response_class=HTMLResponse)
async def admin_camps_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    status_filter: str = "active",
    age_group_filter: str = "",
    location_filter: str = "",
    name_search: str = "",
):
    """Admin-only: Camp management — list of CAMP-category semesters."""
    _admin_guard(user)

    query = db.query(Semester).filter(Semester.semester_category == SemesterCategory.CAMP)
    if status_filter == "active":
        query = query.filter(Semester.status != SemesterStatus.CANCELLED)
    elif status_filter == "cancelled":
        query = query.filter(Semester.status == SemesterStatus.CANCELLED)
    if age_group_filter:
        query = query.filter(Semester.age_group == age_group_filter)
    if name_search:
        query = query.filter(Semester.name.ilike(f"%{name_search}%"))
    camps = query.order_by(Semester.start_date.desc()).all()

    loc_ids = list({c.location_id for c in camps if c.location_id})
    campus_ids_set = list({c.campus_id for c in camps if c.campus_id})
    location_map = {l.id: l for l in db.query(Location).filter(Location.id.in_(loc_ids)).all()} if loc_ids else {}
    campus_map = {c.id: c for c in db.query(Campus).filter(Campus.id.in_(campus_ids_set)).all()} if campus_ids_set else {}

    all_locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()

    total = len(camps)
    ongoing = sum(1 for c in camps if c.status == SemesterStatus.ONGOING)
    upcoming = sum(1 for c in camps if c.start_date and c.start_date >= date.today() and c.status not in [SemesterStatus.CANCELLED, SemesterStatus.COMPLETED])
    completed = sum(1 for c in camps if c.status == SemesterStatus.COMPLETED)

    return templates.TemplateResponse(
        "admin/camps.html",
        {
            "request": request,
            "user": user,
            "camps": camps,
            "location_map": location_map,
            "campus_map": campus_map,
            "all_locations": all_locations,
            "status_filter": status_filter,
            "age_group_filter": age_group_filter,
            "name_search": name_search,
            "total": total,
            "ongoing": ongoing,
            "upcoming": upcoming,
            "completed": completed,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
            "SemesterStatus": SemesterStatus,
        }
    )


@router.post("/admin/camps", response_class=HTMLResponse)
async def admin_create_camp(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    name: str = Form(...),
    code: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    age_group: str = Form(""),
    location_id: str = Form(""),
    campus_id: str = Form(""),
    enrollment_cost: str = Form("0"),
):
    """Admin-only: Create a new Camp semester."""
    _admin_guard(user)

    camp_code = code.strip() if code.strip() else f"CAMP-{_uuid.uuid4().hex[:6].upper()}"
    if not camp_code.startswith("CAMP-"):
        camp_code = f"CAMP-{camp_code}"

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return RedirectResponse(f"/admin/camps?error=Invalid+date+format", status_code=303)

    semester = Semester(
        name=name.strip(),
        code=camp_code,
        start_date=start,
        end_date=end,
        semester_category=SemesterCategory.CAMP,
        status=SemesterStatus.DRAFT,
        age_group=age_group.strip() or None,
        location_id=int(location_id) if location_id.strip() else None,
        campus_id=int(campus_id) if campus_id.strip() else None,
        enrollment_cost=int(enrollment_cost) if enrollment_cost.strip().isdigit() else 0,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    db.add(semester)
    db.commit()
    if semester.location_id:
        return RedirectResponse(
            f"/admin/events/locations/{semester.location_id}?flash=Camp+created",
            status_code=303,
        )
    return RedirectResponse(f"/admin/camps?flash=Camp+created", status_code=303)


@router.get("/admin/camps/{camp_id}/edit", response_class=HTMLResponse)
async def admin_camp_edit_page(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin-only: Edit a Camp semester."""
    _admin_guard(user)
    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
    ).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Camp not found")

    all_locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    campuses_for_loc = (
        db.query(Campus).filter(Campus.location_id == camp.location_id).all()
        if camp.location_id else []
    )
    return templates.TemplateResponse(
        "admin/camp_edit.html",
        {
            "request": request,
            "user": user,
            "camp": camp,
            "all_locations": all_locations,
            "campuses_for_loc": campuses_for_loc,
            "SemesterStatus": SemesterStatus,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/admin/camps/{camp_id}/edit", response_class=HTMLResponse)
async def admin_update_camp(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    name: str = Form(...),
    code: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    age_group: str = Form(""),
    location_id: str = Form(""),
    campus_id: str = Form(""),
    enrollment_cost: str = Form(""),
    status: str = Form(""),
):
    """Admin-only: Update a Camp semester."""
    _admin_guard(user)
    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
    ).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Camp not found")

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return RedirectResponse(
            f"/admin/camps/{camp_id}/edit?error=Invalid+date+format", status_code=303
        )

    camp.name = name.strip()
    if code.strip():
        camp.code = code.strip()
    camp.start_date = start
    camp.end_date = end
    camp.age_group = age_group.strip() or None
    camp.location_id = int(location_id) if location_id.strip() else None
    camp.campus_id = int(campus_id) if campus_id.strip() else None
    if enrollment_cost.strip().isdigit():
        camp.enrollment_cost = int(enrollment_cost)
    if status.strip():
        try:
            camp.status = SemesterStatus(status)
        except ValueError:
            pass
    db.commit()

    loc_id = camp.location_id
    if loc_id:
        return RedirectResponse(
            f"/admin/events/locations/{loc_id}?flash=Camp+updated", status_code=303
        )
    return RedirectResponse(f"/admin/camps/{camp_id}/edit?flash=Camp+updated", status_code=303)


@router.get("/admin/events/locations/{location_id}", response_class=HTMLResponse)
async def admin_location_events_page(
    location_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin-only: Per-location event management — all event types CRUD in one place."""
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    campuses = db.query(Campus).filter(Campus.location_id == location_id).order_by(Campus.name).all()
    campus_ids = [c.id for c in campuses]
    campus_map = {c.id: c for c in campuses}

    # Tournaments (TOURNAMENT category OR legacy TOURN-/OPS- codes) at this location
    tournaments = (
        db.query(Semester)
        .filter(
            or_(
                Semester.semester_category == SemesterCategory.TOURNAMENT,
                Semester.code.like("TOURN-%"),
                Semester.code.like("OPS-%"),
            ),
            Semester.location_id == location_id,
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.desc())
        .all()
    )

    # Camps at this location
    camps = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.location_id == location_id,
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.desc())
        .all()
    )

    # Academy / Mini Seasons at this location
    seasons = (
        db.query(Semester)
        .filter(
            Semester.semester_category.in_(
                [SemesterCategory.ACADEMY_SEASON, SemesterCategory.MINI_SEASON]
            ),
            Semester.location_id == location_id,
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.desc())
        .all()
    )

    # Upcoming sessions at this location's campuses (next 30 days)
    in_30d = date.today() + timedelta(days=30)
    sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.campus_id.in_(campus_ids),
            sqlfunc.date(SessionModel.date_start) >= date.today(),
            sqlfunc.date(SessionModel.date_start) <= in_30d,
            SessionModel.session_status != "cancelled",
        )
        .order_by(SessionModel.date_start)
        .limit(20)
        .all()
        if campus_ids
        else []
    )

    return templates.TemplateResponse(
        "admin/events_location.html",
        {
            "request": request,
            "user": user,
            "loc": loc,
            "campuses": campuses,
            "campus_map": campus_map,
            "tournaments": tournaments,
            "camps": camps,
            "seasons": seasons,
            "sessions": sessions,
            "SemesterStatus": SemesterStatus,
            "SemesterCategory": SemesterCategory,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/admin/locations", response_class=HTMLResponse)
async def admin_locations_page(
    request: Request,
    city_filter: str = "",
    status_filter: str = "active",
    name_search: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Locations & Campuses management with filters"""
    _admin_guard(user)

    q = db.query(Location)
    if status_filter == "active":
        q = q.filter(Location.is_active == True)
    elif status_filter == "inactive":
        q = q.filter(Location.is_active == False)
    if city_filter:
        q = q.filter(Location.city == city_filter)
    if name_search:
        q = q.filter(Location.name.ilike(f"%{name_search}%"))

    locations = q.order_by(Location.name).all()
    # Batch-load all campuses for filtered locations (avoid N+1)
    loc_ids = [loc.id for loc in locations]
    if loc_ids:
        all_campuses = db.query(Campus).filter(Campus.location_id.in_(loc_ids)).order_by(Campus.name).all()
        campus_by_loc = defaultdict(list)
        for c in all_campuses:
            campus_by_loc[c.location_id].append(c)
    else:
        campus_by_loc = {}
    for loc in locations:
        loc.campuses_list = campus_by_loc.get(loc.id, [])

    all_cities = sorted(set(
        loc.city for loc in db.query(Location).all() if loc.city
    ))

    # Batch: active semester counts per location
    semester_counts: dict = {}
    if loc_ids:
        for row in db.query(Semester.location_id, sqlfunc.count(Semester.id)).filter(
            Semester.location_id.in_(loc_ids),
            Semester.status.in_([SemesterStatus.READY_FOR_ENROLLMENT, SemesterStatus.ONGOING])
        ).group_by(Semester.location_id).all():
            semester_counts[row[0]] = row[1]

    # Batch: active instructor counts per location
    instructor_counts: dict = {}
    if loc_ids:
        for row in db.query(
            InstructorAssignment.location_id,
            sqlfunc.count(sqlfunc.distinct(InstructorAssignment.instructor_id))
        ).filter(
            InstructorAssignment.location_id.in_(loc_ids),
            InstructorAssignment.is_active == True  # noqa: E712
        ).group_by(InstructorAssignment.location_id).all():
            instructor_counts[row[0]] = row[1]

    # Group locations by country (sorted alphabetically)
    locations_by_country: dict = defaultdict(list)
    for loc in locations:
        locations_by_country[loc.country or "Unknown"].append(loc)
    locations_by_country = dict(sorted(locations_by_country.items()))

    return templates.TemplateResponse(
        "admin/locations.html",
        {
            "request": request,
            "user": user,
            "locations": locations,
            "locations_by_country": locations_by_country,
            "LocationType": LocationType,
            "all_cities": all_cities,
            "city_filter": city_filter,
            "status_filter": status_filter,
            "name_search": name_search,
            "semester_counts": semester_counts,
            "instructor_counts": instructor_counts,
        }
    )


@router.get("/admin/locations/{location_id}", response_class=HTMLResponse)
async def admin_location_detail_page(
    location_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Location detail — hierarchical view of campus, programs, sessions, instructors."""
    _admin_guard(user)

    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    # Q2: Campuses
    campuses = db.query(Campus).filter(Campus.location_id == location_id).order_by(Campus.name).all()
    campus_ids = [c.id for c in campuses]

    # Q3: Active/upcoming semesters at this location
    _active_statuses = [
        SemesterStatus.READY_FOR_ENROLLMENT,
        SemesterStatus.ONGOING,
        SemesterStatus.INSTRUCTOR_ASSIGNED,
    ]
    semesters = db.query(Semester).filter(
        Semester.location_id == location_id,
        Semester.status.in_(_active_statuses)
    ).order_by(Semester.start_date).all()
    semester_ids = [s.id for s in semesters]

    # Q4: Upcoming sessions — UNION: campus_id IN + semester_id IN
    _today = date.today()
    _two_weeks = _today + timedelta(days=14)
    _base_filters = [
        sqlfunc.date(SessionModel.date_start) >= _today,
        sqlfunc.date(SessionModel.date_start) <= _two_weeks,
        SessionModel.session_status != 'cancelled',
    ]
    if campus_ids and semester_ids:
        _loc_filter = or_(
            SessionModel.campus_id.in_(campus_ids),
            SessionModel.semester_id.in_(semester_ids),
        )
    elif campus_ids:
        _loc_filter = SessionModel.campus_id.in_(campus_ids)
    elif semester_ids:
        _loc_filter = SessionModel.semester_id.in_(semester_ids)
    else:
        _loc_filter = None

    if _loc_filter is not None:
        upcoming_sessions = db.query(SessionModel).filter(
            _loc_filter, *_base_filters
        ).order_by(SessionModel.date_start).limit(30).all()
    else:
        upcoming_sessions = []

    # Q5: Active instructor assignments at this location
    assignments = db.query(InstructorAssignment).filter(
        InstructorAssignment.location_id == location_id,
        InstructorAssignment.is_active == True  # noqa: E712
    ).order_by(InstructorAssignment.age_group, InstructorAssignment.year.desc()).all()

    # Q6: Batch load instructor user objects
    _instr_ids = list({a.instructor_id for a in assignments})
    instructor_map: dict = (
        {u.id: u for u in db.query(User).filter(User.id.in_(_instr_ids)).all()}
        if _instr_ids else {}
    )

    # Q7: Upcoming session counts per campus (direct campus_id only)
    campus_session_counts: dict = {}
    if campus_ids:
        for row in db.query(SessionModel.campus_id, sqlfunc.count(SessionModel.id)).filter(
            SessionModel.campus_id.in_(campus_ids),
            sqlfunc.date(SessionModel.date_start) >= _today,
            SessionModel.session_status != 'cancelled',
        ).group_by(SessionModel.campus_id).all():
            campus_session_counts[row[0]] = row[1]

    # Group semesters by age_group (ordered logically)
    semesters_by_group: dict = defaultdict(list)
    for s in semesters:
        key = s.age_group or (s.semester_category.value if s.semester_category else "OTHER")
        semesters_by_group[key].append(s)
    _group_order = ["PRE", "YOUTH", "AMATEUR", "PRO", "TOURNAMENT", "CAMP", "OTHER"]
    semesters_by_group = {k: semesters_by_group[k] for k in _group_order if k in semesters_by_group}

    return templates.TemplateResponse(
        "admin/location_detail.html",
        {
            "request": request,
            "user": user,
            "loc": loc,
            "campuses": campuses,
            "semesters": semesters,
            "semesters_by_group": semesters_by_group,
            "upcoming_sessions": upcoming_sessions,
            "assignments": assignments,
            "instructor_map": instructor_map,
            "campus_session_counts": campus_session_counts,
            "LocationType": LocationType,
            "SemesterStatus": SemesterStatus,
            "today": _today,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        }
    )


@router.post("/admin/locations")
async def admin_create_location(
    request: Request,
    name: str = Form(...),
    city: str = Form(...),
    country: str = Form(...),
    country_code: str = Form(""),
    location_code: str = Form(""),
    postal_code: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    location_type: str = Form("PARTNER"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = Location(
        name=name.strip(),
        city=city.strip(),
        country=country.strip(),
        country_code=country_code.strip().upper() or None,
        location_code=location_code.strip().upper() or None,
        postal_code=postal_code.strip() or None,
        address=address.strip() or None,
        notes=notes.strip() or None,
        location_type=LocationType(location_type),
        is_active=True,
    )
    db.add(loc)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/locations/{location_id}/toggle")
async def admin_toggle_location(
    location_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    loc.is_active = not loc.is_active
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/locations/{location_id}/delete")
async def admin_delete_location(
    location_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    db.delete(loc)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.get("/admin/locations/{location_id}/edit", response_class=HTMLResponse)
async def admin_edit_location_page(
    location_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    return templates.TemplateResponse(
        "admin/location_edit.html",
        {"request": request, "user": user, "loc": loc, "LocationType": LocationType}
    )


@router.post("/admin/locations/{location_id}/edit")
async def admin_update_location(
    location_id: int,
    request: Request,
    name: str = Form(...),
    city: str = Form(...),
    country: str = Form(...),
    country_code: str = Form(""),
    location_code: str = Form(""),
    postal_code: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    location_type: str = Form("PARTNER"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    loc.name = name.strip()
    loc.city = city.strip()
    loc.country = country.strip()
    loc.country_code = country_code.strip().upper() or None
    loc.location_code = location_code.strip().upper() or None
    loc.postal_code = postal_code.strip() or None
    loc.address = address.strip() or None
    loc.notes = notes.strip() or None
    # K2: Block CENTER→PARTNER when active Academy semesters exist at this location.
    _ACADEMY_SPECS = {
        "LFA_PLAYER_PRE_ACADEMY", "LFA_PLAYER_YOUTH_ACADEMY",
        "LFA_PLAYER_AMATEUR_ACADEMY", "LFA_PLAYER_PRO_ACADEMY",
    }
    _ACTIVE_STATUSES = {SemesterStatus.READY_FOR_ENROLLMENT, SemesterStatus.ONGOING}
    try:
        new_loc_type = LocationType(location_type)
    except ValueError:
        new_loc_type = loc.location_type  # unchanged
    if loc.location_type == LocationType.CENTER and new_loc_type == LocationType.PARTNER:
        conflict = (
            db.query(Semester)
            .filter(
                Semester.location_id == location_id,
                Semester.specialization_type.in_(_ACADEMY_SPECS),
                Semester.status.in_(_ACTIVE_STATUSES),
            )
            .first()
        )
        if conflict:
            loc_for_template = db.query(Location).filter(Location.id == location_id).first()
            return templates.TemplateResponse(
                "admin/location_edit.html",
                {
                    "request": request,
                    "user": user,
                    "loc": loc_for_template,
                    "LocationType": LocationType,
                    "error": (
                        f"Nem változtatható CENTER→PARTNER típusra: "
                        f"'{conflict.name}' ({conflict.code}) aktív Academy Season "
                        f"ehhez a helyszínhez van rendelve."
                    ),
                },
                status_code=409,
            )
    loc.location_type = new_loc_type
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.get("/admin/campuses/{campus_id}", response_class=HTMLResponse)
async def admin_campus_detail_page(
    campus_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Campus detail — programs, upcoming sessions, location instructors."""
    _admin_guard(user)

    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")

    loc = db.query(Location).filter(Location.id == campus.location_id).first()

    # Q2: Active semesters at this campus
    _active_statuses = [
        SemesterStatus.READY_FOR_ENROLLMENT,
        SemesterStatus.ONGOING,
        SemesterStatus.INSTRUCTOR_ASSIGNED,
    ]
    semesters = db.query(Semester).filter(
        Semester.campus_id == campus_id,
        Semester.status.in_(_active_statuses)
    ).order_by(Semester.start_date).all()
    semester_ids = [s.id for s in semesters]

    # Q3: Upcoming sessions at this campus (campus_id OR semester)
    _today = date.today()
    _base_filters = [
        sqlfunc.date(SessionModel.date_start) >= _today,
        SessionModel.session_status != 'cancelled',
    ]
    if semester_ids:
        _sess_filter = or_(
            SessionModel.campus_id == campus_id,
            SessionModel.semester_id.in_(semester_ids),
        )
    else:
        _sess_filter = SessionModel.campus_id == campus_id

    sessions = db.query(SessionModel).filter(
        _sess_filter, *_base_filters
    ).order_by(SessionModel.date_start).limit(20).all()

    # Q4: Active instructor assignments at parent location
    assignments = db.query(InstructorAssignment).filter(
        InstructorAssignment.location_id == campus.location_id,
        InstructorAssignment.is_active == True  # noqa: E712
    ).order_by(InstructorAssignment.age_group).all()
    _instr_ids = list({a.instructor_id for a in assignments})
    instructor_map: dict = (
        {u.id: u for u in db.query(User).filter(User.id.in_(_instr_ids)).all()}
        if _instr_ids else {}
    )

    return templates.TemplateResponse(
        "admin/campus_detail.html",
        {
            "request": request,
            "user": user,
            "campus": campus,
            "loc": loc,
            "semesters": semesters,
            "sessions": sessions,
            "assignments": assignments,
            "instructor_map": instructor_map,
            "today": _today,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        }
    )


@router.get("/admin/campuses/{campus_id}/edit", response_class=HTMLResponse)
async def admin_edit_campus_page(
    campus_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    return templates.TemplateResponse(
        "admin/campus_edit.html",
        {"request": request, "user": user, "campus": campus}
    )


@router.post("/admin/campuses/{campus_id}/edit")
async def admin_update_campus(
    campus_id: int,
    request: Request,
    name: str = Form(...),
    venue: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    campus.name = name.strip()
    campus.venue = venue.strip() or None
    campus.address = address.strip() or None
    campus.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/locations/{location_id}/campuses")
async def admin_create_campus(
    location_id: int,
    name: str = Form(...),
    venue: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    campus = Campus(
        location_id=location_id,
        name=name.strip(),
        venue=venue.strip() or None,
        address=address.strip() or None,
        notes=notes.strip() or None,
        is_active=True,
    )
    db.add(campus)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/campuses/{campus_id}/toggle")
async def admin_toggle_campus(
    campus_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    campus.is_active = not campus.is_active
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/campuses/{campus_id}/delete")
async def admin_delete_campus(
    campus_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    db.delete(campus)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


# ============================================================================
# ADMIN SYSTEM EVENTS
# ============================================================================

@router.get("/admin/system-events", response_class=HTMLResponse)
async def admin_system_events_page(
    request: Request,
    level: str = "",
    resolved: str = "open",
    page: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: System Events log with filters"""
    _admin_guard(user)
    PAGE_SIZE = 50
    q = db.query(SystemEvent)
    if level and level != "All":
        q = q.filter(SystemEvent.level == level)
    if resolved == "open":
        q = q.filter(SystemEvent.resolved == False)
    elif resolved == "resolved":
        q = q.filter(SystemEvent.resolved == True)
    total = q.count()
    events = q.order_by(SystemEvent.created_at.desc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE).all()
    total_pages = max(1, -(-total // PAGE_SIZE))
    return templates.TemplateResponse(
        "admin/system_events.html",
        {
            "request": request, "user": user,
            "events": events, "total": total,
            "page": page, "total_pages": total_pages, "page_size": PAGE_SIZE,
            "filter_level": level, "filter_resolved": resolved,
        }
    )


@router.post("/admin/system-events/{event_id}/resolve")
async def admin_resolve_system_event(
    event_id: int,
    page: int = Form(0),
    level: str = Form(""),
    resolved: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    ev = db.query(SystemEvent).filter(SystemEvent.id == event_id).first()
    if ev:
        ev.resolved = True
        db.commit()
    return RedirectResponse(url=f"/admin/system-events?level={level}&resolved={resolved}&page={page}", status_code=303)


@router.post("/admin/system-events/{event_id}/unresolve")
async def admin_unresolve_system_event(
    event_id: int,
    page: int = Form(0),
    level: str = Form(""),
    resolved: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    ev = db.query(SystemEvent).filter(SystemEvent.id == event_id).first()
    if ev:
        ev.resolved = False
        db.commit()
    return RedirectResponse(url=f"/admin/system-events?level={level}&resolved={resolved}&page={page}", status_code=303)


@router.post("/admin/system-events/purge")
async def admin_purge_system_events(
    retention_days: int = Form(90),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = db.query(SystemEvent).filter(
        SystemEvent.resolved == True,
        SystemEvent.created_at < cutoff
    ).delete(synchronize_session=False)
    db.commit()
    logger.info("admin_events_purged", extra={"admin": user.email, "deleted": deleted, "retention_days": retention_days})
    return RedirectResponse(url="/admin/system-events", status_code=303)


# ============================================================================
# ADMIN GAME PRESETS
# ============================================================================

def _build_skill_groups():
    """Build skill groups from SKILL_CATEGORIES config for template rendering."""
    groups = []
    for cat in SKILL_CATEGORIES:
        groups.append({
            "label": f"{cat['emoji']} {cat['name_en']}",
            "skills": [{"key": s["key"], "name": s["name_en"]} for s in cat["skills"]]
        })
    return groups


@router.get("/admin/game-presets", response_class=HTMLResponse)
async def admin_game_presets_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Game Presets management"""
    _admin_guard(user)
    presets = db.query(GamePreset).order_by(GamePreset.name).all()
    skill_groups = _build_skill_groups()
    return templates.TemplateResponse(
        "admin/game_presets.html",
        {"request": request, "user": user, "presets": presets, "skill_groups": skill_groups}
    )


@router.post("/admin/game-presets")
async def admin_create_game_preset(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    difficulty: str = Form(""),
    min_players: int = Form(4),
    skill_impact: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    form_data = await request.form()
    # Collect selected skills and weights from form
    skills = []
    weights = {}
    for key, val in form_data.multi_items():
        if key.startswith("skill_cb_"):
            skill_key = key[len("skill_cb_"):]
            skills.append(skill_key)
        if key.startswith("skill_w_"):
            skill_key = key[len("skill_w_"):]
            try:
                weights[skill_key] = int(val)
            except (ValueError, TypeError):
                weights[skill_key] = 1

    total = sum(weights.get(s, 1) for s in skills) or 1
    skill_weights = {s: round(weights.get(s, 1) / total, 4) for s in skills}

    game_config = {
        "version": "1.0",
        "format_config": {},
        "skill_config": {
            "skills_tested": skills,
            "skill_weights": skill_weights,
            "skill_impact_on_matches": bool(skill_impact),
        },
        "simulation_config": {},
        "metadata": {
            "game_category": category or None,
            "difficulty_level": difficulty or None,
            "min_players": min_players,
        },
    }
    preset = GamePreset(
        code=code.strip(),
        name=name.strip(),
        description=description.strip() or None,
        game_config=game_config,
        is_active=True,
        created_by=user.id,
    )
    db.add(preset)
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)


@router.get("/admin/game-presets/{preset_id}/edit", response_class=HTMLResponse)
async def admin_edit_game_preset_page(
    preset_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    skill_groups = _build_skill_groups()
    # Extract current skill weights as integer percentages for the form
    sc = (preset.game_config or {}).get("skill_config", {})
    raw_weights = sc.get("skill_weights", {})
    current_skills = sc.get("skills_tested", [])
    total_w = sum(raw_weights.values()) or 1.0
    weight_pcts = {k: max(1, round(v / total_w * 100)) for k, v in raw_weights.items()}
    return templates.TemplateResponse(
        "admin/game_preset_edit.html",
        {
            "request": request, "user": user, "preset": preset,
            "skill_groups": skill_groups, "current_skills": current_skills,
            "weight_pcts": weight_pcts,
        }
    )


@router.post("/admin/game-presets/{preset_id}/edit")
async def admin_edit_game_preset_submit(
    preset_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    difficulty: str = Form(""),
    min_players: int = Form(4),
    skill_impact: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    form_data = await request.form()
    skills = []
    weights = {}
    for key, val in form_data.multi_items():
        if key.startswith("skill_cb_"):
            skills.append(key[len("skill_cb_"):])
        if key.startswith("skill_w_"):
            try:
                weights[key[len("skill_w_"):]] = int(val)
            except (ValueError, TypeError):
                weights[key[len("skill_w_"):]] = 1

    total = sum(weights.get(s, 1) for s in skills) or 1
    skill_weights = {s: round(weights.get(s, 1) / total, 4) for s in skills}

    existing_config = preset.game_config or {}
    new_config = {
        **existing_config,
        "skill_config": {
            "skills_tested": skills,
            "skill_weights": skill_weights,
            "skill_impact_on_matches": bool(skill_impact),
        },
        "metadata": {
            "game_category": category or None,
            "difficulty_level": difficulty or None,
            "min_players": min_players,
        },
    }
    preset.name = name.strip()
    preset.description = description.strip() or None
    preset.game_config = new_config
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)


@router.post("/admin/game-presets/{preset_id}/toggle")
async def admin_toggle_game_preset(
    preset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    preset.is_active = not preset.is_active
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)


@router.post("/admin/game-presets/{preset_id}/delete")
async def admin_delete_game_preset(
    preset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    preset = db.query(GamePreset).filter(GamePreset.id == preset_id).first()
    if not preset:
        raise HTTPException(status_code=404, detail="Game preset not found")
    if getattr(preset, "is_locked", False):
        raise HTTPException(status_code=400, detail="Cannot delete a locked game preset")
    db.delete(preset)
    db.commit()
    return RedirectResponse(url="/admin/game-presets", status_code=303)


# ============================================================================
# ADMIN COUPON CRUD
# ============================================================================

from ...models.coupon import CouponType


@router.post("/admin/coupons")
async def admin_create_coupon(
    code: str = Form(...),
    coupon_type: str = Form(...),
    value: float = Form(...),
    description: str = Form(...),
    max_uses: str = Form(""),
    expires_days: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    from ...models.coupon import Coupon, CouponType as CT
    try:
        ct = CT(coupon_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid coupon type: {coupon_type}")

    # Convert value based on type
    if ct == CT.PURCHASE_DISCOUNT_PERCENT:
        if not (0 < value <= 100):
            raise HTTPException(status_code=400, detail="Discount percent must be between 1 and 100")
        discount_value = value / 100.0  # store as 0-1 fraction
    else:
        if value <= 0:
            raise HTTPException(status_code=400, detail="Credit value must be positive")
        discount_value = float(value)

    if not code.strip():
        raise HTTPException(status_code=400, detail="Coupon code cannot be empty")

    max_uses_int = int(max_uses) if max_uses.strip() else None
    expires_at = None
    if expires_days.strip():
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=int(expires_days))

    coupon = Coupon(
        code=code.strip().upper(),
        type=ct,
        discount_value=discount_value,
        description=description.strip(),
        is_active=True,
        max_uses=max_uses_int,
        expires_at=expires_at,
    )
    coupon.set_flags_based_on_type()
    db.add(coupon)
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


@router.post("/admin/coupons/{coupon_id}/toggle")
async def admin_toggle_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    from ...models.coupon import Coupon
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.is_active = not coupon.is_active
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


@router.post("/admin/coupons/{coupon_id}/delete")
async def admin_delete_coupon(
    coupon_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    from ...models.coupon import Coupon
    coupon = db.query(Coupon).filter(Coupon.id == coupon_id).first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    db.delete(coupon)
    db.commit()
    return RedirectResponse(url="/admin/coupons", status_code=303)


# ============================================================================
# ADMIN SESSIONS MANAGEMENT
# ============================================================================

from ...models.session import Session as SessionModel, SessionType


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions_page(
    request: Request,
    session_type: str = "",
    status: str = "",
    location_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    cleared: str = "",
    event_category: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Session management — hierarchical view (Location → Spec → Semester → Session)"""
    _admin_guard(user)

    # Default date_from to today unless user explicitly cleared filters
    today_str = date.today().isoformat()
    if not date_from and not cleared:
        date_from = today_str

    q = db.query(SessionModel)

    if session_type:
        try:
            q = q.filter(SessionModel.session_type == SessionType(session_type))
        except ValueError:
            pass
    if status:
        q = q.filter(SessionModel.session_status == status)
    if location_filter:
        q = q.filter(SessionModel.location.ilike(f"%{location_filter}%"))
    if date_from:
        try:
            q = q.filter(SessionModel.date_start >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(SessionModel.date_start <= datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59))
        except ValueError:
            pass
    if event_category in ("TRAINING", "MATCH"):
        q = q.filter(SessionModel.event_category == event_category)

    all_sessions = q.order_by(SessionModel.date_start).all()

    # Get booking counts in bulk
    from ...models.booking import Booking
    from sqlalchemy import func as sqlfunc
    booking_counts = dict(
        db.query(Booking.session_id, sqlfunc.count(Booking.id))
        .filter(Booking.session_id.in_([s.id for s in all_sessions]))
        .group_by(Booking.session_id)
        .all()
    ) if all_sessions else {}

    for s in all_sessions:
        s.booking_count = booking_counts.get(s.id, 0)

    # Attach semester info
    semesters = {sem.id: sem for sem in db.query(Semester).all()}
    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.name).all()

    now = datetime.now()

    # Group hierarchically: location_key → spec → semester_id → sessions
    from collections import defaultdict, OrderedDict

    hierarchy = {}  # location_str → {spec → {semester_id → [sessions]}}
    for s in all_sessions:
        loc_key = (s.location or "Unknown Location").strip()
        spec = s.target_specialization.value if s.target_specialization else ("Mixed" if s.mixed_specialization else "General")
        sem_id = s.semester_id
        hierarchy.setdefault(loc_key, {}).setdefault(spec, {}).setdefault(sem_id, []).append(s)

    # Stats
    upcoming = sum(1 for s in all_sessions if s.date_start > now)
    past = sum(1 for s in all_sessions if s.date_start <= now)

    return templates.TemplateResponse(
        "admin/sessions.html",
        {
            "request": request,
            "user": user,
            "all_sessions": all_sessions,
            "hierarchy": hierarchy,
            "semesters": semesters,
            "locations": locations,
            "now": now,
            "upcoming": upcoming,
            "past": past,
            "filter_session_type": session_type,
            "filter_status": status,
            "filter_location": location_filter,
            "filter_date_from": date_from,
            "filter_date_to": date_to,
            "filter_event_category": event_category,
            "SessionType": SessionType,
        }
    )


# ============================================================================
# 💳 INVOICE VERIFICATION — web-layer wrappers (cookie auth for HTML admin)
# ============================================================================

@router.post("/admin/invoices/{invoice_id}/verify")
async def admin_invoice_verify(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Verify invoice payment and credit the student account (cookie auth)."""
    _admin_guard(user)
    invoice = db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "verified":
        raise HTTPException(status_code=400, detail="Invoice already verified")
    if invoice.status == "cancelled":
        raise HTTPException(status_code=400, detail="Cannot verify cancelled invoice")

    student = db.query(User).filter(User.id == invoice.user_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    old_balance = student.credit_balance
    invoice.status = "verified"
    invoice.verified_at = datetime.now(timezone.utc)
    student.credit_balance += invoice.credit_amount
    student.credit_purchased = (student.credit_purchased or 0) + invoice.credit_amount
    ct = CreditTransaction(
        user_id=student.id,
        transaction_type=TransactionType.PURCHASE.value,
        amount=invoice.credit_amount,
        balance_after=student.credit_balance,
        description=f"Invoice #{invoice.id} verified by admin",
        idempotency_key=f"invoice-verify-{invoice.id}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    db.refresh(invoice)
    db.refresh(student)

    return JSONResponse({"success": True, "credits_added": invoice.credit_amount,
                         "student_name": student.name, "new_balance": student.credit_balance})


@router.post("/admin/invoices/{invoice_id}/cancel")
async def admin_invoice_cancel(
    invoice_id: int,
    request: Request,
    reason: str = Form("No reason provided"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Cancel an invoice request (cookie auth)."""
    _admin_guard(user)
    invoice = db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == "verified":
        raise HTTPException(status_code=400, detail="Cannot cancel verified invoice")
    if invoice.status == "cancelled":
        raise HTTPException(status_code=400, detail="Invoice already cancelled")

    invoice.status = "cancelled"
    db.commit()
    return JSONResponse({"success": True, "message": f"Invoice cancelled. Reason: {reason}"})


@router.post("/admin/invoices/{invoice_id}/unverify")
async def admin_invoice_unverify(
    invoice_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Unverify invoice (reverts credits) — cookie auth."""
    _admin_guard(user)
    invoice = db.query(InvoiceRequest).filter(InvoiceRequest.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status != "verified":
        raise HTTPException(status_code=400, detail="Invoice must be verified to unverify")

    student = db.query(User).filter(User.id == invoice.user_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    deducted = min(invoice.credit_amount, student.credit_balance or 0)
    student.credit_balance = max(0, (student.credit_balance or 0) - invoice.credit_amount)
    student.credit_purchased = max(0, (student.credit_purchased or 0) - invoice.credit_amount)
    invoice.status = "pending"
    invoice.verified_at = None
    ct = CreditTransaction(
        user_id=student.id,
        transaction_type=TransactionType.REFUND.value,
        amount=-deducted,
        balance_after=student.credit_balance,
        description=f"Invoice #{invoice.id} unverified by admin",
        idempotency_key=f"invoice-unverify-{invoice.id}",
        performed_by_user_id=user.id,
    )
    db.add(ct)
    db.commit()
    db.refresh(invoice)
    db.refresh(student)

    return JSONResponse({"success": True, "credits_removed": invoice.credit_amount,
                         "student_name": student.name, "new_balance": student.credit_balance})


# ============================================================================
# 📋 BOOKINGS ADMIN PANEL
# ============================================================================

@router.get("/admin/bookings", response_class=HTMLResponse)
async def admin_bookings_page(
    request: Request,
    status_filter: str = "",
    session_id: int = 0,
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all bookings with filters and action buttons."""
    _admin_guard(user)

    q = db.query(Booking).options(
        joinedload(Booking.user),
        joinedload(Booking.session),
        joinedload(Booking.attendance),
    )
    if status_filter:
        try:
            q = q.filter(Booking.status == BookingStatus(status_filter))
        except ValueError:
            pass
    if session_id:
        q = q.filter(Booking.session_id == session_id)

    total = q.count()
    page = max(1, page)
    size = 50
    total_pages = max(1, (total + size - 1) // size)
    page = min(page, total_pages)
    bookings = q.order_by(Booking.created_at.desc()).offset((page - 1) * size).limit(size).all()

    # Stats
    stats = {s.value: db.query(sqlfunc.count(Booking.id)).filter(Booking.status == s).scalar() or 0
             for s in BookingStatus}

    # Sessions for filter dropdown (only those that have bookings)
    sessions_with_bookings = (
        db.query(SessionModel)
        .join(Booking, Booking.session_id == SessionModel.id)
        .distinct()
        .order_by(SessionModel.date_start.desc())
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        "admin/bookings.html",
        {
            "request": request,
            "user": user,
            "bookings": bookings,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "stats": stats,
            "BookingStatus": BookingStatus,
            "AttendanceStatus": AttendanceStatus,
            "filter_status": status_filter,
            "filter_session_id": session_id,
            "sessions_with_bookings": sessions_with_bookings,
        }
    )


@router.post("/admin/bookings/{booking_id}/confirm")
async def admin_booking_confirm(
    booking_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Confirm a booking (admin, cookie auth)."""
    _admin_guard(user)
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status == BookingStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="Booking already confirmed")

    session_obj = db.query(SessionModel).filter(SessionModel.id == booking.session_id).first()
    if session_obj and session_obj.capacity:
        confirmed_count = db.query(sqlfunc.count(Booking.id)).filter(
            Booking.session_id == booking.session_id,
            Booking.status == BookingStatus.CONFIRMED,
        ).scalar() or 0
        if confirmed_count >= session_obj.capacity:
            raise HTTPException(status_code=409, detail=f"Session at capacity ({session_obj.capacity})")

    booking.status = BookingStatus.CONFIRMED
    db.commit()
    return JSONResponse({"success": True, "message": "Booking confirmed"})


@router.post("/admin/bookings/{booking_id}/cancel")
async def admin_booking_cancel(
    booking_id: int,
    request: Request,
    reason: str = Form("Cancelled by admin"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Cancel a booking (admin, cookie auth)."""
    _admin_guard(user)
    booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status == BookingStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Booking already cancelled")

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = datetime.now()
    booking.notes = reason
    db.commit()
    return JSONResponse({"success": True, "message": "Booking cancelled"})


@router.post("/admin/bookings/{booking_id}/attendance")
async def admin_booking_attendance(
    booking_id: int,
    request: Request,
    attendance_status: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Mark/update attendance for a booking (admin, cookie auth)."""
    _admin_guard(user)
    valid_statuses = [s.value for s in AttendanceStatus]
    if attendance_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")

    booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.attendance:
        booking.attendance.status = AttendanceStatus(attendance_status)
        booking.attendance.notes = notes or booking.attendance.notes
        booking.attendance.marked_by = user.id
    else:
        att = Attendance(
            user_id=booking.user_id,
            session_id=booking.session_id,
            booking_id=booking.id,
            status=AttendanceStatus(attendance_status),
            notes=notes or None,
            marked_by=user.id,
        )
        db.add(att)

    booking.update_attendance_status()
    db.commit()
    return JSONResponse({"success": True, "message": f"Attendance marked: {attendance_status}"})


# ── Pitches Management ──────────────────────────────────────────────────────

from ...models.pitch import Pitch  # noqa: E402
from ...models.pitch_instructor_assignment import (  # noqa: E402
    PitchInstructorAssignment,
    PitchAssignmentType,
    PitchAssignmentStatus,
)
from ...services.tournament.pitch_instructor_service import (  # noqa: E402
    assign_instructor_to_pitch_direct,
)


@router.get("/admin/pitches", response_class=HTMLResponse)
async def admin_pitches_page(
    request: Request,
    campus_filter: int = 0,
    location_filter: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: all pitches grouped by campus, with instructor assignment status."""
    _admin_guard(user)

    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.name).all()  # noqa: E712
    all_campuses = db.query(Campus).filter(Campus.is_active == True).order_by(Campus.name).all()  # noqa: E712

    q = db.query(Pitch)
    if campus_filter:
        q = q.filter(Pitch.campus_id == campus_filter)
    elif location_filter:
        campus_ids = [c.id for c in db.query(Campus).filter(Campus.location_id == location_filter).all()]
        q = q.filter(Pitch.campus_id.in_(campus_ids)) if campus_ids else q.filter(False)
    pitches = q.order_by(Pitch.campus_id, Pitch.pitch_number).all()

    # Batch-load active/pending assignments per pitch
    pitch_ids = [p.id for p in pitches]
    active_assignments: dict = defaultdict(list)
    if pitch_ids:
        for a in db.query(PitchInstructorAssignment).filter(
            PitchInstructorAssignment.pitch_id.in_(pitch_ids),
            PitchInstructorAssignment.status.in_([
                PitchAssignmentStatus.ACTIVE.value,
                PitchAssignmentStatus.PENDING.value,
            ]),
        ).all():
            active_assignments[a.pitch_id].append(a)

    campus_map = {c.id: c for c in all_campuses}
    location_map = {loc.id: loc for loc in locations}

    instructors = db.query(User).filter(
        User.role == UserRole.INSTRUCTOR,
        User.is_active == True,  # noqa: E712
    ).order_by(User.name).all()

    return templates.TemplateResponse(
        "admin/pitches.html",
        {
            "request": request,
            "user": user,
            "pitches": pitches,
            "active_assignments": active_assignments,
            "campus_map": campus_map,
            "location_map": location_map,
            "locations": locations,
            "all_campuses": all_campuses,
            "instructors": instructors,
            "campus_filter": campus_filter,
            "location_filter": location_filter,
        },
    )


@router.post("/admin/pitches/create")
async def admin_create_pitch(
    request: Request,
    campus_id: int = Form(...),
    pitch_number: int = Form(...),
    name: str = Form(...),
    capacity: int = Form(default=2),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create a new pitch under a campus."""
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        return RedirectResponse(url="/admin/pitches?error=Campus+not+found", status_code=303)

    existing = db.query(Pitch).filter(
        Pitch.campus_id == campus_id,
        Pitch.pitch_number == pitch_number,
    ).first()
    if existing:
        return RedirectResponse(
            url=f"/admin/pitches?error=Pitch+{pitch_number}+already+exists+on+this+campus",
            status_code=303,
        )

    pitch = Pitch(
        campus_id=campus_id,
        pitch_number=pitch_number,
        name=name.strip(),
        capacity=max(1, capacity),
        is_active=True,
    )
    db.add(pitch)
    db.commit()
    return RedirectResponse(
        url=f"/admin/pitches?campus_filter={campus_id}&msg=Pitch+created",
        status_code=303,
    )


@router.post("/admin/pitches/{pitch_id}/toggle")
async def admin_toggle_pitch(
    pitch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: activate or deactivate a pitch."""
    _admin_guard(user)
    pitch = db.query(Pitch).filter(Pitch.id == pitch_id).first()
    if not pitch:
        raise HTTPException(status_code=404, detail="Pitch not found")
    pitch.is_active = not pitch.is_active
    db.commit()
    return RedirectResponse(url="/admin/pitches", status_code=303)


@router.post("/admin/pitches/{pitch_id}/assign-instructor")
async def admin_assign_instructor_to_pitch(
    request: Request,
    pitch_id: int,
    instructor_id: int = Form(...),
    semester_id: int = Form(default=0),
    is_master: bool = Form(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: directly assign an instructor to a pitch (DIRECT mode)."""
    _admin_guard(user)
    try:
        assign_instructor_to_pitch_direct(
            db=db,
            pitch_id=pitch_id,
            instructor_id=instructor_id,
            assigned_by_id=user.id,
            semester_id=semester_id if semester_id else None,
            is_master=is_master,
        )
        db.commit()
        return RedirectResponse(url="/admin/pitches?msg=Instructor+assigned", status_code=303)
    except HTTPException as e:
        return RedirectResponse(url=f"/admin/pitches?error={e.detail}", status_code=303)


# ── Sport Directors Management ──────────────────────────────────────────────

from ...models.instructor_assignment import SportDirectorAssignment  # noqa: E402


@router.get("/admin/sport-directors", response_class=HTMLResponse)
async def admin_sport_directors_page(
    request: Request,
    location_filter: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list and manage sport director assignments per location."""
    _admin_guard(user)

    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.name).all()  # noqa: E712

    q = db.query(SportDirectorAssignment)
    if location_filter:
        q = q.filter(SportDirectorAssignment.location_id == location_filter)
    assignments = q.order_by(SportDirectorAssignment.location_id, SportDirectorAssignment.is_active.desc()).all()

    # Eligible candidates: users with SPORT_DIRECTOR role OR ADMIN (for assignment dropdown)
    candidates = db.query(User).filter(
        User.role.in_([UserRole.SPORT_DIRECTOR, UserRole.ADMIN]),
        User.is_active == True,  # noqa: E712
    ).order_by(User.name).all()

    location_map = {loc.id: loc for loc in locations}

    return templates.TemplateResponse(
        "admin/sport_directors.html",
        {
            "request": request,
            "user": user,
            "assignments": assignments,
            "locations": locations,
            "candidates": candidates,
            "location_map": location_map,
            "location_filter": location_filter,
        },
    )


@router.post("/admin/sport-directors/assign")
async def admin_assign_sport_director(
    request: Request,
    user_id: int = Form(...),
    location_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: assign a user as Sport Director for a location."""
    _admin_guard(user)

    # Deactivate existing active SD for this location
    existing = db.query(SportDirectorAssignment).filter(
        SportDirectorAssignment.location_id == location_id,
        SportDirectorAssignment.is_active == True,  # noqa: E712
    ).first()
    if existing:
        existing.is_active = False
        existing.deactivated_at = datetime.now(timezone.utc)

    assignment = SportDirectorAssignment(
        user_id=user_id,
        location_id=location_id,
        is_active=True,
        assigned_by=user.id,
    )
    db.add(assignment)
    db.commit()
    return RedirectResponse(url="/admin/sport-directors?msg=Sport+Director+assigned", status_code=303)


@router.post("/admin/sport-directors/{assignment_id}/deactivate")
async def admin_deactivate_sport_director(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: deactivate a sport director assignment."""
    _admin_guard(user)
    a = db.query(SportDirectorAssignment).filter(SportDirectorAssignment.id == assignment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found")
    a.is_active = False
    a.deactivated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url="/admin/sport-directors?msg=Assignment+deactivated", status_code=303)


# ── Teams Management ────────────────────────────────────────────────────────

from ...models.team import Team, TeamMember, TournamentTeamEnrollment  # noqa: E402


@router.get("/admin/teams", response_class=HTMLResponse)
async def admin_teams_page(
    request: Request,
    tournament_filter: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: global teams list, filterable by tournament."""
    _admin_guard(user)

    # All tournaments for filter dropdown (TEAM participant type)
    tournaments = db.query(Semester).filter(
        Semester.semester_category == SemesterCategory.TOURNAMENT,
    ).order_by(Semester.start_date.desc()).all()

    if tournament_filter:
        enrolled_team_ids = [
            e.team_id for e in db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament_filter,
                TournamentTeamEnrollment.is_active == True,  # noqa: E712
            ).all()
        ]
        teams = db.query(Team).filter(
            Team.id.in_(enrolled_team_ids),
            Team.is_active == True,  # noqa: E712
        ).order_by(Team.name).all() if enrolled_team_ids else []
    else:
        teams = db.query(Team).filter(Team.is_active == True).order_by(Team.name).all()  # noqa: E712

    # Batch-load member counts
    member_counts: dict = {}
    if teams:
        team_ids = [t.id for t in teams]
        for row in db.query(
            TeamMember.team_id,
            sqlfunc.count(TeamMember.id),
        ).filter(
            TeamMember.team_id.in_(team_ids),
            TeamMember.is_active == True,  # noqa: E712
        ).group_by(TeamMember.team_id).all():
            member_counts[row[0]] = row[1]

    # Batch-load tournament enrollments per team
    team_enrollments: dict = defaultdict(list)
    if teams:
        for enr in db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.team_id.in_([t.id for t in teams]),
            TournamentTeamEnrollment.is_active == True,  # noqa: E712
        ).all():
            team_enrollments[enr.team_id].append(enr)

    return templates.TemplateResponse(
        "admin/teams.html",
        {
            "request": request,
            "user": user,
            "teams": teams,
            "tournaments": tournaments,
            "member_counts": member_counts,
            "team_enrollments": team_enrollments,
            "tournament_filter": tournament_filter,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# CLUB MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

from fastapi import UploadFile, File  # noqa: E402
from ...models.club import Club, CsvImportLog  # noqa: E402
from ...services.club_service import create_club, get_club, list_clubs  # noqa: E402
from ...services import csv_import_service  # noqa: E402

# Maps club team age-group labels (U15, U18, Adult…) → canonical Semester.age_group values.
# Semester.age_group is shared with season semesters (PRE/YOUTH/AMATEUR/PRO) so promotion
# tournaments must use the same vocabulary to stay filter-compatible.
_CLUB_AGE_GROUP_MAP: dict[str, str] = {
    "U6": "PRE", "U7": "PRE", "U8": "PRE", "U9": "PRE",
    "U10": "PRE", "U11": "PRE", "U12": "PRE",
    "U13": "YOUTH", "U14": "YOUTH", "U15": "YOUTH",
    "U16": "YOUTH", "U17": "YOUTH", "U18": "YOUTH",
    "U19": "AMATEUR", "U20": "AMATEUR", "U21": "AMATEUR",
    "U22": "AMATEUR", "U23": "AMATEUR",
    "SENIOR": "AMATEUR", "ADULT": "AMATEUR",
    "PRE": "PRE", "YOUTH": "YOUTH", "AMATEUR": "AMATEUR", "PRO": "PRO",
}


def _normalize_club_age_group(label: str) -> str:
    """Map club team age-group label to the canonical Semester.age_group vocabulary."""
    return _CLUB_AGE_GROUP_MAP.get(label.strip().upper(), "AMATEUR")


@router.get("/admin/clubs", response_class=HTMLResponse)
async def admin_clubs_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all clubs."""
    _admin_guard(user)

    clubs = list_clubs(db, active_only=False)
    # Batch-load team counts
    club_ids = [c.id for c in clubs]
    team_counts: dict = {}
    member_counts: dict = {}
    if club_ids:
        from ...models.team import TeamMember
        for row in db.query(
            Team.club_id, sqlfunc.count(Team.id)
        ).filter(Team.club_id.in_(club_ids), Team.is_active == True).group_by(Team.club_id).all():  # noqa: E712
            team_counts[row[0]] = row[1]

        # total members per club via JOIN
        team_id_by_club: dict = defaultdict(list)
        for t in db.query(Team.id, Team.club_id).filter(Team.club_id.in_(club_ids), Team.is_active == True).all():  # noqa: E712
            team_id_by_club[t.club_id].append(t.id)
        all_team_ids = [tid for tids in team_id_by_club.values() for tid in tids]
        if all_team_ids:
            for row in db.query(
                TeamMember.team_id, sqlfunc.count(TeamMember.id)
            ).filter(TeamMember.team_id.in_(all_team_ids), TeamMember.is_active == True).group_by(TeamMember.team_id).all():  # noqa: E712
                team_club_id = next((cid for cid, tids in team_id_by_club.items() if row[0] in tids), None)
                if team_club_id:
                    member_counts[team_club_id] = member_counts.get(team_club_id, 0) + row[1]

    total_clubs = len(clubs)
    total_teams = sum(team_counts.values())
    total_players = sum(member_counts.values())

    return templates.TemplateResponse(
        "admin/clubs.html",
        {
            "request": request,
            "user": user,
            "clubs": clubs,
            "team_counts": team_counts,
            "member_counts": member_counts,
            "total_clubs": total_clubs,
            "total_teams": total_teams,
            "total_players": total_players,
        },
    )


@router.post("/admin/clubs/create")
async def admin_create_club(
    request: Request,
    name: str = Form(...),
    city: str = Form(""),
    country: str = Form(""),
    contact_email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create a new Club."""
    _admin_guard(user)

    try:
        club = create_club(
            db,
            name=name,
            city=city or None,
            country=country or None,
            contact_email=contact_email or None,
            created_by_id=user.id,
        )
        db.commit()
        logger.info("admin_club_created", extra={"admin": user.email, "club": club.name, "code": club.code})
        return RedirectResponse(url=f"/admin/clubs/{club.id}?msg=Club+created", status_code=303)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/admin/clubs?create_error={str(exc).replace(' ', '+')}",
            status_code=303,
        )


@router.get("/admin/clubs/{club_id}", response_class=HTMLResponse)
async def admin_club_detail(
    club_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: club detail with teams + CSV import history."""
    _admin_guard(user)

    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Load teams for this club
    from ...models.team import TeamMember
    teams = (
        db.query(Team)
        .filter(Team.club_id == club_id, Team.is_active == True)  # noqa: E712
        .order_by(Team.age_group_label, Team.name)
        .all()
    )
    team_ids = [t.id for t in teams]
    member_counts: dict = {}
    if team_ids:
        for row in db.query(
            TeamMember.team_id, sqlfunc.count(TeamMember.id)
        ).filter(TeamMember.team_id.in_(team_ids), TeamMember.is_active == True).group_by(TeamMember.team_id).all():  # noqa: E712
            member_counts[row[0]] = row[1]

    # Import history
    import_logs = (
        db.query(CsvImportLog)
        .filter(CsvImportLog.club_id == club_id)
        .order_by(CsvImportLog.uploaded_at.desc())
        .limit(10)
        .all()
    )

    # For promotion wizard: game presets + tournament types + campuses
    from ...models.game_preset import GamePreset
    from ...models.tournament_type import TournamentType
    game_presets = db.query(GamePreset).order_by(GamePreset.name).all()
    tournament_types = db.query(TournamentType).order_by(TournamentType.display_name).all()
    campuses = db.query(Campus).filter(Campus.is_active == True).order_by(Campus.name).all()  # noqa: E712

    # Unique age_groups from teams
    age_groups = sorted({t.age_group_label for t in teams if t.age_group_label})

    # Canonical age_group per team: U15 → YOUTH, U12 → PRE (for dual display in UI)
    team_canonical = {
        t.id: _normalize_club_age_group(t.age_group_label)
        for t in teams if t.age_group_label
    }

    return templates.TemplateResponse(
        "admin/club_detail.html",
        {
            "request": request,
            "user": user,
            "club": club,
            "teams": teams,
            "member_counts": member_counts,
            "import_logs": import_logs,
            "game_presets": game_presets,
            "tournament_types": tournament_types,
            "campuses": campuses,
            "age_groups": age_groups,
            "team_canonical": team_canonical,
        },
    )


@router.post("/admin/clubs/{club_id}/edit")
async def admin_edit_club(
    club_id: int,
    request: Request,
    name: str = Form(...),
    city: str = Form(""),
    country: str = Form(""),
    contact_email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: update club details."""
    _admin_guard(user)
    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    club.name = name.strip()
    club.city = city or None
    club.country = country or None
    club.contact_email = contact_email or None
    db.commit()
    return RedirectResponse(url=f"/admin/clubs/{club_id}?msg=Club+updated", status_code=303)


@router.post("/admin/clubs/{club_id}/toggle")
async def admin_toggle_club(
    club_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: activate or deactivate a club."""
    _admin_guard(user)
    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    club.is_active = not club.is_active
    db.commit()
    return RedirectResponse(url=f"/admin/clubs/{club_id}?msg=Status+updated", status_code=303)


@router.post("/admin/clubs/{club_id}/csv-import")
async def admin_club_csv_import(
    club_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: upload + process a CSV file for a club."""
    _admin_guard(user)

    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    content = await file.read()

    # Create log entry first (need id for idempotency keys)
    log = CsvImportLog(
        club_id=club_id,
        uploaded_by=user.id,
        filename=file.filename or "upload.csv",
        status="PROCESSING",
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # Parse + import
    rows = csv_import_service.parse_csv(content)
    csv_import_service.import_rows(db, rows, log, admin_user=user, default_club_id=club_id)
    db.commit()

    logger.info(
        "admin_csv_import_done admin=%s club=%s file=%s created=%d updated=%d failed=%d",
        user.email, club.name, file.filename,
        log.rows_created, log.rows_updated, log.rows_failed,
    )
    return RedirectResponse(
        url=f"/admin/clubs/{club_id}?import_log={log.id}",
        status_code=303,
    )


@router.get("/admin/clubs/{club_id}/csv-import/{log_id}", response_class=HTMLResponse)
async def admin_club_import_log(
    club_id: int,
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: view a specific CSV import log in detail."""
    _admin_guard(user)
    log = db.query(CsvImportLog).filter(
        CsvImportLog.id == log_id, CsvImportLog.club_id == club_id
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Import log not found")

    club = get_club(db, club_id)
    return templates.TemplateResponse(
        "admin/club_detail.html",
        {
            "request": request,
            "user": user,
            "club": club,
            "import_log_detail": log,
            "teams": [],
            "member_counts": {},
            "import_logs": [],
            "game_presets": [],
            "tournament_types": [],
            "campuses": [],
            "age_groups": [],
        },
    )


@router.post("/admin/clubs/{club_id}/promotion")
async def admin_club_promotion(
    club_id: int,
    request: Request,
    tournament_name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    campus_id: str = Form(""),
    game_preset_id: str = Form(""),
    tournament_type_id: str = Form(""),
    age_groups: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create one TEAM tournament per selected age_group and enroll that age group's teams."""
    _admin_guard(user)

    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    if not age_groups:
        return RedirectResponse(
            url=f"/admin/clubs/{club_id}?error=Select+at+least+one+age+group",
            status_code=303,
        )

    from datetime import datetime as _dt
    from ...models.semester import SemesterStatus, SemesterCategory
    from ...models.tournament_configuration import TournamentConfiguration
    from ...models.game_configuration import GameConfiguration
    from ...models.team import TournamentTeamEnrollment

    created_ids = []
    for ag in age_groups:
        suffix = _dt.now().strftime("%H%M%S%f")[:9]
        code = f"PROMO-{date.fromisoformat(start_date).strftime('%Y%m%d')}-{ag.upper()[:6]}-{suffix}"

        t = Semester(
            code=code,
            name=f"{tournament_name} ({ag})",
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            status=SemesterStatus.DRAFT,
            tournament_status="DRAFT",
            semester_category=SemesterCategory.TOURNAMENT,
            specialization_type="LFA_FOOTBALL_PLAYER",
            age_group=_normalize_club_age_group(ag),  # U15 → YOUTH, U12 → PRE, etc.
            enrollment_cost=0,
            campus_id=int(campus_id) if campus_id.strip() else None,
        )
        db.add(t)
        db.flush()

        # Auto-derive location_id from campus (campus always belongs to a location)
        if t.campus_id:
            _campus = db.query(Campus).filter(Campus.id == t.campus_id).first()
            if _campus and _campus.location_id:
                t.location_id = _campus.location_id

        cfg = TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=int(tournament_type_id) if tournament_type_id.strip() else None,
            participant_type="TEAM",
            number_of_rounds=1,
        )
        db.add(cfg)

        if game_preset_id.strip():
            db.add(GameConfiguration(semester_id=t.id, game_preset_id=int(game_preset_id)))

        # Auto-enroll all teams from this club with matching age_group_label
        teams_for_ag = (
            db.query(Team)
            .filter(Team.club_id == club_id, Team.age_group_label == ag, Team.is_active == True)  # noqa: E712
            .all()
        )
        for team in teams_for_ag:
            active_members = db.query(TeamMember).filter(
                TeamMember.team_id == team.id,
                TeamMember.is_active == True,  # noqa: E712
            ).count()
            if active_members == 0:
                continue  # skip empty teams — they cannot participate
            db.add(TournamentTeamEnrollment(
                semester_id=t.id,
                team_id=team.id,
                is_active=True,
                payment_verified=True,  # admin bypass — no credit cost
            ))

        db.flush()
        created_ids.append(t.id)

    db.commit()
    logger.info(
        "admin_promotion_created",
        extra={"admin": user.email, "club": club.name, "age_groups": age_groups, "tournament_ids": created_ids},
    )

    # Redirect to tournaments list; flash is shown via query param
    names_enc = "+".join(ag.replace(" ", "_") for ag in age_groups)
    return RedirectResponse(
        url=f"/admin/tournaments?flash=Promotion+tournaments+created+for+{names_enc}",
        status_code=303,
    )


@router.get("/admin/clubs/{club_id}/teams/{team_id}", response_class=HTMLResponse)
async def admin_club_team_detail(
    request: Request,
    club_id: int,
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: team member list for a club team."""
    _admin_guard(user)
    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    team = db.query(Team).filter(Team.id == team_id, Team.club_id == club_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    from ...models.team import TeamMember
    members = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id)
        .order_by(TeamMember.role.desc(), TeamMember.joined_at)
        .all()
    )
    canonical_age = _normalize_club_age_group(team.age_group_label) if team.age_group_label else None
    return templates.TemplateResponse(
        "admin/club_team_detail.html",
        {
            "request": request,
            "user": user,
            "club": club,
            "team": team,
            "members": members,
            "canonical_age": canonical_age,
        },
    )


@router.get("/admin/users/{user_id}/profile", response_class=HTMLResponse)
async def admin_user_profile(
    request: Request,
    user_id: int,
    from_club: int = None,
    from_team: int = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: FIFA EA Sports-style user profile page (29-skill system)."""
    _admin_guard(user)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # LFA Player license — 29-skill system via UserLicense.football_skills JSONB
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()

    # 29-skill profile — only meaningful when onboarding_completed=True
    lfa_skill_profile = None
    if lfa_license and lfa_license.onboarding_completed:
        from ...services.skill_progression_service import get_skill_profile
        lfa_skill_profile = get_skill_profile(db, user_id)

    # Tournament participations
    from ...models.tournament_achievement import TournamentParticipation, TournamentBadge
    participations = (
        db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(TournamentParticipation.achieved_at.desc())
        .all()
    )
    best_placement = min(
        (p.placement for p in participations if p.placement), default=None
    )
    total_xp = sum(p.xp_awarded or 0 for p in participations)

    # Win/loss/draw aggregates from tournament_rankings
    from sqlalchemy import text
    ranking_agg = db.execute(
        text("SELECT SUM(wins), SUM(losses), SUM(draws) FROM tournament_rankings WHERE user_id=:uid"),
        {"uid": user_id},
    ).fetchone()
    agg_wins   = int(ranking_agg[0] or 0) if ranking_agg else 0
    agg_losses = int(ranking_agg[1] or 0) if ranking_agg else 0
    agg_draws  = int(ranking_agg[2] or 0) if ranking_agg else 0

    # Badges (newest first, max 12)
    badges = (
        db.query(TournamentBadge)
        .filter(TournamentBadge.user_id == user_id)
        .order_by(TournamentBadge.earned_at.desc())
        .limit(12)
        .all()
    )

    # ── Progression chart data (chronological, for Chart.js) ─────────────────
    from ...models.semester import Semester as _SemModel
    from ...services.skill_progression_service import get_avg_skill_level_checkpoints as _get_checkpoints
    _checkpoints = _get_checkpoints(db, user_id)
    progression_chart_data = []
    _chart_rows = (
        db.query(TournamentParticipation, _SemModel)
        .join(_SemModel, TournamentParticipation.semester_id == _SemModel.id)
        .filter(TournamentParticipation.user_id == user_id)
        .filter(TournamentParticipation.skill_rating_delta.isnot(None))
        .order_by(_SemModel.start_date.asc(), TournamentParticipation.id.asc())
        .all()
    )
    if _chart_rows:
        _PODIUM = {"CHAMPION", "RUNNER_UP", "THIRD_PLACE"}
        _badge_by_sem = {b.semester_id: b for b in badges if b.badge_type in _PODIUM}
        for _tp, _sem in _chart_rows:
            _b = _badge_by_sem.get(_sem.id)
            progression_chart_data.append({
                "code": _sem.code or "",
                "name": _sem.name or "",
                "date": _sem.start_date.isoformat() if _sem.start_date else None,
                "placement": _tp.placement,
                "skill_delta": _tp.skill_rating_delta,
                "badge_type": _b.badge_type if _b else None,
                "is_champion": _tp.placement == 1,
                "avg_level": _checkpoints.get(_sem.id),
            })

    # ── Average skill baseline for chart Y-axis origin ───────────────────────
    avg_skill_baseline = 50.0
    if lfa_license and isinstance(lfa_license.football_skills, dict):
        _baselines = [
            v.get("baseline", 50.0) if isinstance(v, dict) else float(v)
            for v in lfa_license.football_skills.values()
        ]
        if _baselines:
            avg_skill_baseline = round(sum(_baselines) / len(_baselines), 1)

    # Back-navigation context (from club_team_detail link)
    from ...models.club import Club
    from ...models.team import Team, TeamMember
    back_club = db.query(Club).filter(Club.id == from_club).first() if from_club else None
    back_team = db.query(Team).filter(Team.id == from_team).first() if from_team else None

    # Team memberships for profile display
    target_teams_info = []
    for tm in db.query(TeamMember).filter(TeamMember.user_id == user_id).all():
        team = db.query(Team).filter(Team.id == tm.team_id).first()
        club = db.query(Club).filter(Club.id == team.club_id).first() if (team and team.club_id) else None
        target_teams_info.append({"team": team, "club": club, "role": tm.role})

    # Age calculation
    from datetime import date as _date
    target_age = None
    if target.date_of_birth:
        today = _date.today()
        dob = target.date_of_birth
        target_age = today.year - dob.year - (
            1 if (today.month, today.day) < (dob.month, dob.day) else 0
        )

    # Position: onboarding stores it in UserLicense.motivation_scores["position"]
    # (STRIKER / MIDFIELDER / DEFENDER / GOALKEEPER)
    # User.position column is only set via PATCH /api/v1/users/me — use as fallback
    target_position = None
    if lfa_license and lfa_license.motivation_scores:
        target_position = lfa_license.motivation_scores.get("position")
    if not target_position:
        target_position = target.position

    return templates.TemplateResponse(
        "admin/user_profile.html",
        {
            "request": request,
            "user": user,
            "target": target,
            "lfa_license": lfa_license,
            "lfa_skill_profile": lfa_skill_profile,
            "skill_categories": SKILL_CATEGORIES,
            "participations": participations,
            "best_placement": best_placement,
            "total_xp": total_xp,
            "agg_wins": agg_wins,
            "agg_losses": agg_losses,
            "agg_draws": agg_draws,
            "badges": badges,
            "back_club": back_club,
            "back_team": back_team,
            "target_teams_info": target_teams_info,
            "target_age": target_age,
            "target_position": target_position,
            "progression_chart_data": progression_chart_data,
            "avg_skill_baseline": avg_skill_baseline,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# LFA PLAYER CARD PHOTO  (admin upload on behalf of any player)
# ══════════════════════════════════════════════════════════════════════════════

from ...services.player_photo_service import (  # noqa: E402
    save_player_photo,
    delete_player_photo,
)


@router.post("/admin/users/{user_id}/lfa-player-photo")
async def admin_upload_player_photo(
    request: Request,
    user_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_player_photo(await file.read(), file.content_type or "", user_id)
        lfa_license.player_card_photo_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/admin/users/{user_id}/lfa-player-photo/delete")
async def admin_delete_player_photo(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    delete_player_photo(user_id)
    lfa_license.player_card_photo_url = None
    db.commit()
    return JSONResponse({"ok": True})


# ============================================================================
# ADMIN: Semester Schedule / Session Generation (Phase 2 — MINI_SEASON / ACADEMY_SEASON)
# ============================================================================

_SCHEDULING_CATEGORIES = {SemesterCategory.MINI_SEASON, SemesterCategory.ACADEMY_SEASON}


@router.get("/admin/semesters/{semester_id}/schedule", response_class=HTMLResponse)
async def semester_schedule_view(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester or semester.semester_category not in _SCHEDULING_CATEGORIES:
        raise HTTPException(status_code=404, detail="Semester not found or not a scheduling semester.")

    config = semester.schedule_config_obj
    sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester_id,
            SessionModel.auto_generated == True,
        )
        .order_by(SessionModel.date_start.asc())
        .all()
    )
    attended_count = (
        db.query(Attendance)
        .join(SessionModel, Attendance.session_id == SessionModel.id)
        .filter(SessionModel.semester_id == semester_id)
        .count()
    )
    location_campuses = []
    if semester.location_id:
        location_campuses = (
            db.query(Campus)
            .filter(Campus.location_id == semester.location_id, Campus.is_active == True)
            .all()
        )

    return templates.TemplateResponse(
        "admin/semester_schedule.html",
        {
            "request": request,
            "user": user,
            "semester": semester,
            "config": config,
            "sessions": sessions,
            "session_count": len(sessions),
            "can_generate": config is None or not config.sessions_generated,
            "can_delete": (
                config is not None
                and config.sessions_generated
                and attended_count == 0
            ),
            "location_campuses": location_campuses,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
        },
    )


@router.post("/admin/semesters/{semester_id}/schedule/generate", response_class=HTMLResponse)
async def semester_schedule_generate(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester or semester.semester_category not in _SCHEDULING_CATEGORIES:
        raise HTTPException(status_code=404, detail="Semester not found or not a scheduling semester.")

    form = await request.form()
    try:
        day_of_week = int(form.get("day_of_week", 0))
        start_time_str = form.get("start_time", "17:00")
        parts = start_time_str.split(":")
        from datetime import time as dt_time
        start_time = dt_time(int(parts[0]), int(parts[1]))
        duration_minutes = int(form.get("duration_minutes", 90))
        sessions_per_week = int(form.get("sessions_per_week", 1))
        campus_id = int(form["campus_id"]) if form.get("campus_id") else None
        pitch_id = int(form["pitch_id"]) if form.get("pitch_id") else None
        skip_conflicts = form.get("skip_conflicts") in ("on", "true", "1", True)
    except (ValueError, KeyError) as exc:
        redirect_url = (
            f"/admin/semesters/{semester_id}/schedule"
            f"?flash=Invalid+form+data&flash_type=error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    # Upsert SemesterScheduleConfig
    config = semester.schedule_config_obj
    if config is None:
        config = SemesterScheduleConfig(semester_id=semester_id)
        db.add(config)
    config.day_of_week = day_of_week
    config.start_time = start_time
    config.duration_minutes = duration_minutes
    config.sessions_per_week = sessions_per_week
    config.campus_id = campus_id
    config.pitch_id = pitch_id
    config.sessions_generated = False
    db.flush()

    generator = MiniSeasonSessionGenerator(db)
    try:
        result = generator.generate(semester, config, skip_conflicts=skip_conflicts)
    except PitchConflictError as exc:
        db.rollback()
        redirect_url = (
            f"/admin/semesters/{semester_id}/schedule"
            f"?flash=Pitch+conflict+on+{exc.detail.date}&flash_type=error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    db.commit()
    msg = (
        f"{result.sessions_created}+sessions+generated"
        + (f",+{result.sessions_skipped}+skipped" if result.sessions_skipped else "")
    )
    redirect_url = (
        f"/admin/semesters/{semester_id}/schedule"
        f"?flash={msg}&flash_type=success"
    )
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/admin/semesters/{semester_id}/schedule/delete-sessions", response_class=HTMLResponse)
async def semester_schedule_delete_sessions(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester or semester.semester_category not in _SCHEDULING_CATEGORIES:
        raise HTTPException(status_code=404, detail="Semester not found or not a scheduling semester.")

    generator = MiniSeasonSessionGenerator(db)
    try:
        deleted = generator.delete_generated_sessions(semester_id)
    except HTTPException as exc:
        redirect_url = (
            f"/admin/semesters/{semester_id}/schedule"
            f"?flash={exc.detail}&flash_type=error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    db.commit()
    redirect_url = (
        f"/admin/semesters/{semester_id}/schedule"
        f"?flash={deleted}+sessions+deleted&flash_type=success"
    )
    return RedirectResponse(url=redirect_url, status_code=303)


# ── Instructor override (per-session hook) ────────────────────────────────────

@router.patch("/admin/semesters/{semester_id}/sessions/{session_id}/instructor")
async def admin_patch_session_instructor(
    semester_id: int,
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Override the instructor on a single auto-generated session.

    Body: {"instructor_id": <int>}   — set to specific instructor
          {"instructor_id": null}    — clear override (reverts to semester default)
    """
    _admin_guard(user)
    body = await request.json()
    instructor_id = body.get("instructor_id")

    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.semester_id == semester_id)
        .first()
    )
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    session.instructor_id = instructor_id
    db.commit()
    return JSONResponse({"ok": True, "instructor_id": instructor_id})

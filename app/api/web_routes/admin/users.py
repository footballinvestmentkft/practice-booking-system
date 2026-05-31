"""Admin user management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone, date
from collections import defaultdict
import logging

from sqlalchemy.orm import joinedload
from sqlalchemy import func as sqlfunc, case

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.license import UserLicense, LicenseProgression
from ....models.specialization import SpecializationType
from ....models.credit_transaction import CreditTransaction, TransactionType
from ....core.security import get_password_hash
import uuid as _uuid
from ....models.event_reward_log import EventRewardLog
from ....models.football_skill_assessment import FootballSkillAssessment
from ....models.notification import Notification, NotificationType
from ....models.audit_log import AuditLog
from ....skills_config import SKILL_CATEGORIES
from ....services.player_photo_service import (
    save_player_photo,
    delete_player_photo,
    save_sponsor_logo,
    delete_sponsor_logo,
)

from . import templates, _admin_guard

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


@router.get("/admin/users/{user_id}/profile", response_class=HTMLResponse)
async def admin_user_profile(
    request: Request,
    user_id: int,
    from_club: int = None,
    from_team: int = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: FClassic-style user profile page (44-skill system)."""
    _admin_guard(user)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # LFA Player license — 44-skill system via UserLicense.football_skills JSONB
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()

    # 44-skill profile — only meaningful when onboarding_completed=True
    lfa_skill_profile = None
    if lfa_license and lfa_license.onboarding_completed:
        from ....services.skill_progression_service import get_skill_profile
        lfa_skill_profile = get_skill_profile(db, user_id)

    # Tournament participations
    from ....models.tournament_achievement import TournamentParticipation, TournamentBadge
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
    from ....models.semester import Semester as _SemModel
    from ....services.skill_progression_service import get_avg_skill_level_checkpoints as _get_checkpoints
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
    from ....models.club import Club
    from ....models.team import Team, TeamMember
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


@router.post("/admin/users/{user_id}/lfa-player-sponsor-logo")
async def admin_upload_sponsor_logo(
    request: Request,
    user_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    await _admin_guard(request, db)
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    try:
        url = save_sponsor_logo(await file.read(), file.content_type or "", user_id)
        lfa_license.sponsor_logo_url = url
        db.commit()
        return JSONResponse({"ok": True, "photo_url": url})
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@router.post("/admin/users/{user_id}/lfa-player-sponsor-logo/delete")
async def admin_delete_sponsor_logo(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    await _admin_guard(request, db)
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return JSONResponse({"ok": False, "error": "Nincs aktív LFA Football Player licensz"}, status_code=404)
    delete_sponsor_logo(user_id)
    lfa_license.sponsor_logo_url = None
    db.commit()
    return JSONResponse({"ok": True})

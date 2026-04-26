"""
Public player card web routes — no authentication required.
"""
from datetime import date

import os
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.user import User
from app.models.license import UserLicense
from app.models.team import Team, TeamMember
from app.models.club import Club
from app.skills_config import SKILL_CATEGORIES

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_POS_COLORS = {
    "STRIKER": "#e53e3e",
    "MIDFIELDER": "#667eea",
    "DEFENDER": "#38a169",
    "GOALKEEPER": "#d69e2e",
}


_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "templates")
_FALLBACK_TEMPLATE = "public/player_card.html"


@router.get("/players/{user_id}/card", response_class=HTMLResponse)
def public_player_card(
    request: Request,
    user_id: int,
    preview: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(
        User.id == user_id, User.is_active == True
    ).first()
    if not user:
        return HTMLResponse("<h2>Player not found</h2>", status_code=404)

    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return HTMLResponse("<h2>No active LFA Player license</h2>", status_code=404)

    # Skill profile
    from app.services.skill_progression_service import get_skill_profile
    from app.models.tournament_achievement import TournamentParticipation
    skill_profile = get_skill_profile(db, user_id) if lfa_license.onboarding_completed else None

    # Last-tournament per-skill delta (for trend arrows on public card)
    from app.models.semester import Semester

    _all_parts = (
        db.query(TournamentParticipation, Semester)
        .join(Semester, Semester.id == TournamentParticipation.semester_id)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(TournamentParticipation.achieved_at.desc(), TournamentParticipation.id.desc())
        .all()
    )
    _last_part = _all_parts[0][0] if _all_parts else None
    last_skill_delta = (
        _last_part.skill_rating_delta
        if _last_part and isinstance(_last_part.skill_rating_delta, dict)
        else {}
    )

    participations_history = []
    for p, s in _all_parts:
        delta = p.skill_rating_delta or {}
        top_skills = sorted(delta.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
        participations_history.append({
            "event_id":        s.id,
            "event_name":      s.name,
            "placement":       p.placement,
            "xp_awarded":      p.xp_awarded,
            "credits_awarded": p.credits_awarded,
            "top_skills":      top_skills,
            "achieved_at":     p.achieved_at,
            "status":          s.tournament_status,
        })

    overall = round(skill_profile["average_level"], 1) if skill_profile else 50.0
    total_tournaments = skill_profile["total_tournaments"] if skill_profile else 0
    skills_data = skill_profile["skills"] if skill_profile else {}

    # Position from motivation_scores
    position = "Unknown"
    ms = lfa_license.motivation_scores
    if ms and isinstance(ms, dict):
        position = ms.get("position", "Unknown")

    # Age group from date_of_birth
    age_group = "AMATEUR"
    if user.date_of_birth:
        dob = user.date_of_birth if hasattr(user.date_of_birth, "year") else user.date_of_birth
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 7:
            age_group = "PRE"
        elif age < 15:
            age_group = "YOUTH"

    # Tier
    if overall >= 90:
        tier_label, tier_color, avatar_bg = "ELITE", "#d69e2e", "#b7791f"
    elif overall >= 75:
        tier_label, tier_color, avatar_bg = "ADVANCED", "#48bb78", "#276749"
    elif overall >= 60:
        tier_label, tier_color, avatar_bg = "COMPETENT", "#667eea", "#434190"
    elif overall >= 50:
        tier_label, tier_color, avatar_bg = "DEVELOPING", "#ed8936", "#c05621"
    else:
        tier_label, tier_color, avatar_bg = "BEGINNER", "#fc8181", "#c53030"

    # Initials
    parts = (user.name or user.email).split()
    initials = "".join(p[0].upper() for p in parts[:2]) if parts else "?"

    # Teams
    teams_info = []
    for tm in db.query(TeamMember).filter(TeamMember.user_id == user_id).all():
        team = db.query(Team).filter(Team.id == tm.team_id).first()
        if not team:
            continue
        club = db.query(Club).filter(Club.id == team.club_id).first() if team.club_id else None
        teams_info.append({
            "team_name": team.name,
            "club_name": club.name if club else None,
            "age_group_label": team.age_group_label,
        })

    player = {
        "name": user.name or user.email,
        "nationality": user.nationality,
        "position": position,
        "age_group": age_group,
        "total_tournaments": total_tournaments,
        "skills": skills_data,
    }

    # ── Theme resolution ──────────────────────────────────────────────────────
    from app.services.card_theme_service import get_theme as _get_theme
    from app.services.card_variant_service import get_variant as _get_variant, VARIANTS as _VARIANTS

    card_theme_id = lfa_license.card_theme or "default"
    theme = _get_theme(card_theme_id)  # falls back to "default" for unknown IDs

    # Variant: ?preview= overrides DB value (preview only, not persisted)
    card_variant_id = lfa_license.card_variant or "fifa"
    if preview and preview in _VARIANTS:
        card_variant_id = preview
    variant = _get_variant(card_variant_id)  # falls back to "fifa" for unknown IDs

    # Template selection: use variant.template if the file exists, else fallback
    template_path = _FALLBACK_TEMPLATE
    candidate = os.path.join(_TEMPLATES_DIR, variant.template)
    if os.path.isfile(candidate):
        template_path = variant.template

    return templates.TemplateResponse(request, template_path, {
        "player": player,
        "overall": overall,
        "tier_label": tier_label,
        "tier_color": tier_color,
        "avatar_bg": avatar_bg,
        "initials": initials,
        "pos_color": _POS_COLORS.get(position, "#667eea"),
        "skill_categories": SKILL_CATEGORIES,
        "teams_info": teams_info,
        "photo_url": lfa_license.player_card_photo_url,
        "last_skill_delta": last_skill_delta,
        "participations_history": participations_history,
        "theme": theme,
        "card_theme_id": theme.id,
        "card_variant_id": variant.id,
    })

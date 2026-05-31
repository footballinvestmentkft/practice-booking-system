"""
Public player card web routes.

  GET /players/{user_id}/card          — public, no auth
  GET /players/{user_id}/card/export   — auth required, returns PNG
"""
import asyncio
from datetime import date
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_current_user_optional, get_current_user_web, get_db
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.team import Team, TeamMember
from app.models.club import Club
from app.services import card_export_service as _export_svc
from app.skills_config import SKILL_CATEGORIES
from app.utils.dominant_foot import calculate_dominant_badge
from app.utils.country_codes import register_filters as _register_country_filters

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_register_country_filters(templates.env)

_POS_COLORS = {
    "STRIKER": "#e53e3e",
    "MIDFIELDER": "#667eea",
    "DEFENDER": "#38a169",
    "GOALKEEPER": "#d69e2e",
}


_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "templates")
_FALLBACK_TEMPLATE = "public/player_card.html"

# Export render layer: platform → format bucket for dedicated export templates.
# Template path resolved as: public/export/{bucket}/{card_variant_id}.html
# Falls back to existing editor template + export-mode class if no file found.
from app.services.card_constants import EXPORT_FORMAT_BUCKETS as _EXPORT_FORMAT_BUCKETS
from app.services.card_design_service import (
    get_supported_buckets as _get_supported_buckets,
    get_design as _get_design,
    resolve_design_id as _resolve_design_id,
)

# CS-6 A-model: archetype_id → {bucket → driver filename}.
# archetype_id identifies the driver family for component_config-backed exports only.
# File-based exports (no component_config entry for that bucket) are unaffected.
# Absence of archetype_id or unknown archetype → file-based Level C fallback.
_ARCHETYPE_DRIVERS: dict[str, dict[str, str]] = {
    "column": {
        "portrait": "column_driver.html",
        "story":    "column_driver.html",
    },
    "pulse": {
        "square":   "pulse_driver.html",
    },
}

# Buckets where a Level C file takes priority over the driver (PORT-v2 pattern).
# Used when a design has a dedicated Level C template that supersedes the shared driver.
# Add new buckets here as each platform gets its PORT-v2 implementation.
_LEVEL_C_PRIORITY_BUCKETS: frozenset[str] = frozenset({"portrait", "story", "og"})


@router.get("/players/{user_id}", response_class=HTMLResponse)
async def public_player_profile(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Public player profile page — auth-optional, social panel, card preview."""
    from app.models.friendship import get_friendship_panel_ctx
    from app.models.license import UserLicense
    from app.models.tournament_achievement import TournamentParticipation
    from app.models.semester import Semester
    from app.services.skill_progression_service import get_skill_profile

    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        return HTMLResponse("<h2>Player not found</h2>", status_code=404)

    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not lfa_license:
        return HTMLResponse("<h2>No public player profile available</h2>", status_code=404)

    # Skill profile
    skill_profile = (
        get_skill_profile(db, user_id)
        if lfa_license.onboarding_completed
        else None
    )
    overall     = round(skill_profile["average_level"], 1) if skill_profile else 50.0
    skills_data = skill_profile["skills"]                  if skill_profile else {}

    # Tier
    if overall >= 90:
        tier_label, tier_color = "ELITE",      "#d69e2e"
    elif overall >= 75:
        tier_label, tier_color = "ADVANCED",   "#48bb78"
    elif overall >= 60:
        tier_label, tier_color = "COMPETENT",  "#667eea"
    elif overall >= 50:
        tier_label, tier_color = "DEVELOPING", "#ed8936"
    else:
        tier_label, tier_color = "BEGINNER",   "#fc8181"

    # Position
    ms       = lfa_license.motivation_scores or {}
    position = ms.get("position", "Unknown")

    # Recent participations (last 5)
    recent_parts = (
        db.query(TournamentParticipation, Semester)
        .join(Semester, Semester.id == TournamentParticipation.semester_id)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(TournamentParticipation.achieved_at.desc())
        .limit(5)
        .all()
    )
    recent_events = [
        {
            "event_name":      s.name,
            "placement":       p.placement,
            "xp_awarded":      p.xp_awarded,
            "credits_awarded": p.credits_awarded,
            "achieved_at":     p.achieved_at,
        }
        for p, s in recent_parts
    ]

    # Friendship panel context
    friendship_panel = get_friendship_panel_ctx(
        db,
        current_user_id=current_user.id if current_user else None,
        profile_user_id=user_id,
    )

    # Initials fallback avatar
    parts    = (user.name or user.email).split()
    initials = "".join(p[0].upper() for p in parts[:2]) if parts else "?"

    # Card display state — platform-first sizing
    from app.services.card_draft_service import CardDraftService as _CardDraftSvc
    from app.services.card_constants import CANVAS_SIZES as _CANVAS_SIZES
    _profile_draft = _CardDraftSvc.get_player_card_draft(db, user_id=lfa_license.user_id)
    card_variant_id = (
        _profile_draft.published_variant
        or lfa_license.published_card_variant
        or "fclassic"
    )
    card_published_v = (
        int(_profile_draft.published_at.timestamp())
        if _profile_draft and _profile_draft.published_at
        else 0
    )

    # Resolve published platform; guard against MagicMock in tests
    _pub_platform = _profile_draft.published_platform if _profile_draft else None
    if not isinstance(_pub_platform, str):
        _pub_platform = None

    if _pub_platform and _pub_platform in _CANVAS_SIZES:
        card_native_w, card_native_h = _CANVAS_SIZES[_pub_platform]
        if card_native_w == card_native_h:
            card_orientation = "square"
        elif card_native_w > card_native_h:
            card_orientation = "landscape"
        else:
            card_orientation = "portrait"
        card_platform_id = _pub_platform
    else:
        _LANDSCAPE_VARIANTS = frozenset({"showcase", "showcase_bg"})
        _NARROW_VARIANTS    = frozenset({"compact", "compact_bg"})
        card_is_landscape   = card_variant_id in _LANDSCAPE_VARIANTS
        card_native_w       = 720 if card_is_landscape else (520 if card_variant_id in _NARROW_VARIANTS else 820)
        card_native_h       = 700 if card_is_landscape else 1080
        card_orientation    = "landscape" if card_is_landscape else "portrait"
        card_platform_id    = "default"

    # Build iframe URL with platform and cache-bust version
    _v_suffix = f"&v={card_published_v}" if card_published_v else ""
    if _pub_platform:
        card_url = f"/players/{user_id}/card?platform={_pub_platform}{_v_suffix}"
    elif card_published_v:
        card_url = f"/players/{user_id}/card?v={card_published_v}"
    else:
        card_url = f"/players/{user_id}/card"

    # Highlight video — YouTube-only Phase 1; reads published_data JSONB
    from app.services.highlight_video_service import get_published_highlight_video as _get_hv
    highlight_video = _get_hv(_profile_draft)

    # Profile grid slots — published slot modules for the 5-column layout
    from app.services.profile_grid_service import build_published_grid_state as _build_pub_grid
    profile_grid_slots = _build_pub_grid(_profile_draft)

    return templates.TemplateResponse(request, "public/player_profile.html", {
        "profile_user":    user,
        "lfa_license":     lfa_license,
        "current_user":    current_user,
        "overall":         overall,
        "tier_label":      tier_label,
        "tier_color":      tier_color,
        "position":        position,
        "skills_data":     skills_data,
        "skill_categories": SKILL_CATEGORIES,
        "recent_events":   recent_events,
        "friendship_panel": friendship_panel,
        "initials":        initials,
        "photo_url":       lfa_license.player_card_photo_url,
        "card_variant_id":  card_variant_id,
        "card_native_w":    card_native_w,
        "card_native_h":    card_native_h,
        "card_orientation": card_orientation,
        "card_platform_id": card_platform_id,
        "card_url":         card_url,
        "highlight_video":    highlight_video,
        "profile_grid_slots": profile_grid_slots,
    })


@router.get("/players/{user_id}/card", response_class=HTMLResponse)
def public_player_card(
    request: Request,
    user_id: int,
    preview: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    theme: Optional[str] = Query(None),
    export: Optional[bool] = Query(default=False),
    animated: Optional[bool] = Query(default=False),
    native_export: Optional[bool] = Query(default=False),
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
    _tournament_delta = (
        _last_part.skill_rating_delta
        if _last_part and isinstance(_last_part.skill_rating_delta, dict)
        else {}
    )

    # Merge VT/training deltas as fallback where tournament delta is absent.
    # Tournament delta takes priority; VT delta only fills gaps.
    # Threshold: abs(delta) < 0.005 → no trend arrow shown.
    from app.services.segment_reward_service import get_training_skill_deltas_for_user as _get_vt_deltas
    _vt_deltas = _get_vt_deltas(db, user_id)
    _VT_ARROW_THRESHOLD = 0.005
    last_skill_delta: dict = dict(_tournament_delta)
    for _sk, _vt_d in _vt_deltas.items():
        if _sk not in last_skill_delta and abs(_vt_d) >= _VT_ARROW_THRESHOLD:
            last_skill_delta[_sk] = _vt_d

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
    player_positions: list[str] = []   # [primary, ...secondaries], empty if not onboarded
    ms = lfa_license.motivation_scores
    if ms and isinstance(ms, dict):
        position = ms.get("position", "Unknown")
        _raw_positions = ms.get("positions", [])
        if _raw_positions and isinstance(_raw_positions, list):
            player_positions = _raw_positions
        elif position != "Unknown":
            player_positions = [position]

    # Pitch display nodes for the position panel (FClassic card lower-right section).
    from app.utils.football_positions import (
        get_pitch_display_nodes as _get_pitch_nodes,
        position_label as _position_label,
    )
    position_nodes = _get_pitch_nodes(position, player_positions) if position != "Unknown" else []
    primary_pos_label = _position_label(position) if position != "Unknown" else None
    secondary_pos_labels = [_position_label(p) for p in player_positions if p != position]

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

    # ── CardDraft (hoisted) ───────────────────────────────────────────────────
    # Must be resolved before the early return so the public profile can read
    # the authoritative published_platform from card_drafts (written by publish_draft).
    # UserLicense.published_card_platform is kept as a legacy fallback for users
    # who existed before the Phase 4D-1 migration (card_drafts row absent / platform NULL).
    from app.services.card_draft_service import CardDraftService as _CardDraftService
    _card_draft = _CardDraftService.get_player_card_draft(db, user_id=lfa_license.user_id)

    # Bare URL (/players/{id}/card with no params) falls through to the full
    # interactive FClassic card render below.  The export portrait iframe wrapper
    # (player_card_public.html) has been retired from this route: the
    # interactive card already provides a complete, branded, responsive page.

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
    # Reads the PUBLISHED snapshot from card_drafts (primary source after 4D-2).
    # Falls back to UserLicense.published_card_* for users who have never visited
    # the editor after the Phase 4D-1 migration (card_drafts row absent).
    # _card_draft is fetched above (hoisted before the teams query).
    from app.services.card_theme_service import get_theme as _get_theme, get_all_themes as _get_all_themes
    from app.services.card_variant_service import get_variant as _get_variant

    card_theme_id = (
        _card_draft.published_theme
        or lfa_license.published_card_theme
        or "default"
    )
    # Editor preview override: ?theme= reflects the draft theme for live preview.
    # Mirrors the ?preview= pattern used for variants.
    if theme:
        _valid_ids = {t.id for t in _get_all_themes(db=db)}
        if theme in _valid_ids:
            card_theme_id = theme
    theme = _get_theme(card_theme_id, db=db)  # falls back to "default" for unknown IDs

    # Variant: ?preview= overrides published value (preview only, not persisted).
    # Validation uses _get_design(preview, db) so DB-backed manifest designs are
    # accepted in addition to designs in the static DESIGNS fallback dict.
    card_variant_id = (
        _card_draft.published_variant
        or lfa_license.published_card_variant
        or "fclassic"
    )
    if preview:
        _preview_def = _get_design(preview, db)
        if _preview_def.id == preview:
            card_variant_id = preview
    variant = _get_variant(card_variant_id)  # falls back to "fclassic" for unknown IDs

    # Template selection: use variant.template if the file exists.
    # If the selected variant has no template yet (not yet implemented), fall back
    # to the explicit fclassic template, then to the legacy fallback.
    # Log a warning so missing templates are never silently hidden.
    template_path = _FALLBACK_TEMPLATE
    candidate = os.path.join(_TEMPLATES_DIR, variant.template)
    if os.path.isfile(candidate):
        template_path = variant.template
    else:
        if card_variant_id != "fclassic":
            logger.warning(
                "card variant template missing — rendering fclassic fallback",
                extra={"card_variant_id": card_variant_id, "expected_template": variant.template},
            )
        fclassic_candidate = os.path.join(_TEMPLATES_DIR, "public/player_card_fclassic.html")
        if os.path.isfile(fclassic_candidate):
            template_path = "public/player_card_fclassic.html"

    # Photo URL resolution per variant family:
    #   FClassic/compact → portrait crop (falls back to original uncropped)
    #   showcase     → landscape crop (falls back to original uncropped)
    _orig_url      = lfa_license.player_card_photo_url
    _portrait_url  = lfa_license.card_photo_portrait_url or _orig_url
    _landscape_url = lfa_license.card_photo_landscape_url or _orig_url

    # ── Platform preset resolution ────────────────────────────────────────────
    # Precedence: URL ?platform= param > published_card_platform > default.
    # published_card_platform is only inherited when an explicit export/preview
    # is requested (?export=1 or ?preview=).  Bare URL and ?native_export=1
    # resolve to None → "default" preset → export layer stays inactive so the
    # interactive FClassic card is served (not a Level-C export template).
    from app.services.card_platform_service import get_preset as _get_preset
    _published_platform = _card_draft.published_platform or lfa_license.published_card_platform
    effective_platform = platform or (
        _published_platform
        if bool(export)
        else None
    )
    platform_preset = _get_preset(effective_platform)

    # ── Export render layer ──────────────────────────────────────────────────
    # Routing priority (CS-4c + PORT-v2):
    #   For buckets in _LEVEL_C_PRIORITY_BUCKETS (currently "portrait"):
    #     1. File-based Level C wins if the file exists.
    #     2. Driver routing fallback for manifest-only designs (no Level C file).
    #   For all other buckets (original CS-4c priority):
    #     1. Driver routing: design has component_config for this bucket AND bucket is
    #        in _ARCHETYPE_DRIVERS → use shared/drivers/<driver>.html + inject config.
    #     2. File-based Level C: public/export/{bucket}/{design_id}.html exists → use it.
    #   3. Fallback: unchanged template_path (editor template + export-mode class).
    # _driver_config is always populated when a config is available so that Level C
    # templates can read component flags (show_position_map, skill_slice, etc.).
    # Covers both export=True (Playwright PNG/video) and browser-preview (export=False).
    # Semantic 422 validation (supported_export_buckets) happens at the export endpoint.
    _driver_config = None
    if platform_preset.id in _EXPORT_FORMAT_BUCKETS:
        _fmt = _EXPORT_FORMAT_BUCKETS[platform_preset.id]
        _design_def = _get_design(card_variant_id, db)
        _bucket_cfg = _design_def.component_config.get(_fmt)
        _archetype  = _design_def.archetype_id or ""
        _driver_tpl = _ARCHETYPE_DRIVERS.get(_archetype, {}).get(_fmt)
        _level_c_tpl = f"public/export/{_fmt}/{_resolve_design_id(card_variant_id)}.html"
        _has_level_c = os.path.isfile(os.path.join(_TEMPLATES_DIR, _level_c_tpl))
        if _fmt in _LEVEL_C_PRIORITY_BUCKETS and _has_level_c:
            # Level C file wins for PORT-v2 priority buckets (portrait).
            template_path = _level_c_tpl
            _driver_config = _bucket_cfg or {}
        elif _bucket_cfg and _driver_tpl:
            template_path = f"public/export/shared/drivers/{_driver_tpl}"
            _driver_config = _bucket_cfg
        elif _has_level_c:
            template_path = _level_c_tpl
            _driver_config = _bucket_cfg or {}

    # animated_mode: True only when both export=1 AND animated=1 are present.
    # The PNG endpoint never passes animated=1 → this is always False for PNG renders.
    animated_mode = bool(export) and bool(animated)
    native_export_mode = bool(native_export)

    _card_resp = templates.TemplateResponse(request, template_path, {
        "player": player,
        "overall": overall,
        "tier_label": tier_label,
        "tier_color": tier_color,
        "avatar_bg": avatar_bg,
        "initials": initials,
        "pos_color": _POS_COLORS.get(position, "#667eea"),
        "skill_categories": SKILL_CATEGORIES,
        "teams_info": teams_info,
        "animated_mode": animated_mode,
        # photo_url kept for FClassic (original, uncropped)
        "photo_url": _orig_url,
        "portrait_photo_url": _portrait_url,   # compact / compact_bg
        "landscape_photo_url": _landscape_url, # showcase / showcase_bg
        "last_skill_delta": last_skill_delta,
        "participations_history": participations_history,
        "theme": theme,
        "card_theme_id": theme.id,
        "card_theme": theme.id,           # base template: <body class="theme-{{ card_theme }}">
        "card_variant_id": variant.id,
        "platform_class": platform_preset.css_class,
        "platform_id":    platform_preset.id,
        "export_mode":        bool(export),
        "native_export_mode": native_export_mode,
        # variant-specific context
        "compact_bg_url": lfa_license.card_bg_compact_url,
        "showcase_bg_url": lfa_license.card_bg_showcase_url,
        "sponsor_logo_url": lfa_license.sponsor_logo_url,
        "app_logo_url":     None,  # Default FClassic card shows no LFA app logo; sponsor_logo_url is the card's logo source
        "compact_photo_position": lfa_license.card_compact_photo_position or "left",
        # Focus points default to match original CSS (compact: center bottom = 50/100, showcase: center = 50/50)
        "compact_focus_x": lfa_license.card_compact_focus_x if lfa_license.card_compact_focus_x is not None else 50,
        "compact_focus_y": lfa_license.card_compact_focus_y if lfa_license.card_compact_focus_y is not None else 100,
        "showcase_focus_x": lfa_license.card_showcase_focus_x if lfa_license.card_showcase_focus_x is not None else 50,
        "showcase_focus_y": lfa_license.card_showcase_focus_y if lfa_license.card_showcase_focus_y is not None else 50,
        # Atlas Profile tab context
        "player_nickname":       user.nickname,
        "player_age":            user.age,
        "player_gender":         user.gender,
        "player_location":       user.current_location or user.country,
        "license_current_level": lfa_license.current_level,
        "license_max_level":     lfa_license.max_achieved_level,
        "license_started":       lfa_license.started_at.strftime("%Y. %b. %d.") if lfa_license.started_at else None,
        "motivation_score":      lfa_license.average_motivation_score,
        "member_since":          user.created_at.strftime("%Y. %B") if user.created_at else None,
        "xp_balance":            user.xp_balance,
        "player_height_cm":      (lfa_license.motivation_scores or {}).get("height_cm"),
        "player_weight_kg":      (lfa_license.motivation_scores or {}).get("weight_kg"),
        "player_preferred_foot": (lfa_license.motivation_scores or {}).get("preferred_foot"),
        "dominant_badge":        calculate_dominant_badge(
            lfa_license.right_foot_score,
            lfa_license.left_foot_score,
        ),
        # Position panel (FClassic Default lower-right section)
        "player_positions":     player_positions,
        "position_nodes":       position_nodes,
        "primary_pos_label":    primary_pos_label,
        "secondary_pos_labels": secondary_pos_labels,
        # CS-4c: populated when driver routing is active; None for file-based routes
        "_driver_config": _driver_config,
    })
    _card_resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    _card_resp.headers["Pragma"]        = "no-cache"
    _card_resp.headers["Expires"]       = "0"
    return _card_resp


# ── Export endpoint ───────────────────────────────────────────────────────────

@router.get("/players/{user_id}/card/export")
async def export_player_card(
    request: Request,
    user_id: int,
    platform: str = Query("instagram_square"),
    theme: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Export a player card as a PNG at a social-media canvas size.

    Auth: authenticated users may only export their own card.
    Admins may export any player's card.
    Rate limit: 5 exports per 60 s per user+IP.
    """
    from app.config import settings

    # Ownership check
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="You can only export your own card")

    # Platform validation — only registered canvas sizes are accepted
    if platform not in _export_svc.CANVAS_SIZES:
        valid = list(_export_svc.CANVAS_SIZES)
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported export platform: {platform!r}. Valid values: {valid}",
        )

    # Rate limit: 5 exports / 60 s per (user_id, client_ip)
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{current_user.id}:{client_ip}"
    if not _export_svc.check_export_rate_limit(rate_key):
        raise HTTPException(
            status_code=429,
            detail="Export rate limit exceeded (5 per minute). Please wait before exporting again.",
        )

    # Validate target user + active LFA Player license
    target_user = db.query(User).filter(
        User.id == user_id, User.is_active == True
    ).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Player not found")

    target_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not target_license:
        raise HTTPException(status_code=404, detail="No active LFA Player license")

    # Resolve the variant that will actually be rendered — must match what the
    # render route uses (card_draft.published_variant, Phase 4D-2 primary source).
    # Falls back to UserLicense.card_variant for users without a draft row.
    from app.services.card_draft_service import CardDraftService as _CDS_export
    _export_draft = _CDS_export.get_player_card_draft(db, user_id)
    card_variant_id = (
        _export_draft.published_variant
        or target_license.card_variant
        or "fclassic"
    )

    # Design ownership guard — all designs require entitlement, including fclassic (legacy inputs sanitized via resolve_design_id).
    # Admin bypass: admins may export any card regardless of ownership.
    if current_user.role != UserRole.ADMIN:
        from app.services.card_design_service import is_design_accessible as _is_accessible
        if not _is_accessible(db, current_user.id, "player_card", card_variant_id):
            raise HTTPException(
                status_code=403,
                detail=f"Design {card_variant_id!r} not owned. Get it at /my-cards/shop",
            )

    if platform != "default":
        _bucket = _EXPORT_FORMAT_BUCKETS[platform]  # safe: CANVAS_SIZES invariant guarantees coverage
        _supported = _get_supported_buckets(card_variant_id, db)
        if _bucket not in _supported:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Design {card_variant_id!r} does not support export to {platform!r} "
                    f"(bucket={_bucket!r}). Supported buckets: {list(_supported)}"
                ),
            )

    # Theme validation: mirror the render-route's logic — accept only known IDs.
    # Invalid or unknown IDs are silently dropped so the export falls back to the
    # published theme (same semantics as the preview route).
    _theme_qs = ""
    if theme:
        from app.services.card_theme_service import get_all_themes as _get_all_themes
        _valid_theme_ids = {t.id for t in _get_all_themes(db=db)}
        if theme not in _valid_theme_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown theme {theme!r}. Valid values: {sorted(_valid_theme_ids)}",
            )
        _theme_qs = f"&theme={theme}"

    # Render URL — constructed server-side only; no user-controlled string.
    # "default" platform: use ?native_export=1 so the template applies
    # native-export-mode CSS (card fills 820px width at natural auto height).
    # All other platforms: standard export render with ?platform=…&export=1.
    _base = f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}/players/{user_id}/card"
    if platform == "default":
        render_url = f"{_base}?native_export=1{_theme_qs}"
    else:
        render_url = f"{_base}?platform={platform}&export=1{_theme_qs}"

    # Screenshot runs in a thread so it does not block the event loop
    try:
        png_bytes = await asyncio.to_thread(
            _export_svc._sync_take_screenshot, render_url, platform
        )
    except _export_svc.CardExportTimeoutError:
        raise HTTPException(status_code=504, detail="Card render timed out")

    filename = f"lfa_card_{user_id}_{platform}.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Export-Platform": platform,
        },
    )


# ── Animated video export endpoint ───────────────────────────────────────────

_SUPPORTED_VIDEO_FORMATS   = {"webm", "mp4"}
_SUPPORTED_VIDEO_DURATIONS = {5}


@router.get("/players/{user_id}/card/export/video")
async def export_player_card_video(
    request: Request,
    user_id: int,
    platform: str = Query("instagram_square"),
    format: str = Query("webm"),
    duration: int = Query(5),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Export an animated player card as a video file.

    Only available for (variant, platform) pairs in ANIMATED_EXPORT_CAPABLE.

    Supported formats:
      - webm: raw Playwright CDP screencast output (~25 fps, VP8/VP9). Works on
              Chrome and Android; NOT supported on iOS or Instagram upload.
      - mp4:  WebM post-processed via FFmpeg (libx264, CRF 22, yuv420p,
              movflags+faststart, silent AAC). Requires ffmpeg binary on server.
              If FFmpeg fails the response falls back to WebM with header
              X-Export-Fallback: ffmpeg-failed.

    Auth: authenticated users may only export their own card.
    Admins may export any player's card.
    Rate limit: 2 video exports per 60 s per user+IP (separate from PNG limit).
    """
    from app.config import settings

    # Ownership check
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="You can only export your own card")

    # MVP format + duration validation
    if format not in _SUPPORTED_VIDEO_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported video format: {format!r}. Supported: {sorted(_SUPPORTED_VIDEO_FORMATS)}",
        )
    if duration not in _SUPPORTED_VIDEO_DURATIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported duration: {duration}. Supported: {sorted(_SUPPORTED_VIDEO_DURATIONS)}",
        )

    # Platform whitelist — "default" has no canvas size and is not an export target
    if platform not in _export_svc.CANVAS_SIZES:
        valid = list(_export_svc.CANVAS_SIZES)
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported export platform: {platform!r}. Valid values: {valid}",
        )

    # Validate target user + active LFA Player license
    target_user = db.query(User).filter(
        User.id == user_id, User.is_active == True
    ).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="Player not found")

    target_license = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not target_license:
        raise HTTPException(status_code=404, detail="No active LFA Player license")

    # Animated capability check — variant comes from DB, never from URL
    card_variant_id = target_license.card_variant or "fclassic"

    # Design ownership guard — same rules as PNG export.
    if current_user.role != UserRole.ADMIN:
        from app.services.card_design_service import is_design_accessible as _is_accessible
        if not _is_accessible(db, current_user.id, "player_card", card_variant_id):
            raise HTTPException(
                status_code=403,
                detail=f"Design {card_variant_id!r} not owned. Get it at /my-cards/shop",
            )

    if not _export_svc.is_animated_capable(card_variant_id, platform):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Animated video export is not available for variant={card_variant_id!r} "
                f"and platform={platform!r}."
            ),
        )

    # Validate design export support using supported_export_buckets.
    _bucket = _EXPORT_FORMAT_BUCKETS.get(platform)
    if _bucket is None:
        raise HTTPException(
            status_code=422,
            detail=f"Platform {platform!r} is not an export platform.",
        )
    _supported = _get_supported_buckets(card_variant_id, db)
    if _bucket not in _supported:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Design {card_variant_id!r} does not support video export to {platform!r} "
                f"(bucket={_bucket!r}). Supported buckets: {list(_supported)}"
            ),
        )

    # Rate limit: 2 video exports / 60 s per (user_id, client_ip)
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{current_user.id}:{client_ip}"
    if not _export_svc.check_video_rate_limit(rate_key):
        raise HTTPException(
            status_code=429,
            detail="Video export rate limit exceeded (2 per minute). Please wait before exporting again.",
        )

    # Render URL — animated=1 activates the animation CSS block in the template.
    # The PNG endpoint never includes animated=1, so static export is unaffected.
    render_url = (
        f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
        f"/players/{user_id}/card?platform={platform}&export=1&animated=1"
    )

    # Video recording runs in a thread so it does not block the event loop
    try:
        webm_bytes = await asyncio.to_thread(
            _export_svc._sync_record_video, render_url, platform, duration
        )
    except _export_svc.CardVideoRecordError:
        raise HTTPException(status_code=504, detail="Card video render timed out or failed")

    # MP4 post-processing: WebM → FFmpeg → MP4 when format=mp4.
    # On CardMp4ConvertError (missing binary or encode failure) we fall back to
    # WebM and set X-Export-Fallback so clients can detect the degradation.
    output_bytes      = webm_bytes
    output_format     = "webm"
    output_media_type = "video/webm"
    fallback_headers: dict[str, str] = {}

    if format == "mp4":
        try:
            output_bytes      = await asyncio.to_thread(_export_svc._webm_to_mp4, webm_bytes)
            output_format     = "mp4"
            output_media_type = "video/mp4"
        except _export_svc.CardMp4ConvertError as exc:
            logger.warning("MP4 conversion failed, falling back to WebM: %s", exc)
            fallback_headers = {"X-Export-Fallback": "ffmpeg-failed"}

    filename = f"lfa_card_{user_id}_{platform}_animated.{output_format}"
    return Response(
        content=output_bytes,
        media_type=output_media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Export-Platform": platform,
            "X-Export-Format": output_format,
            "X-Export-Duration": str(duration),
            **fallback_headers,
        },
    )

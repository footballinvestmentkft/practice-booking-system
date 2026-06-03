"""Card Studio unified shell routes (CS-S0 / CS-S2A / CS-S2B / CS-S4A / CS-S4B).

Single unified studio shell with card-type switcher.
CS-S0 MVP: Welcome Card mode fully functional.
CS-S2A: Player Card mode — preview-only shell.
CS-S2B: Player Card mode — variant/platform/theme selector (write via existing endpoints).
CS-S4A: Challenge Card mode — static placeholder (superseded by CS-S4B).
CS-S4B: Challenge Card mode — challenge selector + phase/platform selector + live preview iframe.

Canonical routes (no new routes in CS-S4B — query param extension):
  GET /card-studio              → shell (Welcome default)
  GET /card-studio/welcome      → shell, Welcome mode
  GET /card-studio/welcome?format=X → shell, Welcome mode, specific format
  GET /card-studio/player       → shell, Player mode (CS-S2A+S2B)
  GET /card-studio/challenge    → shell, Challenge selector list
  GET /card-studio/challenge?challenge_id={id}
    → auto-redirect to first unlocked phase + default platform
  GET /card-studio/challenge?challenge_id={id}&phase={phase}&platform={platform}
    → Challenge preview with iframe

Backward-compat routes remain in card_editor.py unchanged.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.license import UserLicense
from ...models.user import User
from ...services.card_design_service import (
    CHALLENGE_CARD_FORMATS,
    WELCOME_CARD_FORMATS,
    get_card_family,
    get_owned_design_ids,
    is_design_accessible,
)
from ...services.mood_photo_service import get_mood_photos_for_user
from ...services.card_theme_service import (
    get_all_themes as _get_all_themes,
    is_unlocked as _is_theme_unlocked,
    THEME_ORDER as _THEME_ORDER,
    THEMES as _THEMES,
)
from ...services.card_variant_service import VARIANTS as _VARIANTS
from ...services.card_platform_service import PLATFORM_PRESETS as _PLATFORM_PRESETS
from ...services.card_draft_service import CardDraftService as _CardDraftService
from ...models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from ...models.virtual_training import VirtualTrainingAttempt
from .card_editor import (
    _WC_FORMAT_BY_ID,
    _WC_RATIO,
    _WC_VALID_IDS,
    _CC_VALID_IDS,
    _MOOD_SLOT_META,
)
# CS-S4B: Challenge phase helpers (pure functions — no DB session required)
from .vt_challenges import (
    get_unlocked_challenge_card_phases as _get_unlocked_phases,
    get_locked_challenge_card_phases   as _get_locked_phases,
    _EXPORTABLE_PHASES                 as _CC_EXPORTABLE_PHASES,
    _PHASE_MOOD_MAP,
    _winner_ctx,
)
from ...models.user_mood_photos import MOOD_PHOTO_SLOTS

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["card-studio"])

# ── Helpers ───────────────────────────────────────────────────────────────────

_STUDIO_NAV_CTX = {
    "spec_dashboard_url":  "/dashboard/lfa-football-player",
    "spec_dashboard_icon": "⚽",
    "spec_profile_url":    "/profile/lfa-football-player",
    "spec_profile_icon":   "🪪",
}

_WELCOME_FORMATS_ORDERED = WELCOME_CARD_FORMATS  # 7 formats, canonical order


def _license_guard(db: Session, user_id: int):
    """Return active LFA license or None."""
    return db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()


def _resolve_welcome_context(db: Session, user, format_id: str | None):
    """
    Resolve all context needed for Welcome mode in the unified shell.
    Returns (context_dict, redirect_url_or_None).
    redirect_url: caller must issue 303 redirect if set.
    """
    lic = _license_guard(db, user.id)
    if not lic:
        return None, "/dashboard?info=complete_lfa_onboarding_first"
    if not lic.onboarding_completed:
        return None, "/specialization/lfa-player/onboarding"

    owned_set = set(get_owned_design_ids(db, user.id, "welcome_card")) & _WC_VALID_IDS
    owned_formats_ordered = [f for f in _WELCOME_FORMATS_ORDERED if f.design_id in owned_set]
    if not owned_formats_ordered:
        return None, "/shop?type=welcome_card"

    first_owned_id = owned_formats_ordered[0].design_id

    if format_id is None or format_id not in owned_set:
        return None, f"/card-studio/welcome?format={first_owned_id}"

    fmt = _WC_FORMAT_BY_ID[format_id]
    ratio_class = _WC_RATIO.get(fmt.preview_platform, "mfg-ratio-11")

    # CS-COLOR-1A: read active theme from Welcome Card draft, default to "default"
    welcome_draft = _CardDraftService.get_draft(db, user.id, "welcome_card")
    active_theme  = welcome_draft.draft_theme or "default"

    # CS-COLOR-1A: free themes only (no shop/unlock scope in COLOR-1)
    card_themes = [t for t in _get_all_themes(db) if not t.is_premium]

    preview_url = f"/profile/onboarding-card?platform={fmt.preview_platform}&theme={active_theme}"
    export_url  = f"/profile/onboarding-card/export?platform={fmt.preview_platform}&theme={active_theme}"

    owned_format_rows = [
        {
            "design_id":   f.design_id,
            "label":       f.label,
            "style_tag":   f.style_tag,
            "dims":        f.dims,
            "preview_url": f"/profile/onboarding-card?platform={f.preview_platform}&theme={active_theme}",
            "active":      f.design_id == format_id,
        }
        for f in owned_formats_ordered
    ]

    mood_photos = get_mood_photos_for_user(user.id, db)

    ctx = {
        "active_type":        "welcome",
        "active_format":      format_id,
        "active_theme":       active_theme,
        "card_themes":        card_themes,
        "fmt":                fmt,
        "ratio_class":        ratio_class,
        "preview_url":        preview_url,
        "export_url":         export_url,
        "owned_format_rows":  owned_format_rows,
        "wc_photo_url":           lic.wc_photo_url,
        "wc_photo_portrait_url":  lic.wc_photo_portrait_url,
        "wc_photo_landscape_url": lic.wc_photo_landscape_url,
        "mood_photos":            mood_photos,
        "mood_slot_meta":         _MOOD_SLOT_META,
        **_STUDIO_NAV_CTX,
    }
    return ctx, None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/card-studio", response_class=HTMLResponse)
async def card_studio_default(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Card Studio landing — defaults to Welcome mode for CS-S0.

    Renders the unified shell with Welcome Card active.
    Player and Challenge shown as Coming Soon.
    """
    # CS-S0: default to Welcome — redirect to canonical with first owned format
    lic = _license_guard(db, user.id)
    if not lic or not lic.onboarding_completed:
        target = "/dashboard?info=complete_lfa_onboarding_first" if not lic else "/specialization/lfa-player/onboarding"
        return RedirectResponse(url=target, status_code=303)

    owned_set = set(get_owned_design_ids(db, user.id, "welcome_card")) & _WC_VALID_IDS
    if owned_set:
        first = next((f.design_id for f in _WELCOME_FORMATS_ORDERED if f.design_id in owned_set), None)
        if first:
            return RedirectResponse(url=f"/card-studio/welcome?format={first}", status_code=303)

    return RedirectResponse(url="/shop?type=welcome_card", status_code=303)


@router.get("/card-studio/welcome", response_class=HTMLResponse)
async def card_studio_welcome(
    request:   Request,
    format_id: str | None = Query(default=None, alias="format"),
    db:        Session    = Depends(get_db),
    user:      User       = Depends(get_current_user_web),
):
    """Unified Studio shell — Welcome Card mode (CS-S0 fully functional)."""
    ctx, redirect = _resolve_welcome_context(db, user, format_id)
    if redirect:
        return RedirectResponse(url=redirect, status_code=303)

    _cc_owned = any(is_design_accessible(db, user.id, "challenge_card", pid)
                    for pid in _CC_VALID_PLATFORMS)
    return templates.TemplateResponse(
        "card_studio_shell.html",
        {"request": request, "user": user, "cc_owned": _cc_owned, **ctx},
    )


# ── CS-S2A+S2B: Player Card mode ─────────────────────────────────────────────

def _resolve_player_context(db: Session, user):
    """Build context for Player Card shell (CS-S2A preview + CS-S2B write selectors).

    Guards: LFA license + onboarding complete.
    Reads CardDraft(player_card) for active variant/theme/platform.
    CS-S2B: passes variant/platform/theme lists for selector UI.
    Write is handled by existing dashboard endpoints via AJAX (no new routes).
    """
    lic = _license_guard(db, user.id)
    if not lic:
        return None, "/dashboard?info=complete_lfa_onboarding_first"
    if not lic.onboarding_completed:
        return None, "/specialization/lfa-player/onboarding"

    try:
        draft = _CardDraftService.get_draft(db, user.id, "player_card")
        active_variant  = draft.draft_variant  or "fclassic"
        active_theme    = draft.draft_theme    or "default"
        active_platform = draft.draft_platform or "default"
    except Exception:
        active_variant  = "fclassic"
        active_theme    = "default"
        active_platform = "default"

    # CS-S2B: variant selector — owned designs for player_card
    owned_pc_ids = set(get_owned_design_ids(db, user.id, "player_card"))
    player_variants = [
        {
            "id":     vid,
            "label":  v.label,
            "active": vid == active_variant,
            "owned":  vid in owned_pc_ids,
        }
        for vid, v in sorted(_VARIANTS.items(), key=lambda x: x[1].sort_order)
        if v.available
    ]

    # CS-S2B: platform selector — all valid platforms
    player_platforms = [
        {
            "id":     pid,
            "label":  p.label,
            "active": pid == active_platform,
        }
        for pid, p in _PLATFORM_PRESETS.items()
    ]

    # CS-S2B: theme selector — all themes with unlock status
    all_themes = _get_all_themes(db)
    player_themes = [
        {
            "id":         t.id,
            "label":      t.label,
            "dot_color":  t.dot_color,
            "is_premium": t.is_premium,
            "active":     t.id == active_theme,
            "unlocked":   not t.is_premium or _is_theme_unlocked(lic, t.id),
        }
        for t in all_themes
    ]

    preview_url = (
        f"/players/{user.id}/card"
        f"?preview={active_variant}&theme={active_theme}&native_export=1"
    )

    ctx = {
        "active_type":      "player",
        "active_variant":   active_variant,
        "active_theme":     active_theme,
        "active_platform":  active_platform,
        "preview_url":      preview_url,
        "player_user_id":   user.id,
        "player_variants":  player_variants,
        "player_platforms": player_platforms,
        "player_themes":    player_themes,
        "legacy_editor_url": "/card-editor/player",
        **_STUDIO_NAV_CTX,
    }
    return ctx, None


@router.get("/card-studio/player", response_class=HTMLResponse)
async def card_studio_player(
    request: Request,
    db:      Session = Depends(get_db),
    user:    User    = Depends(get_current_user_web),
):
    """CS-S2A+S2B: Player Card Studio — preview + variant/platform/theme selectors.

    Write: variant/platform/theme via existing dashboard endpoints (no new routes).
    Photo upload (CS-S2C) and publish (CS-S2D) remain deferred.
    Legacy editor CTA links to /card-editor/player for full write access.
    """
    ctx, redirect = _resolve_player_context(db, user)
    if redirect:
        return RedirectResponse(url=redirect, status_code=303)

    _cc_owned = any(is_design_accessible(db, user.id, "challenge_card", pid)
                    for pid in _CC_VALID_PLATFORMS)
    return templates.TemplateResponse(
        "card_studio_shell.html",
        {"request": request, "user": user, "cc_owned": _cc_owned, **ctx},
    )


# ── CS-S4B: Challenge Card mode (selector + phase + live preview) ─────────────

_CC_FORMATS_ORDERED = CHALLENGE_CARD_FORMATS  # 2 formats: 16:9 post + 9:16 story

_CC_VALID_PLATFORMS = ("challenge_post_16_9", "challenge_story_9_16")

_CC_RATIO = {
    "challenge_post_16_9":  "mfg-ratio-169",
    "challenge_story_9_16": "mfg-ratio-916",
}

_CC_PLATFORM_LABELS = {
    "challenge_post_16_9":  "Post (16:9)",
    "challenge_story_9_16": "Story (9:16)",
}

# Phase labels (mirrors _PHASE_LABELS in vt_challenges.py — kept local for module independence)
_CC_PHASE_LABELS = {
    "challenge_sent":         "Challenge Sent",
    "challenge_received":     "Challenge Invitation",
    "challenge_accepted":     "Challenge Accepted",
    "waiting_for_opponent":   "Waiting for Opponent",
    "live_lobby_ready":       "Live Lobby",
    "live_in_progress":       "Live — In Progress",
    "completed_score_win":    "Result — Score",
    "completed_draw":         "Result — Draw",
    "completed_forfeit_win":  "Result — Forfeit Win",
    "completed_forfeit_loss": "Result — Forfeit Loss",
    "no_contest":             "No Contest",
    "skill_delta_result":     "Skill Progress",
    "challenge_cancelled":    "Challenge Cancelled",
    "challenge_declined":     "Challenge Declined",
}

# CS-S4B-FIX3: Studio event labels decouple display name from phase_id.
# challenge_received represents the same event as challenge_sent — the invitation
# was sent by the challenger and received by the challenged. Both viewers should
# see "Challenge Sent" as the first timeline event; the sublabel clarifies direction.
_CC_PHASE_EVENT_LABELS: dict[str, str] = {
    "challenge_sent":         "Challenge Sent",
    "challenge_received":     "Challenge Sent",   # same event, challenged perspective
    "challenge_accepted":     "Challenge Accepted",
    "waiting_for_opponent":   "Waiting for Opponent",
    "live_lobby_ready":       "Live Lobby",
    "live_in_progress":       "Live — In Progress",
    "completed_score_win":    "Result — Score",
    "completed_draw":         "Result — Draw",
    "completed_forfeit_win":  "Result — Forfeit Win",
    "completed_forfeit_loss": "Result — Forfeit Loss",
    "no_contest":             "No Contest",
    "skill_delta_result":     "Skill Progress",
    "challenge_cancelled":    "Challenge Cancelled",
    "challenge_declined":     "Challenge Declined",
}

# Viewer-role sublabels shown under the event label
_CC_PHASE_SUBLABELS: dict[str, str] = {
    "challenge_sent":     "sent by you",
    "challenge_received": "sent to you",
}

# CS-S4B-FIX: Chronological timeline order for phase chips.
# Phases with the same order value are peers (e.g. sent/received are the same event
# from two viewer perspectives; score_win/draw/forfeit are mutually exclusive outcomes).
_CC_PHASE_TIMELINE_ORDER: dict[str, int] = {
    "challenge_sent":         1,
    "challenge_received":     1,
    "challenge_accepted":     2,
    "live_lobby_ready":       3,
    "live_in_progress":       4,
    "waiting_for_opponent":   4,
    "completed_score_win":    5,
    "completed_draw":         5,
    "completed_forfeit_win":  5,
    "completed_forfeit_loss": 5,
    "no_contest":             5,
    "skill_delta_result":     6,
    # Terminal rejection phases — timeline position 2 (after sent, before any result)
    "challenge_cancelled":    2,
    "challenge_declined":     2,
}

_CC_ACTIVE_STATUSES = frozenset({
    ChallengeStatus.PENDING,
    ChallengeStatus.ACCEPTED,
    ChallengeStatus.LIVE_LOBBY,
    ChallengeStatus.LIVE_IN_PROGRESS,
})

_CC_FILTER_STATUSES = {
    "active":    list(_CC_ACTIVE_STATUSES),
    "completed": [ChallengeStatus.COMPLETED, ChallengeStatus.EXPIRED,
                  ChallengeStatus.CANCELLED, ChallengeStatus.DECLINED],
    "all":       None,  # None means no status filter
}

_CC_MAX_LIST = 60  # max challenges shown in selector

# Statuses where get_locked_challenge_card_phases() returns [] but the initial
# challenge_sent/received event still happened and is previewable as a locked phase.
# CANCELLED and DECLINED now have their own unlocked phases (challenge_cancelled /
# challenge_declined) so they no longer need the implicit-initial workaround.
_CC_STATUSES_WITH_IMPLICIT_INITIAL: frozenset = frozenset({
    ChallengeStatus.EXPIRED,
})


def _cc_display_name(user_obj) -> str:
    if user_obj is None:
        return "Unknown"
    return user_obj.nickname if getattr(user_obj, "nickname", None) else (user_obj.email or "Unknown")


def _cc_build_challenge_row(ch, user_id: int, my_attempt) -> dict:
    """Build a selector tile row for one challenge."""
    is_challenger  = ch.challenger_id == user_id
    opponent       = ch.challenged if is_challenger else ch.challenger
    opp_attempt_id = ch.challenged_attempt_id if is_challenger else ch.challenger_attempt_id

    # Opponent score — the attempt objects aren't loaded here; use FK presence as proxy
    # (full attempt loading only when challenge is selected for preview)
    my_score  = float(my_attempt.score_normalized) if my_attempt and my_attempt.score_normalized is not None else None

    unlocked = _get_unlocked_phases(ch, user_id, my_attempt)

    # FIX: DECLINED/CANCELLED/EXPIRED always had a send/receive event — has_preview must
    # reflect that, even though get_unlocked_phases() returns [] for these statuses.
    has_preview = len(unlocked) > 0 or ch.status in _CC_STATUSES_WITH_IMPLICIT_INITIAL

    return {
        "id":                    ch.id,
        "opponent_name":         _cc_display_name(opponent),
        "game_name":             ch.game.name if ch.game else "—",
        "status":                ch.status.value,
        "challenge_mode":        ch.challenge_mode or "async",
        "is_challenger":         is_challenger,
        "created_at":            ch.created_at,
        "completed_at":          ch.completed_at,
        "my_score":              my_score,
        "available_phases_count": len(unlocked),
        "available_phases":      unlocked,
        "studio_url":            f"/card-studio/challenge?challenge_id={ch.id}",
        "has_preview":           has_preview,
    }


def _resolve_challenge_context(
    db: Session,
    user,
    challenge_id: int | None = None,
    phase: str | None = None,
    platform: str | None = None,
    filter_val: str = "all",
):
    """Build context for Challenge Card Studio shell (CS-S4B).

    Mode A — Selector (no challenge_id):
      Lists user's challenges with filter. challenge_mode="selector".

    Mode B — Preview (challenge_id provided):
      Loads challenge, computes phases, builds preview URL.
      challenge_mode="preview".

    Guards: LFA license + onboarding complete (no CC format ownership guard —
    users may preview any challenge regardless of format ownership).
    """
    lic = _license_guard(db, user.id)
    if not lic:
        return None, "/dashboard?info=complete_lfa_onboarding_first"
    if not lic.onboarding_completed:
        return None, "/specialization/lfa-player/onboarding"

    # ── Mode A: Challenge selector list ──────────────────────────────────────
    # CC-DESIGN-1: pass mood photo data for media panel in all modes
    mood_photos    = get_mood_photos_for_user(user.id, db)

    if challenge_id is None:
        filter_statuses = _CC_FILTER_STATUSES.get(filter_val)

        q = db.query(VirtualTrainingChallenge).filter(
            (VirtualTrainingChallenge.challenger_id == user.id) |
            (VirtualTrainingChallenge.challenged_id == user.id)
        )
        if filter_statuses is not None:
            q = q.filter(VirtualTrainingChallenge.status.in_(filter_statuses))
        challenges = (
            q.order_by(VirtualTrainingChallenge.created_at.desc())
            .limit(_CC_MAX_LIST)
            .all()
        )

        # Batch-load attempts for score display
        attempt_ids = set()
        for ch in challenges:
            is_c = ch.challenger_id == user.id
            aid  = ch.challenger_attempt_id if is_c else ch.challenged_attempt_id
            if aid:
                attempt_ids.add(aid)
        attempts_map: dict[int, VirtualTrainingAttempt] = {}
        if attempt_ids:
            for a in db.query(VirtualTrainingAttempt).filter(
                VirtualTrainingAttempt.id.in_(attempt_ids)
            ).all():
                attempts_map[a.id] = a

        rows = []
        for ch in challenges:
            is_c = ch.challenger_id == user.id
            aid  = ch.challenger_attempt_id if is_c else ch.challenged_attempt_id
            my_att = attempts_map.get(aid) if aid else None
            rows.append(_cc_build_challenge_row(ch, user.id, my_att))

        ctx = {
            "active_type":       "challenge",
            "challenge_mode":    "selector",
            "challenge_rows":    rows,
            "active_filter":     filter_val if filter_val in _CC_FILTER_STATUSES else "all",
            "preview_url":       None,
            "legacy_editor_url": "/card-editor/challenge",
            "mood_photos":       mood_photos,
            "mood_slot_meta":    _MOOD_SLOT_META,
            **_STUDIO_NAV_CTX,
        }
        return ctx, None

    # ── Mode B: Challenge preview ─────────────────────────────────────────────
    ch = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if ch is None:
        # Safe error state — show selector with error flag
        return {
            "active_type":       "challenge",
            "challenge_mode":    "error",
            "challenge_error":   "not_found",
            "challenge_rows":    [],
            "active_filter":     "all",
            "preview_url":       None,
            "legacy_editor_url": "/card-editor/challenge",
            "mood_photos":       mood_photos,
            "mood_slot_meta":    _MOOD_SLOT_META,
            **_STUDIO_NAV_CTX,
        }, None

    if user.id not in (ch.challenger_id, ch.challenged_id):
        return {
            "active_type":       "challenge",
            "challenge_mode":    "error",
            "challenge_error":   "not_participant",
            "challenge_rows":    [],
            "active_filter":     "all",
            "preview_url":       None,
            "legacy_editor_url": "/card-editor/challenge",
            "mood_photos":       mood_photos,
            "mood_slot_meta":    _MOOD_SLOT_META,
            **_STUDIO_NAV_CTX,
        }, None

    # Load viewer's attempt for phase calculation
    is_challenger  = user.id == ch.challenger_id
    my_attempt_id  = ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
    my_attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(VirtualTrainingAttempt.id == my_attempt_id)
        .first()
    ) if my_attempt_id else None

    unlocked = _get_unlocked_phases(ch, user.id, my_attempt)
    locked   = _get_locked_phases(ch, user.id)

    # EXPIRED: get_locked_challenge_card_phases() still excludes EXPIRED from the
    # initial-phase logic (tech debt). Augment locked here so the timeline is complete.
    # CANCELLED/DECLINED are now handled correctly in get_locked_challenge_card_phases().
    if ch.status in _CC_STATUSES_WITH_IMPLICIT_INITIAL:
        initial = "challenge_sent" if is_challenger else "challenge_received"
        if initial not in unlocked and initial not in locked:
            locked = locked + [initial]

    # NOTE: waiting_for_opponent for COMPLETED+attempt is now returned directly
    # by get_locked_challenge_card_phases() (updated in vt_challenges.py) so no
    # local augmentation is needed here.

    # CS-S4B-FIX-1: Build chronological phase list.
    # Merge unlocked + locked, deduplicate, sort by _CC_PHASE_TIMELINE_ORDER.
    all_phase_ids = sorted(
        set(unlocked) | set(locked),
        key=lambda p: (_CC_PHASE_TIMELINE_ORDER.get(p, 99), p),
    )

    # Auto-select phase: first by timeline order (prefer unlocked, else locked)
    if phase is None or phase not in set(all_phase_ids):
        unlocked_ordered = [p for p in all_phase_ids if p in set(unlocked)]
        locked_ordered   = [p for p in all_phase_ids if p not in set(unlocked)]
        if unlocked_ordered:
            phase = unlocked_ordered[0]
        elif locked_ordered:
            phase = locked_ordered[0]
        else:
            return None, "/card-studio/challenge"

    # Default/validate platform
    if platform not in _CC_VALID_PLATFORMS:
        platform = _CC_VALID_PLATFORMS[0]

    ratio_class = _CC_RATIO[platform]
    # CS-S4B-FIX4: is_historical_phase replaces is_locked_phase — preview-only
    # historical phases are NOT "locked" (they ARE previewable), just not exportable.
    _hist_set     = set(locked)
    _current_set  = set(unlocked)
    is_historical_phase = phase in _hist_set and phase not in _current_set

    # Preview URL — uses existing /challenges/{id}/card/preview route
    preview_url = (
        f"/challenges/{challenge_id}/card/preview"
        f"?platform={platform}&phase={phase}"
    )

    # CS-S4B-FIX-1+FIX3+FIX4: Phase chips.
    # State model:
    #   is_historical  = happened in the past, previewable, not exportable (no lock!)
    #   is_previewable = True for all phases in the timeline (backend accepts them)
    #   is_exportable  = True only for result phases
    #   is_disabled    = False for all phases currently in timeline
    phase_chips = [
        {
            "id":            p,
            "label":         _CC_PHASE_LABELS.get(p, p),
            "event_label":   _CC_PHASE_EVENT_LABELS.get(p, _CC_PHASE_LABELS.get(p, p)),
            "sublabel":      _CC_PHASE_SUBLABELS.get(p, ""),
            "active":        p == phase,
            "is_historical": p in _hist_set and p not in _current_set,
            "is_previewable": True,
            "is_exportable": p in _CC_EXPORTABLE_PHASES,
            "is_disabled":   False,
        }
        for p in all_phase_ids
    ]

    is_exportable_phase = phase in _CC_EXPORTABLE_PHASES
    # CDO ownership check for the active platform
    active_platform_owned = is_design_accessible(db, user.id, "challenge_card", platform)
    # Export URL only when phase is exportable AND format is owned
    export_url = (
        f"/challenges/{challenge_id}/card/export"
        f"?platform={platform}&phase={phase}"
    ) if (is_exportable_phase and active_platform_owned) else None

    # Platform chips for UI
    platform_chips = [
        {
            "id":     pid,
            "label":  _CC_PLATFORM_LABELS[pid],
            "active": pid == platform,
        }
        for pid in _CC_VALID_PLATFORMS
    ]

    opponent = ch.challenged if is_challenger else ch.challenger

    # CC-DESIGN-1 Phase-B: Auto-selected mood indicator for the viewer's side.
    # Determines which slot would be (or was) automatically chosen so the UI can
    # show "Auto: Celebration" / "Fallback: Neutral" / "Manual override".
    _viewer_uid     = user.id
    _viewer_is_ch   = is_challenger
    _viewer_snapshot = (
        ch.challenger_card_photo_url if _viewer_is_ch else ch.challenged_card_photo_url
    )
    _viewer_winner  = _winner_ctx(ch, _viewer_uid)
    _pref, _alt     = _PHASE_MOOD_MAP.get(
        (phase, _viewer_winner),
        _PHASE_MOOD_MAP.get((phase, None), (None, "mood_intro_neutral")),
    )
    # Determine which slot is actually in use by walking the fallback chain
    _auto_slot: str | None = None
    _auto_source = "fallback"  # "preferred" | "alternative" | "fallback" | "license"
    for _s_label, _slot in [
        ("preferred",    _pref),
        ("alternative",  _alt),
        ("fallback",     "mood_intro_neutral"),
    ]:
        if not _slot:
            continue
        _mp = mood_photos.get(_slot)
        if _mp and (_mp.processed_png_url or _mp.original_url):
            _auto_slot   = _slot
            _auto_source = _s_label
            break
    # Build human label for the auto indicator
    _slot_label_map = {m["slot"]: m["label"] for m in _MOOD_SLOT_META}
    if _viewer_snapshot:
        _auto_mood_info = {
            "state":  "manual",
            "label":  "Manual override",
            "slot":   None,
        }
    elif _auto_slot:
        _auto_mood_info = {
            "state":  "auto" if _auto_source == "preferred" else "fallback",
            "label":  f"{'Auto' if _auto_source == 'preferred' else 'Fallback'}: {_slot_label_map.get(_auto_slot, _auto_slot)}",
            "slot":   _auto_slot,
        }
    else:
        _auto_mood_info = {
            "state":  "fallback",
            "label":  "Fallback: Player photo",
            "slot":   None,
        }

    ctx = {
        "active_type":          "challenge",
        "challenge_mode":       "preview",
        "selected_challenge_id": challenge_id,
        "selected_challenge": {
            "id":             ch.id,
            "opponent_name":  _cc_display_name(opponent),
            "game_name":      ch.game.name if ch.game else "—",
            "status":         ch.status.value,
            "challenge_mode": ch.challenge_mode or "async",
            "created_at":     ch.created_at,
            "completed_at":   ch.completed_at,
        },
        "phase_chips":          phase_chips,
        "platform_chips":       platform_chips,
        "active_phase":         phase,
        "active_platform":      platform,
        "is_historical_phase":      is_historical_phase,
        "is_exportable_phase":      is_exportable_phase,
        "active_platform_owned":    active_platform_owned,
        "challenge_export_url":     export_url,
        "ratio_class":              ratio_class,
        "preview_url":              preview_url,
        "legacy_editor_url":        "/card-editor/challenge",
        # CC-DESIGN-1: mood photo media panel for challenge Studio
        "mood_photos":          mood_photos,
        "mood_slot_meta":       _MOOD_SLOT_META,
        # CC-DESIGN-1 Phase-B: auto-selected mood indicator
        "auto_mood_info":       _auto_mood_info,
        **_STUDIO_NAV_CTX,
    }
    return ctx, None


@router.get("/card-studio/challenge", response_class=HTMLResponse)
async def card_studio_challenge_studio(
    request:      Request,
    challenge_id: int | None = Query(default=None),
    phase:        str | None = Query(default=None),
    platform:     str | None = Query(default=None),
    filter_val:   str        = Query(default="all", alias="filter"),
    db:           Session    = Depends(get_db),
    user:         User       = Depends(get_current_user_web),
):
    """CS-S4B: Challenge Card Studio.

    No query params → challenge selector list.
    ?challenge_id={id} → auto-select first unlocked phase.
    ?challenge_id={id}&phase={phase}&platform={platform} → live preview iframe.

    Preview iframe: GET /challenges/{id}/card/preview?platform=...&phase=...
    (existing route, session cookie auth, participant guard).
    No new routes, no DB migrations.
    """
    # CDO guard: user must own at least one challenge card format to access the Studio.
    # No ownership → redirect to shop with a clear message.
    _any_cc_owned = any(
        is_design_accessible(db, user.id, "challenge_card", pid)
        for pid in _CC_VALID_PLATFORMS
    )
    if not _any_cc_owned:
        return RedirectResponse(
            url="/shop?type=challenge_card&info=purchase_required_for_studio",
            status_code=303,
        )

    ctx, redirect = _resolve_challenge_context(
        db, user, challenge_id=challenge_id, phase=phase,
        platform=platform, filter_val=filter_val,
    )
    if redirect:
        return RedirectResponse(url=redirect, status_code=303)

    return templates.TemplateResponse(
        "card_studio_shell.html",
        {"request": request, "user": user, "cc_owned": True, **ctx},
    )

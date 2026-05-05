"""Admin tournament edit page route."""
from collections import defaultdict as _defaultdict

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.campus import Campus
from ....models.game_preset import GamePreset
from ....models.location import Location
from ....models.semester import Semester, SemesterCategory
from ....services.tournament import get_allowed_age_groups
from ....models.semester_enrollment import SemesterEnrollment
from ....models.session import Session as SessionModel, EventCategory
from ....models.team import Team, TournamentTeamEnrollment
from ....models.tournament_ranking import TournamentRanking
from ....models.tournament_type import TournamentType
from ....models.user import User, UserRole
import app.services.tournament.instructor_planning_service as _ip_service
from . import templates, _admin_only

router = APIRouter()


# ── Tournament Edit Page ────────────────────────────────────────────────────────

@router.get("/admin/tournaments/{tournament_id}/edit", response_class=HTMLResponse)
async def admin_tournament_edit_page(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: tournament edit page — all lifecycle management in one place."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)

    # Enrollments with user details
    enrollments = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
        )
        .all()
    )
    enrolled_user_ids = [e.user_id for e in enrollments]
    enrolled_users = {}
    if enrolled_user_ids:
        for u in db.query(User).filter(User.id.in_(enrolled_user_ids)).all():
            enrolled_users[u.id] = u

    # Sessions generated
    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .order_by(SessionModel.date_start)
        .limit(10)
        .all()
    )
    session_count = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .count()
    )

    # Reference data for dropdowns
    game_presets = db.query(GamePreset).filter(GamePreset.is_active == True).all()
    tournament_types = db.query(TournamentType).all()
    campuses = db.query(Campus).filter(Campus.is_active == True).all()
    locations = db.query(Location).filter(Location.is_active == True).all()

    # Schedule config (from tournament_config_obj)
    cfg = t.tournament_config_obj
    _checkin_iso = t.checkin_opens_at.isoformat() if getattr(t, 'checkin_opens_at', None) else None
    schedule = {
        "match_duration_minutes": cfg.match_duration_minutes if cfg else None,
        "break_duration_minutes": cfg.break_duration_minutes if cfg else None,
        "parallel_fields": cfg.parallel_fields if cfg else 1,
        "checkin_opens_at": _checkin_iso,
        "number_of_legs": cfg.number_of_legs if cfg else 1,
        "track_home_away": cfg.track_home_away if cfg else False,
    }

    # Reward config summary
    reward_cfg = t.reward_config  # property → dict or None

    # Game preset info (for session gen guard)
    game_cfg = t.game_config_obj
    preset = None
    preset_min_players = None
    if game_cfg and game_cfg.game_preset_id:
        preset = db.query(GamePreset).filter(GamePreset.id == game_cfg.game_preset_id).first()
        if preset:
            preset_min_players = preset.game_config.get("metadata", {}).get("min_players")

    checked_in_count = sum(
        1 for e in enrollments if e.tournament_checked_in_at is not None
    )

    # Session result status (for Section 7 — result entry panel)
    all_match_sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.event_category == EventCategory.MATCH,
        )
        .order_by(SessionModel.date_start)
        .all()
    )

    def _matchup_label(s, teams_dict: dict, users_dict: dict):
        """Return 'Team A vs Team B' / 'Player X vs Player Y' / 'N participants' / None."""
        if s.participant_team_ids:
            names = [teams_dict.get(tid, f"Team #{tid}") for tid in s.participant_team_ids[:2]]
            return " vs ".join(names) if len(names) >= 2 else names[0]
        if s.participant_user_ids:
            if s.match_format == "HEAD_TO_HEAD" and len(s.participant_user_ids) >= 2:
                u1 = users_dict.get(s.participant_user_ids[0])
                u2 = users_dict.get(s.participant_user_ids[1])
                n1 = u1.name if u1 else f"Player #{s.participant_user_ids[0]}"
                n2 = u2.name if u2 else f"Player #{s.participant_user_ids[1]}"
                return f"{n1} vs {n2}"
            return f"{len(s.participant_user_ids)} participants"
        return None

    # Team name map for TEAM tournaments (team_id → name) — built first for matchup_label
    enrolled_teams: dict = {}
    team_enrollments = (
        db.query(TournamentTeamEnrollment)
        .filter(
            TournamentTeamEnrollment.semester_id == tournament_id,
            TournamentTeamEnrollment.is_active == True,
        )
        .all()
    )
    if team_enrollments:
        team_ids = [e.team_id for e in team_enrollments]
        teams = db.query(Team).filter(Team.id.in_(team_ids)).all()
        enrolled_teams = {t.id: t.name for t in teams}

    sessions_result_status = [
        {
            "id": s.id,
            "title": s.title or f"Session #{s.id}",
            "date_start": s.date_start.strftime("%Y-%m-%d %H:%M") if s.date_start else "",
            "match_format": s.match_format or "INDIVIDUAL_RANKING",
            "has_results": bool(
                (s.rounds_data and s.rounds_data.get("round_results"))
                or s.game_results
            ),
            "participant_user_ids": s.participant_user_ids or [],
            "participant_team_ids": s.participant_team_ids or [],
            "tournament_round": s.tournament_round,
            "group_identifier": s.group_identifier,
            "matchup_label": _matchup_label(s, enrolled_teams, enrolled_users),
            "postponed_reason": s.postponed_reason,
        }
        for s in all_match_sessions
    ]

    # Existing rankings (for Section 8 — rankings panel)
    existing_rankings = (
        db.query(TournamentRanking)
        .filter(TournamentRanking.tournament_id == tournament_id)
        .order_by(TournamentRanking.rank)
        .all()
    )
    ranking_users = {r.user_id: enrolled_users.get(r.user_id) for r in existing_rankings if r.user_id is not None}

    # Group standings: sessions with group_identifier → per-group TournamentRanking rows
    _group_participants: dict = _defaultdict(set)
    for s in all_match_sessions:
        if not s.group_identifier:
            continue
        for tid in (s.participant_team_ids or []):
            _group_participants[s.group_identifier].add(("team", tid))
        for uid in (s.participant_user_ids or []):
            _group_participants[s.group_identifier].add(("user", uid))

    group_standings: dict = {}
    for grp in sorted(_group_participants.keys()):
        parts = _group_participants[grp]
        grp_rows = [
            r for r in existing_rankings
            if ("team", r.team_id) in parts or ("user", r.user_id) in parts
        ]
        grp_rows.sort(key=lambda r: r.rank or 999)
        if grp_rows:
            group_standings[grp] = grp_rows

    # ranking_teams: team_id → Team object (parallel to ranking_users)
    _team_ids_in_rankings = {r.team_id for r in existing_rankings if r.team_id}
    ranking_teams = (
        {t.id: t for t in db.query(Team).filter(Team.id.in_(_team_ids_in_rankings)).all()}
        if _team_ids_in_rankings else {}
    )

    # Instructor roster (Section 4.5)
    from app.models.pitch import Pitch as PitchModel
    instructor_roster = _ip_service.get_roster(db, tournament_id)
    eligible_instructors = (
        db.query(User)
        .filter(User.role == UserRole.INSTRUCTOR, User.is_active == True)
        .order_by(User.name)
        .all()
    )
    pitches_for_roster = (
        db.query(PitchModel)
        .filter(PitchModel.is_active == True)
        .order_by(PitchModel.name)
        .all()
    )
    has_absent_field = any(
        s["role"] == "FIELD" and s["status"] == "ABSENT"
        for s in instructor_roster
    )

    # Wizard context: enrolled_count + completed_session_count
    _participant_type = cfg.participant_type if cfg else "INDIVIDUAL"
    enrolled_count = len(team_enrollments) if _participant_type == "TEAM" else len(enrollments)
    completed_session_count = sum(
        1 for s in all_match_sessions
        if s.game_results or (s.rounds_data and s.rounds_data.get("round_results"))
    )

    return templates.TemplateResponse(
        "admin/tournament_edit.html",
        {
            "request": request,
            "user": user,
            "t": t,
            "cfg": cfg,
            "schedule": schedule,
            "reward_cfg": reward_cfg,
            "game_cfg": game_cfg,
            "preset": preset,
            "preset_min_players": preset_min_players,
            "enrollments": enrollments,
            "enrolled_users": enrolled_users,
            "checked_in_count": checked_in_count,
            "enrolled_count": enrolled_count,
            "completed_session_count": completed_session_count,
            "sessions": sessions,
            "session_count": session_count,
            "sessions_result_status": sessions_result_status,
            "enrolled_teams": enrolled_teams,
            "existing_rankings": existing_rankings,
            "ranking_users": ranking_users,
            "group_standings": group_standings,
            "ranking_teams": ranking_teams,
            "game_presets": game_presets,
            "tournament_types": tournament_types,
            "campuses": campuses,
            "locations": locations,
            "instructor_roster": instructor_roster,
            "eligible_instructors": eligible_instructors,
            "pitches_for_roster": pitches_for_roster,
            "has_absent_field": has_absent_field,
            "is_promotion_event": t.semester_category == SemesterCategory.PROMOTION_EVENT,
            "promotion_age_groups": get_allowed_age_groups(t),
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )

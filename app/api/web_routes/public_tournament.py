"""
Public tournament/event detail page — no authentication required.
URL: GET /events/{tournament_id}
"""
import json
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case as sql_case
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.semester import Semester
from app.models.tournament_ranking import TournamentRanking
from app.models.user import User
from app.models.team import Team, TournamentTeamEnrollment, TeamMember
from app.models.club import Club
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.session import Session as SessionModel

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_STATUS_LABEL = {
    "DRAFT": "Coming Soon",
    "ENROLLMENT_OPEN": "Enrollment Open",
    "ENROLLMENT_CLOSED": "Enrollment Closed",
    "CHECK_IN_OPEN": "Check-In Open",
    "IN_PROGRESS": "In Progress",
    "COMPLETED": "Completed",
    "REWARDS_DISTRIBUTED": "Rewards Distributed",
    "CANCELLED": "Cancelled",
}

_STATUS_COLOR = {
    "DRAFT": "#a0aec0",
    "ENROLLMENT_OPEN": "#48bb78",
    "ENROLLMENT_CLOSED": "#ed8936",
    "CHECK_IN_OPEN": "#667eea",
    "IN_PROGRESS": "#e53e3e",
    "COMPLETED": "#38a169",
    "REWARDS_DISTRIBUTED": "#d69e2e",
    "CANCELLED": "#718096",
}


@router.get("/events/{tournament_id}", response_class=HTMLResponse)
def public_event_detail(
    request: Request,
    tournament_id: int,
    db: Session = Depends(get_db),
):
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        return HTMLResponse("<h2>Event not found</h2>", status_code=404)

    # Every existing event has a public page — visibility is state-driven, not binary.
    # 404 is reserved for non-existent IDs only.
    status = tournament.tournament_status or "DRAFT"

    cfg = tournament.tournament_config_obj
    participant_type = cfg.participant_type if cfg else "INDIVIDUAL"
    tournament_format = tournament.format  # HEAD_TO_HEAD / INDIVIDUAL_RANKING
    max_players = cfg.max_players if cfg else None
    match_duration = cfg.match_duration_minutes if cfg else None
    number_of_legs    = cfg.number_of_legs if cfg and hasattr(cfg, "number_of_legs") else 1
    scoring_type      = cfg.scoring_type if cfg else None
    ranking_direction = cfg.ranking_direction if cfg else None
    measurement_unit  = cfg.measurement_unit if cfg else None

    # Location / Campus (multi-campus aware)
    location_name = None
    campus_name = None
    extra_campuses: list[dict] = []
    try:
        from app.models.location import Location
        from app.models.campus import Campus as CampusModel
        if tournament.location_id:
            loc = db.query(Location).filter(Location.id == tournament.location_id).first()
            location_name = loc.city if loc else None
        if tournament.campus_id:
            camp = db.query(CampusModel).filter(CampusModel.id == tournament.campus_id).first()
            campus_name = camp.name if camp else None
            if camp and not location_name and camp.location_id:
                loc = db.query(Location).filter(Location.id == camp.location_id).first()
                location_name = loc.city if loc else None
        # Additional campuses from sessions (multi-campus tournaments)
        session_campus_ids = (
            db.query(SessionModel.campus_id)
            .filter(
                SessionModel.semester_id == tournament_id,
                SessionModel.campus_id.isnot(None),
                SessionModel.campus_id != tournament.campus_id,
            )
            .distinct()
            .all()
        )
        for (cid,) in session_campus_ids:
            c = db.query(CampusModel).filter(CampusModel.id == cid).first()
            if not c:
                continue
            loc2 = db.query(Location).filter(Location.id == c.location_id).first() if c.location_id else None
            extra_campuses.append({
                "name": c.name,
                "location_name": loc2.city if loc2 else None,
            })
    except Exception:
        pass

    # ── Instructors ──────────────────────────────────────────────────────────
    instructors: list[dict] = []
    try:
        from app.models.tournament_instructor_slot import TournamentInstructorSlot
        seen_ids: set[int] = set()
        if tournament.master_instructor_id:
            mi = db.query(User).filter(User.id == tournament.master_instructor_id).first()
            if mi:
                instructors.append({"name": mi.name or mi.email, "role": "Master"})
                seen_ids.add(mi.id)
        slots = db.query(TournamentInstructorSlot).filter(
            TournamentInstructorSlot.semester_id == tournament_id
        ).all()
        for slot in slots:
            if slot.instructor_id not in seen_ids:
                u = db.query(User).filter(User.id == slot.instructor_id).first()
                if u:
                    role_label = "Master" if slot.role == "MASTER" else "Field"
                    instructors.append({"name": u.name or u.email, "role": role_label})
                    seen_ids.add(u.id)
    except Exception:
        pass

    # ── Game Preset ──────────────────────────────────────────────────────────
    game_preset_name: str | None = None
    game_preset_skills: list[str] = []
    try:
        gc = tournament.game_config_obj
        if gc and gc.game_preset:
            gp = gc.game_preset
            game_preset_name = gp.name
            game_preset_skills = gp.skills_tested or []
    except Exception:
        pass

    # Tournament type display name + code
    type_name = ""
    tournament_type_code = ""
    try:
        if cfg and cfg.tournament_type:
            type_name = cfg.tournament_type.display_name  # BUG-1 fix: was .name (no such attr)
            tournament_type_code = cfg.tournament_type.code or ""
    except Exception:
        pass

    # ── Rankings ──────────────────────────────────────────────────────────────
    ranking_rows = db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == tournament_id
    ).order_by(TournamentRanking.rank.asc().nulls_last()).all()

    rankings = []
    if participant_type == "TEAM":
        for row in ranking_rows:
            team = db.query(Team).filter(Team.id == row.team_id).first() if row.team_id else None
            club = db.query(Club).filter(Club.id == team.club_id).first() if team and team.club_id else None
            # IR+TEAM: load team members so the public page can show who competed
            members_list: list[dict] = []
            if tournament_format == "INDIVIDUAL_RANKING" and row.team_id:
                mem_rows = (
                    db.query(User, TeamMember)
                    .join(TeamMember, TeamMember.user_id == User.id)
                    .filter(TeamMember.team_id == row.team_id, TeamMember.is_active == True)
                    .order_by(TeamMember.role.desc(), User.name)
                    .all()
                )
                members_list = [
                    {"name": u.name or u.email, "user_id": u.id, "role": tm.role}
                    for u, tm in mem_rows
                ]
            rankings.append({
                "rank": row.rank,
                "name": team.name if team else f"Team #{row.team_id}",
                "club_name": club.name if club else None,
                "points": row.points,
                "wins": row.wins,
                "draws": row.draws,
                "losses": row.losses,
                "goals_for": row.goals_for,
                "goals_against": row.goals_against,
                "team_id": row.team_id,
                "user_id": None,
                "members": members_list,
            })
    else:
        for row in ranking_rows:
            user = db.query(User).filter(User.id == row.user_id).first() if row.user_id else None
            rankings.append({
                "rank": row.rank,
                "name": user.name if user and user.name else (user.email if user else f"Player #{row.user_id}"),
                "club_name": None,
                "points": row.points,
                "wins": row.wins,
                "draws": row.draws,
                "losses": row.losses,
                "goals_for": row.goals_for,
                "goals_against": row.goals_against,
                "team_id": None,
                "user_id": row.user_id,
                "members": [],
            })

    has_rankings = len(rankings) > 0

    # Explicit domain flag from TournamentType — never derived from raw field values
    from app.models.ranking_type import RankingType, StandingsState
    ranking_type: str = tournament.ranking_type   # "SCORING_ONLY" | "WDL_BASED"
    show_wdl: bool = (ranking_type == RankingType.WDL_BASED)
    # standings_state is computed below after sessions_total is known

    # ── Enrolled participants (shown when no final rankings yet) ──────────────
    enrolled_count = 0
    participants = []  # [{name, club_name}] for pre-result display

    if participant_type == "TEAM":
        team_enrollments = db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.semester_id == tournament_id,
            TournamentTeamEnrollment.is_active == True,
        ).all()
        enrolled_count = len(team_enrollments)
        if not has_rankings:
            for te in team_enrollments:
                team = db.query(Team).filter(Team.id == te.team_id).first()
                club = db.query(Club).filter(Club.id == team.club_id).first() if team and team.club_id else None
                participants.append({
                    "name": team.name if team else f"Team #{te.team_id}",
                    "club_name": club.name if club else None,
                })
    else:
        enrolled_count = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
        ).count()
        if not has_rankings:
            rows = db.query(SemesterEnrollment, User).join(
                User, SemesterEnrollment.user_id == User.id
            ).filter(
                SemesterEnrollment.semester_id == tournament_id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            ).all()
            for enr, user in rows:
                participants.append({
                    "name": user.name if user.name else user.email,
                    "club_name": None,
                })

    # ── Schedule (shown when sessions exist, any state) ───────────────────────
    # KO-03: explicit phase ordering (alphabetical asc is wrong: FINALS < GROUP_STAGE)
    _phase_order = sql_case(
        (SessionModel.tournament_phase == "GROUP_STAGE", 1),
        (SessionModel.tournament_phase == "KNOCKOUT", 2),
        (SessionModel.tournament_phase == "FINALS", 3),
        (SessionModel.tournament_phase == "PLACEMENT", 4),
        (SessionModel.tournament_phase == "SWISS", 5),
        (SessionModel.tournament_phase == "INDIVIDUAL_RANKING", 6),
        else_=9,
    )
    raw_sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .order_by(
            _phase_order,
            SessionModel.round_number.asc().nulls_last(),
            SessionModel.id,
        )
        .all()
    )
    sessions_total = len(raw_sessions)

    # standings_state: explicit UI state — never guessed from ad-hoc conditions
    _FINAL_STATUSES = ("COMPLETED", "REWARDS_DISTRIBUTED")
    if has_rankings:
        standings_state: str = (
            StandingsState.FINAL if status in _FINAL_STATUSES else StandingsState.LIVE
        )
    elif sessions_total > 0:
        standings_state = StandingsState.PENDING
    else:
        standings_state = StandingsState.NONE

    # Build a team-name cache to avoid N+1 queries.
    # Also scan rounds_data result keys (team_XXX) to cover old sessions where
    # participant_team_ids was not yet populated by the generator.
    all_team_ids: set[int] = set()
    for sess in raw_sessions:
        for tid in (sess.participant_team_ids or []):
            all_team_ids.add(tid)
        _r1 = (sess.rounds_data or {}).get("round_results", {}).get("1", {})
        for _k in _r1:
            if _k.startswith("team_"):
                try:
                    all_team_ids.add(int(_k.split("_", 1)[1]))
                except ValueError:
                    pass
    team_cache: dict[int, str] = {}
    for team in db.query(Team).filter(Team.id.in_(all_team_ids)).all():
        team_cache[team.id] = team.name

    # Build a player-name cache for INDIVIDUAL HEAD_TO_HEAD schedules (Swiss, etc.)
    # participant_user_ids stores 1v1 pairings when participant_type == INDIVIDUAL.
    # Also scan game_results.participants for knockout sessions where UIDs may not
    # be in participant_user_ids (e.g. GROUP_KNOCKOUT sessions).
    all_schedule_uids: set[int] = set()
    for sess in raw_sessions:
        for uid in (sess.participant_user_ids or []):
            all_schedule_uids.add(uid)
        if sess.game_results:
            try:
                gr = json.loads(sess.game_results) if isinstance(sess.game_results, str) else {}
                for p in (gr.get("participants") or []):
                    if isinstance(p.get("user_id"), int):
                        all_schedule_uids.add(p["user_id"])
            except Exception:
                pass
    schedule_player_cache: dict[int, str] = {}
    for u in db.query(User).filter(User.id.in_(all_schedule_uids)).all():
        schedule_player_cache[u.id] = u.name or u.email

    # ── Per-session venue caches ──────────────────────────────────────────────
    from app.models.campus import Campus as CampusModel
    from app.models.pitch import Pitch as PitchModel
    _s_campus_ids = {s.campus_id    for s in raw_sessions if s.campus_id}
    # Include tournament-level campus so venue fallback works (sess.campus_id or tournament.campus_id)
    if tournament.campus_id:
        _s_campus_ids.add(tournament.campus_id)
    _s_pitch_ids  = {s.pitch_id     for s in raw_sessions if s.pitch_id}
    _s_instr_ids  = {s.instructor_id for s in raw_sessions if s.instructor_id}
    session_campus_cache: dict[int, str] = {
        c.id: c.name
        for c in db.query(CampusModel).filter(CampusModel.id.in_(_s_campus_ids))
    } if _s_campus_ids else {}
    session_pitch_cache: dict[int, str] = {
        p.id: p.name
        for p in db.query(PitchModel).filter(PitchModel.id.in_(_s_pitch_ids))
    } if _s_pitch_ids else {}
    session_instr_cache: dict[int, str] = {
        u.id: (u.name or u.email)
        for u in db.query(User).filter(User.id.in_(_s_instr_ids))
    } if _s_instr_ids else {}

    # Build player_data caches for IR per-player display
    player_score_map: dict[int, dict] = {}  # {user_id: {score, position}}
    user_cache: dict[int, "User"] = {}       # {user_id: User}
    if tournament_format == "INDIVIDUAL_RANKING":
        all_player_uids: set[int] = set()
        for sess in raw_sessions:
            pd = (sess.rounds_data or {}).get("round_results", {}).get("1", {}).get("player_data", {})
            for k, v in pd.items():
                if k.startswith("user_"):
                    try:
                        uid = int(k.split("_", 1)[1])
                        all_player_uids.add(uid)
                        player_score_map[uid] = {
                            "score": float(v["score"]) if "score" in v else None,
                            "position": v.get("position"),
                        }
                    except (ValueError, KeyError):
                        pass
        if all_player_uids:
            for u in db.query(User).filter(User.id.in_(all_player_uids)).all():
                user_cache[u.id] = u

    # Enrich TEAM+IR ranking members with individual score/position
    if tournament_format == "INDIVIDUAL_RANKING" and player_score_map:
        for rank_row in rankings:
            for m in rank_row.get("members", []):
                uid = m.get("user_id")
                if uid and uid in player_score_map:
                    m["score"] = player_score_map[uid]["score"]
                    m["position"] = player_score_map[uid]["position"]
            rank_row["members"].sort(key=lambda m: m.get("position") or 999)

    # ── Group Stage Standings (GROUP_KNOCKOUT only) ──────────────────────────
    # BUG-2 fix: handles both INDIVIDUAL (game_results) and TEAM (rounds_data) storage
    group_standings: dict[str, list[dict]] = {}
    group_matches: dict[str, list[dict]] = {}   # per-group match list for official record
    qualifiers_set: set[int] = set()  # participant IDs that appear in first KO round
    qualifiers_per_group: int = 0
    if tournament_type_code == "group_knockout":
        # Build qualifier set from round_number==1 knockout sessions
        for sess in raw_sessions:
            if sess.tournament_phase == "KNOCKOUT" and sess.round_number == 1:
                for tid in (sess.participant_team_ids or []):
                    qualifiers_set.add(tid)
                for uid in (sess.participant_user_ids or []):
                    qualifiers_set.add(uid)

        grp_data: dict[str, dict[int, dict]] = {}
        for sess in raw_sessions:
            if sess.tournament_phase != "GROUP_STAGE":
                continue
            sc = sess.structure_config or {}
            grp = sc.get("group") or "?"

            # Path A: INDIVIDUAL — results in game_results
            if sess.game_results:
                try:
                    gr = json.loads(sess.game_results) if isinstance(sess.game_results, str) else sess.game_results
                    parts = gr.get("participants") or []
                    if len(parts) < 2:
                        continue
                    pa, pb = parts[0], parts[1]
                    uid_a, uid_b = pa.get("user_id"), pb.get("user_id")
                    s_a = float(pa.get("score") or 0)
                    s_b = float(pb.get("score") or 0)
                    for uid in (uid_a, uid_b):
                        if uid is not None:
                            grp_data.setdefault(grp, {}).setdefault(uid, {"W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "is_team": False})
                    for uid, s_for, s_against, result in [
                        (uid_a, int(s_a), int(s_b), pa.get("result", "")),
                        (uid_b, int(s_b), int(s_a), pb.get("result", "")),
                    ]:
                        if uid is not None and grp in grp_data and uid in grp_data[grp]:
                            e = grp_data[grp][uid]
                            e["GF"] += s_for
                            e["GA"] += s_against
                            if result == "win":
                                e["W"] += 1
                            elif result == "draw":
                                e["D"] += 1
                            else:
                                e["L"] += 1
                    # Record per-group match for official record
                    n_a = schedule_player_cache.get(uid_a, f"#{uid_a}") if uid_a else "?"
                    n_b = schedule_player_cache.get(uid_b, f"#{uid_b}") if uid_b else "?"
                    group_matches.setdefault(grp, []).append({
                        "team_a": n_a, "team_b": n_b,
                        "score_a": int(s_a), "score_b": int(s_b),
                        "done": sess.session_status == "completed",
                        "date": sess.date_start,
                        "campus_name": session_campus_cache.get(sess.campus_id or tournament.campus_id),
                        "pitch_name":  session_pitch_cache.get(sess.pitch_id),
                        "session_instructor": session_instr_cache.get(sess.instructor_id),
                    })
                except Exception:
                    pass

            # Path B: TEAM — results in rounds_data["round_results"]["1"]["team_XXXX"]
            elif sess.rounds_data:
                try:
                    tids_gs = list(sess.participant_team_ids or [])
                    if len(tids_gs) < 2:
                        continue
                    rr_gs = sess.rounds_data.get("round_results", {})
                    r1_gs = rr_gs.get("1", {}) if rr_gs else {}
                    raw_a_gs = r1_gs.get(f"team_{tids_gs[0]}")
                    raw_b_gs = r1_gs.get(f"team_{tids_gs[1]}")
                    if raw_a_gs is None and raw_b_gs is None:
                        continue
                    s_a_gs = float(raw_a_gs or 0)
                    s_b_gs = float(raw_b_gs or 0)
                    for tid in tids_gs[:2]:
                        grp_data.setdefault(grp, {}).setdefault(tid, {"W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "is_team": True})
                    res_a = "win" if s_a_gs > s_b_gs else ("draw" if s_a_gs == s_b_gs else "loss")
                    res_b = "win" if s_b_gs > s_a_gs else ("draw" if s_b_gs == s_a_gs else "loss")
                    for pid, s_for, s_against, result in [
                        (tids_gs[0], int(s_a_gs), int(s_b_gs), res_a),
                        (tids_gs[1], int(s_b_gs), int(s_a_gs), res_b),
                    ]:
                        if grp in grp_data and pid in grp_data[grp]:
                            e = grp_data[grp][pid]
                            e["GF"] += s_for
                            e["GA"] += s_against
                            if result == "win":
                                e["W"] += 1
                            elif result == "draw":
                                e["D"] += 1
                            else:
                                e["L"] += 1
                    # Record per-group match for official record
                    n_a = team_cache.get(tids_gs[0], f"Team #{tids_gs[0]}")
                    n_b = team_cache.get(tids_gs[1], f"Team #{tids_gs[1]}")
                    group_matches.setdefault(grp, []).append({
                        "team_a": n_a, "team_b": n_b,
                        "score_a": int(s_a_gs), "score_b": int(s_b_gs),
                        "done": sess.session_status == "completed",
                        "date": sess.date_start,
                        "campus_name": session_campus_cache.get(sess.campus_id or tournament.campus_id),
                        "pitch_name":  session_pitch_cache.get(sess.pitch_id),
                        "session_instructor": session_instr_cache.get(sess.instructor_id),
                    })
                except Exception:
                    pass

        for grp in sorted(grp_data.keys()):
            rows_in_group = []
            for pid, s in grp_data[grp].items():
                pts = s["W"] * 3 + s["D"]
                gd = s["GF"] - s["GA"]
                if s["is_team"]:
                    name = team_cache.get(pid, f"Team #{pid}")
                else:
                    name = schedule_player_cache.get(pid, f"Player #{pid}")
                rows_in_group.append({
                    "name": name,
                    "id": pid,
                    "W": s["W"], "D": s["D"], "L": s["L"],
                    "GF": s["GF"], "GA": s["GA"], "GD": gd, "Pts": pts,
                    "qualifies": pid in qualifiers_set,
                })
            rows_in_group.sort(key=lambda r: (-r["Pts"], -r["GD"], -r["GF"], r["id"]))
            group_standings[grp] = rows_in_group

    # How many qualifiers per group (for "top N advance" note)
    qualifiers_per_group = (
        len(qualifiers_set) // max(len(group_standings), 1) if group_standings else 0
    )

    schedule: list[dict] = []
    for sess in raw_sessions:
        tids = list(sess.participant_team_ids or [])
        uids = sess.participant_user_ids or []
        score_a = score_b = None
        rr = (sess.rounds_data or {}).get("round_results", {})
        r1 = rr.get("1", {}) if rr else {}

        # Fallback: derive team IDs from rounds_data result keys when participant_team_ids
        # was not populated (old sessions created before the column was actively set).
        if not tids and r1:
            derived = sorted(
                int(k.split("_", 1)[1])
                for k in r1
                if k.startswith("team_") and k.split("_", 1)[1].isdigit()
            )
            if len(derived) >= 2:
                tids = derived

        if len(tids) >= 2:
            # TEAM HEAD_TO_HEAD: participant_team_ids carries the pairing
            name_a = team_cache.get(tids[0], f"Team #{tids[0]}")
            name_b = team_cache.get(tids[1], f"Team #{tids[1]}")
            raw_a = r1.get(f"team_{tids[0]}")
            raw_b = r1.get(f"team_{tids[1]}")
            if raw_a is not None:
                score_a = int(float(raw_a))
            if raw_b is not None:
                score_b = int(float(raw_b))
        elif len(uids) >= 2:
            # INDIVIDUAL HEAD_TO_HEAD: participant_user_ids carries the 1v1 pairing
            name_a = schedule_player_cache.get(uids[0], f"Player #{uids[0]}")
            name_b = schedule_player_cache.get(uids[1], f"Player #{uids[1]}")
            raw_a = r1.get(str(uids[0]))
            raw_b = r1.get(str(uids[1]))
            if raw_a is not None:
                score_a = int(float(raw_a))
            if raw_b is not None:
                score_b = int(float(raw_b))
        else:
            name_a = name_b = "TBD"

        # game_results score fallback (GROUP_KNOCKOUT sessions use game_results, not rounds_data)
        if score_a is None and score_b is None and sess.game_results:
            try:
                gr = json.loads(sess.game_results) if isinstance(sess.game_results, str) else {}
                gr_parts = gr.get("participants") or []
                if len(uids) >= 2 and len(gr_parts) >= 2:
                    by_uid = {p["user_id"]: p for p in gr_parts}
                    pa = by_uid.get(uids[0])
                    pb = by_uid.get(uids[1])
                    if pa is not None and pb is not None:
                        score_a = int(pa.get("score") or 0)
                        score_b = int(pb.get("score") or 0)
            except Exception:
                pass

        # Compute round_name for display (uses structure_config.round_name when available)
        sc = sess.structure_config or {}
        phase = sess.tournament_phase or ""
        if sc.get("round_name"):
            # Knockout sessions: use stored round_name ("Round of 8", "Final", etc.)
            round_name = sc["round_name"]
        elif phase == "GROUP_STAGE":
            group_label = sc.get("group", "")
            rnum = sess.tournament_round or sess.round_number
            round_name = f"Group {group_label} – Round {rnum}" if group_label else f"Group Round {rnum}"
        elif sess.round_number:
            round_name = f"Round {sess.round_number}"
        else:
            round_name = "—"

        # Skip sessions where both participants are unknown (bracket not yet seeded)
        if name_a == "TBD" and name_b == "TBD":
            continue

        schedule.append({
            "round":              sess.round_number,
            "phase":              phase,
            "round_name":         round_name,
            "date":               sess.date_start,
            "team_a":             name_a,
            "team_b":             name_b,
            "score_a":            score_a,
            "score_b":            score_b,
            "done":               sess.session_status == "completed",
            "leg_number":         getattr(sess, "leg_number", None),
            "campus_name":        session_campus_cache.get(sess.campus_id or tournament.campus_id),
            "pitch_name":         session_pitch_cache.get(sess.pitch_id),
            "session_instructor": session_instr_cache.get(sess.instructor_id),
        })

    # ── Knockout bracket rounds (KO-01: visual bracket display) ─────────────────
    _KO_PHASES = {"KNOCKOUT", "FINALS", "PLACEMENT"}
    bracket_rounds: list[dict] = []
    if tournament_type_code in ("knockout", "group_knockout"):
        round_map: dict[int, dict] = {}
        for sess in raw_sessions:
            phase_s = sess.tournament_phase or ""
            if tournament_type_code == "group_knockout" and phase_s not in _KO_PHASES:
                continue
            rn = sess.round_number or 0
            sc_b = sess.structure_config or {}
            rname = sc_b.get("round_name") or (f"Round {rn}" if rn > 0 else "Round")
            tids_b = list(sess.participant_team_ids or [])
            uids_b = list(sess.participant_user_ids or [])
            rr_b = (sess.rounds_data or {}).get("round_results", {})
            r1_b = rr_b.get("1", {}) if rr_b else {}
            score_a_b = score_b_b = None
            if len(tids_b) >= 2:
                a_b = team_cache.get(tids_b[0], "TBD")
                b_b = team_cache.get(tids_b[1], "TBD")
                raw_a_b = r1_b.get(f"team_{tids_b[0]}")
                raw_b_b = r1_b.get(f"team_{tids_b[1]}")
                if raw_a_b is not None:
                    score_a_b = int(float(raw_a_b))
                if raw_b_b is not None:
                    score_b_b = int(float(raw_b_b))
            elif len(uids_b) >= 2:
                a_b = schedule_player_cache.get(uids_b[0], "TBD")
                b_b = schedule_player_cache.get(uids_b[1], "TBD")
                raw_a_b = r1_b.get(str(uids_b[0]))
                raw_b_b = r1_b.get(str(uids_b[1]))
                if raw_a_b is not None:
                    score_a_b = int(float(raw_a_b))
                if raw_b_b is not None:
                    score_b_b = int(float(raw_b_b))
            else:
                a_b = b_b = "TBD"
            if rn not in round_map:
                round_map[rn] = {"round": rn, "round_name": rname, "matches": []}
            round_map[rn]["matches"].append({
                "team_a":  a_b,
                "team_b":  b_b,
                "score_a": score_a_b,
                "score_b": score_b_b,
                "done":    sess.session_status == "completed",
                "tbd":     a_b == "TBD" and b_b == "TBD",
                "date":              sess.date_start,
                "campus_name":       session_campus_cache.get(sess.campus_id or tournament.campus_id),
                "pitch_name":        session_pitch_cache.get(sess.pitch_id),
                "session_instructor": session_instr_cache.get(sess.instructor_id),
            })
        bracket_rounds = sorted(round_map.values(), key=lambda r: r["round"])

    # ── Venue-organized Draw (group_knockout / knockout) ─────────────────────
    # Groups all sessions by campus → pitch → time for player-friendly "where/when/who" view
    venue_schedule: list[dict] = []
    if tournament_type_code in ("group_knockout", "knockout"):
        _vmap: dict[tuple[str, str], list[dict]] = {}
        for sess in raw_sessions:
            eff_campus_id = sess.campus_id or tournament.campus_id
            c_name = session_campus_cache.get(eff_campus_id) or "—"
            p_name = session_pitch_cache.get(sess.pitch_id) or "—"
            tids_v = list(sess.participant_team_ids or [])
            uids_v = list(sess.participant_user_ids or [])
            rr_v = (sess.rounds_data or {}).get("round_results", {})
            r1_v = rr_v.get("1", {}) if rr_v else {}
            sa_v = sb_v = None
            if len(tids_v) >= 2:
                na_v = team_cache.get(tids_v[0], "TBD")
                nb_v = team_cache.get(tids_v[1], "TBD")
                raw_av = r1_v.get(f"team_{tids_v[0]}")
                raw_bv = r1_v.get(f"team_{tids_v[1]}")
                if raw_av is not None:
                    sa_v = int(float(raw_av))
                if raw_bv is not None:
                    sb_v = int(float(raw_bv))
            elif len(uids_v) >= 2:
                na_v = schedule_player_cache.get(uids_v[0], "TBD")
                nb_v = schedule_player_cache.get(uids_v[1], "TBD")
                raw_av = r1_v.get(str(uids_v[0]))
                raw_bv = r1_v.get(str(uids_v[1]))
                if raw_av is not None:
                    sa_v = int(float(raw_av))
                if raw_bv is not None:
                    sb_v = int(float(raw_bv))
                if sa_v is None and sess.game_results:
                    try:
                        gr_v = json.loads(sess.game_results) if isinstance(sess.game_results, str) else {}
                        by_uid_v = {p["user_id"]: p for p in (gr_v.get("participants") or [])}
                        pa_v = by_uid_v.get(uids_v[0])
                        pb_v = by_uid_v.get(uids_v[1])
                        if pa_v and pb_v:
                            sa_v = int(pa_v.get("score") or 0)
                            sb_v = int(pb_v.get("score") or 0)
                    except Exception:
                        pass
            else:
                na_v = nb_v = "TBD"
            # Context label (phase/round)
            sc_v = sess.structure_config or {}
            ph_v = sess.tournament_phase or ""
            if sc_v.get("round_name"):
                ctx_v = sc_v["round_name"]
            elif ph_v == "GROUP_STAGE":
                gv = sc_v.get("group", "")
                rn_v = sess.tournament_round or sess.round_number
                ctx_v = f"Group {gv} – Round {rn_v}" if gv else f"Group Round {rn_v}"
            elif sess.round_number:
                ctx_v = f"Round {sess.round_number}"
            else:
                ctx_v = ph_v.replace("_", " ").title() if ph_v else "—"
            _vmap.setdefault((c_name, p_name), []).append({
                "date":    sess.date_start,
                "team_a":  na_v, "team_b": nb_v,
                "score_a": sa_v, "score_b": sb_v,
                "done":    sess.session_status == "completed",
                "context": ctx_v,
                "session_instructor": session_instr_cache.get(sess.instructor_id),
            })
        # Sort matches within each venue chronologically (None dates last)
        for key in _vmap:
            _vmap[key].sort(key=lambda m: (m["date"] is None, m["date"]))
        # Build venue_schedule: campus → pitches list
        _cmap: dict[str, list[dict]] = {}
        for (c_name, p_name), matches in _vmap.items():
            _cmap.setdefault(c_name, []).append({"pitch_name": p_name, "matches": matches})
        for c in _cmap:
            _cmap[c].sort(key=lambda p: p["pitch_name"] or "")
        # Primary tournament campus first, rest alphabetical
        _primary_c = session_campus_cache.get(tournament.campus_id) if tournament.campus_id else None
        if _primary_c and _primary_c in _cmap:
            venue_schedule.append({"campus_name": _primary_c, "pitches": _cmap.pop(_primary_c)})
        for c_name, pitches in sorted(_cmap.items()):
            venue_schedule.append({"campus_name": c_name, "pitches": pitches})

    # ── IR results (INDIVIDUAL_RANKING sessions, shown instead of H2H schedule) ─
    ir_results: list[dict] = []
    if tournament_format == "INDIVIDUAL_RANKING":
        for sess in raw_sessions:
            tids = list(sess.participant_team_ids or [])
            uids = list(getattr(sess, "participant_user_ids", None) or [])
            rr = (sess.rounds_data or {}).get("round_results", {}).get("1", {})
            entries: list[dict] = []
            for team_id in tids:
                raw = rr.get(f"team_{team_id}")
                entries.append({
                    "label": team_cache.get(team_id, f"Team #{team_id}"),
                    "score": float(raw) if raw is not None else None,
                    "is_team": True,
                })
            for uid in uids:
                raw = rr.get(str(uid))
                u = db.query(User).filter(User.id == uid).first()
                entries.append({
                    "label": (u.name or u.email) if u else f"#{uid}",
                    "score": float(raw) if raw is not None else None,
                    "is_team": False,
                })
            # Per-player individual entries from player_data
            player_entries: list[dict] = []
            pd = rr.get("player_data", {})
            for key, val in pd.items():
                if not key.startswith("user_"):
                    continue
                try:
                    uid = int(key.split("_", 1)[1])
                except ValueError:
                    continue
                u = user_cache.get(uid)
                tid = val.get("team_id")
                player_entries.append({
                    "user_id":   uid,
                    "name":      (u.name or u.email) if u else f"#{uid}",
                    "team_id":   tid,
                    "team_name": team_cache.get(tid, "") if tid else "",
                    "score":     float(val["score"]) if "score" in val else None,
                    "position":  val.get("position"),
                })
            player_entries.sort(key=lambda x: x["position"] or 999)
            if entries or player_entries:
                ir_results.append({
                    "round":              sess.round_number or 1,
                    "entries":            entries,
                    "player_entries":     player_entries,
                    "done":              sess.session_status == "completed",
                    "campus_name":        session_campus_cache.get(sess.campus_id or tournament.campus_id),
                    "pitch_name":         session_pitch_cache.get(sess.pitch_id),
                    "session_instructor": session_instr_cache.get(sess.instructor_id),
                })

    # ── Prize pool (all states — motivational) ────────────────────────────────
    from app.services.tournament.tournament_reward_orchestrator import load_reward_policy_from_config
    prize_pool: list[dict] = []
    try:
        policy = load_reward_policy_from_config(db, tournament_id)
        entries = [
            {"placement": 1, "xp": policy.first_place_xp,  "credits": policy.first_place_credits},
            {"placement": 2, "xp": policy.second_place_xp, "credits": policy.second_place_credits},
            {"placement": 3, "xp": policy.third_place_xp,  "credits": policy.third_place_credits},
        ]
        if policy.participant_xp > 0 or policy.participant_credits > 0:
            entries.append({"placement": 0, "xp": policy.participant_xp, "credits": policy.participant_credits})
        prize_pool = [e for e in entries if e["xp"] > 0 or e["credits"] > 0]
    except Exception:
        prize_pool = []
    has_prize_pool = len(prize_pool) > 0

    # ── Awards (COMPLETED + REWARDS_DISTRIBUTED) ──────────────────────────────
    awards: list[dict] = []
    if status in ("COMPLETED", "REWARDS_DISTRIBUTED"):
        from app.models.tournament_achievement import TournamentParticipation
        parts = db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == tournament_id
        ).order_by(TournamentParticipation.placement.asc()).all()

        placement_map: dict[int, dict] = {}
        for p in parts:
            pl = p.placement
            if pl not in placement_map:
                placement_map[pl] = {
                    "placement": pl,
                    "xp": p.xp_awarded,
                    "credits": p.credits_awarded,
                    "count": 0,
                    "names": [],
                    "players": [],  # [{name, user_id}] for individual; [{name}] for team
                }
            placement_map[pl]["count"] += 1
            if p.team_id:
                team = db.query(Team).filter(Team.id == p.team_id).first()
                name = team.name if team else f"Team #{p.team_id}"
                if name and name not in placement_map[pl]["names"]:
                    placement_map[pl]["names"].append(name)
                    placement_map[pl]["players"].append({"name": name, "user_id": None})
            elif p.user_id:
                user = db.query(User).filter(User.id == p.user_id).first()
                name = (user.name or user.email) if user else f"Player #{p.user_id}"
                if name and name not in placement_map[pl]["names"]:
                    placement_map[pl]["names"].append(name)
                    placement_map[pl]["players"].append({"name": name, "user_id": p.user_id})
            else:
                name = None
        awards = sorted(placement_map.values(), key=lambda x: x["placement"])

    has_awards = len(awards) > 0

    return templates.TemplateResponse(request, "public/tournament_detail.html", {
        "t": tournament,
        "status": status,
        "status_label": _STATUS_LABEL.get(status, status),
        "status_color": _STATUS_COLOR.get(status, "#a0aec0"),
        "theme": tournament.theme or "",
        "focus_description": tournament.focus_description or "",
        "participant_type": participant_type,
        "tournament_format": tournament_format,
        "type_name": type_name,
        "max_players": max_players,
        "match_duration": match_duration,
        "number_of_legs": number_of_legs,
        "location_name": location_name,
        "campus_name": campus_name,
        "extra_campuses": extra_campuses,
        "rankings": rankings,
        "has_rankings": has_rankings,
        "ranking_type": ranking_type,       # "SCORING_ONLY" | "WDL_BASED"
        "show_wdl": show_wdl,               # True iff ranking_type == WDL_BASED
        "standings_state": standings_state, # "FINAL" | "LIVE" | "PENDING" | "NONE"
        "enrolled_count": enrolled_count,
        "participants": participants,
        "sessions_total": sessions_total,
        "schedule": schedule,
        "ir_results": ir_results,
        "scoring_type": scoring_type,
        "ranking_direction": ranking_direction,
        "measurement_unit": measurement_unit,
        "prize_pool": prize_pool,
        "has_prize_pool": has_prize_pool,
        "awards": awards,
        "has_awards": has_awards,
        "is_draft": status == "DRAFT",
        "is_cancelled": status == "CANCELLED",
        "instructors": instructors,
        "group_standings": group_standings,
        "group_matches": group_matches,
        "qualifiers_per_group": qualifiers_per_group,
        "tournament_type_code": tournament_type_code,
        "game_preset_name": game_preset_name,
        "game_preset_skills": game_preset_skills,
        "bracket_rounds": bracket_rounds,
        "venue_schedule": venue_schedule,
        "session_type_config": cfg.session_type_config if cfg else "on_site",
    })

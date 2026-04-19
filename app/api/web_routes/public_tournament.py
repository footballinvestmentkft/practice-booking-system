"""
Public tournament/event detail page — no authentication required.
URL: GET /events/{tournament_id}

Query budget: ≤15 queries per request (enforced by test_query_budget.py).
N+1 patterns resolved via selectinload (rankings, awards) and explicit batch IN queries.
Q1 uses a plain PK lookup (no JOIN) — two fast PK lookups beat one JOIN under load.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, selectinload

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


# ── Phase helpers ──────────────────────────────────────────────────────────────


def _build_location_campus_ctx(
    db: Session,
    tournament: Semester,
) -> "tuple[str | None, str | None, list[dict]]":
    """Phase 2: Location / Campus (multi-campus aware).
    Returns (location_name, campus_name, extra_campuses).
    Lazy-imports Location + Campus to preserve original import behaviour.
    """
    location_name = None
    campus_name = None
    extra_campuses: list[dict] = []
    try:
        from app.models.location import Location
        from app.models.campus import Campus as CampusModel
        # Q2 (conditional): single Location lookup
        if tournament.location_id:
            loc = db.query(Location).filter(Location.id == tournament.location_id).first()
            location_name = loc.city if loc else None
        # Q3 (conditional): single Campus lookup
        if tournament.campus_id:
            camp = db.query(CampusModel).filter(CampusModel.id == tournament.campus_id).first()
            campus_name = camp.name if camp else None
            if camp and not location_name and camp.location_id:
                # Q4 (conditional): campus → location
                loc = db.query(Location).filter(Location.id == camp.location_id).first()
                location_name = loc.city if loc else None
        # Q5: distinct campus IDs from sessions (multi-campus tournaments)
        session_campus_ids = (
            db.query(SessionModel.campus_id)
            .filter(
                SessionModel.semester_id == tournament.id,
                SessionModel.campus_id.isnot(None),
                SessionModel.campus_id != tournament.campus_id,
            )
            .distinct()
            .all()
        )
        if session_campus_ids:
            # Q6+Q7: batch-fetch extra campuses + their locations (2 queries regardless of N)
            _extra_ids = [cid for (cid,) in session_campus_ids]
            _campuses = {c.id: c for c in db.query(CampusModel).filter(CampusModel.id.in_(_extra_ids)).all()}
            _loc_ids = [c.location_id for c in _campuses.values() if c.location_id]
            _locs = {l.id: l for l in db.query(Location).filter(Location.id.in_(_loc_ids)).all()} if _loc_ids else {}
            for (cid,) in session_campus_ids:
                c = _campuses.get(cid)
                if not c:
                    continue
                loc2 = _locs.get(c.location_id)
                extra_campuses.append({
                    "name": c.name,
                    "location_name": loc2.city if loc2 else None,
                })
    except Exception:
        pass
    return location_name, campus_name, extra_campuses


def _build_rankings_block(
    db: Session,
    tournament_id: int,
    participant_type: str,
    tournament_format: str,
) -> "tuple[list[dict], bool]":
    """Phases 4–5 (rankings): TournamentRanking rows with eager-loaded user/team/club.
    Returns (rankings, has_rankings).
    """
    # Q8 + Q9 (selectinload user batch) + Q10 (selectinload team batch) + Q11 (club batch)
    # selectinload fires one additional SELECT per relationship, regardless of row count.
    # For INDIVIDUAL: user batch fires, team/club batches are empty (no teams).
    # For TEAM: team+club batches fire, user batch is empty (no users in team rankings).
    ranking_rows = (
        db.query(TournamentRanking)
        .options(
            selectinload(TournamentRanking.user),
            selectinload(TournamentRanking.team).selectinload(Team.club),
        )
        .filter(TournamentRanking.tournament_id == tournament_id)
        .order_by(TournamentRanking.rank.asc().nulls_last())
        .all()
    )

    rankings = []
    if participant_type == "TEAM":
        for row in ranking_rows:
            team = row.team   # already loaded — no additional query
            club = team.club if team else None   # already loaded — no additional query
            members_list: list[dict] = []
            if tournament_format == "INDIVIDUAL_RANKING" and row.team_id:
                # Q (conditional, INDIVIDUAL_RANKING+TEAM only): member join query
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
            user = row.user   # already loaded — no additional query
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

    return rankings, len(rankings) > 0


def _build_participants_block(
    db: Session,
    tournament_id: int,
    participant_type: str,
    has_rankings: bool,
) -> "tuple[int, list[dict]]":
    """Phase 5 (enrollments): enrolled count + pre-result participant list.
    Returns (enrolled_count, participants).
    """
    enrolled_count = 0
    participants: list[dict] = []

    if participant_type == "TEAM":
        # Q12 + Q13 (team batch) + Q14 (club batch): team enrollments with eager loading
        team_enrollments = (
            db.query(TournamentTeamEnrollment)
            .options(
                selectinload(TournamentTeamEnrollment.team).selectinload(Team.club)
            )
            .filter(
                TournamentTeamEnrollment.semester_id == tournament_id,
                TournamentTeamEnrollment.is_active == True,
            )
            .all()
        )
        enrolled_count = len(team_enrollments)
        if not has_rankings:
            for te in team_enrollments:
                team = te.team   # already loaded — no additional query
                club = team.club if team else None   # already loaded — no additional query
                participants.append({
                    "name": team.name if team else f"Team #{te.team_id}",
                    "club_name": club.name if club else None,
                })
    else:
        # Q12: enrollment count
        enrolled_count = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
        ).count()
        if not has_rankings:
            # Q13: enrollment + user join (single query, not N+1)
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

    return enrolled_count, participants


def _build_schedule_block(
    db: Session,
    tournament_id: int,
    tournament_format: str,
    rankings: list,
) -> "tuple[int, list[dict], list[dict]]":
    """Phases 6–7 (sessions + IR results): explicit 6-col select (Phase 11 optimisation),
    team/player cache build, schedule list, IR results list.
    Mutates rankings[*]["members"] in-place to enrich with score/position from player_data.
    Returns (sessions_total, schedule, ir_results).

    IMPORTANT — preserve Phase 11 column narrowing:
    SELECT * width=1,439 B/row × 32 sessions = 46 KB per request at idle.
    Selecting only the 6 consumed fields reduces payload to ~105 B/row (13.7×).
    SQLAlchemy returns sqlalchemy.engine.Row objects — attribute access (sess.field)
    works identically to ORM objects for these six columns.
    """
    # Q (TEAM+INDIVIDUAL path): sessions fetch — explicit columns only (Phase 11)
    # SELECT * width=1,439 B/row × 32 sessions = 46 KB per request at idle.
    # Selecting only the 6 consumed fields reduces payload to ~105 B/row (13.7×).
    # SQLAlchemy returns sqlalchemy.engine.Row objects — attribute access (sess.field)
    # works identically to ORM objects for these six columns.
    raw_sessions = (
        db.query(
            SessionModel.round_number,
            SessionModel.session_status,
            SessionModel.date_start,
            SessionModel.participant_team_ids,
            SessionModel.participant_user_ids,
            SessionModel.rounds_data,
        )
        .filter(SessionModel.semester_id == tournament_id)
        .order_by(SessionModel.round_number.asc().nulls_last(), SessionModel.id)
        .all()
    )
    sessions_total = len(raw_sessions)

    # Build team-name cache — 1 batch query regardless of session count
    all_team_ids: set[int] = set()
    for sess in raw_sessions:
        for tid in (sess.participant_team_ids or []):
            all_team_ids.add(tid)
    team_cache: dict[int, str] = {}
    if all_team_ids:
        for team in db.query(Team).filter(Team.id.in_(all_team_ids)).all():
            team_cache[team.id] = team.name

    # Build player_data caches for IR per-player display — 1 batch query
    player_score_map: dict[int, dict] = {}
    user_cache: dict[int, "User"] = {}
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
            # Also collect participant_user_ids for IR results display
            for uid in (sess.participant_user_ids or []):
                all_player_uids.add(uid)
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

    schedule: list[dict] = []
    for sess in raw_sessions:
        tids = sess.participant_team_ids or []
        name_a = team_cache.get(tids[0], f"Team #{tids[0]}") if len(tids) > 0 else "TBD"
        name_b = team_cache.get(tids[1], f"Team #{tids[1]}") if len(tids) > 1 else "TBD"
        score_a = score_b = None
        rr = (sess.rounds_data or {}).get("round_results", {})
        if rr and len(tids) >= 2:
            r1 = rr.get("1", {})
            raw_a = r1.get(f"team_{tids[0]}")
            raw_b = r1.get(f"team_{tids[1]}")
            if raw_a is not None:
                score_a = int(float(raw_a))
            if raw_b is not None:
                score_b = int(float(raw_b))
        schedule.append({
            "round":   sess.round_number,
            "date":    sess.date_start,
            "team_a":  name_a,
            "team_b":  name_b,
            "score_a": score_a,
            "score_b": score_b,
            "done":    sess.session_status == "completed",
        })

    # ── IR results (INDIVIDUAL_RANKING sessions, shown instead of H2H schedule) ─
    ir_results: list[dict] = []
    if tournament_format == "INDIVIDUAL_RANKING":
        for sess in raw_sessions:
            tids = list(sess.participant_team_ids or [])
            uids = list(sess.participant_user_ids or [])
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
                u = user_cache.get(uid)   # use pre-fetched cache — no additional query
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
                u = user_cache.get(uid)   # use pre-fetched cache — no additional query
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
                    "round": sess.round_number or 1,
                    "entries": entries,
                    "player_entries": player_entries,
                    "done": sess.session_status == "completed",
                })

    return sessions_total, schedule, ir_results


def _build_prize_pool_block(
    db: Session,
    tournament_id: int,
    tournament: Semester,
) -> "tuple[list[dict], bool]":
    """Phase 8 (prize pool): load reward policy and build prize tier list.
    Returns (prize_pool, has_prize_pool).
    Lazy-imports load_reward_policy_from_config to preserve original import behaviour.
    """
    from app.services.tournament.tournament_reward_orchestrator import load_reward_policy_from_config
    prize_pool: list[dict] = []
    try:
        # Pass already-loaded tournament to skip the redundant Semester re-fetch
        policy = load_reward_policy_from_config(db, tournament_id, tournament=tournament)
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
    return prize_pool, len(prize_pool) > 0


def _build_awards_block(
    db: Session,
    tournament_id: int,
    status: str,
) -> "tuple[list[dict], bool]":
    """Phase 9 (awards): TournamentParticipation with eager-loaded user/team.
    Only runs when status in (COMPLETED, REWARDS_DISTRIBUTED).
    Returns (awards, has_awards).
    Lazy-imports TournamentParticipation to preserve original import behaviour.
    """
    if status not in ("COMPLETED", "REWARDS_DISTRIBUTED"):
        return [], False

    from app.models.tournament_achievement import TournamentParticipation
    # Q + selectinload(user) + selectinload(team): 1-3 queries regardless of placement count
    parts = (
        db.query(TournamentParticipation)
        .options(
            selectinload(TournamentParticipation.user),
            selectinload(TournamentParticipation.team),
        )
        .filter(TournamentParticipation.semester_id == tournament_id)
        .order_by(TournamentParticipation.placement.asc())
        .all()
    )

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
                "players": [],
            }
        placement_map[pl]["count"] += 1
        if p.team_id:
            team = p.team   # already loaded — no additional query
            name = team.name if team else f"Team #{p.team_id}"
            if name and name not in placement_map[pl]["names"]:
                placement_map[pl]["names"].append(name)
                placement_map[pl]["players"].append({"name": name, "user_id": None})
        elif p.user_id:
            user = p.user   # already loaded — no additional query
            name = (user.name or user.email) if user else f"Player #{p.user_id}"
            if name and name not in placement_map[pl]["names"]:
                placement_map[pl]["names"].append(name)
                placement_map[pl]["players"].append({"name": name, "user_id": p.user_id})
    awards = sorted(placement_map.values(), key=lambda x: x["placement"])
    return awards, len(awards) > 0


# ── Route ──────────────────────────────────────────────────────────────────────


@router.get("/events/{tournament_id}", response_class=HTMLResponse)
def public_event_detail(
    request: Request,
    tournament_id: int,
    db: Session = Depends(get_db),
):
    # Q1: Semester (simple PK index scan — faster under load than JOIN)
    # Q2 (conditional): TournamentConfiguration lazy-loaded on first access below.
    # Rationale: a LEFT JOIN on tournament_configurations makes Q1 heavier under
    # concurrent load even though it saves one round-trip; two fast PK lookups
    # outperform one JOIN when >50 concurrent requests share the connection pool.
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

    location_name, campus_name, extra_campuses = _build_location_campus_ctx(db, tournament)

    # Tournament type display name
    type_name = ""
    try:
        if cfg and cfg.tournament_type:
            type_name = cfg.tournament_type.name
    except Exception:
        pass

    rankings, has_rankings = _build_rankings_block(db, tournament_id, participant_type, tournament_format)
    enrolled_count, participants = _build_participants_block(db, tournament_id, participant_type, has_rankings)
    sessions_total, schedule, ir_results = _build_schedule_block(db, tournament_id, tournament_format, rankings)
    prize_pool, has_prize_pool = _build_prize_pool_block(db, tournament_id, tournament)
    awards, has_awards = _build_awards_block(db, tournament_id, status)

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
    })

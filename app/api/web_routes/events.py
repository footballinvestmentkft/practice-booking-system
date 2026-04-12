"""
Student Events web routes — event-domain landing + per-type browse/detail.

URL structure:
    GET  /events                        — landing page with type cards
    GET  /events/tournaments            — TOURNAMENT browse (enrolled + browse)
    GET  /events/tournaments/{id}       — TOURNAMENT student detail
    GET  /events/camps                  — CAMP browse (enrolled + browse)
    GET  /events/camps/{id}             — CAMP detail
    POST /events/camps/{id}/enroll      — CAMP self-service enrollment
    POST /events/camps/{id}/unenroll    — CAMP withdrawal (50% refund)
    GET  /events/academy-seasons        — Coming Soon stub
    GET  /events/mini-seasons           — Coming Soon stub
"""
from datetime import datetime, date
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import update as sql_update, text
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.booking import Booking, BookingStatus
from ...models.credit_transaction import CreditTransaction
from ...models.license import UserLicense
from ...models.quiz import SessionQuiz, QuizAttempt
from ...models.semester import Semester, SemesterStatus, SemesterCategory
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.session import Session as SessionModel
from ...models.tournament_configuration import TournamentConfiguration
from ...models.tournament_ranking import TournamentRanking
from ...models.ranking_type import RankingType
from ...models.user import User, UserRole
from .student_features import _spec_ctx

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


# ── Landing ────────────────────────────────────────────────────────────────────

@router.get("/events", response_class=HTMLResponse)
async def events_landing(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Events landing page — 4 type cards linking to per-type browse pages."""
    tournament_enrolled = (
        db.query(SemesterEnrollment)
        .join(Semester, Semester.id == SemesterEnrollment.semester_id)
        .filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active.is_(True),
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            Semester.semester_category == SemesterCategory.TOURNAMENT,
        )
        .count()
    )
    camp_enrolled = (
        db.query(SemesterEnrollment)
        .join(Semester, Semester.id == SemesterEnrollment.semester_id)
        .filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active.is_(True),
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            Semester.semester_category == SemesterCategory.CAMP,
        )
        .count()
    )
    tournament_available = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.TOURNAMENT,
            Semester.tournament_status == "ENROLLMENT_OPEN",
            Semester.status != SemesterStatus.CANCELLED,
        )
        .count()
    )
    camp_available = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.tournament_status == "ENROLLMENT_OPEN",
            Semester.status != SemesterStatus.CANCELLED,
        )
        .count()
    )
    return templates.TemplateResponse(
        "events_landing.html",
        {
            "request": request,
            "user": user,
            "tournament_enrolled": tournament_enrolled,
            "camp_enrolled": camp_enrolled,
            "tournament_available": tournament_available,
            "camp_available": camp_available,
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


# ── Tournaments ────────────────────────────────────────────────────────────────

@router.get("/events/tournaments", response_class=HTMLResponse)
async def events_tournaments_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """TOURNAMENT browse — enrolled (all active statuses) + browse (all non-cancelled)."""
    from ...models.quiz import SessionQuiz

    # Enrolled: all tournaments where the student has an active enrollment (no date filter)
    enrolled_semester_ids = [
        row.semester_id
        for row in db.query(SemesterEnrollment.semester_id)
        .filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active.is_(True),
        )
        .all()
    ]

    all_tournaments = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.TOURNAMENT,
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.desc())  # most recent first for discovery
        .all()
    )

    # ── Batch queries (0 extra N+1) ──────────────────────────────────────────
    all_tournament_ids = [t.id for t in all_tournaments]

    # Batch: my TournamentRanking in all tournaments at once
    my_rankings_by_tid: dict[int, TournamentRanking] = {
        r.tournament_id: r
        for r in db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id.in_(all_tournament_ids),
            TournamentRanking.user_id == user.id,
        ).all()
    } if all_tournament_ids else {}

    # Batch: current phase + session counts per tournament (1 SQL)
    _PHASE_BY_RANK = {
        1: "GROUP_STAGE", 2: "KNOCKOUT", 3: "FINALS",
        4: "PLACEMENT",   5: "SWISS",    6: "INDIVIDUAL_RANKING",
    }
    _PHASE_LABELS = {
        "GROUP_STAGE":        "Group Stage",
        "KNOCKOUT":           "Knockout",
        "FINALS":             "Finals",
        "PLACEMENT":          "Placement",
        "SWISS":              "Swiss",
        "INDIVIDUAL_RANKING": "Individual Ranking",
    }
    session_stats: dict[int, dict] = {}
    if all_tournament_ids:
        _phase_sql = text("""
            SELECT
                semester_id,
                MAX(CASE tournament_phase
                    WHEN 'GROUP_STAGE'        THEN 1
                    WHEN 'KNOCKOUT'           THEN 2
                    WHEN 'FINALS'             THEN 3
                    WHEN 'PLACEMENT'          THEN 4
                    WHEN 'SWISS'              THEN 5
                    WHEN 'INDIVIDUAL_RANKING' THEN 6
                    ELSE 0 END) AS phase_rank,
                COUNT(*) AS total,
                COUNT(CASE WHEN session_status = 'completed' THEN 1 END) AS completed
            FROM sessions
            WHERE semester_id = ANY(:ids)
            GROUP BY semester_id
        """)
        for row in db.execute(_phase_sql, {"ids": all_tournament_ids}).fetchall():
            session_stats[row.semester_id] = {
                "current_phase": _PHASE_BY_RANK.get(row.phase_rank),
                "total":         int(row.total or 0),
                "completed":     int(row.completed or 0),
            }

    enrolled_id_set = set(enrolled_semester_ids)
    enrolled_events = []
    browse_events = []
    for t in all_tournaments:
        enrollment_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == t.id,
                SemesterEnrollment.is_active.is_(True),
            )
            .count()
        )
        is_enrolled = t.id in enrolled_id_set
        user_enrollment = None
        if is_enrolled:
            user_enrollment = (
                db.query(SemesterEnrollment)
                .filter(
                    SemesterEnrollment.semester_id == t.id,
                    SemesterEnrollment.user_id == user.id,
                    SemesterEnrollment.is_active.is_(True),
                )
                .first()
            )
        instructor = None
        if t.master_instructor_id:
            instructor = db.query(User).filter(User.id == t.master_instructor_id).first()

        cfg = t.tournament_config_obj
        session_count = db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()
        has_quiz = (
            db.query(SessionQuiz)
            .join(SessionModel, SessionModel.id == SessionQuiz.session_id)
            .filter(SessionModel.semester_id == t.id)
            .count()
        ) > 0
        session_type_config = cfg.session_type_config if cfg else "on_site"
        tournament_type_code = (
            cfg.tournament_type.code if cfg and cfg.tournament_type else None
        )

        st = session_stats.get(t.id, {})
        current_phase = st.get("current_phase")
        my_rank = my_rankings_by_tid.get(t.id)

        info = {
            "tournament": t,
            "enrollment_count": enrollment_count,
            "max_players": t.max_players or 999,
            "is_enrolled": is_enrolled,
            "enrollment_status": user_enrollment.request_status.value if user_enrollment else None,
            "instructor": instructor,
            "session_count": session_count,
            "has_quiz": has_quiz,
            "session_type_config": session_type_config,
            "tournament_type_code": tournament_type_code,
            # State machine fields
            "current_phase":        current_phase,
            "current_phase_label":  _PHASE_LABELS.get(current_phase, ""),
            "completed_sessions":   st.get("completed", 0),
            "my_ranking":           my_rank,
            "show_wdl":             (t.ranking_type == RankingType.WDL_BASED),
        }
        if is_enrolled:
            enrolled_events.append(info)
        else:
            browse_events.append(info)

    return templates.TemplateResponse(
        "events_tournaments.html",
        {
            "request": request,
            "user": user,
            "enrolled_events": enrolled_events,
            "browse_events": browse_events,
            "tournaments": enrolled_events + browse_events,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.get("/events/tournaments/{tournament_id}", response_class=HTMLResponse)
async def events_tournament_detail(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Student-facing TOURNAMENT event detail page — with round structure and standings."""
    import json as _json
    from itertools import groupby as _groupby
    from sqlalchemy import case as sql_case
    from app.models.ranking_type import RankingType, StandingsState

    tournament = db.query(Semester).filter(
        Semester.id == tournament_id,
        Semester.semester_category == SemesterCategory.TOURNAMENT,
    ).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="Event not found")

    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active.is_(True),
    ).first()

    cfg = tournament.tournament_config_obj
    instructor = None
    if tournament.master_instructor_id:
        instructor = db.query(User).filter(User.id == tournament.master_instructor_id).first()

    enrollment_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active.is_(True),
    ).count()

    # ── Tournament type + domain flags ────────────────────────────────────────
    tournament_type_code = cfg.tournament_type.code if (cfg and cfg.tournament_type) else ""
    type_display_name = cfg.tournament_type.display_name if (cfg and cfg.tournament_type) else (
        "Individual Ranking" if not tournament_type_code else tournament_type_code.replace("_", " ").title()
    )
    ranking_type: str = tournament.ranking_type          # "WDL_BASED" | "SCORING_ONLY"
    show_wdl: bool = (ranking_type == RankingType.WDL_BASED)

    # ── My ranking ────────────────────────────────────────────────────────────
    my_ranking = db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == tournament_id,
        TournamentRanking.user_id == user.id,
    ).first()

    has_any_rankings = (
        db.query(TournamentRanking)
        .filter(TournamentRanking.tournament_id == tournament_id)
        .count()
    ) > 0

    _FINAL_STATUSES = ("COMPLETED", "REWARDS_DISTRIBUTED")
    status = tournament.tournament_status or "DRAFT"
    if has_any_rankings:
        standings_state: str = StandingsState.FINAL if status in _FINAL_STATUSES else StandingsState.LIVE
    elif enrollment_count > 0:
        standings_state = StandingsState.PENDING
    else:
        standings_state = StandingsState.NONE

    # ── All rankings (for standings table) ───────────────────────────────────
    ranking_rows = (
        db.query(TournamentRanking)
        .filter(TournamentRanking.tournament_id == tournament_id)
        .order_by(TournamentRanking.rank.asc().nulls_last())
        .all()
    )
    rank_uid_set = {r.user_id for r in ranking_rows if r.user_id}
    rank_user_cache = {
        u.id: (u.name or u.email)
        for u in db.query(User).filter(User.id.in_(rank_uid_set)).all()
    } if rank_uid_set else {}
    live_rankings = [
        {
            "rank": r.rank,
            "name": rank_user_cache.get(r.user_id, f"Player #{r.user_id}"),
            "points": r.points,
            "wins": r.wins,
            "draws": r.draws,
            "losses": r.losses,
            "goals_for": r.goals_for,
            "goals_against": r.goals_against,
            "gd": (r.goals_for or 0) - (r.goals_against or 0),
            "is_me": r.user_id == user.id,
        }
        for r in ranking_rows
    ]

    # ── Phase-ordered sessions ────────────────────────────────────────────────
    _phase_order = sql_case(
        (SessionModel.tournament_phase == "GROUP_STAGE", 1),
        (SessionModel.tournament_phase == "KNOCKOUT", 2),
        (SessionModel.tournament_phase == "FINALS", 3),
        (SessionModel.tournament_phase == "PLACEMENT", 4),
        (SessionModel.tournament_phase == "SWISS", 5),
        (SessionModel.tournament_phase == "INDIVIDUAL_RANKING", 6),
        else_=9,
    )
    sessions_ordered = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .order_by(_phase_order, SessionModel.round_number.asc().nulls_last(), SessionModel.id)
        .all()
    )

    # Build user name cache for all session participants
    all_participant_uids: set[int] = set()
    for s in sessions_ordered:
        for uid in (s.participant_user_ids or []):
            all_participant_uids.add(uid)
    player_cache: dict[int, str] = {
        u.id: (u.name or u.email)
        for u in db.query(User).filter(User.id.in_(all_participant_uids)).all()
    } if all_participant_uids else {}

    # Quiz lookup per session
    sq_by_session: dict[int, SessionQuiz] = {
        sq.session_id: sq
        for sq in db.query(SessionQuiz).filter(
            SessionQuiz.session_id.in_([s.id for s in sessions_ordered])
        ).all()
    }
    completed_quiz_ids: set[int] = set()
    if enrollment:
        quiz_ids_all = {sq.quiz_id for sq in sq_by_session.values()}
        for qa in db.query(QuizAttempt).filter(
            QuizAttempt.quiz_id.in_(quiz_ids_all),
            QuizAttempt.user_id == user.id,
            QuizAttempt.completed_at.isnot(None),
        ).all():
            completed_quiz_ids.add(qa.quiz_id)

    # Sessions where user is an explicit participant
    my_session_ids: set[int] = {
        s.id for s in sessions_ordered if user.id in (s.participant_user_ids or [])
    }

    # My group (group_knockout)
    my_group: str | None = None
    if tournament_type_code == "group_knockout":
        for s in sessions_ordered:
            if s.tournament_phase == "GROUP_STAGE" and user.id in (s.participant_user_ids or []):
                my_group = (s.structure_config or {}).get("group")
                break

    # ── Enrich sessions ───────────────────────────────────────────────────────
    def _round_label(s: SessionModel) -> str:
        sc = s.structure_config or {}
        phase = s.tournament_phase or ""
        if sc.get("round_name"):
            return sc["round_name"]
        if phase == "GROUP_STAGE":
            grp = sc.get("group", "")
            rn = s.tournament_round or s.round_number
            return f"Group {grp} – Round {rn}" if grp else f"Group Round {rn}"
        if s.round_number:
            return f"Round {s.round_number}"
        return phase.replace("_", " ").title() if phase else "Session"

    def _parse_h2h_scores(s: SessionModel, uids: list[int]) -> tuple:
        """Returns (score_a, score_b) from game_results; None if not available."""
        if not s.game_results or len(uids) < 2:
            return None, None
        try:
            gr = _json.loads(s.game_results) if isinstance(s.game_results, str) else s.game_results
            by_uid = {p["user_id"]: p for p in (gr.get("participants") or [])}
            pa, pb = by_uid.get(uids[0]), by_uid.get(uids[1])
            if pa and pb:
                return int(pa.get("score") or 0), int(pb.get("score") or 0)
        except Exception:
            pass
        return None, None

    enriched_sessions = []
    for s in sessions_ordered:
        sq = sq_by_session.get(s.id)
        quiz_id = sq.quiz_id if sq else None
        quiz_completed = quiz_id in completed_quiz_ids if quiz_id else False
        quiz_pending = bool(quiz_id and not quiz_completed and enrollment)

        phase = s.tournament_phase or ""
        round_name = _round_label(s)
        uids = list(s.participant_user_ids or [])
        score_a, score_b = _parse_h2h_scores(s, uids)

        # Opponent for H2H sessions
        opponent_label = None
        if len(uids) == 2:
            opp_uid = uids[1] if uids[0] == user.id else uids[0]
            opponent_label = player_cache.get(opp_uid, f"Player #{opp_uid}")

        # My score for IR sessions
        my_ir_score = my_ir_position = None
        if phase == "INDIVIDUAL_RANKING" or (not tournament_type_code and phase == ""):
            rr = (s.rounds_data or {}).get("round_results", {})
            r1 = rr.get("1", {}) if rr else {}
            raw = r1.get(str(user.id))
            if raw is not None:
                try:
                    my_ir_score = float(raw)
                except (ValueError, TypeError):
                    my_ir_score = raw
            pd = r1.get("player_data", {})
            my_pd = pd.get(f"user_{user.id}", {})
            my_ir_position = my_pd.get("position")

        # Meeting link for virtual sessions
        meeting_link = None
        if cfg and cfg.session_type_config in ("virtual", "hybrid"):
            meeting_link = getattr(s, "meeting_link", None) or (cfg.meeting_link if cfg else None)

        enriched_sessions.append({
            "session": s,
            "quiz_id": quiz_id,
            "quiz_completed": quiz_completed,
            "quiz_pending": quiz_pending,
            "phase": phase,
            "round_name": round_name,
            "opponent_label": opponent_label,
            "score_a": score_a,
            "score_b": score_b,
            "is_my_session": s.id in my_session_ids,
            "my_ir_score": my_ir_score,
            "my_ir_position": my_ir_position,
            "meeting_link": meeting_link,
        })

    # Group sessions by phase/round for template rendering
    schedule_by_phase = []
    for (phase, round_name), items in _groupby(
        enriched_sessions, key=lambda x: (x["phase"], x["round_name"])
    ):
        schedule_by_phase.append({
            "phase": phase,
            "phase_label": phase.replace("_", " ").title() if phase else "Sessions",
            "round_name": round_name,
            "sessions": list(items),
        })

    # ── Group standings (group_knockout) ──────────────────────────────────────
    group_standings: dict[str, list[dict]] = {}
    if tournament_type_code == "group_knockout":
        grp_data: dict[str, dict[int, dict]] = {}
        for s in sessions_ordered:
            if s.tournament_phase != "GROUP_STAGE" or not s.game_results:
                continue
            sc = s.structure_config or {}
            grp = sc.get("group") or "?"
            try:
                gr = _json.loads(s.game_results) if isinstance(s.game_results, str) else s.game_results
                parts = gr.get("participants") or []
                if len(parts) < 2:
                    continue
                pa, pb = parts[0], parts[1]
                uid_a, uid_b = pa.get("user_id"), pb.get("user_id")
                s_a, s_b = float(pa.get("score") or 0), float(pb.get("score") or 0)
                for uid in (uid_a, uid_b):
                    if uid is not None:
                        grp_data.setdefault(grp, {}).setdefault(uid, {"W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0})
                for uid, sf, sa, res in [
                    (uid_a, int(s_a), int(s_b), pa.get("result", "")),
                    (uid_b, int(s_b), int(s_a), pb.get("result", "")),
                ]:
                    if uid is not None and grp in grp_data and uid in grp_data[grp]:
                        e = grp_data[grp][uid]
                        e["GF"] += sf; e["GA"] += sa
                        if res == "win":   e["W"] += 1
                        elif res == "draw": e["D"] += 1
                        else:              e["L"] += 1
            except Exception:
                pass
        for grp in sorted(grp_data.keys()):
            rows_g = []
            for pid, s in grp_data[grp].items():
                pts = s["W"] * 3 + s["D"]
                gd = s["GF"] - s["GA"]
                rows_g.append({
                    "name": player_cache.get(pid, f"Player #{pid}"),
                    "id": pid,
                    "W": s["W"], "D": s["D"], "L": s["L"],
                    "GF": s["GF"], "GA": s["GA"], "GD": gd, "Pts": pts,
                    "is_me": pid == user.id,
                })
            rows_g.sort(key=lambda r: (-r["Pts"], -r["GD"], -r["GF"], r["id"]))
            group_standings[grp] = rows_g

    # ── Bracket rounds (knockout / group_knockout KO phase) ───────────────────
    bracket_rounds: list[dict] = []
    if tournament_type_code in ("knockout", "group_knockout"):
        _KO_PHASES = {"KNOCKOUT", "FINALS", "PLACEMENT"}
        round_map: dict[int, dict] = {}
        for s in sessions_ordered:
            phase_s = s.tournament_phase or ""
            if tournament_type_code == "group_knockout" and phase_s not in _KO_PHASES:
                continue
            rn = s.round_number or 0
            sc_b = s.structure_config or {}
            rname = sc_b.get("round_name") or (f"Round {rn}" if rn > 0 else "Round")
            uids_b = list(s.participant_user_ids or [])
            a_b = player_cache.get(uids_b[0], "TBD") if uids_b else "TBD"
            b_b = player_cache.get(uids_b[1], "TBD") if len(uids_b) >= 2 else "TBD"
            sa_b, sb_b = _parse_h2h_scores(s, uids_b)
            if rn not in round_map:
                round_map[rn] = {"round": rn, "round_name": rname, "matches": []}
            round_map[rn]["matches"].append({
                "team_a": a_b, "team_b": b_b,
                "score_a": sa_b, "score_b": sb_b,
                "done": s.session_status == "completed",
                "tbd": a_b == "TBD" and b_b == "TBD",
                "is_my_match": user.id in (s.participant_user_ids or []),
                "date": s.date_start,
            })
        bracket_rounds = sorted(round_map.values(), key=lambda r: r["round"])

    return templates.TemplateResponse(
        "tournament_detail.html",
        {
            "request": request,
            "user": user,
            "tournament": tournament,
            "cfg": cfg,
            "enrollment": enrollment,
            "session_info": enriched_sessions,
            "schedule_by_phase": schedule_by_phase,
            "my_ranking": my_ranking,
            "enrollment_count": enrollment_count,
            "max_players": tournament.max_players,
            "instructor": instructor,
            # Tournament structure context
            "tournament_type_code": tournament_type_code,
            "type_display_name": type_display_name,
            "ranking_type": ranking_type,
            "show_wdl": show_wdl,
            "standings_state": standings_state,
            "live_rankings": live_rankings,
            "group_standings": group_standings,
            "bracket_rounds": bracket_rounds,
            "my_group": my_group,
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


# ── Camps ──────────────────────────────────────────────────────────────────────

@router.get("/events/camps", response_class=HTMLResponse)
async def events_camps_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """CAMP browse — enrolled section + available section."""
    camps = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.tournament_status.in_(["ENROLLMENT_OPEN", "IN_PROGRESS", "ENROLLMENT_CLOSED"]),
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.asc())
        .all()
    )

    enrolled_events = []
    browse_events = []
    for c in camps:
        enrollment_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == c.id,
                SemesterEnrollment.is_active.is_(True),
            )
            .count()
        )
        user_enrollment = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == c.id,
                SemesterEnrollment.user_id == user.id,
                SemesterEnrollment.is_active.is_(True),
            )
            .first()
        )
        info = {
            "camp": c,
            "enrollment_count": enrollment_count,
            "max_players": c.max_players or 999,
            "is_enrolled": user_enrollment is not None,
        }
        if user_enrollment is not None:
            enrolled_events.append(info)
        else:
            browse_events.append(info)

    return templates.TemplateResponse(
        "events_camps.html",
        {
            "request": request,
            "user": user,
            "enrolled_events": enrolled_events,
            "browse_events": browse_events,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.get("/events/camps/{camp_id}", response_class=HTMLResponse)
async def events_camp_detail(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """CAMP student detail page — enrollment panel + session list."""
    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
    ).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Camp not found")

    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active.is_(True),
    ).first()

    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == camp_id)
        .order_by(SessionModel.date_start.asc())
        .all()
    )

    enrollment_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.is_active.is_(True),
    ).count()

    instructor = None
    if camp.master_instructor_id:
        instructor = db.query(User).filter(User.id == camp.master_instructor_id).first()

    return templates.TemplateResponse(
        "events_camp_detail.html",
        {
            "request": request,
            "user": user,
            "camp": camp,
            "enrollment": enrollment,
            "sessions": sessions,
            "enrollment_count": enrollment_count,
            "max_players": camp.max_players,
            "instructor": instructor,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.post("/events/camps/{camp_id}/enroll", response_class=HTMLResponse)
async def events_camp_enroll(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Enroll current student in a camp (auto-approved, deducts credits)."""

    def _err(msg: str):
        return RedirectResponse(
            url=f"/events/camps/{camp_id}?flash={msg}&flash_type=error", status_code=303
        )

    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
        Semester.status != SemesterStatus.CANCELLED,
    ).first()
    if not camp:
        return RedirectResponse(url="/events/camps?flash=Camp+not+found&flash_type=error", status_code=303)

    if camp.tournament_status not in ("ENROLLMENT_OPEN", "IN_PROGRESS"):
        return _err("Camp+not+open+for+enrollment")

    if user.role != UserRole.STUDENT:
        return _err("Only+students+can+enroll")

    # License: any active LFA_FOOTBALL_PLAYER license
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.is_active.is_(True),
    ).first()
    if not license:
        return _err("No+active+license+found.+Complete+onboarding+first.")

    # Not already enrolled
    existing = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active.is_(True),
    ).first()
    if existing:
        return RedirectResponse(
            url=f"/events/camps/{camp_id}?flash=Already+enrolled&flash_type=info", status_code=303
        )

    cost = camp.enrollment_cost if camp.enrollment_cost is not None else 0
    if user.credit_balance < cost:
        return _err(f"Insufficient+credits+(need+{cost}%2C+have+{user.credit_balance})")

    max_p = camp.max_players if camp.max_players else 999
    enrolled_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.is_active.is_(True),
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
    ).count()
    if enrolled_count >= max_p:
        return _err("Camp+is+full")

    enrollment = SemesterEnrollment(
        user_id=user.id,
        semester_id=camp_id,
        user_license_id=license.id,
        request_status=EnrollmentStatus.APPROVED,
        approved_at=datetime.utcnow(),
        approved_by=user.id,
        payment_verified=True,
        is_active=True,
        enrolled_at=datetime.utcnow(),
        requested_at=datetime.utcnow(),
    )
    db.add(enrollment)
    db.flush()

    if cost > 0:
        result = db.execute(
            sql_update(User)
            .where(User.id == user.id, User.credit_balance >= cost)
            .values(credit_balance=User.credit_balance - cost)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            db.rollback()
            return _err("Insufficient+credits+(concurrent+update)")
        db.refresh(user)

        db.add(CreditTransaction(
            user_license_id=license.id,
            transaction_type="TOURNAMENT_ENROLLMENT",
            amount=-cost,
            balance_after=user.credit_balance,
            description=f"Camp enrollment: {camp.name} ({camp.code})",
            semester_id=camp_id,
            enrollment_id=enrollment.id,
            idempotency_key=f"web_camp_enroll_{camp_id}_{user.id}_{enrollment.id}",
        ))

    db.commit()

    camp_name = camp.name.replace(" ", "+")
    return RedirectResponse(
        url=f"/events/camps/{camp_id}?flash=Successfully+enrolled+in+{camp_name}&flash_type=success",
        status_code=303,
    )


@router.post("/events/camps/{camp_id}/unenroll", response_class=HTMLResponse)
async def events_camp_unenroll(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Withdraw student from camp (50% refund)."""
    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.is_active.is_(True),
    ).first()
    if not enrollment:
        return RedirectResponse(
            url=f"/events/camps/{camp_id}?flash=No+active+enrollment+found&flash_type=error",
            status_code=303,
        )

    camp = db.query(Semester).filter(Semester.id == camp_id).first()
    cost = camp.enrollment_cost if camp and camp.enrollment_cost else 0
    refund = cost // 2

    enrollment.is_active = False
    enrollment.request_status = EnrollmentStatus.WITHDRAWN
    db.add(enrollment)

    if refund > 0:
        db.execute(
            sql_update(User)
            .where(User.id == user.id)
            .values(credit_balance=User.credit_balance + refund)
            .execution_options(synchronize_session=False)
        )
        db.refresh(user)

        db.add(CreditTransaction(
            user_license_id=enrollment.user_license_id,
            transaction_type="TOURNAMENT_UNENROLL_REFUND",
            amount=refund,
            balance_after=user.credit_balance,
            description=f"Camp unenrollment refund (50%): {camp.name if camp else camp_id}",
            semester_id=camp_id,
            enrollment_id=enrollment.id,
            idempotency_key=f"web_camp_unenroll_{camp_id}_{user.id}_{enrollment.id}",
        ))

    db.commit()

    return RedirectResponse(
        url=f"/events/camps/{camp_id}?flash=Unenrolled.+{refund}+credits+refunded.&flash_type=info",
        status_code=303,
    )


# ── Stubs — Coming Soon ────────────────────────────────────────────────────────

@router.get("/events/academy-seasons", response_class=HTMLResponse)
async def events_academy_seasons(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "events_stub.html",
        {
            "request": request,
            "user": user,
            "event_type": "Academy Seasons",
            "event_icon": "📚",
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.get("/events/mini-seasons", response_class=HTMLResponse)
async def events_mini_seasons(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "events_stub.html",
        {
            "request": request,
            "user": user,
            "event_type": "Mini Seasons",
            "event_icon": "📅",
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )

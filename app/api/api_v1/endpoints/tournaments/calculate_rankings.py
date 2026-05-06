"""
Tournament Rankings Calculation Endpoint

Calculates and stores final tournament rankings for both INDIVIDUAL and HEAD_TO_HEAD tournaments.

For INDIVIDUAL tournaments:
- Uses existing ranking strategies (TIME_BASED, SCORE_BASED, ROUNDS_BASED, etc.)

For HEAD_TO_HEAD tournaments:
- League: Points-based ranking (Win=3, Tie=1, Loss=0) with tiebreakers
- Knockout: Bracket-based ranking (Final winner=1, Runner-up=2, etc.)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import and_

import json

from app.database import get_db
from app.dependencies import get_current_user, get_current_admin_or_instructor_user_hybrid
from app.models.user import User, UserRole
from app.models.semester import Semester
from app.models.session import Session as SessionModel, EventCategory
from app.models.booking import Booking
from app.models.tournament_ranking import TournamentRanking
from app.models.tournament_achievement import TournamentParticipation
from app.models.team import Team
from app.services.tournament.ranking.strategies.factory import RankingStrategyFactory

router = APIRouter()


@router.post("/{tournament_id}/calculate-rankings", status_code=status.HTTP_200_OK)
def calculate_tournament_rankings(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user_hybrid)
):
    """
    Calculate and store tournament rankings

    For INDIVIDUAL tournaments:
    - Reads session results (rounds_data or game_results)
    - Applies appropriate ranking strategy (TIME_BASED, SCORE_BASED, etc.)
    - Stores rankings in tournament_rankings table

    For HEAD_TO_HEAD tournaments:
    - Reads all match results from sessions
    - Applies league or knockout ranking logic
    - Stores rankings in tournament_rankings table

    Authorization: Master Instructor or Admin only
    Idempotent: Can be called multiple times (overwrites existing rankings)
    """
    # Get tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    # Authorization: Only master instructor or admin
    if current_user.role != UserRole.ADMIN and tournament.master_instructor_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the tournament's master instructor can calculate rankings"
        )

    # Get tournament format
    tournament_format = tournament.format  # "INDIVIDUAL_RANKING" or "HEAD_TO_HEAD"

    # Get all sessions for this tournament
    all_sessions = db.query(SessionModel).filter(
        and_(
            SessionModel.semester_id == tournament_id,
            SessionModel.event_category == EventCategory.MATCH
        )
    ).all()

    if not all_sessions:
        raise HTTPException(
            status_code=400,
            detail="No tournament sessions found. Generate sessions first."
        )

    # Get tournament type to determine which sessions to validate
    tournament_type_code = None
    if tournament.tournament_config_obj and tournament.tournament_config_obj.tournament_type:
        tournament_type_code = tournament.tournament_config_obj.tournament_type.code

    # DEBUG: Log tournament type detection
    print(f"🔍 [calculate_rankings] tournament_id={tournament_id}")
    print(f"🔍 [calculate_rankings] tournament_type_code={tournament_type_code}")
    print(f"🔍 [calculate_rankings] all_sessions count={len(all_sessions)}")

    # For group+knockout tournaments: use GROUP_STAGE sessions for validation,
    # but pass ALL completed sessions (group + knockout) to the ranking strategy
    # so final ranks reflect bracket position, not group stage points.
    if tournament_type_code and tournament_type_code.startswith("group_knockout"):
        group_sessions = [s for s in all_sessions if s.tournament_phase == "GROUP_STAGE"]
        knockout_sessions = [
            s for s in all_sessions
            if s.tournament_phase == "KNOCKOUT" and s.game_results
        ]
        print(f"🔍 [calculate_rankings] group_knockout: {len(group_sessions)} group, {len(knockout_sessions)} completed knockout sessions")
        if not group_sessions:
            raise HTTPException(
                status_code=400,
                detail="No GROUP_STAGE sessions found. Cannot calculate rankings."
            )
        # Validate only group stage sessions must all have results
        group_missing = [s for s in group_sessions if not s.game_results]
        if group_missing:
            raise HTTPException(
                status_code=400,
                detail=f"{len(group_missing)} GROUP_STAGE session(s) do not have results yet."
            )

        # Assign semifinal participants if qualification policy requires it.
        # Must run after group-complete validation above (group_missing guard)
        # so that assign_semifinal_participants() always sees complete standings.
        # Policy is stored per player-count in group_configuration[N_players] —
        # same location the generator reads it from; top-level lookup was wrong.
        _tt_cfg = (
            tournament.tournament_config_obj.tournament_type.config
            if tournament.tournament_config_obj
            and tournament.tournament_config_obj.tournament_type
            else {}
        )
        _tc = tournament.tournament_config_obj
        _snap = (getattr(_tc, 'enrollment_snapshot', None) or {})
        _total_enrolled = _snap.get('total_enrolled')
        if not _total_enrolled:
            from sqlalchemy import func
            from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
            _total_enrolled = db.query(func.count(SemesterEnrollment.id)).filter(
                SemesterEnrollment.semester_id == tournament_id,
                SemesterEnrollment.is_active == True,
                SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            ).scalar() or 0
        _per_size_cfg = _tt_cfg.get('group_configuration', {}).get(
            f'{_total_enrolled}_players', {}
        )
        _q_policy = _per_size_cfg.get('qualification_policy', 'fixed_per_group')
        _brc = int(_per_size_cfg.get('best_runner_up_count', 0))
        if _q_policy == 'winners_plus_best_runner_up' and _brc > 0:
            from app.services.tournament.qualification import assign_semifinal_participants
            assign_semifinal_participants(db, tournament_id, _brc)

        # Pass all sessions (group + completed knockout) to strategy
        sessions = group_sessions + knockout_sessions
    else:
        sessions = all_sessions
        print(f"🔍 [calculate_rankings] Using all sessions: {len(sessions)} sessions")
        # Validate all sessions have results
        sessions_with_results = [s for s in sessions if s.game_results or (s.rounds_data and s.rounds_data.get("round_results"))]
        if len(sessions_with_results) < len(sessions):
            missing_count = len(sessions) - len(sessions_with_results)
            raise HTTPException(
                status_code=400,
                detail=f"{missing_count} session(s) do not have results submitted yet. Submit all results first."
            )

    # Swiss format guard: no automatic ranking strategy available
    if tournament_type_code == "swiss":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Swiss format does not support automatic ranking calculation. "
                "Rankings must be entered manually."
            ),
        )

    # Calculate rankings based on tournament format
    try:
        if tournament_format == "HEAD_TO_HEAD":
            # Get tournament type (league, knockout, etc.)
            tournament_type_code = None
            if tournament.tournament_config_obj and tournament.tournament_config_obj.tournament_type:
                tournament_type_code = tournament.tournament_config_obj.tournament_type.code

            if not tournament_type_code:
                raise HTTPException(
                    status_code=400,
                    detail="HEAD_TO_HEAD tournament missing tournament_type"
                )

            _cfg = tournament.tournament_config_obj
            _is_team = _cfg and _cfg.participant_type == "TEAM"

            if _is_team:
                # TEAM HEAD_TO_HEAD: results stored in rounds_data (game_results is NULL).
                # Standard strategies read only game_results — handle TEAM inline.
                from collections import defaultdict as _dd
                team_stats = _dd(lambda: {
                    "points": 0, "wins": 0, "ties": 0, "losses": 0,
                    "goals_scored": 0.0, "goals_conceded": 0.0,
                })
                for _s in sessions:
                    for _rd in (_s.rounds_data or {}).get("round_results", {}).values():
                        _tscores: dict = {}
                        for _key, _val in (_rd or {}).items():
                            if _key.startswith("team_"):
                                try:
                                    _tid = int(_key.split("_", 1)[1])
                                    _tscores[_tid] = float(_val)
                                except (ValueError, IndexError):
                                    pass
                        if len(_tscores) != 2:
                            continue
                        (_t1, _s1), (_t2, _s2) = list(_tscores.items())
                        team_stats[_t1]["goals_scored"] += _s1
                        team_stats[_t1]["goals_conceded"] += _s2
                        team_stats[_t2]["goals_scored"] += _s2
                        team_stats[_t2]["goals_conceded"] += _s1
                        if _s1 > _s2:
                            team_stats[_t1]["points"] += 3
                            team_stats[_t1]["wins"] += 1
                            team_stats[_t2]["losses"] += 1
                        elif _s2 > _s1:
                            team_stats[_t2]["points"] += 3
                            team_stats[_t2]["wins"] += 1
                            team_stats[_t1]["losses"] += 1
                        else:
                            team_stats[_t1]["points"] += 1
                            team_stats[_t1]["ties"] += 1
                            team_stats[_t2]["points"] += 1
                            team_stats[_t2]["ties"] += 1
                if not team_stats:
                    raise ValueError("No round results found in TEAM HEAD_TO_HEAD sessions.")
                _ranked = sorted(
                    team_stats.items(),
                    key=lambda x: (
                        -x[1]["points"],
                        -(x[1]["goals_scored"] - x[1]["goals_conceded"]),
                        -x[1]["goals_scored"],
                    ),
                )
                rankings = [
                    {
                        "team_id": _tid, "user_id": None, "rank": i + 1,
                        "points": _st["points"], "wins": _st["wins"],
                        "ties": _st["ties"], "losses": _st["losses"],
                        "goals_for": _st["goals_scored"], "goals_against": _st["goals_conceded"],
                    }
                    for i, (_tid, _st) in enumerate(_ranked)
                ]
            else:
                # Create ranking strategy
                strategy = RankingStrategyFactory.create(
                    tournament_format="HEAD_TO_HEAD",
                    tournament_type_code=tournament_type_code
                )

                # Calculate rankings
                rankings = strategy.calculate_rankings(sessions, db)

        else:
            # INDIVIDUAL_RANKING format — check whether TEAM or INDIVIDUAL participant_type
            cfg = tournament.tournament_config_obj
            is_team = cfg and cfg.participant_type == "TEAM"

            ranking_direction = "ASC"
            if cfg:
                ranking_direction = cfg.ranking_direction or "ASC"

            # Aggregate round_results across all sessions
            combined_round_results: dict = {}
            for _s in sessions:
                _rd = _s.rounds_data or {}
                _rr = _rd.get("round_results", {})
                if isinstance(_rr, dict):
                    for _rk, _pv in _rr.items():
                        if isinstance(_pv, dict):
                            if _rk not in combined_round_results:
                                combined_round_results[_rk] = {}
                            combined_round_results[_rk].update(_pv)

            if not combined_round_results:
                raise HTTPException(
                    status_code=400,
                    detail="No round results found in sessions. Submit all results first."
                )

            if is_team:
                # TEAM aggregation: keys are "team_{id}" → aggregate per team
                team_scores: dict[int, float] = {}
                for rk, player_values in combined_round_results.items():
                    for key, val in player_values.items():
                        if key.startswith("team_"):
                            try:
                                tid = int(key.split("_", 1)[1])
                                team_scores[tid] = team_scores.get(tid, 0.0) + float(val)
                            except (ValueError, IndexError):
                                pass
                ranked_teams = sorted(
                    team_scores.items(),
                    key=lambda x: x[1],
                    reverse=(ranking_direction == "DESC")
                )
                rankings = [
                    {"team_id": tid, "user_id": None, "rank": i + 1, "points": score}
                    for i, (tid, score) in enumerate(ranked_teams)
                ]
            else:
                from app.services.tournament.results.calculators.ranking_aggregator import RankingAggregator
                _user_finals = RankingAggregator.aggregate_user_values(combined_round_results, ranking_direction)
                _perf_rankings = RankingAggregator.calculate_performance_rankings(_user_finals, ranking_direction)
                rankings = [
                    {
                        "user_id": r["user_id"],
                        "team_id": None,
                        "rank": r["rank"],
                        "points": r["final_value"],
                    }
                    for r in _perf_rankings
                ]

    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Ranking calculation error: {str(e)}"
        )

    # Delete existing rankings for this tournament (idempotency)
    db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == tournament_id
    ).delete()

    # Determine participant_type from config
    cfg = tournament.tournament_config_obj
    is_team = cfg and cfg.participant_type == "TEAM"
    pt_label = "TEAM" if is_team else "INDIVIDUAL"

    # Insert new rankings
    for ranking_data in rankings:
        ranking_record = TournamentRanking(
            tournament_id=tournament_id,
            user_id=ranking_data.get("user_id"),
            team_id=ranking_data.get("team_id"),
            participant_type=pt_label,
            rank=ranking_data["rank"],
            points=ranking_data.get("points", 0),
            wins=ranking_data.get("wins", 0),
            losses=ranking_data.get("losses", 0),
            draws=ranking_data.get("ties", 0),
            goals_for=ranking_data.get("goals_for", ranking_data.get("goals_scored", 0)),
            goals_against=ranking_data.get("goals_against", ranking_data.get("goals_conceded", 0)),
        )
        db.add(ranking_record)

    db.commit()

    return {
        "tournament_id": tournament_id,
        "tournament_format": tournament_format,
        "participant_type": pt_label,
        "rankings_count": len(rankings),
        "rankings": rankings,
        "message": "Tournament rankings calculated and stored successfully"
    }


@router.get("/{tournament_id}/rankings", status_code=status.HTTP_200_OK)
def get_tournament_rankings(
    tournament_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get tournament rankings

    Returns stored rankings from tournament_rankings table
    """
    # Get tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()

    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    # Get rankings with player names (prefer nickname for uniqueness) and team names
    rows = (
        db.query(TournamentRanking, User.nickname, User.name, Team.name.label("team_name"))
        .join(User, User.id == TournamentRanking.user_id, isouter=True)
        .join(Team, Team.id == TournamentRanking.team_id, isouter=True)
        .filter(TournamentRanking.tournament_id == tournament_id)
        .order_by(TournamentRanking.rank)
        .all()
    )

    if not rows:
        return {
            "tournament_id": tournament_id,
            "rankings": [],
            "message": "No rankings calculated yet. Call /calculate-rankings first."
        }

    # Build user_id -> group_identifier map from GROUP_STAGE sessions
    user_group_map: dict = {}
    group_sessions = (
        db.query(SessionModel.group_identifier, SessionModel.participant_user_ids, SessionModel.id)
        .filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.group_identifier.isnot(None)
        )
        .all()
    )
    for gid, participant_user_ids_raw, session_id in group_sessions:
        if not gid:
            continue
        # Try participant_user_ids JSON column first
        if participant_user_ids_raw:
            try:
                uid_list = participant_user_ids_raw if isinstance(participant_user_ids_raw, list) else json.loads(participant_user_ids_raw)
                for uid in uid_list:
                    if uid not in user_group_map:
                        user_group_map[int(uid)] = gid
            except (ValueError, TypeError):
                pass
        # Fallback: derive from bookings
        if not participant_user_ids_raw:
            booking_uids = db.query(Booking.user_id).filter(Booking.session_id == session_id).all()
            for (uid,) in booking_uids:
                if uid not in user_group_map:
                    user_group_map[uid] = gid

    # Load reward data if tournament is REWARDS_DISTRIBUTED
    reward_map: dict = {}       # user_id  → per-user reward summary
    team_reward_map: dict = {}  # team_id  → aggregated reward summary across members
    if tournament.tournament_status == "REWARDS_DISTRIBUTED":
        participation_rows = db.query(TournamentParticipation).filter(
            TournamentParticipation.semester_id == tournament_id
        ).all()

        for p in participation_rows:
            if p.user_id:
                # skill_rating_delta is written at reward-distribution time and is
                # isolated to this tournament — authoritative, not derived from global state
                reward_map[p.user_id] = {
                    "xp_earned": p.xp_awarded or 0,
                    "credits_earned": p.credits_awarded or 0,
                    "skills_awarded": p.skill_points_awarded or {},
                    "skill_rating_delta": p.skill_rating_delta or {},
                }
                # Aggregate into team bucket when the participation is team-linked
                if p.team_id:
                    bucket = team_reward_map.setdefault(p.team_id, {
                        "xp_earned": 0, "credits_earned": 0,
                        "skills_awarded": {}, "skill_rating_delta": {},
                    })
                    bucket["xp_earned"] += p.xp_awarded or 0
                    bucket["credits_earned"] += p.credits_awarded or 0

    # For INDIVIDUAL_RANKING tournaments, points = measured_value (set by ResultProcessor)
    is_ir_tournament = tournament.format == "INDIVIDUAL_RANKING"

    # Build per-user round results map for ROUNDS_BASED sessions
    # Structure: {user_id: {"1": "18.2", "2": "17.1", "total_rounds": 3}}
    user_round_results: dict = {}
    if is_ir_tournament:
        ir_sessions = db.query(SessionModel).filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.event_category == EventCategory.MATCH,
            SessionModel.match_format == "INDIVIDUAL_RANKING",
        ).all()
        for ir_sess in ir_sessions:
            rd = ir_sess.rounds_data or {}
            rr = rd.get("round_results")
            # Only process if round_results is a proper dict (not old list format)
            if not isinstance(rr, dict):
                continue
            total_rounds = int(rd.get("total_rounds", len(rr)))
            for round_key, player_values in rr.items():
                if not isinstance(player_values, dict):
                    continue
                for uid_str, val in player_values.items():
                    try:
                        uid = int(uid_str)
                    except (ValueError, TypeError):
                        continue
                    if uid not in user_round_results:
                        user_round_results[uid] = {"total_rounds": total_rounds}
                    user_round_results[uid][round_key] = val

    # Format response
    rankings_data = []
    for r, nickname, name, team_name in rows:
        display_name = nickname or name
        points_val = float(r.points) if r.points is not None else 0
        entry = {
            "user_id": r.user_id,
            "team_id": r.team_id,
            "team_name": team_name,
            "name": display_name,
            "rank": r.rank,
            "points": points_val,
            "wins": r.wins,
            "losses": r.losses,
            "draws": r.draws,
            "goals_for": r.goals_for,
            "goals_against": r.goals_against,
            "goal_difference": (r.goals_for or 0) - (r.goals_against or 0),
            "group_identifier": user_group_map.get(r.user_id),
        }
        # For IR tournaments expose the stored value under measured_value so the
        # leaderboard component can display it (points = measured_value for IR)
        if is_ir_tournament:
            entry["measured_value"] = points_val
            # Attach per-round breakdown if available (ROUNDS_BASED sessions)
            if r.user_id in user_round_results:
                entry["round_results"] = user_round_results[r.user_id]
        if reward_map or team_reward_map:
            # Always include reward fields when tournament is REWARDS_DISTRIBUTED.
            # TEAM rows aggregate across all active team members; INDIVIDUAL rows use per-user data.
            defaults = {
                "xp_earned": 0,
                "credits_earned": 0,
                "skills_awarded": {},
                "skill_rating_delta": {},
            }
            if r.team_id is not None:
                defaults.update(team_reward_map.get(r.team_id, {}))
            else:
                defaults.update(reward_map.get(r.user_id, {}))
            entry.update(defaults)
        rankings_data.append(entry)

    return {
        "tournament_id": tournament_id,
        "tournament_format": tournament.format,
        "rankings_count": len(rankings_data),
        "rankings": rankings_data
    }

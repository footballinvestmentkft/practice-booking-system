"""INDIVIDUAL_RANKING simulation helpers."""
import logging as _logging
from sqlalchemy.orm import Session

from ._session_helpers import _get_tournament_sessions


def _calculate_ir_rankings(tournament, sessions: list, logger: _logging.Logger) -> list:
    """Calculate INDIVIDUAL_RANKING rankings using RankingAggregator.

    Aggregates per-round results across all sessions for the tournament,
    then ranks players by their final value (direction-aware: ASC for time, DESC for score).

    Returns:
        List of ranking dicts: [{"user_id": int, "rank": int, "final_value": float}]
        Empty list if no round results are found.
    """
    from app.services.tournament.results.calculators.ranking_aggregator import RankingAggregator

    _combined_rr: dict = {}
    for _s in sessions:
        _rd = _s.rounds_data or {}
        _rr = _rd.get("round_results", {})
        if isinstance(_rr, dict):
            for _rk, _pv in _rr.items():
                if isinstance(_pv, dict):
                    _combined_rr[_rk] = _pv

    _ranking_direction = "ASC"
    if tournament.tournament_config_obj:
        _ranking_direction = tournament.tournament_config_obj.ranking_direction or "ASC"

    logger.info(
        "[ops] INDIVIDUAL_RANKING aggregator: direction=%s, rounds=%d",
        _ranking_direction,
        len(_combined_rr),
    )
    if _combined_rr:
        _user_finals = RankingAggregator.aggregate_user_values(_combined_rr, _ranking_direction)
        return RankingAggregator.calculate_performance_rankings(_user_finals, _ranking_direction)
    return []


def _simulate_individual_ranking(
    db: Session,
    tournament,
    logger: _logging.Logger,
) -> tuple[bool, str]:
    """
    Simulate INDIVIDUAL_RANKING tournament results.

    Generates random performance data based on scoring_type:
    - TIME_BASED: Random times (lower is better)
    - SCORE_BASED: Random scores (higher is better)
    - ROUNDS_BASED: Random rounds completed (higher is better)

    Returns:
        (success: bool, message: str)
    """
    import random
    import json
    from app.models.session import Session as SessionModel

    tournament_id = tournament.id

    # Get scoring_type from tournament configuration
    scoring_type = None
    if tournament.tournament_config_obj:
        scoring_type = tournament.tournament_config_obj.scoring_type

    if not scoring_type:
        return False, "INDIVIDUAL_RANKING tournament missing scoring_type"

    logger.info("[ops] Starting INDIVIDUAL_RANKING simulation: scoring_type=%s", scoring_type)

    # Get all tournament sessions
    sessions = _get_tournament_sessions(db, tournament_id)

    if not sessions:
        return False, "No tournament sessions found"

    simulated_count = 0
    skipped_count = 0

    for session in sessions:
        # Skip if no participants
        if not session.participant_user_ids or len(session.participant_user_ids) == 0:
            skipped_count += 1
            continue

        participants = session.participant_user_ids
        is_rounds_based = session.scoring_type == "ROUNDS_BASED"

        if is_rounds_based:
            # Multi-round session: skip only if ALL rounds already done
            rd = session.rounds_data or {}
            total_r = int(rd.get("total_rounds", 1))
            completed_r = int(rd.get("completed_rounds", 0))
            if completed_r >= total_r > 0:
                skipped_count += 1
                continue

            # Simulate each missing round using the underlying scoring type
            underlying = (session.structure_config or {}).get("scoring_method") or scoring_type
            new_rd = dict(rd)
            if "round_results" not in new_rd:
                new_rd["round_results"] = {}

            for rn in range(completed_r + 1, total_r + 1):
                rn_key = str(rn)
                if rn_key in new_rd["round_results"]:
                    continue  # already submitted
                round_entry = {}
                for user_id in participants:
                    if "TIME" in underlying:
                        val = f"{round(random.uniform(30.0, 120.0), 2)}"
                    elif "DISTANCE" in underlying:
                        val = f"{round(random.uniform(1.0, 50.0), 2)}"
                    else:
                        val = f"{random.randint(50, 100)}"
                    round_entry[str(user_id)] = val
                new_rd["round_results"][rn_key] = round_entry

            new_rd["completed_rounds"] = total_r
            session.rounds_data = new_rd
            from sqlalchemy.orm.attributes import flag_modified as _fm
            _fm(session, "rounds_data")

        else:
            # Single-round session: skip if already has results
            if session.game_results:
                skipped_count += 1
                continue

            round_results = []
            if scoring_type == "TIME_BASED":
                for user_id in participants:
                    round_results.append({
                        "user_id": user_id,
                        "measured_value": round(random.uniform(30.0, 120.0), 2),
                    })
            elif scoring_type == "SCORE_BASED":
                for user_id in participants:
                    round_results.append({
                        "user_id": user_id,
                        "measured_value": float(random.randint(50, 100)),
                    })
            elif scoring_type == "DISTANCE_BASED":
                for user_id in participants:
                    round_results.append({
                        "user_id": user_id,
                        "measured_value": round(random.uniform(1.0, 50.0), 2),
                    })
            else:
                logger.warning("[ops] Unsupported scoring_type: %s, skipping session %d",
                               scoring_type, session.id)
                skipped_count += 1
                continue

            # Use the same ResultProcessor that the manual submit endpoint uses
            try:
                from app.services.tournament.result_processor import ResultProcessor
                processor = ResultProcessor(db)
                processor.process_match_results(
                    db=db,
                    session=session,
                    tournament=tournament,
                    raw_results=round_results,
                    match_notes="OPS auto-simulated",
                    recorded_by_user_id=0,
                    recorded_by_name="OPS",
                )
            except Exception as _e:
                logger.warning("[ops] process_match_results failed for session %d: %s", session.id, _e)
                skipped_count += 1
                continue

        simulated_count += 1

    db.commit()

    logger.info(
        "[ops] INDIVIDUAL_RANKING simulation complete: %d sessions simulated, %d skipped",
        simulated_count, skipped_count
    )

    return True, f"{simulated_count} sessions simulated ({scoring_type}), {skipped_count} skipped"

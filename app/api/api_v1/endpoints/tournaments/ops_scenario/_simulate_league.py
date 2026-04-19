"""LEAGUE (round-robin) simulation helper."""
import logging as _logging
from sqlalchemy.orm import Session

from ._session_helpers import _get_tournament_sessions, _build_h2h_game_results


def _simulate_league_tournament(
    db: Session,
    tournament_id: int,
    logger: _logging.Logger,
) -> tuple[bool, str]:
    """
    Simulate LEAGUE (Round Robin) tournament:
    - All players play against each other once
    - Each match is HEAD_TO_HEAD
    - Final rankings based on points, goal difference, goals scored

    Returns:
        (success: bool, message: str)
    """
    import random
    import json
    from app.models.session import Session as SessionModel
    from sqlalchemy import asc

    logger.info("[ops] Starting LEAGUE (Round Robin) simulation for tournament_id=%d", tournament_id)

    # Get all tournament sessions
    sessions = _get_tournament_sessions(db, tournament_id, ordered=True)

    if not sessions:
        return False, "No tournament sessions found for simulation"

    simulated_count = 0
    skipped_count = 0

    for session in sessions:
        # Skip if already has results
        if session.game_results:
            skipped_count += 1
            continue

        # Verify session has participants
        if not session.participant_user_ids or len(session.participant_user_ids) < 2:
            logger.warning("[ops] League session %d has no participants, skipping", session.id)
            skipped_count += 1
            continue

        # Simulate HEAD_TO_HEAD match (1v1)
        user_id_1, user_id_2 = session.participant_user_ids[0], session.participant_user_ids[1]

        # Random match result: win, loss, or draw
        outcome = random.choice(["win", "draw", "win"])  # Bias towards decisive results

        if outcome == "draw":
            score_1 = random.randint(0, 3)
            score_2 = score_1  # Equal scores for draw
            result_1 = "draw"
            result_2 = "draw"
        else:
            # Winner gets higher score
            winner_score = random.randint(1, 5)
            loser_score = random.randint(0, winner_score - 1)

            if random.choice([True, False]):  # Randomly assign winner
                score_1 = winner_score
                score_2 = loser_score
                result_1 = "win"
                result_2 = "loss"
            else:
                score_1 = loser_score
                score_2 = winner_score
                result_1 = "loss"
                result_2 = "win"

        session.game_results = _build_h2h_game_results(
            [{"user_id": user_id_1, "result": result_1, "score": score_1},
             {"user_id": user_id_2, "result": result_2, "score": score_2}],
            session.tournament_round or 1,
        )
        session.session_status = "completed"
        simulated_count += 1

    db.commit()

    logger.info(
        "[ops] LEAGUE simulation complete: %d sessions simulated, %d skipped",
        simulated_count, skipped_count
    )

    return True, f"{simulated_count} league sessions simulated, {skipped_count} skipped"

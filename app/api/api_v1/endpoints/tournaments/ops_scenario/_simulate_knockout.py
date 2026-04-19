"""HEAD_TO_HEAD knockout simulation helpers."""
import logging as _logging
from sqlalchemy.orm import Session

from ._session_helpers import _get_tournament_sessions, _build_h2h_game_results


def _simulate_head_to_head_knockout(
    db: Session,
    tournament_id: int,
    logger: _logging.Logger,
) -> tuple[bool, str]:
    """
    Simulate HEAD_TO_HEAD knockout tournament with full bracket advancement.

    1. Process sessions round-by-round (Semi-finals → Final/3rd Place)
    2. For each session: randomly select a winner, populate game_results
    3. Advance winners/losers to next round sessions (update participant_user_ids)
    4. Continue until all rounds are simulated

    Returns:
        (success: bool, message: str)
    """
    import random
    import json
    from app.models.session import Session as SessionModel
    from sqlalchemy import asc

    logger.info("[ops] Starting HEAD_TO_HEAD knockout bracket simulation for tournament_id=%d", tournament_id)

    # Get all tournament sessions, ordered by tournament_round
    sessions = _get_tournament_sessions(db, tournament_id, ordered=True)

    if not sessions:
        return False, "No tournament sessions found for simulation"

    # Group sessions by round
    from collections import defaultdict
    rounds = defaultdict(list)
    for session in sessions:
        round_num = session.tournament_round or 1
        rounds[round_num].append(session)

    total_simulated = 0
    total_skipped = 0

    # Process rounds in order
    for round_num in sorted(rounds.keys()):
        round_sessions = rounds[round_num]
        logger.info("[ops] Processing Round %d (%d sessions)", round_num, len(round_sessions))

        round_simulated = 0
        round_winners = []
        round_losers = []

        for session in round_sessions:
            # Skip if already has results
            if session.game_results:
                total_skipped += 1
                continue

            # Skip if no participants assigned
            if not session.participant_user_ids or len(session.participant_user_ids) == 0:
                total_skipped += 1
                logger.warning("[ops] Session %d (Round %d, Match %d) has no participants — skipping",
                              session.id, session.tournament_round, session.tournament_match_number)
                continue

            # Knockout sessions have exactly 2 participants
            if len(session.participant_user_ids) != 2:
                total_skipped += 1
                logger.warning("[ops] Session %d has %d participants (expected 2) — skipping",
                              session.id, len(session.participant_user_ids))
                continue

            # Simulate: randomly pick a winner
            winner_id = random.choice(session.participant_user_ids)
            loser_id = [uid for uid in session.participant_user_ids if uid != winner_id][0]

            # Update session
            session.game_results = _build_h2h_game_results(
                [{"user_id": winner_id, "result": "win", "score": 3},
                 {"user_id": loser_id, "result": "loss", "score": 0}],
                session.tournament_round,
            )
            session.session_status = "completed"
            round_simulated += 1

            # Track winners and losers for bracket advancement
            round_winners.append(winner_id)
            round_losers.append(loser_id)

            logger.info("[ops] Round %d, Match %d: User %d defeats User %d",
                       round_num, session.tournament_match_number, winner_id, loser_id)

        # Commit after each round
        db.commit()
        total_simulated += round_simulated
        logger.info("[ops] Round %d complete: %d sessions simulated", round_num, round_simulated)

        # Bracket advancement: assign winners/losers to next round sessions
        next_round = round_num + 1
        if next_round in rounds:
            next_round_sessions = sorted(rounds[next_round], key=lambda s: s.tournament_match_number or 0)

            # Separate playoff (3rd place) sessions from main bracket sessions
            main_sessions = [s for s in next_round_sessions
                             if "3rd Place" not in (s.title or "") and "Playoff" not in (s.title or "")]
            playoff_sessions = [s for s in next_round_sessions
                                if "3rd Place" in (s.title or "") or "Playoff" in (s.title or "")]

            # General bracket pairing: pair winners into main bracket sessions (2 per session)
            for idx, ns in enumerate(main_sessions):
                p1_idx = idx * 2
                p2_idx = idx * 2 + 1
                if p1_idx < len(round_winners) and p2_idx < len(round_winners):
                    ns.participant_user_ids = [round_winners[p1_idx], round_winners[p2_idx]]
                    logger.info("[ops] Round %d→%d, Match %d: Assigned winners %s to session %d (%s)",
                               round_num, next_round, idx + 1,
                               ns.participant_user_ids, ns.id, ns.title)
                else:
                    logger.warning("[ops] Not enough winners (%d) to fill main session %d (slot %d-%d)",
                                   len(round_winners), ns.id, p1_idx, p2_idx)

            # Assign losers to 3rd Place Playoff (only if losers exist and there's a playoff session)
            if playoff_sessions and len(round_losers) >= 2:
                playoff_sessions[0].participant_user_ids = round_losers[:2]
                logger.info("[ops] Assigned losers %s to 3rd Place Playoff (session %d)",
                           round_losers[:2], playoff_sessions[0].id)

            db.commit()

    logger.info(
        "[ops] Bracket simulation complete: %d sessions simulated, %d skipped",
        total_simulated, total_skipped
    )

    return True, f"{total_simulated} sessions simulated, {total_skipped} skipped"


def _simulate_knockout_bracket(
    db: Session,
    knockout_sessions: list,
    logger: _logging.Logger,
) -> tuple[int, int]:
    """
    Helper: Simulate knockout bracket sessions round-by-round.
    Used by both pure knockout and group+knockout tournaments.

    Returns:
        (simulated_count, skipped_count)
    """
    import random
    import json
    from collections import defaultdict
    from sqlalchemy import asc

    # Group sessions by round
    rounds = defaultdict(list)
    for session in knockout_sessions:
        round_num = session.tournament_round or 1
        rounds[round_num].append(session)

    total_simulated = 0
    total_skipped = 0

    # Process rounds in order
    for round_num in sorted(rounds.keys()):
        round_sessions = rounds[round_num]
        logger.info("[ops] Processing Knockout Round %d (%d sessions)", round_num, len(round_sessions))

        round_winners = []
        round_losers = []

        for session in round_sessions:
            # Skip if already has results
            if session.game_results:
                total_skipped += 1
                continue

            # Skip if no participants yet (waiting for previous round)
            if not session.participant_user_ids or len(session.participant_user_ids) < 2:
                logger.info("[ops] Session %d has no participants yet (waiting for previous round), skipping", session.id)
                total_skipped += 1
                continue

            # Randomly select winner
            winner_id = random.choice(session.participant_user_ids)
            loser_id = [uid for uid in session.participant_user_ids if uid != winner_id][0]

            session.game_results = _build_h2h_game_results(
                [{"user_id": winner_id, "result": "win", "score": random.randint(1, 5)},
                 {"user_id": loser_id, "result": "loss", "score": random.randint(0, 3)}],
                session.tournament_round,
            )
            session.session_status = "completed"

            round_winners.append(winner_id)
            round_losers.append(loser_id)

            logger.info("[ops] Simulated knockout session %d: Winner=%d, Loser=%d",
                       session.id, winner_id, loser_id)

            total_simulated += 1

        # Bracket advancement: assign winners/losers to next round
        next_round = round_num + 1
        if next_round in rounds:
            next_round_sessions = rounds[next_round]

            # General bracket pairing: separate playoff from main bracket
            next_round_sessions_sorted = sorted(next_round_sessions, key=lambda s: s.tournament_match_number or 0)
            main_sessions = [s for s in next_round_sessions_sorted
                             if "3rd Place" not in (s.title or "") and "Playoff" not in (s.title or "")]
            playoff_sessions = [s for s in next_round_sessions_sorted
                                if "3rd Place" in (s.title or "") or "Playoff" in (s.title or "")]

            # Assign winners into main bracket sessions (2 per session, in order)
            for idx, ns in enumerate(main_sessions):
                p1_idx = idx * 2
                p2_idx = idx * 2 + 1
                if p1_idx < len(round_winners) and p2_idx < len(round_winners):
                    ns.participant_user_ids = [round_winners[p1_idx], round_winners[p2_idx]]
                    logger.info("[ops] Round %d→%d, Match %d: Assigned winners %s to session %d (%s)",
                               round_num, next_round, idx + 1,
                               ns.participant_user_ids, ns.id, ns.title)

            # Assign losers to 3rd Place Playoff
            if playoff_sessions and len(round_losers) >= 2:
                playoff_sessions[0].participant_user_ids = round_losers[:2]
                logger.info("[ops] Assigned 3rd Place Playoff participants: %s", round_losers[:2])

    return total_simulated, total_skipped

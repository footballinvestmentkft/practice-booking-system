"""GROUP_STAGE + KNOCKOUT simulation helper."""
import logging as _logging
from sqlalchemy.orm import Session

from ._session_helpers import _get_tournament_sessions, _build_h2h_game_results
from ._simulate_knockout import _simulate_knockout_bracket


def _simulate_group_knockout_tournament(
    db: Session,
    tournament_id: int,
    logger: _logging.Logger,
) -> tuple[bool, str]:
    """
    Simulate GROUP_STAGE + KNOCKOUT tournament:
    1. Simulate all group stage sessions (HEAD_TO_HEAD matches within groups)
    2. Calculate group standings (wins, goal difference, etc.)
    3. Determine qualifiers (top N from each group)
    4. Assign qualifiers to knockout bracket sessions
    5. Simulate knockout stage (winners advance to next round)

    Returns:
        (success: bool, message: str)
    """
    import random
    import json
    from app.models.session import Session as SessionModel
    from app.models.tournament_enums import TournamentPhase
    from sqlalchemy import asc
    from collections import defaultdict

    logger.info("[ops] Starting GROUP + KNOCKOUT simulation for tournament_id=%d", tournament_id)

    # Get all tournament sessions, ordered by phase, round, match
    sessions = _get_tournament_sessions(db, tournament_id, with_phase=True)

    if not sessions:
        return False, "No tournament sessions found for simulation"

    # Separate sessions by phase
    group_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.GROUP_STAGE.value]
    knockout_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.KNOCKOUT.value]

    logger.info("[ops] Found %d group sessions, %d knockout sessions", len(group_sessions), len(knockout_sessions))

    # ============================================================================
    # PHASE 1: Simulate Group Stage
    # ============================================================================
    group_simulated = 0
    group_skipped = 0

    # Track group standings: {group_identifier: {user_id: {wins, losses, draws, gf, ga, points}}}
    group_standings = defaultdict(lambda: defaultdict(lambda: {
        "wins": 0, "losses": 0, "draws": 0, "goals_for": 0, "goals_against": 0, "points": 0
    }))

    for session in group_sessions:
        # Skip if already has results
        if session.game_results:
            group_skipped += 1
            continue

        # Verify session has participants
        if not session.participant_user_ids or len(session.participant_user_ids) < 2:
            logger.warning("[ops] Group session %d has no participants, skipping", session.id)
            group_skipped += 1
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

        # Update group standings
        group_id = session.group_identifier or "A"

        # Update user_1 stats
        group_standings[group_id][user_id_1]["goals_for"] += score_1
        group_standings[group_id][user_id_1]["goals_against"] += score_2
        if result_1 == "win":
            group_standings[group_id][user_id_1]["wins"] += 1
            group_standings[group_id][user_id_1]["points"] += 3
        elif result_1 == "draw":
            group_standings[group_id][user_id_1]["draws"] += 1
            group_standings[group_id][user_id_1]["points"] += 1
        else:
            group_standings[group_id][user_id_1]["losses"] += 1

        # Update user_2 stats
        group_standings[group_id][user_id_2]["goals_for"] += score_2
        group_standings[group_id][user_id_2]["goals_against"] += score_1
        if result_2 == "win":
            group_standings[group_id][user_id_2]["wins"] += 1
            group_standings[group_id][user_id_2]["points"] += 3
        elif result_2 == "draw":
            group_standings[group_id][user_id_2]["draws"] += 1
            group_standings[group_id][user_id_2]["points"] += 1
        else:
            group_standings[group_id][user_id_2]["losses"] += 1

        group_simulated += 1

    db.commit()

    logger.info("[ops] Group stage simulation: %d sessions simulated, %d skipped", group_simulated, group_skipped)

    # ============================================================================
    # PHASE 2: Calculate Group Qualifiers
    # ============================================================================
    # Sort each group by: points DESC, goal_diff DESC, goals_for DESC
    group_qualifiers = {}  # {group_id: [user_id_rank1, user_id_rank2, ...]}

    for group_id, standings in group_standings.items():
        # Convert to list of (user_id, stats)
        standings_list = [(user_id, stats) for user_id, stats in standings.items()]

        # Sort by: points DESC, goal_diff DESC, goals_for DESC
        standings_list.sort(
            key=lambda x: (
                -x[1]["points"],
                -(x[1]["goals_for"] - x[1]["goals_against"]),  # goal difference
                -x[1]["goals_for"],
                x[0]  # user_id as tiebreaker (stable sort)
            )
        )

        # Extract top qualifiers (typically top 2 from each group)
        group_qualifiers[group_id] = [user_id for user_id, _ in standings_list]

        logger.info("[ops] Group %s standings: %s", group_id,
                   [(uid, stats["points"], stats["goals_for"] - stats["goals_against"])
                    for uid, stats in standings_list])

    # ============================================================================
    # PHASE 3: Assign Qualifiers to Knockout Bracket
    # ============================================================================
    # Determine seeding order: A1, B1, C1, D1, A2, B2, C2, D2, ...
    # For standard group_knockout: top 2 from each group qualify
    # Seeding: Winners from each group first, then runners-up

    group_ids_sorted = sorted(group_qualifiers.keys())  # ['A', 'B', 'C', 'D', ...]
    qualifiers_per_group = 2  # Standard config

    # Build seeded list: [A1, B1, C1, D1, A2, B2, C2, D2]
    seeded_qualifiers = []
    for rank_index in range(qualifiers_per_group):
        for group_id in group_ids_sorted:
            if rank_index < len(group_qualifiers[group_id]):
                seeded_qualifiers.append(group_qualifiers[group_id][rank_index])

    logger.info("[ops] Seeded qualifiers for knockout: %s", seeded_qualifiers)

    # Assign qualifiers to first knockout round sessions
    # First round knockout sessions should have participant_user_ids = None (need assignment)
    first_round_sessions = [s for s in knockout_sessions if s.tournament_round == 1]

    # Standard bracket seeding: 1 vs N, 2 vs N-1, 3 vs N-2, etc.
    if len(seeded_qualifiers) != len(first_round_sessions) * 2:
        logger.warning("[ops] Seeded qualifiers count (%d) doesn't match first round sessions (%d * 2)",
                      len(seeded_qualifiers), len(first_round_sessions))

    # Assign participants to first round sessions using standard bracket seeding
    for i, session in enumerate(first_round_sessions):
        if i * 2 + 1 < len(seeded_qualifiers):
            # Standard seeding: 1v8, 2v7, 3v6, 4v5 for 8 players
            seed_high = i  # 0, 1, 2, 3
            seed_low = len(seeded_qualifiers) - 1 - i  # 7, 6, 5, 4

            session.participant_user_ids = [seeded_qualifiers[seed_high], seeded_qualifiers[seed_low]]

            logger.info("[ops] Assigned knockout R1 Match %d: Seed %d (user %d) vs Seed %d (user %d)",
                       i + 1, seed_high + 1, seeded_qualifiers[seed_high],
                       seed_low + 1, seeded_qualifiers[seed_low])

    db.commit()

    # ============================================================================
    # PHASE 4: Simulate Knockout Stage (same as pure knockout)
    # ============================================================================
    knockout_simulated, knockout_skipped = _simulate_knockout_bracket(
        db, knockout_sessions, logger
    )

    db.commit()

    total_simulated = group_simulated + knockout_simulated
    total_skipped = group_skipped + knockout_skipped

    logger.info(
        "[ops] GROUP+KNOCKOUT simulation complete: %d total sessions simulated, %d skipped",
        total_simulated, total_skipped
    )

    return True, f"{total_simulated} sessions simulated (group={group_simulated}, knockout={knockout_simulated}), {total_skipped} skipped"

"""
HEAD_TO_HEAD Group + Knockout Ranking Strategy

Hybrid ranking for tournaments with GROUP_STAGE + KNOCKOUT phases:

1. GROUP_STAGE: Calculate group standings (isolated per group)
   - Points (Win = 3, Draw = 1, Loss = 0)
   - Goal difference
   - Goals scored

2. KNOCKOUT: Calculate final tournament rankings
   - Champion: Winner of Final (rank 1)
   - Runner-up: Loser of Final (rank 2)
   - 3rd Place: Winner of 3rd Place Playoff (rank 3)
   - 4th Place: Loser of 3rd Place Playoff (rank 4)
   - Group stage losers: Ranked by group finish (5+)

Final rankings prioritize knockout stage progression over group stage performance.
"""
from typing import Dict, List
from collections import defaultdict
from app.utils.game_results import parse_game_results


class HeadToHeadGroupKnockoutRankingStrategy:
    """
    Ranking strategy for HEAD_TO_HEAD Group + Knockout tournaments

    Returns unified rankings across both phases:
    - Knockout participants get final tournament ranks (1-N based on knockout finish)
    - Group stage only participants get ranks based on group finish (N+1 onwards)
    """

    def calculate_rankings(
        self,
        sessions: List,
        db_session
    ) -> List[Dict]:
        """
        Calculate hybrid group+knockout rankings

        Args:
            sessions: List of Session objects with game_results populated
            db_session: SQLAlchemy database session

        Returns:
            List of ranking dicts sorted by final tournament placement
        """
        from app.models.tournament_enums import TournamentPhase

        # Separate sessions by phase
        group_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.GROUP_STAGE.value]
        knockout_sessions = [s for s in sessions if s.tournament_phase == TournamentPhase.KNOCKOUT.value]

        # ============================================================================
        # PHASE 1: Calculate Group Stage Rankings (per group)
        # ============================================================================
        group_standings = self._calculate_group_standings(group_sessions)

        # ============================================================================
        # PHASE 2: Calculate Knockout Stage Rankings
        # ============================================================================
        knockout_rankings = self._calculate_knockout_rankings(knockout_sessions)

        # ============================================================================
        # PHASE 3: Merge Rankings
        # ============================================================================
        # Knockout participants get their knockout ranks (1-N)
        # Group-only participants get ranks starting from N+1

        final_rankings = []

        # Build flat lookup: user_id → group stage stats for all participants
        group_stats_by_user: Dict[int, Dict] = {}
        for standings in group_standings.values():
            for participant in standings:
                group_stats_by_user[participant["user_id"]] = participant

        # Add knockout participants first (sorted by rank), enriched with group stage stats
        knockout_user_ids = set([r["user_id"] for r in knockout_rankings])
        for ko_rank in knockout_rankings:
            uid = ko_rank["user_id"]
            gs = group_stats_by_user.get(uid, {})
            final_rankings.append({
                **ko_rank,
                "points": gs.get("points", 0),
                "wins": gs.get("wins", 0),
                "draws": gs.get("draws", 0),
                "losses": gs.get("losses", 0),
                "goals_for": gs.get("goals_for", 0),
                "goals_against": gs.get("goals_against", 0),
                "goal_difference": gs.get("goal_difference", 0),
            })

        # Add group-only participants (did not qualify for knockout)
        # Rank them by group finish: A1, B1, C1, A2, B2, C2, etc. (already eliminated)
        group_only_participants = []

        # Flatten group standings (sorted by group, then rank within group)
        for group_id in sorted(group_standings.keys()):
            standings = group_standings[group_id]
            for participant in standings:
                if participant["user_id"] not in knockout_user_ids:
                    group_only_participants.append({
                        "user_id": participant["user_id"],
                        "group": group_id,
                        "group_rank": participant["rank"],
                        "points": participant["points"],
                        "goal_difference": participant["goal_difference"],
                        "goals_for": participant["goals_for"],
                        "goals_against": participant["goals_against"],
                        "wins": participant["wins"],
                        "draws": participant["draws"],
                        "losses": participant["losses"]
                    })

        # Sort group-only participants: by group rank first, then by group (stable)
        group_only_participants.sort(key=lambda x: (x["group_rank"], x["group"]))

        # Assign ranks to group-only participants starting from max(knockout_rank) + 1
        next_rank = len(knockout_rankings) + 1
        for participant in group_only_participants:
            final_rankings.append({
                "user_id": participant["user_id"],
                "rank": next_rank,
                "points": participant["points"],
                "wins": participant["wins"],
                "draws": participant["draws"],
                "losses": participant["losses"],
                "goals_for": participant["goals_for"],
                "goals_against": participant["goals_against"],
                "goal_difference": participant["goal_difference"]
            })
            next_rank += 1

        return final_rankings

    def _calculate_group_standings(self, group_sessions: List) -> Dict[str, List[Dict]]:
        """
        Calculate standings per group

        Returns:
            {
                "A": [{user_id: 1, rank: 1, points: 9, ...}, ...],
                "B": [{user_id: 5, rank: 1, points: 7, ...}, ...],
                ...
            }
        """
        # Track stats per group: {group_id: {user_id: stats}}
        group_stats = defaultdict(lambda: defaultdict(lambda: {
            "wins": 0, "draws": 0, "losses": 0,
            "goals_for": 0, "goals_against": 0, "points": 0
        }))

        for session in group_sessions:
            if not session.game_results:
                continue

            match_data = parse_game_results(session.game_results)

            if match_data.get("match_format") != "HEAD_TO_HEAD":
                continue

            participants = match_data.get("participants", [])
            if len(participants) != 2:
                continue

            # Extract group identifier
            group_id = session.group_identifier or "A"

            # Process match result
            for participant in participants:
                user_id = participant["user_id"]
                result = participant["result"]
                score = participant.get("score", 0)

                # Find opponent's score
                opponent = [p for p in participants if p["user_id"] != user_id][0]
                opponent_score = opponent.get("score", 0)

                # Update stats
                group_stats[group_id][user_id]["goals_for"] += score
                group_stats[group_id][user_id]["goals_against"] += opponent_score

                if result == "win":
                    group_stats[group_id][user_id]["wins"] += 1
                    group_stats[group_id][user_id]["points"] += 3
                elif result == "draw":
                    group_stats[group_id][user_id]["draws"] += 1
                    group_stats[group_id][user_id]["points"] += 1
                else:  # loss
                    group_stats[group_id][user_id]["losses"] += 1

        # Convert to standings per group (sorted)
        group_standings = {}
        for group_id, stats in group_stats.items():
            standings_list = []
            for user_id, user_stats in stats.items():
                standings_list.append({
                    "user_id": user_id,
                    "points": user_stats["points"],
                    "wins": user_stats["wins"],
                    "draws": user_stats["draws"],
                    "losses": user_stats["losses"],
                    "goals_for": user_stats["goals_for"],
                    "goals_against": user_stats["goals_against"],
                    "goal_difference": user_stats["goals_for"] - user_stats["goals_against"]
                })

            # Sort: points DESC, goal_diff DESC, goals_for DESC
            standings_list.sort(
                key=lambda x: (-x["points"], -x["goal_difference"], -x["goals_for"], x["user_id"])
            )

            # Assign ranks within group
            for idx, participant in enumerate(standings_list):
                participant["rank"] = idx + 1

            group_standings[group_id] = standings_list

        return group_standings

    def _calculate_knockout_rankings(self, knockout_sessions: List) -> List[Dict]:
        """
        Calculate final tournament rankings from knockout stage

        Returns list of rankings sorted by final placement (rank 1 = champion)
        """
        # Use existing HeadToHeadKnockoutRankingStrategy
        from .head_to_head_knockout import HeadToHeadKnockoutRankingStrategy

        knockout_strategy = HeadToHeadKnockoutRankingStrategy()
        return knockout_strategy.calculate_rankings(knockout_sessions, db_session=None)

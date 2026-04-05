"""
Group Stage Standings Calculator

Calculates group standings from HEAD_TO_HEAD match results.
Extracted from match_results.py as part of P2 decomposition.
"""

from collections import defaultdict
from typing import Dict, List, Any
from sqlalchemy.orm import Session
import json

from app.models.session import Session as SessionModel
from app.models.user import User as UserModel
from app.models.team import Team as TeamModel


class StandingsCalculator:
    """
    Calculate group stage standings from match results.

    Handles:
    - Points calculation (3 pts win, 1 pt draw, 0 loss)
    - Goal difference calculation
    - Tie-breaking rules (points > goal_diff > goals_for)
    - Rank assignment
    """

    def __init__(self, db: Session):
        """
        Initialize calculator with database session.

        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def calculate_group_standings(
        self,
        group_sessions: List[SessionModel]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Dispatch to TEAM or INDIVIDUAL standings calculation."""
        is_team = any(
            getattr(s, "participant_team_ids", None)
            for s in group_sessions
        )
        if is_team:
            return self._calculate_group_standings_team(group_sessions)
        return self._calculate_group_standings_individual(group_sessions)

    def _calculate_group_standings_team(
        self,
        group_sessions: List[SessionModel],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Calculate group standings for TEAM tournaments (results in rounds_data)."""
        group_stats: dict = defaultdict(lambda: defaultdict(lambda: {
            "wins": 0, "losses": 0, "draws": 0, "points": 0,
            "goals_for": 0.0, "goals_against": 0.0, "matches_played": 0,
        }))

        for session in group_sessions:
            if session.group_identifier and session.participant_team_ids:
                for tid in session.participant_team_ids:
                    _ = group_stats[session.group_identifier][tid]

        for session in group_sessions:
            if not session.group_identifier:
                continue
            round_results = (session.rounds_data or {}).get("round_results", {})
            if not round_results:
                continue

            team_totals: dict = {}
            for round_data in round_results.values():
                for key, val in (round_data or {}).items():
                    if key.startswith("team_"):
                        try:
                            tid = int(key.split("_", 1)[1])
                            team_totals[tid] = team_totals.get(tid, 0.0) + float(val)
                        except (ValueError, IndexError):
                            pass

            if len(team_totals) != 2:
                continue

            (t1, s1), (t2, s2) = list(team_totals.items())
            gid = session.group_identifier
            group_stats[gid][t1]["goals_for"] += s1
            group_stats[gid][t1]["goals_against"] += s2
            group_stats[gid][t1]["matches_played"] += 1
            group_stats[gid][t2]["goals_for"] += s2
            group_stats[gid][t2]["goals_against"] += s1
            group_stats[gid][t2]["matches_played"] += 1

            if s1 > s2:
                group_stats[gid][t1]["wins"] += 1
                group_stats[gid][t1]["points"] += 3
                group_stats[gid][t2]["losses"] += 1
            elif s2 > s1:
                group_stats[gid][t2]["wins"] += 1
                group_stats[gid][t2]["points"] += 3
                group_stats[gid][t1]["losses"] += 1
            else:
                group_stats[gid][t1]["draws"] += 1
                group_stats[gid][t1]["points"] += 1
                group_stats[gid][t2]["draws"] += 1
                group_stats[gid][t2]["points"] += 1

        group_standings: dict = {}
        for group_id, stats in group_stats.items():
            team_ids = list(stats.keys())
            teams = self.db.query(TeamModel).filter(TeamModel.id.in_(team_ids)).all()
            team_dict = {t.id: t for t in teams}

            standings_list = []
            for team_id, ts in stats.items():
                team = team_dict.get(team_id)
                goal_diff = ts["goals_for"] - ts["goals_against"]
                standings_list.append({
                    "team_id": team_id,
                    "name": team.name if team else f"Team {team_id}",
                    "points": ts["points"],
                    "wins": ts["wins"],
                    "draws": ts["draws"],
                    "losses": ts["losses"],
                    "goals_for": ts["goals_for"],
                    "goals_against": ts["goals_against"],
                    "goal_difference": goal_diff,
                    "matches_played": ts["matches_played"],
                })

            standings_list.sort(
                key=lambda x: (-x["points"], -x["goal_difference"], -x["goals_for"], x["team_id"]),
            )
            for rank, entry in enumerate(standings_list, start=1):
                entry["rank"] = rank

            group_standings[group_id] = standings_list

        return group_standings

    def _calculate_group_standings_individual(
        self,
        group_sessions: List[SessionModel],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Calculate standings for all groups from completed matches.

        Args:
            group_sessions: List of group stage sessions

        Returns:
            Dictionary mapping group_id to standings list:
            {
                "Group A": [
                    {
                        "user_id": 123,
                        "name": "John Doe",
                        "rank": 1,
                        "points": 9,
                        "wins": 3,
                        "draws": 0,
                        "losses": 0,
                        "goals_for": 10,
                        "goals_against": 2,
                        "goal_difference": 8,
                        "matches_played": 3
                    },
                    ...
                ],
                ...
            }
        """
        # Structure: {group_id: {user_id: {wins, losses, draws, points, goals_for, goals_against}}}
        group_stats = defaultdict(lambda: defaultdict(lambda: {
            'wins': 0, 'losses': 0, 'draws': 0, 'points': 0,
            'goals_for': 0, 'goals_against': 0, 'matches_played': 0
        }))

        # Initialize all groups with all participants (even if no matches played yet)
        for session in group_sessions:
            if session.group_identifier and session.participant_user_ids:
                group_id = session.group_identifier
                for user_id in session.participant_user_ids:
                    _ = group_stats[group_id][user_id]

        # Process completed matches
        for session in group_sessions:
            if not session.game_results or not session.group_identifier:
                continue

            results = session.game_results

            # Parse game_results if it's a JSON string
            if isinstance(results, str):
                try:
                    results = json.loads(results)
                except json.JSONDecodeError:
                    continue

            # game_results is a dict with "raw_results" key for HEAD_TO_HEAD
            if isinstance(results, dict):
                raw_results = results.get('raw_results', [])
            elif isinstance(results, list):
                raw_results = results  # Fallback for old format
            else:
                continue

            if len(raw_results) != 2:
                continue  # HEAD_TO_HEAD should have exactly 2 players

            group_id = session.group_identifier
            player1 = raw_results[0]
            player2 = raw_results[1]

            user1_id = player1['user_id']
            user2_id = player2['user_id']
            score1 = player1.get('score', 0)
            score2 = player2.get('score', 0)

            # Update goals
            group_stats[group_id][user1_id]['goals_for'] += score1
            group_stats[group_id][user1_id]['goals_against'] += score2
            group_stats[group_id][user1_id]['matches_played'] += 1

            group_stats[group_id][user2_id]['goals_for'] += score2
            group_stats[group_id][user2_id]['goals_against'] += score1
            group_stats[group_id][user2_id]['matches_played'] += 1

            # Determine win/loss/draw and update points (Football: 3 pts win, 1 pt draw, 0 loss)
            if score1 > score2:
                group_stats[group_id][user1_id]['wins'] += 1
                group_stats[group_id][user1_id]['points'] += 3
                group_stats[group_id][user2_id]['losses'] += 1
            elif score2 > score1:
                group_stats[group_id][user2_id]['wins'] += 1
                group_stats[group_id][user2_id]['points'] += 3
                group_stats[group_id][user1_id]['losses'] += 1
            else:  # Draw
                group_stats[group_id][user1_id]['draws'] += 1
                group_stats[group_id][user1_id]['points'] += 1
                group_stats[group_id][user2_id]['draws'] += 1
                group_stats[group_id][user2_id]['points'] += 1

        # Convert to sorted standings with user details
        group_standings = {}

        for group_id, stats in group_stats.items():
            # Get user details
            user_ids = list(stats.keys())
            users = self.db.query(UserModel).filter(UserModel.id.in_(user_ids)).all()
            user_dict = {user.id: user for user in users}

            # Create standings list
            standings_list = []
            for user_id, user_stats in stats.items():
                user = user_dict.get(user_id)
                if not user:
                    continue

                goal_difference = user_stats['goals_for'] - user_stats['goals_against']

                standings_list.append({
                    'user_id': user_id,
                    'name': user.name or user.email,
                    'points': user_stats['points'],
                    'wins': user_stats['wins'],
                    'draws': user_stats['draws'],
                    'losses': user_stats['losses'],
                    'goals_for': user_stats['goals_for'],
                    'goals_against': user_stats['goals_against'],
                    'goal_difference': goal_difference,
                    'matches_played': user_stats['matches_played']
                })

            # Sort by: points (desc), goal_difference (desc), goals_for (desc), user_id (asc) as stable tie-breaker
            standings_list.sort(
                key=lambda x: (-x['points'], -x['goal_difference'], -x['goals_for'], x['user_id'])
            )

            # Add rank
            for rank, player in enumerate(standings_list, start=1):
                player['rank'] = rank

            group_standings[group_id] = standings_list

        return group_standings


# Export main class
__all__ = ["StandingsCalculator"]

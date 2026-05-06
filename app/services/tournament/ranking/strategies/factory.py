"""
Ranking Strategy Factory

Creates the appropriate ranking strategy based on scoring_type (INDIVIDUAL)
or tournament_type (HEAD_TO_HEAD).

This ensures that:
1. TIME_BASED tournaments ONLY use TimeBasedStrategy
2. SCORE_BASED tournaments ONLY use ScoreBasedStrategy
3. ROUNDS_BASED tournaments ONLY use RoundsBasedStrategy
4. HEAD_TO_HEAD League tournaments use HeadToHeadLeagueRankingStrategy
5. HEAD_TO_HEAD Knockout tournaments use HeadToHeadKnockoutRankingStrategy
6. No mixing of business logic between strategies
"""
from typing import Optional
from .base import RankingStrategy
from .time_based import TimeBasedStrategy
from .score_based import ScoreBasedStrategy
from .rounds_based import RoundsBasedStrategy
from .head_to_head_league import HeadToHeadLeagueRankingStrategy
from .head_to_head_knockout import HeadToHeadKnockoutRankingStrategy
from .head_to_head_group_knockout import HeadToHeadGroupKnockoutRankingStrategy
from .placement import PlacementStrategy


class RankingStrategyFactory:
    """
    Factory for creating ranking strategies.

    Usage:
        strategy = RankingStrategyFactory.create("ROUNDS_BASED")
        rank_groups = strategy.calculate_rankings(round_results, participants)
    """

    @staticmethod
    def create(scoring_type: str = None, tournament_format: str = None, tournament_type_code: str = None):
        """
        Create a ranking strategy based on scoring_type (INDIVIDUAL) or tournament format (HEAD_TO_HEAD).

        Args:
            scoring_type: For INDIVIDUAL tournaments - 'TIME_BASED', 'SCORE_BASED', 'ROUNDS_BASED', 'DISTANCE_BASED', 'PLACEMENT'
            tournament_format: 'INDIVIDUAL_RANKING' or 'HEAD_TO_HEAD'
            tournament_type_code: For HEAD_TO_HEAD - 'league', 'knockout', 'group_knockout', 'swiss'

        Returns:
            RankingStrategy instance (or HEAD_TO_HEAD strategy)

        Raises:
            ValueError: If parameters are invalid
        """
        # HEAD_TO_HEAD tournaments
        if tournament_format == "HEAD_TO_HEAD":
            if not tournament_type_code:
                raise ValueError("HEAD_TO_HEAD tournaments must specify tournament_type_code")

            if tournament_type_code == "league":
                return HeadToHeadLeagueRankingStrategy()
            elif tournament_type_code == "knockout":
                return HeadToHeadKnockoutRankingStrategy()
            elif tournament_type_code and tournament_type_code.startswith("group_knockout"):
                return HeadToHeadGroupKnockoutRankingStrategy()
            else:
                raise ValueError(
                    f"Unsupported HEAD_TO_HEAD tournament type: '{tournament_type_code}'. "
                    f"Supported: league, knockout, group_knockout"
                )

        # INDIVIDUAL tournaments
        scoring_type = scoring_type.upper() if scoring_type else ""

        if scoring_type == "TIME_BASED":
            return TimeBasedStrategy()

        elif scoring_type == "SCORE_BASED":
            return ScoreBasedStrategy()

        elif scoring_type == "ROUNDS_BASED":
            return RoundsBasedStrategy()

        elif scoring_type == "DISTANCE_BASED":
            # DISTANCE_BASED uses same logic as SCORE_BASED (higher is better, SUM aggregation)
            return ScoreBasedStrategy()

        elif scoring_type == "PLACEMENT":
            # PLACEMENT: lower placement number = better (1st place wins).
            # Uses SUM aggregation (total placement across rounds) + ASC sort.
            # Fixed: previously incorrectly mapped to ScoreBasedStrategy (DESC) — BUG-02.
            return PlacementStrategy()

        else:
            raise ValueError(
                f"Unknown scoring_type: '{scoring_type}'. "
                f"Supported types: TIME_BASED, SCORE_BASED, ROUNDS_BASED, DISTANCE_BASED, PLACEMENT"
            )

    @staticmethod
    def get_supported_types() -> list[str]:
        """Get list of supported scoring types"""
        return ["TIME_BASED", "SCORE_BASED", "ROUNDS_BASED", "DISTANCE_BASED", "PLACEMENT"]

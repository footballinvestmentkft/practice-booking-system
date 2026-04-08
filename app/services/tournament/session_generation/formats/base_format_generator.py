"""
Base Format Generator

Abstract base class for all tournament format generators.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from sqlalchemy.orm import Session

from app.models.semester import Semester
from app.models.tournament_type import TournamentType


_BASE_XP_BY_SESSION_TYPE: dict = {
    "on_site": 75,
    "virtual": 50,
    "hybrid": 100,
}


class BaseFormatGenerator(ABC):
    """
    Abstract base class for tournament format generators
    """

    def __init__(self, db: Session):
        """
        Initialize generator with database session

        Args:
            db: SQLAlchemy database session
        """
        self.db = db

    def _resolve_session_type(self, tournament: Semester) -> str:
        """Return the session delivery type configured for this tournament (default: on_site)."""
        cfg = getattr(tournament, "tournament_config_obj", None)
        if cfg and cfg.session_type_config:
            return cfg.session_type_config
        return "on_site"

    def _resolve_base_xp(self, session_type: str) -> int:
        """Return the base XP for a session type (on_site=75, virtual=50, hybrid=100)."""
        return _BASE_XP_BY_SESSION_TYPE.get(session_type, 75)

    @abstractmethod
    def generate(
        self,
        tournament: Semester,
        tournament_type: TournamentType,
        player_count: int,
        parallel_fields: int,
        session_duration: int,
        break_minutes: int,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Generate sessions for this tournament format

        Args:
            tournament: Tournament (Semester) instance
            tournament_type: TournamentType configuration
            player_count: Number of enrolled players
            parallel_fields: Number of fields available for parallel matches
            session_duration: Duration of each session in minutes
            break_minutes: Break time between sessions in minutes
            **kwargs: Additional format-specific parameters

        Returns:
            List of session data dictionaries
        """
        pass

"""
Tournament Data Access Repositories

Phase 2.2: Service Layer Isolation
Abstract repository pattern for tournament data access, enabling:
- Easy mocking/faking for unit tests
- Clear data access boundaries
- Potential for caching, logging, etc.
- Separation between business logic and data access
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.session import Session as SessionModel, EventCategory
from app.models.semester import Semester
from app.models.tournament_enums import TournamentPhase


class SessionRepository(ABC):
    """
    Abstract repository for session data access.

    Provides high-level query methods for tournament progression logic.
    Concrete implementations (SQL, NoSQL, etc.) implement these methods.
    """

    @abstractmethod
    def get_sessions_by_phase_and_round(
        self,
        tournament_id: int,
        phase: TournamentPhase,
        round_num: int,
        exclude_bronze: bool = True
    ) -> List[SessionModel]:
        """
        Get all sessions for a specific tournament phase and round.

        Args:
            tournament_id: Tournament/Semester ID
            phase: Tournament phase (e.g., TournamentPhase.KNOCKOUT)
            round_num: Round number (1 = first round, 2 = second, etc.)
            exclude_bronze: If True, exclude bronze/3rd place matches

        Returns:
            List of matching sessions

        Example:
            >>> repo.get_sessions_by_phase_and_round(100, TournamentPhase.KNOCKOUT, 1)
            [<Session id=1 title="Semi-final 1">, <Session id=2 title="Semi-final 2">]
        """
        ...

    @abstractmethod
    def get_distinct_rounds(
        self,
        tournament_id: int,
        phase: TournamentPhase
    ) -> List[int]:
        """
        Get list of distinct round numbers for a tournament phase.

        Args:
            tournament_id: Tournament/Semester ID
            phase: Tournament phase

        Returns:
            Sorted list of round numbers

        Example:
            >>> repo.get_distinct_rounds(100, TournamentPhase.KNOCKOUT)
            [1, 2]  # Has rounds 1 and 2
        """
        ...

    @abstractmethod
    def count_completed_sessions(
        self,
        sessions: List[SessionModel]
    ) -> int:
        """
        Count how many sessions have game results.

        Args:
            sessions: List of sessions to check

        Returns:
            Number of sessions with non-null game_results

        Example:
            >>> repo.count_completed_sessions(sessions)
            2  # 2 out of N sessions have results
        """
        ...

    @abstractmethod
    def get_winner_from_session(
        self,
        session: SessionModel
    ) -> Optional[int]:
        """
        Extract winner user ID from session game results.

        Args:
            session: Session with game_results

        Returns:
            Winner user ID, or None if no winner (tie/incomplete)

        Example:
            >>> repo.get_winner_from_session(session)
            42  # User ID 42 won
        """
        ...

    @abstractmethod
    def create_session(
        self,
        tournament: Semester,
        session_data: Dict[str, Any]
    ) -> SessionModel:
        """
        Create a new tournament session.

        Args:
            tournament: Tournament/Semester context
            session_data: Session attributes (title, participants, round, etc.)

        Returns:
            Created session with ID assigned

        Example:
            >>> data = {
            ...     "title": "Final",
            ...     "participant_user_ids": [10, 12],
            ...     "tournament_round": 2,
            ...     "tournament_phase": TournamentPhase.KNOCKOUT
            ... }
            >>> session = repo.create_session(tournament, data)
            >>> session.id
            123
        """
        ...


class SQLSessionRepository(SessionRepository):
    """
    SQLAlchemy implementation of SessionRepository.

    Phase 2.2: Concrete implementation for PostgreSQL database access.
    """

    def __init__(self, db: Session):
        """
        Initialize repository with database session.

        Args:
            db: SQLAlchemy session
        """
        self.db = db

    def get_sessions_by_phase_and_round(
        self,
        tournament_id: int,
        phase: TournamentPhase,
        round_num: int,
        exclude_bronze: bool = True
    ) -> List[SessionModel]:
        """
        Query sessions by phase and round with optional bronze match exclusion.
        """
        query = self.db.query(SessionModel).filter(
            and_(
                SessionModel.semester_id == tournament_id,
                SessionModel.tournament_phase == phase,
                SessionModel.tournament_round == round_num,
                SessionModel.event_category == EventCategory.MATCH
            )
        )

        if exclude_bronze:
            query = query.filter(
                and_(
                    ~SessionModel.title.ilike("%bronze%"),
                    ~SessionModel.title.ilike("%3rd%")
                )
            )

        return query.all()

    def get_distinct_rounds(
        self,
        tournament_id: int,
        phase: TournamentPhase
    ) -> List[int]:
        """
        Query distinct round numbers for a tournament phase.
        """
        rounds = self.db.query(SessionModel.tournament_round).filter(
            and_(
                SessionModel.semester_id == tournament_id,
                SessionModel.event_category == EventCategory.MATCH,
                SessionModel.tournament_phase == phase
            )
        ).distinct().all()

        # Extract integers from tuples and sort
        return sorted([r[0] for r in rounds if r[0] is not None])

    def count_completed_sessions(
        self,
        sessions: List[SessionModel]
    ) -> int:
        """
        Count sessions with non-null game_results.
        """
        return sum(1 for session in sessions if session.game_results is not None)

    def get_winner_from_session(
        self,
        session: SessionModel
    ) -> Optional[int]:
        """
        Extract winner user ID from game_results JSONB.
        """
        if not session.game_results:
            return None

        # Handle both dict and JSON string
        game_results = session.game_results
        if isinstance(game_results, str):
            import json
            game_results = json.loads(game_results)

        return game_results.get("winner_user_id")

    def create_session(
        self,
        tournament: Semester,
        session_data: Dict[str, Any]
    ) -> SessionModel:
        """
        Create new session in database.

        Phase 2.2: Extracts session creation logic from service layer.
        """
        # Build session object
        new_session = SessionModel(
            semester_id=tournament.id,
            title=session_data["title"],
            description=session_data.get("description", ""),
            date_start=session_data["date_start"],
            date_end=session_data["date_end"],
            session_type=session_data.get("session_type", "on_site"),
            capacity=session_data.get("capacity", tournament.max_participants),
            location=session_data.get("location", tournament.location),
            event_category=EventCategory.MATCH,
            tournament_phase=session_data["tournament_phase"],
            tournament_round=session_data["tournament_round"],
            tournament_match_number=session_data.get("tournament_match_number"),
            participant_user_ids=session_data.get("participant_user_ids", []),
            format=tournament.format,
            specialization_type=tournament.specialization_type
        )

        self.db.add(new_session)
        self.db.flush()  # Get ID without committing transaction

        return new_session


class FakeSessionRepository(SessionRepository):
    """
    In-memory fake repository for unit testing.

    Phase 2.2: Enables fast, deterministic unit tests without database.
    No mocks needed - this is a real implementation backed by lists.
    """

    def __init__(self, sessions: List[SessionModel] = None):
        """
        Initialize with optional pre-populated sessions.

        Args:
            sessions: Initial session list (useful for test setup)
        """
        self.sessions = sessions or []
        self.created_sessions = []
        self._next_id = 1000  # Start IDs at 1000 to avoid conflicts

    def get_sessions_by_phase_and_round(
        self,
        tournament_id: int,
        phase: TournamentPhase,
        round_num: int,
        exclude_bronze: bool = True
    ) -> List[SessionModel]:
        """
        Filter in-memory sessions by phase and round.
        """
        matching = [
            s for s in self.sessions
            if (s.semester_id == tournament_id and
                s.tournament_phase == phase and
                s.tournament_round == round_num and
                s.event_category == EventCategory.MATCH)
        ]

        if exclude_bronze:
            matching = [
                s for s in matching
                if not ("bronze" in s.title.lower() or "3rd" in s.title.lower())
            ]

        return matching

    def get_distinct_rounds(
        self,
        tournament_id: int,
        phase: TournamentPhase
    ) -> List[int]:
        """
        Get unique round numbers from in-memory sessions.
        """
        rounds = {
            s.tournament_round
            for s in self.sessions
            if (s.semester_id == tournament_id and
                s.tournament_phase == phase and
                s.tournament_round is not None)
        }
        return sorted(list(rounds))

    def count_completed_sessions(
        self,
        sessions: List[SessionModel]
    ) -> int:
        """
        Count sessions with game_results.
        """
        return sum(1 for s in sessions if s.game_results is not None)

    def get_winner_from_session(
        self,
        session: SessionModel
    ) -> Optional[int]:
        """
        Extract winner from game_results.
        """
        if not session.game_results:
            return None

        if isinstance(session.game_results, dict):
            return session.game_results.get("winner_user_id")

        # Handle mock objects
        return getattr(session.game_results, "winner_user_id", None)

    def create_session(
        self,
        tournament: Semester,
        session_data: Dict[str, Any]
    ) -> SessionModel:
        """
        Create fake session in memory.
        """
        from unittest.mock import Mock

        # Create mock session with specified attributes
        mock_session = Mock(spec=SessionModel)
        mock_session.id = self._next_id
        self._next_id += 1

        mock_session.semester_id = tournament.id
        mock_session.title = session_data["title"]
        mock_session.tournament_phase = session_data["tournament_phase"]
        mock_session.tournament_round = session_data["tournament_round"]
        mock_session.participant_user_ids = session_data.get("participant_user_ids", [])
        mock_session.game_results = None
        mock_session.event_category = EventCategory.MATCH

        # Store for verification
        self.created_sessions.append(mock_session)
        self.sessions.append(mock_session)

        return mock_session

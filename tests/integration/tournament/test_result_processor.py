"""
Integration tests for ResultProcessor — Round 2A + 2B: IR and HEAD_TO_HEAD paths

Uses real PostgreSQL with SAVEPOINT isolation (test_db fixture).

What is tested:
  Full orchestration chain for INDIVIDUAL_RANKING:
    process_match_results
      → _process_individual_ranking_tournament
        → get_or_create_ranking  (creates TournamentRanking rows in DB)
        → db.flush               (points written)
        → calculate_ranks        (reads ranking rows, assigns .rank)
        → session.game_results   (JSONB updated on the Session row)

What is NOT tested:
  - Internal service call order (no mock assertions)
  - PointsCalculatorService (not invoked in IR path)
  - KnockoutProgressionService (not invoked in IR path)

Why INDIVIDUAL_RANKING is the easiest integration path:
  Semester.format property falls back to "INDIVIDUAL_RANKING" (Priority 3)
  when no TournamentConfiguration exists — zero extra DB rows required.

Fixture dependency tree (all function-scoped, SAVEPOINT-isolated):
  test_db
  ├── instructor_user          (from tests/integration/conftest.py)
  ├── ir_tournament            (local: plain Semester, no config)
  ├── ir_session               (local: Session linked to ir_tournament)
  └── two_ir_students          (local: 2 User rows, UUID-suffixed)
"""
import json
import uuid
import pytest
from decimal import Decimal
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.user import User, UserRole
from app.models.tournament_ranking import TournamentRanking
from app.services.tournament.result_processor import ResultProcessor
from app.core.security import get_password_hash


# ============================================================================
# Local fixtures — Round 2A only, no conftest changes
# ============================================================================

@pytest.fixture
def ir_tournament(test_db: Session) -> Semester:
    """
    Plain Semester with NO TournamentConfiguration.
    Semester.format → Priority 3 default → "INDIVIDUAL_RANKING".
    No master_instructor required (nullable FK).
    """
    sem = Semester(
        code=f"IR-{uuid.uuid4().hex[:8]}",
        name="IR Integration Test Tournament",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=90),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.commit()
    test_db.refresh(sem)
    return sem


@pytest.fixture
def ir_session(test_db: Session, ir_tournament: Semester, instructor_user: User) -> SessionModel:
    """
    Minimal tournament session linked to ir_tournament.
    is_tournament_game=True so that calculate_ranks can detect scoring_type.
    No match_format set — defaults to None → "INDIVIDUAL_RANKING" in the processor.
    """
    session_start = datetime.now() + timedelta(days=1)
    sess = SessionModel(
        title="IR Integration Test Session",
        date_start=session_start,
        date_end=session_start + timedelta(hours=2),
        session_type=SessionType.on_site,
        capacity=20,
        instructor_id=instructor_user.id,
        semester_id=ir_tournament.id,
        event_category=EventCategory.MATCH,
    )
    test_db.add(sess)
    test_db.commit()
    test_db.refresh(sess)
    return sess


@pytest.fixture
def two_ir_students(test_db: Session):
    """Two STUDENT users for use as participants in IR tests."""
    users = []
    for i in range(2):
        u = User(
            email=f"ir-student-{i}-{uuid.uuid4().hex[:6]}@test.com",
            name=f"IR Student {i}",
            password_hash=get_password_hash("test"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(u)
        users.append(u)
    test_db.commit()
    for u in users:
        test_db.refresh(u)
    return users


# ============================================================================
# TestProcessMatchResultsIR — INDIVIDUAL_RANKING orchestration
# ============================================================================

@pytest.mark.integration
class TestProcessMatchResultsIR:
    """
    Validates the full DB-writing pipeline for INDIVIDUAL_RANKING tournaments.

    Assertions target final DB state, not implementation internals.
    """

    def test_happy_path_two_users_creates_ranking_rows(
        self,
        test_db: Session,
        ir_tournament: Semester,
        ir_session: SessionModel,
        two_ir_students: list,
    ):
        """
        Two users with distinct measured_value:
          - TournamentRanking rows created for both
          - points stored as Decimal(measured_value)
          - higher measured_value gets rank 1 (default DESC ranking_direction)
        """
        user_a, user_b = two_ir_students
        raw_results = [
            {"user_id": user_a.id, "measured_value": 100},
            {"user_id": user_b.id, "measured_value": 50},
        ]

        proc = ResultProcessor(db=test_db)
        proc.process_match_results(
            db=test_db,
            session=ir_session,
            tournament=ir_tournament,
            raw_results=raw_results,
        )

        # ── DB state assertions ──────────────────────────────────────────────
        rankings = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == ir_tournament.id)
            .all()
        )
        assert len(rankings) == 2, "Expected exactly 2 TournamentRanking rows"

        by_user = {r.user_id: r for r in rankings}
        assert user_a.id in by_user
        assert user_b.id in by_user

        # Points stored exactly as measured_value
        assert by_user[user_a.id].points == Decimal("100")
        assert by_user[user_b.id].points == Decimal("50")

        # Ranks assigned: higher points → better rank (lower number) with DESC direction
        assert by_user[user_a.id].rank == 1
        assert by_user[user_b.id].rank == 2

    def test_single_participant_gets_rank_1(
        self,
        test_db: Session,
        ir_tournament: Semester,
        ir_session: SessionModel,
        two_ir_students: list,
    ):
        """
        Single result entry: exactly one TournamentRanking row, rank=1.
        calculate_ranks must not crash with a single-row tournament.
        """
        solo_user = two_ir_students[0]
        raw_results = [{"user_id": solo_user.id, "measured_value": 42.5}]

        proc = ResultProcessor(db=test_db)
        proc.process_match_results(
            db=test_db,
            session=ir_session,
            tournament=ir_tournament,
            raw_results=raw_results,
        )

        rankings = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == ir_tournament.id)
            .all()
        )
        assert len(rankings) == 1
        assert rankings[0].rank == 1
        assert rankings[0].points == Decimal("42.5")

    def test_game_results_json_structure(
        self,
        test_db: Session,
        ir_tournament: Semester,
        ir_session: SessionModel,
        two_ir_students: list,
    ):
        """
        session.game_results is updated with valid JSON containing the
        documented keys: recorded_at, tournament_format, raw_results,
        derived_rankings.
        tournament_format must be "INDIVIDUAL_RANKING".
        derived_rankings must contain one entry per participant.
        """
        user_a, user_b = two_ir_students
        raw_results = [
            {"user_id": user_a.id, "measured_value": 75},
            {"user_id": user_b.id, "measured_value": 25},
        ]

        proc = ResultProcessor(db=test_db)
        proc.process_match_results(
            db=test_db,
            session=ir_session,
            tournament=ir_tournament,
            raw_results=raw_results,
        )

        # game_results must be set on the session
        test_db.refresh(ir_session)
        assert ir_session.game_results is not None, "session.game_results must be set"

        data = json.loads(ir_session.game_results)

        # Required top-level keys
        for key in ("recorded_at", "tournament_format", "raw_results", "derived_rankings"):
            assert key in data, f"Missing key in game_results: {key!r}"

        # Format marker
        assert data["tournament_format"] == "INDIVIDUAL_RANKING"

        # derived_rankings: one entry per participant
        assert isinstance(data["derived_rankings"], list)
        assert len(data["derived_rankings"]) == 2

        # Each derived ranking entry has user_id, rank, measured_value
        for entry in data["derived_rankings"]:
            assert "user_id" in entry
            assert "rank" in entry
            assert "measured_value" in entry

    def test_idempotent_reprocessing_updates_points(
        self,
        test_db: Session,
        ir_tournament: Semester,
        ir_session: SessionModel,
        two_ir_students: list,
    ):
        """
        Calling process_match_results twice for the same user:
        get_or_create_ranking returns the existing row on the second call,
        and points are overwritten (not accumulated).
        Final state: 2 rows (not 4), updated points.
        """
        user_a, user_b = two_ir_students
        proc = ResultProcessor(db=test_db)

        # First submission
        proc.process_match_results(
            db=test_db,
            session=ir_session,
            tournament=ir_tournament,
            raw_results=[
                {"user_id": user_a.id, "measured_value": 10},
                {"user_id": user_b.id, "measured_value": 5},
            ],
        )

        # Second submission with updated values
        proc.process_match_results(
            db=test_db,
            session=ir_session,
            tournament=ir_tournament,
            raw_results=[
                {"user_id": user_a.id, "measured_value": 90},
                {"user_id": user_b.id, "measured_value": 80},
            ],
        )

        rankings = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == ir_tournament.id)
            .all()
        )
        # Still 2 rows — no duplicates created
        assert len(rankings) == 2

        by_user = {r.user_id: r for r in rankings}
        # Points reflect the second submission
        assert by_user[user_a.id].points == Decimal("90")
        assert by_user[user_b.id].points == Decimal("80")

    def test_missing_required_field_raises_before_db_write(
        self,
        test_db: Session,
        ir_tournament: Semester,
        ir_session: SessionModel,
        two_ir_students: list,
    ):
        """
        Raw result missing 'measured_value' → ValueError raised,
        no TournamentRanking rows written.
        """
        user_a = two_ir_students[0]
        proc = ResultProcessor(db=test_db)

        with pytest.raises(ValueError, match="measured_value"):
            proc.process_match_results(
                db=test_db,
                session=ir_session,
                tournament=ir_tournament,
                raw_results=[{"user_id": user_a.id}],  # missing measured_value
            )

        count = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == ir_tournament.id)
            .count()
        )
        assert count == 0, "No DB rows should be written on validation failure"


# ============================================================================
# Round 2B — HEAD_TO_HEAD fixtures (minimal, local, no conftest changes)
# ============================================================================

@pytest.fixture
def h2h_tournament_type(test_db: Session):
    """
    TournamentType with format="HEAD_TO_HEAD".
    Required so that Semester.format property resolves to "HEAD_TO_HEAD"
    via Priority 1: tournament_config_obj.tournament_type.format.
    """
    from app.models.tournament_type import TournamentType
    tt = TournamentType(
        code=f"h2h-test-{uuid.uuid4().hex[:6]}",
        display_name="H2H Integration Test Type",
        format="HEAD_TO_HEAD",
        config={},
    )
    test_db.add(tt)
    test_db.commit()
    test_db.refresh(tt)
    return tt


@pytest.fixture
def h2h_tournament(test_db: Session, h2h_tournament_type) -> Semester:
    """
    Semester + TournamentConfiguration → Semester.format == "HEAD_TO_HEAD".

    Chain:
      Semester.tournament_config_obj → TournamentConfiguration
      TournamentConfiguration.tournament_type → TournamentType(format="HEAD_TO_HEAD")
      → Semester.format property returns "HEAD_TO_HEAD"
    """
    from app.models.tournament_configuration import TournamentConfiguration
    sem = Semester(
        code=f"H2H-{uuid.uuid4().hex[:8]}",
        name="H2H Integration Test Tournament",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=90),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.commit()
    test_db.refresh(sem)

    config = TournamentConfiguration(
        semester_id=sem.id,
        tournament_type_id=h2h_tournament_type.id,
    )
    test_db.add(config)
    test_db.commit()
    test_db.refresh(sem)  # reload relationships so Semester.format resolves
    return sem


@pytest.fixture
def h2h_session(test_db: Session, h2h_tournament: Semester, instructor_user: User) -> SessionModel:
    """
    Tournament session for H2H tests.
    tournament_phase=None → KnockoutProgressionService is explicitly skipped.
    match_format="HEAD_TO_HEAD" → process_results uses the H2H processor.
    """
    session_start = datetime.now() + timedelta(days=1)
    sess = SessionModel(
        title="H2H Integration Test Session",
        date_start=session_start,
        date_end=session_start + timedelta(hours=2),
        session_type=SessionType.on_site,
        capacity=20,
        instructor_id=instructor_user.id,
        semester_id=h2h_tournament.id,
        event_category=EventCategory.MATCH,
        match_format="HEAD_TO_HEAD",
        tournament_phase=None,  # explicit: KnockoutProgressionService never invoked
    )
    test_db.add(sess)
    test_db.commit()
    test_db.refresh(sess)
    return sess


@pytest.fixture
def two_h2h_students(test_db: Session):
    """Two STUDENT users for H2H match participants."""
    users = []
    for i in range(2):
        u = User(
            email=f"h2h-student-{i}-{uuid.uuid4().hex[:6]}@test.com",
            name=f"H2H Student {i}",
            password_hash=get_password_hash("test"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(u)
        users.append(u)
    test_db.commit()
    for u in users:
        test_db.refresh(u)
    return users


# ============================================================================
# TestProcessMatchResultsH2H — HEAD_TO_HEAD orchestration
# ============================================================================

@pytest.mark.integration
class TestProcessMatchResultsH2H:
    """
    Validates the full DB-writing pipeline for HEAD_TO_HEAD tournaments.

    tournament_phase=None → KnockoutProgressionService is never invoked.
    PointsCalculatorService runs against real DB using DEFAULT_RANKING_POINTS
    (rank 1 = 3 pts, rank 2 = 2 pts) — no mock, no patching.

    Assertions target final DB state only.
    """

    def test_win_loss_creates_ranking_rows_with_correct_counters(
        self,
        test_db: Session,
        h2h_tournament: Semester,
        h2h_session: SessionModel,
        two_h2h_students: list,
    ):
        """
        WIN_LOSS format happy path:
          - 2 TournamentRanking rows created
          - winner.wins == 1, winner.losses == 0
          - loser.losses == 1, loser.wins == 0
          - winner.points > loser.points  (3.0 vs 2.0 from DEFAULT_RANKING_POINTS)
        """
        winner, loser = two_h2h_students
        raw_results = [
            {"user_id": winner.id, "result": "WIN"},
            {"user_id": loser.id,  "result": "LOSS"},
        ]

        proc = ResultProcessor(db=test_db)
        proc.process_match_results(
            db=test_db,
            session=h2h_session,
            tournament=h2h_tournament,
            raw_results=raw_results,
        )

        rankings = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == h2h_tournament.id)
            .all()
        )
        assert len(rankings) == 2

        by_user = {r.user_id: r for r in rankings}
        assert winner.id in by_user
        assert loser.id in by_user

        # Wins / losses counters
        assert by_user[winner.id].wins   == 1
        assert by_user[winner.id].losses == 0
        assert by_user[loser.id].losses  == 1
        assert by_user[loser.id].wins    == 0

        # Points: PointsCalculatorService rank1=3pts, rank2=2pts
        assert by_user[winner.id].points > by_user[loser.id].points

    def test_score_based_game_results_json_contains_h2h_format(
        self,
        test_db: Session,
        h2h_tournament: Semester,
        h2h_session: SessionModel,
        two_h2h_students: list,
    ):
        """
        SCORE_BASED format:
          - session.game_results updated with valid JSON
          - game_results["tournament_format"] == "HEAD_TO_HEAD"
          - participants list present with result/score fields
          - higher score player wins (rank 1)
        """
        player_a, player_b = two_h2h_students
        raw_results = [
            {"user_id": player_a.id, "score": 3, "opponent_score": 1},
            {"user_id": player_b.id, "score": 1, "opponent_score": 3},
        ]

        proc = ResultProcessor(db=test_db)
        proc.process_match_results(
            db=test_db,
            session=h2h_session,
            tournament=h2h_tournament,
            raw_results=raw_results,
        )

        test_db.refresh(h2h_session)
        assert h2h_session.game_results is not None

        data = json.loads(h2h_session.game_results)

        # Required keys
        for key in ("recorded_at", "tournament_format", "match_format",
                    "participants", "derived_rankings"):
            assert key in data, f"Missing key in game_results: {key!r}"

        # Format markers
        assert data["tournament_format"] == "HEAD_TO_HEAD"
        assert data["match_format"]       == "HEAD_TO_HEAD"

        # participants: built for SCORE_BASED 1v1 — contains result strings
        assert isinstance(data["participants"], list)
        assert len(data["participants"]) == 2

        results_in_participants = {p["result"] for p in data["participants"]}
        assert "win"  in results_in_participants
        assert "loss" in results_in_participants

        # player_a (score=3) must be the winner
        by_user = {p["user_id"]: p for p in data["participants"]}
        assert by_user[player_a.id]["result"] == "win"
        assert by_user[player_b.id]["result"] == "loss"

    def test_idempotent_reprocessing_accumulates_wins_and_points(
        self,
        test_db: Session,
        h2h_tournament: Semester,
        h2h_session: SessionModel,
        two_h2h_students: list,
    ):
        """
        H2H: points and wins/losses ACCUMULATE across calls (unlike IR where points
        are SET). Second call with the same winner adds another win and 3 more points.

        Final state after 2 identical submissions:
          - Still 2 rows (no duplicates)
          - winner.wins == 2, loser.losses == 2
          - winner.points == 6.0 (3+3), loser.points == 4.0 (2+2)
          - winner.points still > loser.points (invariant preserved)
        """
        winner, loser = two_h2h_students
        raw_results = [
            {"user_id": winner.id, "result": "WIN"},
            {"user_id": loser.id,  "result": "LOSS"},
        ]

        proc = ResultProcessor(db=test_db)

        # First submission
        proc.process_match_results(
            db=test_db,
            session=h2h_session,
            tournament=h2h_tournament,
            raw_results=raw_results,
        )
        # Second submission — same match data
        proc.process_match_results(
            db=test_db,
            session=h2h_session,
            tournament=h2h_tournament,
            raw_results=raw_results,
        )

        rankings = (
            test_db.query(TournamentRanking)
            .filter(TournamentRanking.tournament_id == h2h_tournament.id)
            .all()
        )
        # No duplicate rows created
        assert len(rankings) == 2

        by_user = {r.user_id: r for r in rankings}
        # Counters accumulated
        assert by_user[winner.id].wins   == 2
        assert by_user[loser.id].losses  == 2
        # Points accumulated: 3+3=6 vs 2+2=4
        assert by_user[winner.id].points == Decimal("6")
        assert by_user[loser.id].points  == Decimal("4")
        # Invariant: winner still ahead
        assert by_user[winner.id].points > by_user[loser.id].points

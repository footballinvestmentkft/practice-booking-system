"""
Integration Tests: Dual Finalization Path Prevention

Tests that verify the system prevents duplicate rankings from multiple finalization paths.

Critical fixes implemented (2026-02-01):
1. DB unique constraint prevents duplicates
2. SessionFinalizer has hard idempotency guard

Test scenarios:
- Manual finalization after sandbox creation (blocked)
- Double finalization attempt (blocked)
- DB constraint enforcement (IntegrityError)
- Ranking count = player count invariant
"""
import uuid
import pytest
from datetime import date as date_type, datetime
from sqlalchemy.exc import IntegrityError
from app.models.tournament_ranking import TournamentRanking
from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel, EventCategory
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.tournament.results.finalization.session_finalizer import SessionFinalizer


def test_session_finalizer_idempotency_tournament_level(
    test_db,
    sample_tournament_individual,
    sample_session_individual,
    sample_user
):
    """
    Test that SessionFinalizer rejects finalization if tournament_rankings already exist.

    This prevents DUAL PATH bug even if session.game_results is empty but
    rankings were created by another path.
    """
    # Manually create a ranking (simulating sandbox path)
    ranking = TournamentRanking(
        tournament_id=sample_tournament_individual.id,
        user_id=sample_user.id,
        participant_type="INDIVIDUAL",
        rank=1,
        points=100,
        wins=0,
        losses=0,
        draws=0
    )
    test_db.add(ranking)
    test_db.commit()

    # Attempt to finalize session via SessionFinalizer
    finalizer = SessionFinalizer(test_db)

    # Should be BLOCKED by tournament_rankings idempotency guard
    with pytest.raises(ValueError, match="already has .* ranking"):
        finalizer.finalize(
            tournament=sample_tournament_individual,
            session=sample_session_individual,
            recorded_by_id=sample_user.id,
            recorded_by_name=sample_user.name
        )

    # Verify STILL only 1 ranking (no duplicates created)
    rankings_after = test_db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == sample_tournament_individual.id
    ).all()
    assert len(rankings_after) == 1


def test_db_unique_constraint_prevents_duplicates(test_db, sample_tournament_individual, sample_user):
    """
    Test that database unique constraint prevents duplicate rankings.

    This is the FINAL defense layer - even if all code guards fail,
    the DB will reject duplicates.
    """
    # Create first ranking
    ranking1 = TournamentRanking(
        tournament_id=sample_tournament_individual.id,
        user_id=sample_user.id,
        participant_type="INDIVIDUAL",
        rank=1,
        points=100,
        wins=0,
        losses=0,
        draws=0
    )
    test_db.add(ranking1)
    test_db.commit()

    # Attempt to create duplicate (same tournament_id, user_id, participant_type)
    ranking2 = TournamentRanking(
        tournament_id=sample_tournament_individual.id,
        user_id=sample_user.id,
        participant_type="INDIVIDUAL",  # Same as ranking1
        rank=2,  # Different rank
        points=200,  # Different points
        wins=0,
        losses=0,
        draws=0
    )
    test_db.add(ranking2)

    # Should raise IntegrityError due to unique constraint
    with pytest.raises(IntegrityError, match="uq_tournament_rankings_tournament_user_type"):
        test_db.commit()

    test_db.rollback()

    # Verify only 1 ranking exists
    rankings = test_db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == sample_tournament_individual.id,
        TournamentRanking.user_id == sample_user.id
    ).all()
    assert len(rankings) == 1


def test_ranking_count_equals_player_count(
    test_db,
    sample_tournament_individual,
    sample_session_individual,
    sample_users_8
):
    """
    Test the invariant: ranking count MUST equal unique player count.

    This is the ultimate business rule validation.
    """
    # Create rankings for 8 players using real user IDs
    player_count = len(sample_users_8)
    for i, user in enumerate(sample_users_8):
        ranking = TournamentRanking(
            tournament_id=sample_tournament_individual.id,
            user_id=user.id,
            participant_type="INDIVIDUAL",
            rank=i + 1,
            points=100 - (i * 10),
            wins=0,
            losses=0,
            draws=0
        )
        test_db.add(ranking)

    test_db.commit()

    # Verify invariant
    total_rankings = test_db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == sample_tournament_individual.id
    ).count()

    unique_players = test_db.query(TournamentRanking.user_id).filter(
        TournamentRanking.tournament_id == sample_tournament_individual.id
    ).distinct().count()

    assert total_rankings == unique_players == player_count, (
        f"CRITICAL INVARIANT VIOLATION: "
        f"total_rankings={total_rankings}, "
        f"unique_players={unique_players}, "
        f"expected={player_count}. "
        f"Each player must have exactly ONE ranking!"
    )


def test_double_finalization_blocked(
    test_db,
    sample_tournament_individual,
    sample_users_3
):
    """
    Test that double finalization is blocked.

    First finalization succeeds, second is rejected.
    """
    u1, u2, u3 = sample_users_3
    session = SessionModel(
        semester_id=sample_tournament_individual.id,
        title="Test Double Finalization Session",
        date_start=datetime(2026, 2, 1, 10, 0),
        date_end=datetime(2026, 2, 1, 12, 0),
        match_format="INDIVIDUAL_RANKING",
        event_category=EventCategory.MATCH,
        rounds_data={
            "total_rounds": 2,
            "completed_rounds": 2,
            "round_results": {
                "1": {str(u1.id): "10.5s", str(u2.id): "11.2s", str(u3.id): "12.0s"},
                "2": {str(u1.id): "10.3s", str(u2.id): "11.5s", str(u3.id): "11.8s"}
            }
        },
        game_results=None
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)

    finalizer = SessionFinalizer(test_db)

    # First finalization - should succeed
    result1 = finalizer.finalize(
        tournament=sample_tournament_individual,
        session=session,
        recorded_by_id=u1.id,
        recorded_by_name=u1.name
    )
    assert result1["success"] is True

    # Second finalization - should be BLOCKED
    with pytest.raises(ValueError, match="already finalized"):
        finalizer.finalize(
            tournament=sample_tournament_individual,
            session=session,
            recorded_by_id=u1.id,
            recorded_by_name=u1.name
        )

    # Verify only ONE set of rankings exists
    rankings = test_db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == sample_tournament_individual.id
    ).all()

    unique_users = set(r.user_id for r in rankings)
    assert len(rankings) == len(unique_users), (
        "Duplicate rankings detected after double finalization attempt!"
    )


# ============================================================================
# FIXTURES  (all function-scoped, SAVEPOINT-isolated via test_db)
# ============================================================================

@pytest.fixture(scope="function")
def sample_user(test_db):
    """Create a single real user for FK-safe tournament ranking tests."""
    user = User(
        email=f"dual_path_{uuid.uuid4().hex[:8]}@test.com",
        password_hash=get_password_hash("test123"),
        name="Dual Path User",
        role=UserRole.STUDENT,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture(scope="function")
def sample_users_8(test_db):
    """Create 8 real users for ranking count invariant test."""
    users = []
    for i in range(8):
        user = User(
            email=f"dp8_p{i}_{uuid.uuid4().hex[:8]}@test.com",
            password_hash=get_password_hash("test123"),
            name=f"Player {i + 1}",
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(user)
        users.append(user)
    test_db.commit()
    for u in users:
        test_db.refresh(u)
    return users


@pytest.fixture(scope="function")
def sample_users_3(test_db):
    """Create 3 real users for double-finalization test."""
    users = []
    for i in range(3):
        user = User(
            email=f"dp3_{i}_{uuid.uuid4().hex[:8]}@test.com",
            password_hash=get_password_hash("test123"),
            name=f"DFPlayer {i + 1}",
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(user)
        users.append(user)
    test_db.commit()
    for u in users:
        test_db.refresh(u)
    return users


@pytest.fixture(scope="function")
def sample_tournament_individual(test_db):
    """Create a sample INDIVIDUAL_RANKING tournament"""
    tournament = Semester(
        name="Test INDIVIDUAL Tournament",
        code=f"TEST-IND-{uuid.uuid4().hex[:8]}",
        start_date=date_type(2026, 2, 1),
        end_date=date_type(2026, 6, 1),
        status=SemesterStatus.ONGOING,
        tournament_status="IN_PROGRESS"
    )
    test_db.add(tournament)
    test_db.commit()
    test_db.refresh(tournament)
    return tournament


@pytest.fixture(scope="function")
def sample_session_individual(test_db, sample_tournament_individual):
    """Create a sample INDIVIDUAL_RANKING session"""
    session = SessionModel(
        semester_id=sample_tournament_individual.id,
        title="Test Session",
        date_start=datetime(2026, 2, 1, 10, 0),
        date_end=datetime(2026, 2, 1, 12, 0),
        match_format="INDIVIDUAL_RANKING",
        event_category=EventCategory.MATCH,
        rounds_data={
            "total_rounds": 3,
            "completed_rounds": 3,
            "round_results": {
                "1": {"1": "10.5s", "2": "11.2s"},
                "2": {"1": "10.3s", "2": "11.5s"},
                "3": {"1": "10.7s", "2": "11.0s"}
            }
        }
    )
    test_db.add(session)
    test_db.commit()
    test_db.refresh(session)
    return session

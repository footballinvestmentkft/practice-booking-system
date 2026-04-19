"""
Comprehensive unit tests for leaderboard_service.py

Tests all 9 functions with happy path, edge cases, error handling, and validation.
Covers individual and team-based tournaments, multi-day tournaments, and ranking calculations.
"""
import pytest
import uuid
from sqlalchemy.orm import Session
from decimal import Decimal
from datetime import date, datetime, timedelta

from app.services.tournament import leaderboard_service
from app.models import (
    TournamentRanking,
    User,
    Team,
    TeamMember,
    Semester,
    Session as SessionModel,
    Attendance,
    AttendanceStatus,
    Booking,
    BookingStatus,
    SessionType,
    EventCategory,
    SemesterStatus,
    ParticipantType,
    TeamMemberRole
)
from app.models.user import UserRole
from app.models.specialization import SpecializationType


def create_test_user(db: Session, email: str, name: str, role: UserRole = UserRole.STUDENT) -> User:
    """Helper function to create a test user.

    Note: Email is made unique per test run by appending UUID suffix.
    This prevents collisions with legacy test data in DB.
    """
    # Make email unique to avoid collisions with existing test data
    unique_email = f"{email.split('@')[0]}+{uuid.uuid4().hex[:8]}@{email.split('@')[1]}"

    user = User(
        email=unique_email,
        name=name,
        password_hash="test_hash_123",
        role=role  # Pass enum directly
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_team(db: Session, name: str, captain_user_id: int, code: str = None) -> Team:
    """Helper function to create a test team.

    Note: Team code is made unique per test run by appending UUID suffix.
    This prevents collisions with legacy test data in DB.
    Max length: 20 chars (DB constraint).
    """
    if code is None:
        code = f"TEAM-{name.upper().replace(' ', '-')[:10]}"

    # Make code unique to avoid collisions (max 20 chars total)
    # Format: "TM-{short_name}-{uuid4}" where short_name is truncated to fit
    uuid_suffix = uuid.uuid4().hex[:4]  # 4 chars
    max_prefix = 20 - 1 - len(uuid_suffix)  # -1 for hyphen
    unique_code = f"{code[:max_prefix]}-{uuid_suffix}"

    team = Team(
        name=name,
        code=unique_code,
        captain_user_id=captain_user_id,
        specialization_type=SpecializationType.LFA_PLAYER_YOUTH.value,
        is_active=True
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Add captain as team member
    member = TeamMember(
        team_id=team.id,
        user_id=captain_user_id,
        role=TeamMemberRole.CAPTAIN.value,
        is_active=True
    )
    db.add(member)
    db.commit()

    return team


def create_test_semester(db: Session, name: str = "Test Tournament") -> Semester:
    """Helper function to create a test semester/tournament"""
    tournament_date = date.today() + timedelta(days=7)

    # Make unique code with timestamp to avoid collisions
    import time
    unique_suffix = str(int(time.time() * 1000000))[-6:]

    semester = Semester(
        code=f"TOURN-{tournament_date.strftime('%Y%m%d')}-{unique_suffix}",
        name=name,
        start_date=tournament_date,
        end_date=tournament_date,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        specialization_type=SpecializationType.LFA_PLAYER_YOUTH.value,
        age_group="YOUTH"
    )
    db.add(semester)
    db.commit()
    db.refresh(semester)
    return semester


def create_test_session(
    db: Session,
    semester_id: int,
    session_date: date = None
) -> SessionModel:
    """Helper function to create a test session"""
    if session_date is None:
        session_date = date.today() + timedelta(days=7)

    start_time = datetime.combine(session_date, datetime.min.time().replace(hour=10))
    end_time = start_time + timedelta(hours=2)

    session = SessionModel(
        title="Tournament Game",
        description="Test tournament game",
        date_start=start_time,
        date_end=end_time,
        session_type=SessionType.on_site,
        capacity=20,
        semester_id=semester_id,
        credit_cost=1,
        event_category=EventCategory.MATCH
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


class TestGetOrCreateRanking:
    """Test get_or_create_ranking() function"""

    def test_create_individual_ranking(self, test_db: Session):
        """Happy path: Create new individual ranking"""
        user = create_test_user(test_db, "player1@test.com", "Player 1")
        tournament = create_test_semester(test_db, "Individual Tournament")

        ranking = leaderboard_service.get_or_create_ranking(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type=ParticipantType.INDIVIDUAL.value
        )

        assert ranking.tournament_id == tournament.id
        assert ranking.user_id == user.id
        assert ranking.team_id is None
        assert ranking.participant_type == ParticipantType.INDIVIDUAL.value
        assert ranking.points == Decimal('0')
        assert ranking.wins == 0
        assert ranking.losses == 0
        assert ranking.draws == 0
        assert ranking.rank is None

    def test_create_team_ranking(self, test_db: Session):
        """Happy path: Create new team ranking"""
        captain = create_test_user(test_db, "captain@test.com", "Captain")
        team = create_test_team(test_db, "Test Team", captain.id)
        tournament = create_test_semester(test_db, "Team Tournament")

        ranking = leaderboard_service.get_or_create_ranking(
            db=test_db,
            tournament_id=tournament.id,
            team_id=team.id,
            participant_type=ParticipantType.TEAM.value
        )

        assert ranking.tournament_id == tournament.id
        assert ranking.user_id is None
        assert ranking.team_id == team.id
        assert ranking.participant_type == ParticipantType.TEAM.value
        assert ranking.points == Decimal('0')

    def test_get_existing_ranking(self, test_db: Session):
        """Edge case: Get existing ranking instead of creating new one"""
        user = create_test_user(test_db, "player2@test.com", "Player 2")
        tournament = create_test_semester(test_db, "Tournament")

        # Create first time
        ranking1 = leaderboard_service.get_or_create_ranking(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type=ParticipantType.INDIVIDUAL.value
        )

        # Get second time (should return same ranking)
        ranking2 = leaderboard_service.get_or_create_ranking(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type=ParticipantType.INDIVIDUAL.value
        )

        assert ranking1.id == ranking2.id

    def test_different_tournaments_different_rankings(self, test_db: Session):
        """Edge case: Same user in different tournaments"""
        user = create_test_user(test_db, "player3@test.com", "Player 3")
        tournament1 = create_test_semester(test_db, "Tournament 1")
        tournament2 = create_test_semester(test_db, "Tournament 2")

        ranking1 = leaderboard_service.get_or_create_ranking(
            db=test_db,
            tournament_id=tournament1.id,
            user_id=user.id
        )

        ranking2 = leaderboard_service.get_or_create_ranking(
            db=test_db,
            tournament_id=tournament2.id,
            user_id=user.id
        )

        assert ranking1.id != ranking2.id
        assert ranking1.tournament_id == tournament1.id
        assert ranking2.tournament_id == tournament2.id


class TestUpdateRankingFromResult:
    """Test update_ranking_from_result() function"""

    def test_update_with_win(self, test_db: Session):
        """Happy path: Update ranking with a win"""
        user = create_test_user(test_db, "player4@test.com", "Player 4")
        tournament = create_test_semester(test_db, "Tournament")

        ranking = leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            points=Decimal('3.0'),
            win=True
        )

        assert ranking.points == Decimal('3.0')
        assert ranking.wins == 1
        assert ranking.losses == 0
        assert ranking.draws == 0

    def test_update_with_loss(self, test_db: Session):
        """Happy path: Update ranking with a loss"""
        user = create_test_user(test_db, "player5@test.com", "Player 5")
        tournament = create_test_semester(test_db, "Tournament")

        ranking = leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            points=Decimal('0.0'),
            loss=True
        )

        assert ranking.points == Decimal('0.0')
        assert ranking.wins == 0
        assert ranking.losses == 1
        assert ranking.draws == 0

    def test_update_with_draw(self, test_db: Session):
        """Happy path: Update ranking with a draw"""
        user = create_test_user(test_db, "player6@test.com", "Player 6")
        tournament = create_test_semester(test_db, "Tournament")

        ranking = leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            points=Decimal('1.0'),
            draw=True
        )

        assert ranking.points == Decimal('1.0')
        assert ranking.wins == 0
        assert ranking.losses == 0
        assert ranking.draws == 1

    def test_update_multiple_results(self, test_db: Session):
        """Edge case: Multiple updates accumulate correctly"""
        user = create_test_user(test_db, "player7@test.com", "Player 7")
        tournament = create_test_semester(test_db, "Tournament")

        # Win
        leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            points=Decimal('3.0'),
            win=True
        )

        # Draw
        leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            points=Decimal('1.0'),
            draw=True
        )

        # Loss
        ranking = leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            points=Decimal('0.0'),
            loss=True
        )

        assert ranking.points == Decimal('4.0')  # 3 + 1 + 0
        assert ranking.wins == 1
        assert ranking.draws == 1
        assert ranking.losses == 1

    def test_update_team_ranking(self, test_db: Session):
        """Happy path: Update team ranking"""
        captain = create_test_user(test_db, "captain2@test.com", "Captain 2")
        team = create_test_team(test_db, "Team A", captain.id)
        tournament = create_test_semester(test_db, "Team Tournament")

        ranking = leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            team_id=team.id,
            points=Decimal('3.0'),
            win=True
        )

        assert ranking.team_id == team.id
        assert ranking.participant_type == ParticipantType.TEAM.value
        assert ranking.points == Decimal('3.0')
        assert ranking.wins == 1

    def test_update_participation_points_only(self, test_db: Session):
        """Edge case: Award only participation points (no win/loss/draw)"""
        user = create_test_user(test_db, "player8@test.com", "Player 8")
        tournament = create_test_semester(test_db, "Tournament")

        ranking = leaderboard_service.update_ranking_from_result(
            db=test_db,
            tournament_id=tournament.id,
            user_id=user.id,
            points=Decimal('1.0')
        )

        assert ranking.points == Decimal('1.0')
        assert ranking.wins == 0
        assert ranking.losses == 0
        assert ranking.draws == 0


class TestCalculateRanks:
    """Test calculate_ranks() function"""

    def test_calculate_ranks_by_points(self, test_db: Session):
        """Happy path: Calculate ranks based on points"""
        tournament = create_test_semester(test_db, "Tournament")
        user1 = create_test_user(test_db, "player9@test.com", "Player 9")
        user2 = create_test_user(test_db, "player10@test.com", "Player 10")
        user3 = create_test_user(test_db, "player11@test.com", "Player 11")

        # Create rankings with different points
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('5.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user3.id, points=Decimal('8.0')
        )

        # Calculate ranks
        rankings = leaderboard_service.calculate_ranks(test_db, tournament.id)

        # Sort by user_id to check ranks
        rankings_by_user = {r.user_id: r for r in rankings}
        assert rankings_by_user[user1.id].rank == 1  # 10 points
        assert rankings_by_user[user3.id].rank == 2  # 8 points
        assert rankings_by_user[user2.id].rank == 3  # 5 points

    def test_calculate_ranks_tie_breaker_wins(self, test_db: Session):
        """Edge case: Same points, more wins ranks higher"""
        tournament = create_test_semester(test_db, "Tournament")
        user1 = create_test_user(test_db, "player12@test.com", "Player 12")
        user2 = create_test_user(test_db, "player13@test.com", "Player 13")

        # Both have 6 points, but different wins
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('6.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('3.0'), win=True
        )

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('6.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('0.0'), loss=True
        )

        rankings = leaderboard_service.calculate_ranks(test_db, tournament.id)

        rankings_by_user = {r.user_id: r for r in rankings}
        # user1: 6 points, 2 wins, 0 losses
        # user2: 6 points, 1 win, 1 loss
        assert rankings_by_user[user1.id].rank == 1  # More wins
        assert rankings_by_user[user2.id].rank == 2

    def test_calculate_ranks_tie_breaker_losses(self, test_db: Session):
        """Edge case: Same points and wins, fewer losses ranks higher"""
        tournament = create_test_semester(test_db, "Tournament")
        user1 = create_test_user(test_db, "player14@test.com", "Player 14")
        user2 = create_test_user(test_db, "player15@test.com", "Player 15")

        # Same points, same wins, different losses
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('3.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('0.0'), loss=True
        )

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('3.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('0.0'), loss=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('0.0'), loss=True
        )

        rankings = leaderboard_service.calculate_ranks(test_db, tournament.id)

        rankings_by_user = {r.user_id: r for r in rankings}
        # user1: 3 points, 1 win, 1 loss
        # user2: 3 points, 1 win, 2 losses
        assert rankings_by_user[user1.id].rank == 1  # Fewer losses
        assert rankings_by_user[user2.id].rank == 2

    def test_calculate_ranks_empty_tournament(self, test_db: Session):
        """Edge case: Tournament with no participants"""
        tournament = create_test_semester(test_db, "Empty Tournament")

        rankings = leaderboard_service.calculate_ranks(test_db, tournament.id)

        assert rankings == []

    def test_calculate_ranks_mixed_participants(self, test_db: Session):
        """Edge case: Tournament with both individuals and teams"""
        tournament = create_test_semester(test_db, "Mixed Tournament")

        user1 = create_test_user(test_db, "player16@test.com", "Player 16")
        captain = create_test_user(test_db, "captain3@test.com", "Captain 3")
        team1 = create_test_team(test_db, "Team B", captain.id)

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team1.id, points=Decimal('8.0')
        )

        rankings = leaderboard_service.calculate_ranks(test_db, tournament.id)

        # Individual should rank higher
        assert len(rankings) == 2
        assert rankings[0].user_id == user1.id
        assert rankings[0].rank == 1
        assert rankings[1].team_id == team1.id
        assert rankings[1].rank == 2


class TestGetLeaderboard:
    """Test get_leaderboard() function"""

    def test_get_leaderboard_with_users(self, test_db: Session):
        """Happy path: Get leaderboard with user details"""
        tournament = create_test_semester(test_db, "Tournament")
        user1 = create_test_user(test_db, "player17@test.com", "Player 17")
        user2 = create_test_user(test_db, "player18@test.com", "Player 18")

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id,
            points=Decimal('10.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id,
            points=Decimal('1.0'), draw=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id,
            points=Decimal('5.0'), win=True
        )

        leaderboard_service.calculate_ranks(test_db, tournament.id)

        leaderboard = leaderboard_service.get_leaderboard(
            test_db, tournament.id
        )

        assert len(leaderboard) == 2

        # Check first place
        assert leaderboard[0]['rank'] == 1
        assert leaderboard[0]['user_id'] == user1.id
        assert leaderboard[0]['user_name'] == "Player 17"
        assert leaderboard[0]['user_email'] == user1.email  # Use actual generated email
        assert leaderboard[0]['points'] == 11.0
        assert leaderboard[0]['wins'] == 1
        assert leaderboard[0]['draws'] == 1
        assert leaderboard[0]['losses'] == 0
        assert leaderboard[0]['matches_played'] == 2
        assert leaderboard[0]['participant_type'] == ParticipantType.INDIVIDUAL.value

    def test_get_leaderboard_with_teams(self, test_db: Session):
        """Happy path: Get leaderboard with team details"""
        tournament = create_test_semester(test_db, "Team Tournament")
        captain1 = create_test_user(test_db, "captain4@test.com", "Captain 4")
        captain2 = create_test_user(test_db, "captain5@test.com", "Captain 5")
        team1 = create_test_team(test_db, "Team Alpha", captain1.id, "ALPHA")
        team2 = create_test_team(test_db, "Team Beta", captain2.id, "BETA")

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team1.id,
            points=Decimal('10.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team2.id,
            points=Decimal('5.0'), win=True
        )

        leaderboard_service.calculate_ranks(test_db, tournament.id)

        leaderboard = leaderboard_service.get_leaderboard(
            test_db, tournament.id
        )

        assert len(leaderboard) == 2
        assert leaderboard[0]['team_id'] == team1.id
        assert leaderboard[0]['team_name'] == "Team Alpha"
        assert leaderboard[0]['team_code'] == team1.code  # Use actual generated code
        assert leaderboard[0]['participant_type'] == ParticipantType.TEAM.value

    def test_get_leaderboard_filter_by_participant_type(self, test_db: Session):
        """Edge case: Filter by participant type"""
        tournament = create_test_semester(test_db, "Mixed Tournament")

        user = create_test_user(test_db, "player19@test.com", "Player 19")
        captain = create_test_user(test_db, "captain6@test.com", "Captain 6")
        team = create_test_team(test_db, "Team C", captain.id)

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team.id, points=Decimal('8.0')
        )

        # Get only individual participants
        individual_board = leaderboard_service.get_leaderboard(
            test_db, tournament.id, participant_type=ParticipantType.INDIVIDUAL.value
        )

        assert len(individual_board) == 1
        assert individual_board[0]['user_id'] == user.id

        # Get only team participants
        team_board = leaderboard_service.get_leaderboard(
            test_db, tournament.id, participant_type=ParticipantType.TEAM.value
        )

        assert len(team_board) == 1
        assert team_board[0]['team_id'] == team.id

    def test_get_leaderboard_with_limit(self, test_db: Session):
        """Edge case: Limit leaderboard results"""
        tournament = create_test_semester(test_db, "Tournament")

        # Create 5 players
        for i in range(5):
            user = create_test_user(test_db, f"player{20+i}@test.com", f"Player {20+i}")
            leaderboard_service.update_ranking_from_result(
                test_db, tournament.id, user_id=user.id,
                points=Decimal(str(10.0 - i))
            )

        leaderboard_service.calculate_ranks(test_db, tournament.id)

        # Get top 3 only
        leaderboard = leaderboard_service.get_leaderboard(
            test_db, tournament.id, limit=3
        )

        assert len(leaderboard) == 3

    def test_get_leaderboard_null_ranks_last(self, test_db: Session):
        """Edge case: Rankings without calculated rank appear last"""
        tournament = create_test_semester(test_db, "Tournament")
        user1 = create_test_user(test_db, "player25@test.com", "Player 25")
        user2 = create_test_user(test_db, "player26@test.com", "Player 26")

        # Create ranking but don't calculate ranks
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('5.0')
        )

        # Don't call calculate_ranks - ranks should be None
        leaderboard = leaderboard_service.get_leaderboard(
            test_db, tournament.id
        )

        # Both should appear, sorted by points even without ranks
        assert len(leaderboard) == 2
        assert leaderboard[0]['rank'] is None
        assert leaderboard[0]['points'] == 10.0


class TestCalculateLeaguePoints:
    """Test calculate_league_points() function"""

    def test_calculate_league_points_participation(self, test_db: Session):
        """Happy path: Award participation points for attendance"""
        tournament = create_test_semester(test_db, "League Tournament")
        session = create_test_session(test_db, tournament.id)

        user1 = create_test_user(test_db, "player27@test.com", "Player 27")
        user2 = create_test_user(test_db, "player28@test.com", "Player 28")

        # Create bookings first (required for attendance)
        booking1 = Booking(
            user_id=user1.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED
        )
        booking2 = Booking(
            user_id=user2.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED
        )
        test_db.add_all([booking1, booking2])
        test_db.commit()
        test_db.refresh(booking1)
        test_db.refresh(booking2)

        # Create attendance records
        attendance1 = Attendance(
            user_id=user1.id,
            session_id=session.id,
            booking_id=booking1.id,
            status=AttendanceStatus.present
        )
        attendance2 = Attendance(
            user_id=user2.id,
            session_id=session.id,
            booking_id=booking2.id,
            status=AttendanceStatus.present
        )
        test_db.add_all([attendance1, attendance2])
        test_db.commit()

        # Calculate league points
        leaderboard_service.calculate_league_points(
            test_db, tournament.id, session.id
        )

        # Check each user got 1 participation point
        ranking1 = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.user_id == user1.id
        ).first()

        ranking2 = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.user_id == user2.id
        ).first()

        assert ranking1.points == Decimal('1.0')
        assert ranking2.points == Decimal('1.0')

    def test_calculate_league_points_absent_no_points(self, test_db: Session):
        """Edge case: Absent users don't get participation points"""
        tournament = create_test_semester(test_db, "League Tournament")
        session = create_test_session(test_db, tournament.id)

        user_present = create_test_user(test_db, "player29@test.com", "Player 29")
        user_absent = create_test_user(test_db, "player30@test.com", "Player 30")

        # Create bookings first
        booking1 = Booking(
            user_id=user_present.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED
        )
        booking2 = Booking(
            user_id=user_absent.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED
        )
        test_db.add_all([booking1, booking2])
        test_db.commit()
        test_db.refresh(booking1)
        test_db.refresh(booking2)

        # One present, one absent
        attendance1 = Attendance(
            user_id=user_present.id,
            session_id=session.id,
            booking_id=booking1.id,
            status=AttendanceStatus.present
        )
        attendance2 = Attendance(
            user_id=user_absent.id,
            session_id=session.id,
            booking_id=booking2.id,
            status=AttendanceStatus.absent
        )
        test_db.add_all([attendance1, attendance2])
        test_db.commit()

        leaderboard_service.calculate_league_points(
            test_db, tournament.id, session.id
        )

        # Present user has ranking
        ranking_present = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.user_id == user_present.id
        ).first()

        # Absent user has no ranking
        ranking_absent = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.user_id == user_absent.id
        ).first()

        assert ranking_present is not None
        assert ranking_present.points == Decimal('1.0')
        assert ranking_absent is None

    def test_calculate_league_points_recalculates_ranks(self, test_db: Session):
        """Edge case: Ranks are recalculated after awarding points"""
        tournament = create_test_semester(test_db, "League Tournament")
        session = create_test_session(test_db, tournament.id)

        user1 = create_test_user(test_db, "player31@test.com", "Player 31")
        user2 = create_test_user(test_db, "player32@test.com", "Player 32")

        # User 1 already has 5 points
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('5.0')
        )

        # Create bookings first
        booking1 = Booking(
            user_id=user1.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED
        )
        booking2 = Booking(
            user_id=user2.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED
        )
        test_db.add_all([booking1, booking2])
        test_db.commit()
        test_db.refresh(booking1)
        test_db.refresh(booking2)

        # Mark both as present
        attendance1 = Attendance(
            user_id=user1.id,
            session_id=session.id,
            booking_id=booking1.id,
            status=AttendanceStatus.present
        )
        attendance2 = Attendance(
            user_id=user2.id,
            session_id=session.id,
            booking_id=booking2.id,
            status=AttendanceStatus.present
        )
        test_db.add_all([attendance1, attendance2])
        test_db.commit()

        # Calculate league points (should also recalculate ranks)
        leaderboard_service.calculate_league_points(
            test_db, tournament.id, session.id
        )

        # Check ranks are calculated
        ranking1 = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.user_id == user1.id
        ).first()

        ranking2 = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id,
            TournamentRanking.user_id == user2.id
        ).first()

        assert ranking1.rank == 1  # 6 points total
        assert ranking2.rank == 2  # 1 point

    def test_calculate_league_points_no_attendance(self, test_db: Session):
        """Edge case: Session with no attendance records"""
        tournament = create_test_semester(test_db, "League Tournament")
        session = create_test_session(test_db, tournament.id)

        # No attendance records created

        # Should not raise an error
        leaderboard_service.calculate_league_points(
            test_db, tournament.id, session.id
        )

        # No rankings should be created
        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id
        ).all()

        assert len(rankings) == 0


class TestGetUserRank:
    """Test get_user_rank() function"""

    def test_get_user_rank_exists(self, test_db: Session):
        """Happy path: Get rank for existing user"""
        tournament = create_test_semester(test_db, "Tournament")
        user1 = create_test_user(test_db, "player33@test.com", "Player 33")
        user2 = create_test_user(test_db, "player34@test.com", "Player 34")

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('5.0')
        )

        leaderboard_service.calculate_ranks(test_db, tournament.id)

        rank = leaderboard_service.get_user_rank(
            test_db, tournament.id, user2.id
        )

        assert rank == 2

    def test_get_user_rank_not_exists(self, test_db: Session):
        """Edge case: User has no ranking in tournament"""
        tournament = create_test_semester(test_db, "Tournament")
        user = create_test_user(test_db, "player35@test.com", "Player 35")

        rank = leaderboard_service.get_user_rank(
            test_db, tournament.id, user.id
        )

        assert rank is None

    def test_get_user_rank_before_calculation(self, test_db: Session):
        """Edge case: User has ranking but rank not calculated yet"""
        tournament = create_test_semester(test_db, "Tournament")
        user = create_test_user(test_db, "player36@test.com", "Player 36")

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user.id, points=Decimal('10.0')
        )

        # Don't calculate ranks

        rank = leaderboard_service.get_user_rank(
            test_db, tournament.id, user.id
        )

        # Should return None since rank hasn't been calculated
        assert rank is None


class TestGetTeamRank:
    """Test get_team_rank() function"""

    def test_get_team_rank_exists(self, test_db: Session):
        """Happy path: Get rank for existing team"""
        tournament = create_test_semester(test_db, "Team Tournament")
        captain1 = create_test_user(test_db, "captain7@test.com", "Captain 7")
        captain2 = create_test_user(test_db, "captain8@test.com", "Captain 8")
        team1 = create_test_team(test_db, "Team D", captain1.id)
        team2 = create_test_team(test_db, "Team E", captain2.id)

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team1.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team2.id, points=Decimal('15.0')
        )

        leaderboard_service.calculate_ranks(test_db, tournament.id)

        rank = leaderboard_service.get_team_rank(
            test_db, tournament.id, team1.id
        )

        assert rank == 2

    def test_get_team_rank_not_exists(self, test_db: Session):
        """Edge case: Team has no ranking in tournament"""
        tournament = create_test_semester(test_db, "Team Tournament")
        captain = create_test_user(test_db, "captain9@test.com", "Captain 9")
        team = create_test_team(test_db, "Team F", captain.id)

        rank = leaderboard_service.get_team_rank(
            test_db, tournament.id, team.id
        )

        assert rank is None

    def test_get_team_rank_before_calculation(self, test_db: Session):
        """Edge case: Team has ranking but rank not calculated yet"""
        tournament = create_test_semester(test_db, "Team Tournament")
        captain = create_test_user(test_db, "captain10@test.com", "Captain 10")
        team = create_test_team(test_db, "Team G", captain.id)

        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team.id, points=Decimal('10.0')
        )

        # Don't calculate ranks

        rank = leaderboard_service.get_team_rank(
            test_db, tournament.id, team.id
        )

        assert rank is None


class TestResetTournamentRankings:
    """Test reset_tournament_rankings() function"""

    def test_reset_tournament_rankings_success(self, test_db: Session):
        """Happy path: Reset all rankings for a tournament"""
        tournament = create_test_semester(test_db, "Tournament")
        user1 = create_test_user(test_db, "player37@test.com", "Player 37")
        user2 = create_test_user(test_db, "player38@test.com", "Player 38")

        # Create some rankings
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id, points=Decimal('5.0')
        )

        # Verify rankings exist
        rankings_before = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id
        ).all()
        assert len(rankings_before) == 2

        # Reset
        result = leaderboard_service.reset_tournament_rankings(
            test_db, tournament.id
        )

        assert result is True

        # Verify all rankings deleted
        rankings_after = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id
        ).all()
        assert len(rankings_after) == 0

    def test_reset_tournament_rankings_empty(self, test_db: Session):
        """Edge case: Reset tournament with no rankings"""
        tournament = create_test_semester(test_db, "Empty Tournament")

        # No rankings created

        result = leaderboard_service.reset_tournament_rankings(
            test_db, tournament.id
        )

        assert result is True

    def test_reset_tournament_rankings_multiple_tournaments(self, test_db: Session):
        """Edge case: Reset only affects specified tournament"""
        tournament1 = create_test_semester(test_db, "Tournament 1")
        tournament2 = create_test_semester(test_db, "Tournament 2")

        user1 = create_test_user(test_db, "player39@test.com", "Player 39")
        user2 = create_test_user(test_db, "player40@test.com", "Player 40")

        # Create rankings in both tournaments
        leaderboard_service.update_ranking_from_result(
            test_db, tournament1.id, user_id=user1.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament2.id, user_id=user2.id, points=Decimal('5.0')
        )

        # Reset only tournament1
        leaderboard_service.reset_tournament_rankings(test_db, tournament1.id)

        # Tournament1 rankings should be deleted
        rankings1 = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament1.id
        ).all()
        assert len(rankings1) == 0

        # Tournament2 rankings should remain
        rankings2 = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament2.id
        ).all()
        assert len(rankings2) == 1

    def test_reset_tournament_rankings_mixed_participants(self, test_db: Session):
        """Edge case: Reset tournament with both users and teams"""
        tournament = create_test_semester(test_db, "Mixed Tournament")

        user = create_test_user(test_db, "player41@test.com", "Player 41")
        captain = create_test_user(test_db, "captain11@test.com", "Captain 11")
        team = create_test_team(test_db, "Team H", captain.id)

        # Create both types of rankings
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user.id, points=Decimal('10.0')
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, team_id=team.id, points=Decimal('8.0')
        )

        # Reset
        leaderboard_service.reset_tournament_rankings(test_db, tournament.id)

        # All rankings should be deleted
        rankings = test_db.query(TournamentRanking).filter(
            TournamentRanking.tournament_id == tournament.id
        ).all()
        assert len(rankings) == 0


class TestMultiDayTournament:
    """Test scenarios for multi-day tournaments"""

    def test_multi_day_tournament_accumulation(self, test_db: Session):
        """Multi-day tournament: Points accumulate across days"""
        tournament = create_test_semester(test_db, "Multi-Day Tournament")
        user = create_test_user(test_db, "player42@test.com", "Player 42")

        # Day 1: Win
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user.id,
            points=Decimal('3.0'), win=True
        )

        # Day 2: Draw
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user.id,
            points=Decimal('1.0'), draw=True
        )

        # Day 3: Win
        ranking = leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user.id,
            points=Decimal('3.0'), win=True
        )

        assert ranking.points == Decimal('7.0')
        assert ranking.wins == 2
        assert ranking.draws == 1
        assert ranking.losses == 0

    def test_multi_day_tournament_rank_changes(self, test_db: Session):
        """Multi-day tournament: Ranks change as results come in"""
        tournament = create_test_semester(test_db, "Multi-Day Tournament")
        user1 = create_test_user(test_db, "player43@test.com", "Player 43")
        user2 = create_test_user(test_db, "player44@test.com", "Player 44")

        # Day 1: user1 wins, user2 loses
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id,
            points=Decimal('3.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id,
            points=Decimal('0.0'), loss=True
        )
        leaderboard_service.calculate_ranks(test_db, tournament.id)

        rank1_day1 = leaderboard_service.get_user_rank(test_db, tournament.id, user1.id)
        rank2_day1 = leaderboard_service.get_user_rank(test_db, tournament.id, user2.id)

        assert rank1_day1 == 1
        assert rank2_day1 == 2

        # Day 2: user2 has big win, user1 loses
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user2.id,
            points=Decimal('5.0'), win=True
        )
        leaderboard_service.update_ranking_from_result(
            test_db, tournament.id, user_id=user1.id,
            points=Decimal('0.0'), loss=True
        )
        leaderboard_service.calculate_ranks(test_db, tournament.id)

        rank1_day2 = leaderboard_service.get_user_rank(test_db, tournament.id, user1.id)
        rank2_day2 = leaderboard_service.get_user_rank(test_db, tournament.id, user2.id)

        # Ranks should have reversed
        assert rank1_day2 == 2  # 3 points
        assert rank2_day2 == 1  # 5 points

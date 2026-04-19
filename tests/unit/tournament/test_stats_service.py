"""
Comprehensive unit tests for stats_service.py

Tests all 4 functions with happy path, edge cases, and statistics calculation accuracy.
"""
import pytest
import uuid
from sqlalchemy.orm import Session
from decimal import Decimal
from datetime import datetime, timedelta, date
import json

from app.services.tournament import stats_service
from app.models import (
    TournamentStats,
    TournamentRanking,
    Semester,
    Session as SessionModel,
    Attendance,
    SemesterEnrollment,
    TournamentTeamEnrollment,
    User,
    Team,
    TeamMember,
    Booking
)
from app.models.attendance import AttendanceStatus
from app.models.user import UserRole
from app.models.specialization import SpecializationType
from app.models.semester import SemesterStatus
from app.models.session import SessionType, EventCategory
from app.models.tournament_enums import TeamMemberRole
from app.models.license import UserLicense


def create_test_user(db: Session, email: str, name: str, role: UserRole = UserRole.STUDENT) -> User:
    """Helper function to create a test user with unique email"""
    # Add UUID suffix to prevent duplicate key violations
    unique_email = f"{email.split('@')[0]}+{uuid.uuid4().hex[:8]}@{email.split('@')[1]}"
    user = User(
        email=unique_email,
        name=name,
        password_hash="test_hash_123",
        role=role
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_test_tournament(db: Session, code: str, name: str, enrollment_cost: int = 10) -> Semester:
    """Helper function to create a test tournament with unique code"""
    # Add UUID suffix to prevent duplicate key violations (max 20 chars for code)
    uuid_suffix = uuid.uuid4().hex[:4]
    max_prefix = 20 - 1 - len(uuid_suffix)  # Max 20 chars DB constraint
    unique_code = f"{code[:max_prefix]}-{uuid_suffix}"

    tournament_date = date.today() + timedelta(days=7)
    tournament = Semester(
        code=unique_code,
        name=name,
        start_date=tournament_date,
        end_date=tournament_date,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        specialization_type=SpecializationType.LFA_PLAYER_YOUTH.value,
        age_group="YOUTH",
        enrollment_cost=enrollment_cost
    )
    db.add(tournament)
    db.commit()
    db.refresh(tournament)
    return tournament


def create_test_user_license(db: Session, user_id: int, specialization_type: str = "LFA_PLAYER") -> UserLicense:
    """Helper function to create a test user license"""
    license = UserLicense(
        user_id=user_id,
        specialization_type=specialization_type,
        credit_balance=1000,  # Give enough credits for testing
        onboarding_completed=True,
        started_at=datetime.now()  # Required field
    )
    db.add(license)
    db.commit()
    db.refresh(license)
    return license


def create_test_team(db: Session, name: str, captain_id: int) -> Team:
    """Helper function to create a test team with unique code"""
    # Add UUID suffix to prevent duplicate key violations (max 20 chars for code)
    uuid_suffix = uuid.uuid4().hex[:4]
    max_prefix = 20 - 1 - len(uuid_suffix)  # Max 20 chars DB constraint
    code_prefix = f"TEAM-{name.upper().replace(' ', '-')[:10]}"
    unique_code = f"{code_prefix[:max_prefix]}-{uuid_suffix}"

    team = Team(
        name=name,
        captain_user_id=captain_id,
        code=unique_code,
        specialization_type="LFA_PLAYER",
        is_active=True
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Add captain as team member
    member = TeamMember(
        team_id=team.id,
        user_id=captain_id,
        role=TeamMemberRole.CAPTAIN.value,
        is_active=True
    )
    db.add(member)
    db.commit()

    return team


class TestGetOrCreateStats:
    """Test get_or_create_stats() function"""

    def test_create_new_stats(self, test_db: Session):
        """Happy path: Create new stats for tournament"""
        tournament = create_test_tournament(test_db, "TOURN-001", "Test Tournament")

        stats = stats_service.get_or_create_stats(test_db, tournament.id)

        assert stats is not None
        assert stats.tournament_id == tournament.id
        assert stats.total_participants == 0
        assert stats.total_teams == 0
        assert stats.total_matches == 0
        assert stats.completed_matches == 0
        assert stats.total_revenue == 0
        assert stats.avg_attendance_rate == Decimal('0')

    def test_get_existing_stats(self, test_db: Session):
        """Happy path: Get existing stats"""
        tournament = create_test_tournament(test_db, "TOURN-002", "Test Tournament 2")

        # Create stats first time
        stats1 = stats_service.get_or_create_stats(test_db, tournament.id)
        stats1.total_participants = 10
        test_db.commit()

        # Get stats second time (should return same record)
        stats2 = stats_service.get_or_create_stats(test_db, tournament.id)

        assert stats2.id == stats1.id
        assert stats2.total_participants == 10

    def test_stats_idempotent(self, test_db: Session):
        """Edge case: Multiple calls don't create duplicates"""
        tournament = create_test_tournament(test_db, "TOURN-003", "Test Tournament 3")

        # Call multiple times
        stats1 = stats_service.get_or_create_stats(test_db, tournament.id)
        stats2 = stats_service.get_or_create_stats(test_db, tournament.id)
        stats3 = stats_service.get_or_create_stats(test_db, tournament.id)

        # All should be the same record
        assert stats1.id == stats2.id == stats3.id

        # Verify only one stats record exists
        all_stats = test_db.query(TournamentStats).filter(
            TournamentStats.tournament_id == tournament.id
        ).all()
        assert len(all_stats) == 1


class TestUpdateTournamentStats:
    """Test update_tournament_stats() function"""

    def test_update_stats_with_participants(self, test_db: Session):
        """Happy path: Update stats with individual participants"""
        tournament = create_test_tournament(test_db, "TOURN-004", "Test Tournament 4", enrollment_cost=10)

        # Add 3 individual participants
        for i in range(3):
            user = create_test_user(test_db, f"student{i}@test.com", f"Student {i}")
            license = create_test_user_license(test_db, user.id)
            enrollment = SemesterEnrollment(
                semester_id=tournament.id,
                user_id=user.id,
                user_license_id=license.id,
                is_active=True
            )
            test_db.add(enrollment)
        test_db.commit()

        stats = stats_service.update_tournament_stats(test_db, tournament.id)

        assert stats.total_participants == 3
        assert stats.total_teams == 0
        assert stats.total_revenue == 30  # 3 participants * 10 credits

    def test_update_stats_with_teams(self, test_db: Session):
        """Happy path: Update stats with team enrollments"""
        tournament = create_test_tournament(test_db, "TOURN-005", "Test Tournament 5", enrollment_cost=20)

        # Create 2 teams
        captain1 = create_test_user(test_db, "captain1@test.com", "Captain 1")
        captain2 = create_test_user(test_db, "captain2@test.com", "Captain 2")

        team1 = create_test_team(test_db, "Team Alpha", captain1.id)
        team2 = create_test_team(test_db, "Team Beta", captain2.id)

        # Enroll teams
        enrollment1 = TournamentTeamEnrollment(
            semester_id=tournament.id,
            team_id=team1.id,
            is_active=True
        )
        enrollment2 = TournamentTeamEnrollment(
            semester_id=tournament.id,
            team_id=team2.id,
            is_active=True
        )
        test_db.add(enrollment1)
        test_db.add(enrollment2)
        test_db.commit()

        stats = stats_service.update_tournament_stats(test_db, tournament.id)

        assert stats.total_participants == 0  # No individual enrollments
        assert stats.total_teams == 2
        assert stats.total_revenue == 40  # 2 teams * 20 credits

    def test_update_stats_with_matches(self, test_db: Session):
        """Happy path: Update stats with tournament matches"""
        tournament = create_test_tournament(test_db, "TOURN-006", "Test Tournament 6")

        # Create 5 tournament games (3 completed, 2 pending)
        for i in range(5):
            session = SessionModel(
                title=f"Match {i+1}",
                description="Tournament match",
                date_start=datetime.now() + timedelta(days=1, hours=i),
                date_end=datetime.now() + timedelta(days=1, hours=i+2),
                session_type=SessionType.on_site,
                capacity=20,
                semester_id=tournament.id,
                event_category=EventCategory.MATCH,
                game_results=json.dumps({"winner": "Team A"}) if i < 3 else None  # First 3 have results
            )
            test_db.add(session)
        test_db.commit()

        stats = stats_service.update_tournament_stats(test_db, tournament.id)

        assert stats.total_matches == 5
        assert stats.completed_matches == 3

    def test_update_stats_with_attendance(self, test_db: Session):
        """Happy path: Calculate attendance rate correctly"""
        tournament = create_test_tournament(test_db, "TOURN-007", "Test Tournament 7")

        # Create session
        session = SessionModel(
            title="Match 1",
            description="Tournament match",
            date_start=datetime.now() + timedelta(days=1),
            date_end=datetime.now() + timedelta(days=1, hours=2),
            session_type=SessionType.on_site,
            capacity=20,
            semester_id=tournament.id,
            event_category=EventCategory.MATCH
        )
        test_db.add(session)
        test_db.commit()

        # Add 10 attendance records (7 present, 3 absent)
        for i in range(10):
            user = create_test_user(test_db, f"att{i}@test.com", f"Attendee {i}")
            # Create booking first (required for attendance)
            booking = Booking(
                user_id=user.id,
                session_id=session.id
            )
            test_db.add(booking)
            test_db.flush()  # Ensure booking has an ID

            attendance = Attendance(
                user_id=user.id,
                session_id=session.id,
                booking_id=booking.id,
                status=AttendanceStatus.present if i < 7 else AttendanceStatus.absent
            )
            test_db.add(attendance)
        test_db.commit()

        stats = stats_service.update_tournament_stats(test_db, tournament.id)

        # 7 present out of 10 = 70%
        assert stats.avg_attendance_rate == Decimal('70.00')

    def test_update_stats_comprehensive(self, test_db: Session):
        """Comprehensive: All stats combined"""
        tournament = create_test_tournament(test_db, "TOURN-008", "Comprehensive Tournament", enrollment_cost=15)

        # Add 2 individual participants
        for i in range(2):
            user = create_test_user(test_db, f"indiv{i}@test.com", f"Individual {i}")
            license = create_test_user_license(test_db, user.id)
            enrollment = SemesterEnrollment(
                semester_id=tournament.id,
                user_id=user.id,
                user_license_id=license.id,
                is_active=True
            )
            test_db.add(enrollment)

        # Add 1 team
        captain = create_test_user(test_db, "team_captain@test.com", "Team Captain")
        team = create_test_team(test_db, "Test Team", captain.id)
        team_enrollment = TournamentTeamEnrollment(
            semester_id=tournament.id,
            team_id=team.id,
            is_active=True
        )
        test_db.add(team_enrollment)

        # Add 3 matches (2 completed)
        for i in range(3):
            session = SessionModel(
                title=f"Match {i+1}",
                description="Match",
                date_start=datetime.now() + timedelta(days=1, hours=i),
                date_end=datetime.now() + timedelta(days=1, hours=i+2),
                session_type=SessionType.on_site,
                capacity=20,
                semester_id=tournament.id,
                event_category=EventCategory.MATCH,
                game_results=json.dumps({"winner": "A"}) if i < 2 else None
            )
            test_db.add(session)

        test_db.commit()

        stats = stats_service.update_tournament_stats(test_db, tournament.id)

        assert stats.total_participants == 2
        assert stats.total_teams == 1
        assert stats.total_revenue == 45  # (2 * 15) + (1 * 15)
        assert stats.total_matches == 3
        assert stats.completed_matches == 2

    def test_update_stats_zero_attendance(self, test_db: Session):
        """Edge case: No attendance records"""
        tournament = create_test_tournament(test_db, "TOURN-009", "Empty Tournament")

        stats = stats_service.update_tournament_stats(test_db, tournament.id)

        assert stats.avg_attendance_rate == Decimal('0')

    def test_update_stats_inactive_excluded(self, test_db: Session):
        """Edge case: Inactive enrollments excluded"""
        tournament = create_test_tournament(test_db, "TOURN-010", "Test Tournament 10")

        # Add 2 active and 1 inactive enrollment
        for i in range(3):
            user = create_test_user(test_db, f"enroll{i}@test.com", f"User {i}")
            license = create_test_user_license(test_db, user.id)
            enrollment = SemesterEnrollment(
                semester_id=tournament.id,
                user_id=user.id,
                user_license_id=license.id,
                is_active=(i < 2)  # First 2 are active
            )
            test_db.add(enrollment)
        test_db.commit()

        stats = stats_service.update_tournament_stats(test_db, tournament.id)

        assert stats.total_participants == 2  # Only active ones

    def test_update_stats_nonexistent_tournament(self, test_db: Session):
        """Edge case: Tournament doesn't exist — returns None without touching DB."""
        result = stats_service.update_tournament_stats(test_db, 99999)

        assert result is None


class TestGetTournamentAnalytics:
    """Test get_tournament_analytics() function"""

    def test_get_analytics_basic(self, test_db: Session):
        """Happy path: Get analytics with basic data"""
        tournament = create_test_tournament(test_db, "TOURN-011", "Analytics Test")

        analytics = stats_service.get_tournament_analytics(test_db, tournament.id)

        assert analytics is not None
        assert analytics['tournament_id'] == tournament.id
        assert 'stats' in analytics
        assert 'top_10_rankings' in analytics
        assert analytics['stats']['total_participants'] == 0
        assert analytics['stats']['completion_rate'] == 0.0

    def test_get_analytics_with_rankings(self, test_db: Session):
        """Happy path: Analytics include top rankings"""
        tournament = create_test_tournament(test_db, "TOURN-012", "Rankings Test")

        # Create 5 rankings
        for i in range(5):
            user = create_test_user(test_db, f"rank{i}@test.com", f"Ranker {i}")
            ranking = TournamentRanking(
                tournament_id=tournament.id,
                user_id=user.id,
                participant_type="INDIVIDUAL",
                rank=i+1,
                points=Decimal(100 - (i * 10)),
                wins=10 - i,
                losses=i,
                draws=0
            )
            test_db.add(ranking)
        test_db.commit()

        analytics = stats_service.get_tournament_analytics(test_db, tournament.id)

        assert len(analytics['top_10_rankings']) == 5
        assert analytics['top_10_rankings'][0]['rank'] == 1
        assert analytics['top_10_rankings'][0]['points'] == 100.0

    def test_get_analytics_completion_rate(self, test_db: Session):
        """Happy path: Completion rate calculated correctly"""
        tournament = create_test_tournament(test_db, "TOURN-013", "Completion Test")

        # Create 10 matches, 6 completed
        for i in range(10):
            session = SessionModel(
                title=f"Match {i+1}",
                description="Match",
                date_start=datetime.now() + timedelta(days=1, hours=i),
                date_end=datetime.now() + timedelta(days=1, hours=i+2),
                session_type=SessionType.on_site,
                capacity=20,
                semester_id=tournament.id,
                event_category=EventCategory.MATCH,
                game_results=json.dumps({"winner": "A"}) if i < 6 else None
            )
            test_db.add(session)
        test_db.commit()

        analytics = stats_service.get_tournament_analytics(test_db, tournament.id)

        assert analytics['stats']['total_matches'] == 10
        assert analytics['stats']['completed_matches'] == 6
        assert analytics['stats']['completion_rate'] == 60.0  # 6/10 * 100

    def test_get_analytics_limit_rankings(self, test_db: Session):
        """Edge case: Only top 10 rankings returned"""
        tournament = create_test_tournament(test_db, "TOURN-014", "Top 10 Test")

        # Create 15 rankings
        for i in range(15):
            user = create_test_user(test_db, f"top{i}@test.com", f"Player {i}")
            ranking = TournamentRanking(
                tournament_id=tournament.id,
                user_id=user.id,
                participant_type="INDIVIDUAL",
                rank=i+1,
                points=Decimal(150 - (i * 5)),
                wins=15 - i,
                losses=i,
                draws=0
            )
            test_db.add(ranking)
        test_db.commit()

        analytics = stats_service.get_tournament_analytics(test_db, tournament.id)

        # Should only return top 10
        assert len(analytics['top_10_rankings']) == 10
        assert analytics['top_10_rankings'][0]['rank'] == 1
        assert analytics['top_10_rankings'][9]['rank'] == 10

    def test_get_analytics_mixed_participants(self, test_db: Session):
        """Analytics with both individual and team participants"""
        tournament = create_test_tournament(test_db, "TOURN-015", "Mixed Test", enrollment_cost=10)

        # Add individual participant
        user1 = create_test_user(test_db, "individual@test.com", "Individual Player")
        license1 = create_test_user_license(test_db, user1.id)
        enrollment1 = SemesterEnrollment(
            semester_id=tournament.id,
            user_id=user1.id,
            user_license_id=license1.id,
            is_active=True
        )
        test_db.add(enrollment1)

        # Add team
        captain = create_test_user(test_db, "captain@test.com", "Captain")
        team = create_test_team(test_db, "Team X", captain.id)
        enrollment2 = TournamentTeamEnrollment(
            semester_id=tournament.id,
            team_id=team.id,
            is_active=True
        )
        test_db.add(enrollment2)

        # Add rankings for both
        ranking1 = TournamentRanking(
            tournament_id=tournament.id,
            user_id=user1.id,
            participant_type="INDIVIDUAL",
            rank=1,
            points=Decimal(100),
            wins=5,
            losses=0,
            draws=0
        )
        ranking2 = TournamentRanking(
            tournament_id=tournament.id,
            team_id=team.id,
            participant_type="TEAM",
            rank=2,
            points=Decimal(80),
            wins=4,
            losses=1,
            draws=0
        )
        test_db.add(ranking1)
        test_db.add(ranking2)
        test_db.commit()

        analytics = stats_service.get_tournament_analytics(test_db, tournament.id)

        assert analytics['stats']['total_participants'] == 1
        assert analytics['stats']['total_teams'] == 1
        assert analytics['stats']['total_revenue'] == 20  # 1 individual + 1 team * 10
        assert len(analytics['top_10_rankings']) == 2


class TestGetParticipantStats:
    """Test get_participant_stats() function"""

    def test_get_participant_stats_individual(self, test_db: Session):
        """Happy path: Get stats for individual participant"""
        tournament = create_test_tournament(test_db, "TOURN-016", "Individual Stats Test")
        user = create_test_user(test_db, "player@test.com", "Player")

        # Create ranking
        ranking = TournamentRanking(
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type="INDIVIDUAL",
            rank=1,
            points=Decimal(150),
            wins=10,
            losses=2,
            draws=3
        )
        test_db.add(ranking)
        test_db.commit()

        stats = stats_service.get_participant_stats(test_db, tournament.id, user_id=user.id)

        assert stats is not None
        assert stats['rank'] == 1
        assert stats['points'] == 150.0
        assert stats['matches_played'] == 15  # 10 + 2 + 3
        assert stats['wins'] == 10
        assert stats['losses'] == 2
        assert stats['draws'] == 3
        # Win rate = 10/15 * 100 = 66.67%
        assert stats['win_rate'] == 66.67

    def test_get_participant_stats_team(self, test_db: Session):
        """Happy path: Get stats for team participant"""
        tournament = create_test_tournament(test_db, "TOURN-017", "Team Stats Test")
        captain = create_test_user(test_db, "team_cap@test.com", "Team Captain")
        team = create_test_team(test_db, "Test Team", captain.id)

        # Create ranking
        ranking = TournamentRanking(
            tournament_id=tournament.id,
            team_id=team.id,
            participant_type="TEAM",
            rank=3,
            points=Decimal(95.5),
            wins=7,
            losses=5,
            draws=2
        )
        test_db.add(ranking)
        test_db.commit()

        stats = stats_service.get_participant_stats(test_db, tournament.id, team_id=team.id)

        assert stats is not None
        assert stats['rank'] == 3
        assert stats['points'] == 95.5
        assert stats['matches_played'] == 14  # 7 + 5 + 2
        assert stats['wins'] == 7
        # Win rate = 7/14 * 100 = 50.00%
        assert stats['win_rate'] == 50.0

    def test_get_participant_stats_perfect_record(self, test_db: Session):
        """Edge case: Perfect win rate (100%)"""
        tournament = create_test_tournament(test_db, "TOURN-018", "Perfect Test")
        user = create_test_user(test_db, "perfect@test.com", "Perfect Player")

        ranking = TournamentRanking(
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type="INDIVIDUAL",
            rank=1,
            points=Decimal(200),
            wins=10,
            losses=0,
            draws=0
        )
        test_db.add(ranking)
        test_db.commit()

        stats = stats_service.get_participant_stats(test_db, tournament.id, user_id=user.id)

        assert stats['win_rate'] == 100.0

    def test_get_participant_stats_no_wins(self, test_db: Session):
        """Edge case: No wins (0% win rate)"""
        tournament = create_test_tournament(test_db, "TOURN-019", "No Wins Test")
        user = create_test_user(test_db, "loser@test.com", "Unlucky Player")

        ranking = TournamentRanking(
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type="INDIVIDUAL",
            rank=10,
            points=Decimal(10),
            wins=0,
            losses=8,
            draws=2
        )
        test_db.add(ranking)
        test_db.commit()

        stats = stats_service.get_participant_stats(test_db, tournament.id, user_id=user.id)

        assert stats['win_rate'] == 0.0
        assert stats['matches_played'] == 10

    def test_get_participant_stats_no_matches(self, test_db: Session):
        """Edge case: No matches played (0% win rate)"""
        tournament = create_test_tournament(test_db, "TOURN-020", "No Matches Test")
        user = create_test_user(test_db, "noplay@test.com", "No Play Player")

        ranking = TournamentRanking(
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type="INDIVIDUAL",
            rank=None,  # Unranked
            points=Decimal(0),
            wins=0,
            losses=0,
            draws=0
        )
        test_db.add(ranking)
        test_db.commit()

        stats = stats_service.get_participant_stats(test_db, tournament.id, user_id=user.id)

        assert stats['matches_played'] == 0
        assert stats['win_rate'] == 0.0

    def test_get_participant_stats_not_found(self, test_db: Session):
        """Edge case: Participant not found"""
        tournament = create_test_tournament(test_db, "TOURN-021", "Not Found Test")

        stats = stats_service.get_participant_stats(test_db, tournament.id, user_id=99999)

        assert stats is None

    def test_get_participant_stats_no_identifier(self, test_db: Session):
        """Edge case: No user_id or team_id provided"""
        tournament = create_test_tournament(test_db, "TOURN-022", "No ID Test")

        stats = stats_service.get_participant_stats(test_db, tournament.id)

        assert stats is None

    def test_get_participant_stats_win_rate_precision(self, test_db: Session):
        """Edge case: Win rate precision (2 decimal places)"""
        tournament = create_test_tournament(test_db, "TOURN-023", "Precision Test")
        user = create_test_user(test_db, "precise@test.com", "Precise Player")

        # 5 wins out of 7 matches = 71.428571...%
        ranking = TournamentRanking(
            tournament_id=tournament.id,
            user_id=user.id,
            participant_type="INDIVIDUAL",
            rank=2,
            points=Decimal(120),
            wins=5,
            losses=2,
            draws=0
        )
        test_db.add(ranking)
        test_db.commit()

        stats = stats_service.get_participant_stats(test_db, tournament.id, user_id=user.id)

        # Should be rounded to 2 decimal places
        assert stats['win_rate'] == 71.43

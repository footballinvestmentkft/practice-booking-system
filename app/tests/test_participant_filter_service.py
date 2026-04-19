"""
Unit Tests for ParticipantFilterService

Tests the Unified Multi-Player Ranking System's participant filtering logic:
- ALL_PARTICIPANTS: All enrolled players (League)
- GROUP_ISOLATED: Only group members (Group Stage)
- QUALIFIED_ONLY: Top performers from previous rounds (Knockout)
- PERFORMANCE_POD: Performance-based pods (Swiss System)
- TIERED: All players with tier-based point distribution (Knockout)
"""
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.services.tournament.participant_filter_service import ParticipantFilterService
from app.models.user import User, UserRole
from app.models.session import Session as SessionModel, EventCategory
from app.models.semester import Semester
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_ranking import TournamentRanking


class TestParticipantFilterService:
    """Test suite for ParticipantFilterService"""

    @pytest.fixture
    def filter_service(self, db_session: Session):
        """Create ParticipantFilterService instance with test database"""
        return ParticipantFilterService(db_session)

    @pytest.fixture
    def test_instructor(self, db_session: Session):
        """Create a test instructor user"""
        user = User(
            name="Test Instructor",
            email="instructor@example.com",
            password_hash="test_hash",
            role=UserRole.INSTRUCTOR,
            is_active=True
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    @pytest.fixture
    def test_tournament(self, db_session: Session, test_instructor):
        """Create a test tournament (semester)"""
        tournament = Semester(
            code="TOUR/2026/01",
            name="Test Tournament",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=test_instructor.id
        )
        db_session.add(tournament)
        db_session.commit()
        db_session.refresh(tournament)
        return tournament

    @pytest.fixture
    def enrolled_players(self, db_session: Session, test_tournament):
        """Create 8 enrolled players for tournament"""
        from app.models.license import UserLicense

        players = []
        for i in range(1, 9):
            user = User(
                name=f"Player {i}",
                email=f"player{i}@example.com",
                password_hash="test_hash",
                role=UserRole.STUDENT,
                is_active=True
            )
            db_session.add(user)
            db_session.flush()

            # Create user license (required for enrollment)
            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                credit_balance=100,
                credit_purchased=100
            )
            db_session.add(license)
            db_session.flush()

            # Create enrollment
            enrollment = SemesterEnrollment(
                user_id=user.id,
                semester_id=test_tournament.id,
                user_license_id=license.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED
            )
            db_session.add(enrollment)
            players.append(user)

        db_session.commit()
        return players

    # ========================================================================
    # TEST 1: ALL_PARTICIPANTS (League sessions)
    # ========================================================================

    def test_all_participants_mode(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """
        Test ALL_PARTICIPANTS mode - should return all enrolled players.
        Used in League tournaments where everyone competes together.
        """
        # Create League session
        session = SessionModel(
            title="League Round 1",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=7),
            date_end=datetime.now(timezone.utc) + timedelta(days=7, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='ALL_PARTICIPANTS',
            expected_participants=8,
            participant_filter=None,
            group_identifier=None,
            pod_tier=None
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        # Get participants
        participant_ids = filter_service.get_session_participants(session.id)

        # Assert all 8 players are included
        assert len(participant_ids) == 8
        player_ids = {p.id for p in enrolled_players}
        assert set(participant_ids) == player_ids

    # ========================================================================
    # TEST 2: GROUP_ISOLATED (Group Stage sessions)
    # ========================================================================

    def test_group_isolated_mode_group_a(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """
        Test GROUP_ISOLATED mode - should return only Group A members.
        With 8 players and 2 groups, Group A should have players 1, 3, 5, 7 (round-robin assignment).
        """
        # Create Group A session
        session = SessionModel(
            title="Group A - Round 1",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=7),
            date_end=datetime.now(timezone.utc) + timedelta(days=7, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='GROUP_ISOLATED',
            expected_participants=4,
            participant_filter='group_membership',
            group_identifier='A',
            pod_tier=None
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        # Get participants
        participant_ids = filter_service.get_session_participants(session.id)

        # Assert only 4 players in Group A
        assert len(participant_ids) == 4

        # Group A should have players at indices 0, 2, 4, 6 (sorted by user_id)
        # Round-robin: idx % 2 == 0 → Group A
        sorted_players = sorted(enrolled_players, key=lambda p: p.id)
        expected_group_a = [sorted_players[0].id, sorted_players[2].id, sorted_players[4].id, sorted_players[6].id]
        assert set(participant_ids) == set(expected_group_a)

    def test_group_isolated_mode_group_b(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """
        Test GROUP_ISOLATED mode - should return only Group B members.
        With 8 players and 2 groups, Group B should have players 2, 4, 6, 8.
        """
        # Create Group B session
        session = SessionModel(
            title="Group B - Round 1",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=7),
            date_end=datetime.now(timezone.utc) + timedelta(days=7, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='GROUP_ISOLATED',
            expected_participants=4,
            participant_filter='group_membership',
            group_identifier='B',
            pod_tier=None
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        # Get participants
        participant_ids = filter_service.get_session_participants(session.id)

        # Assert only 4 players in Group B
        assert len(participant_ids) == 4

        # Group B should have players at indices 1, 3, 5, 7
        sorted_players = sorted(enrolled_players, key=lambda p: p.id)
        expected_group_b = [sorted_players[1].id, sorted_players[3].id, sorted_players[5].id, sorted_players[7].id]
        assert set(participant_ids) == set(expected_group_b)

    # ========================================================================
    # TEST 3: QUALIFIED_ONLY (Knockout Stage sessions)
    # ========================================================================

    def test_qualified_only_mode(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """
        Test QUALIFIED_ONLY mode - should return only top-ranked players.
        Simulates knockout stage where top 4 qualifiers advance.
        """
        # Create rankings for all players
        for i, player in enumerate(enrolled_players):
            ranking = TournamentRanking(
                tournament_id=test_tournament.id,
                user_id=player.id,
                participant_type='USER',
                points=10 - i,  # Player 1 has 10 points, Player 8 has 3 points
                rank=i + 1
            )
            db_session.add(ranking)

        db_session.commit()

        # Create Knockout session (top 4 qualifiers)
        session = SessionModel(
            title="Knockout Semifinals",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=10),
            date_end=datetime.now(timezone.utc) + timedelta(days=10, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='QUALIFIED_ONLY',
            expected_participants=4,
            participant_filter='top_group_qualifiers',
            group_identifier=None,
            pod_tier=None
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        # Get participants
        participant_ids = filter_service.get_session_participants(session.id)

        # Assert only top 4 players qualify
        assert len(participant_ids) == 4

        # Top 4 should be Players 1, 2, 3, 4
        expected_qualified = [enrolled_players[0].id, enrolled_players[1].id,
                             enrolled_players[2].id, enrolled_players[3].id]
        assert set(participant_ids) == set(expected_qualified)

    # ========================================================================
    # TEST 4: PERFORMANCE_POD (Swiss System sessions)
    # ========================================================================

    def test_performance_pod_mode_top_pod(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """
        Test PERFORMANCE_POD mode - should return players in top performance pod.
        Simulates Swiss System where players are grouped by performance after Round 1.
        """
        # Create rankings (simulate after Round 1)
        for i, player in enumerate(enrolled_players):
            ranking = TournamentRanking(
                tournament_id=test_tournament.id,
                user_id=player.id,
                participant_type='USER',
                points=10 - i,  # Player 1=10pts, Player 2=9pts, etc.
                rank=i + 1
            )
            db_session.add(ranking)

        db_session.commit()

        # Create Swiss Round 2 - Top Pod (players 1-4)
        session = SessionModel(
            title="Swiss Round 2 - Pod 1",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=8),
            date_end=datetime.now(timezone.utc) + timedelta(days=8, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='PERFORMANCE_POD',
            expected_participants=4,
            participant_filter='dynamic_swiss_pairing',
            group_identifier=None,
            pod_tier=1  # Top pod
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        # Get participants
        participant_ids = filter_service.get_session_participants(session.id)

        # Assert only top 4 performers in Pod 1
        assert len(participant_ids) == 4
        expected_pod_1 = [enrolled_players[0].id, enrolled_players[1].id,
                         enrolled_players[2].id, enrolled_players[3].id]
        assert set(participant_ids) == set(expected_pod_1)

    def test_performance_pod_mode_bottom_pod(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """
        Test PERFORMANCE_POD mode - should return players in bottom performance pod.
        """
        # Create rankings
        for i, player in enumerate(enrolled_players):
            ranking = TournamentRanking(
                tournament_id=test_tournament.id,
                user_id=player.id,
                participant_type='USER',
                points=10 - i,
                rank=i + 1
            )
            db_session.add(ranking)

        db_session.commit()

        # Create Swiss Round 2 - Bottom Pod (players 5-8)
        session = SessionModel(
            title="Swiss Round 2 - Pod 2",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=8),
            date_end=datetime.now(timezone.utc) + timedelta(days=8, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='PERFORMANCE_POD',
            expected_participants=4,
            participant_filter='dynamic_swiss_pairing',
            group_identifier=None,
            pod_tier=2  # Bottom pod
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        # Get participants
        participant_ids = filter_service.get_session_participants(session.id)

        # Assert only bottom 4 performers in Pod 2
        assert len(participant_ids) == 4
        expected_pod_2 = [enrolled_players[4].id, enrolled_players[5].id,
                         enrolled_players[6].id, enrolled_players[7].id]
        assert set(participant_ids) == set(expected_pod_2)

    # ========================================================================
    # TEST 5: TIERED (Knockout with all players)
    # ========================================================================

    def test_tiered_mode(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """
        Test TIERED mode - should return all players.
        Used in Knockout where all players compete but tier affects point distribution.
        """
        # Create Knockout session with TIERED mode
        session = SessionModel(
            title="Knockout Finals",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=12),
            date_end=datetime.now(timezone.utc) + timedelta(days=12, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='TIERED',
            expected_participants=8,
            participant_filter=None,
            group_identifier=None,
            pod_tier=3  # Finals tier
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        # Get participants
        participant_ids = filter_service.get_session_participants(session.id)

        # Assert all 8 players participate
        assert len(participant_ids) == 8
        player_ids = {p.id for p in enrolled_players}
        assert set(participant_ids) == player_ids

    # ========================================================================
    # TEST 6: Edge Cases
    # ========================================================================

    def test_nonexistent_session(self, db_session: Session, filter_service):
        """Test filtering with nonexistent session ID"""
        participant_ids = filter_service.get_session_participants(99999)
        assert participant_ids == []

    def test_session_with_no_enrollments(self, db_session: Session, filter_service, test_instructor):
        """Test filtering for tournament with no enrolled players"""
        # Create empty tournament
        empty_tournament = Semester(
            code="EMPTY/2026",
            name="Empty Tournament",
            start_date=(datetime.now(timezone.utc) + timedelta(days=30)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=37)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=test_instructor.id
        )
        db_session.add(empty_tournament)
        db_session.commit()

        # Create session
        session = SessionModel(
            title="Empty Session",
            semester_id=empty_tournament.id,
            instructor_id=test_instructor.id,
            date_start=datetime.now(timezone.utc) + timedelta(days=30),
            date_end=datetime.now(timezone.utc) + timedelta(days=30, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='ALL_PARTICIPANTS'
        )
        db_session.add(session)
        db_session.commit()

        participant_ids = filter_service.get_session_participants(session.id)
        assert participant_ids == []

    def test_get_group_assignment(self, db_session: Session, filter_service, test_tournament, enrolled_players):
        """Test get_group_assignment method"""
        # Create Group A session to define group configuration
        session = SessionModel(
            title="Group A - Round 1",
            semester_id=test_tournament.id,
            instructor_id=test_tournament.master_instructor_id,
            date_start=datetime.now(timezone.utc) + timedelta(days=7),
            date_end=datetime.now(timezone.utc) + timedelta(days=7, hours=2),
            event_category=EventCategory.MATCH,
            ranking_mode='GROUP_ISOLATED',
            expected_participants=4,
            group_identifier='A'
        )
        db_session.add(session)
        db_session.commit()

        # Get group assignments
        sorted_players = sorted(enrolled_players, key=lambda p: p.id)

        # Players at even indices (0, 2, 4, 6) should be in Group A
        group_a = filter_service.get_group_assignment(test_tournament.id, sorted_players[0].id)
        assert group_a == 'A'

        # Players at odd indices (1, 3, 5, 7) should be in Group B
        group_b = filter_service.get_group_assignment(test_tournament.id, sorted_players[1].id)
        assert group_b == 'B'

    def test_get_group_assignment_nonexistent_user(self, db_session: Session, filter_service, test_tournament):
        """Test get_group_assignment with user not enrolled"""
        group = filter_service.get_group_assignment(test_tournament.id, 99999)
        assert group is None

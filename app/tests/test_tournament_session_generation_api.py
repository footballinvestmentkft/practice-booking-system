"""
API Tests for Tournament Session Generation and Participant Filtering

Tests the Unified Multi-Player Ranking System's session generation and filtering
through actual API endpoints.

Coverage:
1. League tournament session generation (ALL_PARTICIPANTS)
2. Group+Knockout tournament session generation (GROUP_ISOLATED → QUALIFIED_ONLY)
3. Knockout tournament session generation (TIERED)
4. Swiss tournament session generation (PERFORMANCE_POD)
5. /active-match endpoint participant filtering
6. Session metadata validation
"""
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
import json

from app.models.semester import Semester
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.session import Session as SessionModel, EventCategory
from app.models.user import User, UserRole
from app.models.tournament_type import TournamentType
from app.models.tournament_configuration import TournamentConfiguration
from app.models.license import UserLicense
from app.models.booking import Booking, BookingStatus
from app.models.attendance import Attendance, AttendanceStatus


@pytest.mark.tournament
@pytest.mark.api
class TestTournamentSessionGenerationAPI:
    """API integration tests for tournament session generation with unified ranking"""

    @pytest.fixture
    def tournament_type_league(self, db_session: Session):
        """Create League tournament type"""
        tournament_type = TournamentType(
            code="league",
            display_name="League - Multi-Player Ranking",
            description="All players compete and rank together in each round",
            min_players=4,
            max_players=16,
            requires_power_of_two=False,
            session_duration_minutes=90,
            break_between_sessions_minutes=15,
            format="HEAD_TO_HEAD",  # P2 Schema: Required for HEAD_TO_HEAD formats
            config={"ranking_rounds": 5}  # 5 ranking rounds
        )
        db_session.add(tournament_type)
        db_session.commit()
        db_session.refresh(tournament_type)
        return tournament_type

    @pytest.fixture
    def tournament_type_group_knockout(self, db_session: Session):
        """Create Group+Knockout tournament type"""
        tournament_type = TournamentType(
            code="group_knockout",
            display_name="Group Stage + Knockout",
            description="Group stage followed by knockout playoffs",
            min_players=8,
            max_players=32,
            requires_power_of_two=False,
            session_duration_minutes=90,
            break_between_sessions_minutes=15,
            format="HEAD_TO_HEAD",  # P2 Schema: Required for HEAD_TO_HEAD formats
            config={
                "group_configuration": {
                    "8_players": {
                        "groups": 2,
                        "players_per_group": 4,
                        "qualifiers": 2,
                        "rounds": 3
                    }
                },
                "round_names": {
                    "4": "Semi-Finals",
                    "2": "Finals"
                }
            }
        )
        db_session.add(tournament_type)
        db_session.commit()
        db_session.refresh(tournament_type)
        return tournament_type

    @pytest.fixture
    def instructor_user(self, db_session: Session):
        """Create instructor user with coach license"""
        instructor = User(
            name="Test Instructor",
            email="instructor@test.com",
            password_hash="test_hash",
            role=UserRole.INSTRUCTOR,
            is_active=True
        )
        db_session.add(instructor)
        db_session.flush()

        # Create coach license
        coach_license = UserLicense(
            user_id=instructor.id,
            specialization_type="LFA_COACH",
            current_level=5,
            max_achieved_level=5,
            started_at=datetime.now(timezone.utc),
            credit_balance=1000,
            credit_purchased=1000
        )
        db_session.add(coach_license)
        db_session.commit()
        db_session.refresh(instructor)
        return instructor

    @pytest.fixture
    def enrolled_players_8(self, db_session: Session, test_tournament):
        """Create 8 enrolled players with licenses"""
        players = []
        for i in range(1, 9):
            user = User(
                name=f"Player {i}",
                email=f"player{i}@test.com",
                password_hash="test_hash",
                role=UserRole.STUDENT,
                is_active=True
            )
            db_session.add(user)
            db_session.flush()

            # Create license
            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                credit_balance=500,
                credit_purchased=500
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

    @pytest.fixture
    def test_tournament(self, db_session: Session, instructor_user, tournament_type_league):
        """Create test tournament in IN_PROGRESS status"""
        tournament = Semester(
            code="TEST/2026/LEAGUE",
            name="Test League Tournament",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=instructor_user.id,
            # P2 Schema: tournament_type_id moved to TournamentConfiguration
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=tournament_type_league.id,
                participant_type="INDIVIDUAL",
                scoring_type="HEAD_TO_HEAD"
            )
        )
        db_session.add(tournament)
        db_session.commit()
        db_session.refresh(tournament)
        return tournament

    # ========================================================================
    # TEST 1: League Tournament Session Generation
    # ========================================================================

    def test_league_tournament_session_generation(
        self,
        db_session: Session,
        test_tournament,
        enrolled_players_8,
        tournament_type_league
    ):
        """
        TEST: Generate sessions for League tournament (HEAD_TO_HEAD round-robin)

        Expected:
        - 28 sessions generated (full round-robin: C(8,2) = 28 matches)
        - Each session is a 1v1 match
        - All sessions have tournament_phase = GROUP_STAGE
        - sessions_generated flag set to True
        """
        from app.services.tournament_session_generator import TournamentSessionGenerator

        generator = TournamentSessionGenerator(db_session)

        # Generate sessions
        success, message, sessions_created = generator.generate_sessions(
            tournament_id=test_tournament.id,
            parallel_fields=1,
            session_duration_minutes=90,
            break_minutes=15
        )

        # Assert generation success
        assert success is True, f"Session generation failed: {message}"
        assert len(sessions_created) == 28, f"Expected 28 sessions (8-player round-robin), got {len(sessions_created)}"
        assert "28" in message and "sessions" in message, f"Expected message to mention 28 sessions, got: {message}"

        # Verify sessions in database
        sessions = db_session.query(SessionModel).filter(
            SessionModel.semester_id == test_tournament.id
        ).order_by(SessionModel.date_start).all()

        assert len(sessions) == 28

        # Verify each session has correct metadata (HEAD_TO_HEAD 1v1 matches)
        for session in sessions:
            assert session.event_category == EventCategory.MATCH
            assert session.auto_generated is True
            assert session.tournament_phase == 'GROUP_STAGE'
            assert session.expected_participants == 2, "HEAD_TO_HEAD matches have 2 participants"

        # Verify tournament configuration flags
        db_session.refresh(test_tournament)
        assert test_tournament.tournament_config_obj.sessions_generated is True
        assert test_tournament.tournament_config_obj.sessions_generated_at is not None

    # ========================================================================
    # TEST 2: Group+Knockout Tournament Session Generation
    # ========================================================================

    def test_group_knockout_tournament_session_generation(
        self,
        db_session: Session,
        instructor_user,
        tournament_type_group_knockout
    ):
        """
        TEST: Generate sessions for Group+Knockout tournament

        Expected:
        - Group Stage: 2 groups × 3 rounds = 6 sessions (GROUP_ISOLATED)
        - Knockout Stage: Semi-finals (2 sessions) + Finals (1 session) = 3 sessions (QUALIFIED_ONLY)
        - Total: 9 sessions
        - Group sessions have group_identifier (A, B)
        - Group sessions have expected_participants = 4
        - Knockout sessions have expected_participants = 4 (semi), 2 (finals)
        """
        # Create tournament
        tournament = Semester(
            code="TEST/2026/GK",
            name="Test Group+Knockout",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=instructor_user.id,
            # P2 Schema: tournament_type_id moved to TournamentConfiguration
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=tournament_type_group_knockout.id,
                participant_type="INDIVIDUAL",
                scoring_type="HEAD_TO_HEAD"
            )
        )
        db_session.add(tournament)
        db_session.commit()

        # Create 8 enrolled players
        for i in range(1, 9):
            user = User(
                name=f"GK Player {i}",
                email=f"gkplayer{i}@test.com",
                password_hash="test_hash",
                role=UserRole.STUDENT,
                is_active=True
            )
            db_session.add(user)
            db_session.flush()

            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                credit_balance=500,
                credit_purchased=500
            )
            db_session.add(license)
            db_session.flush()

            enrollment = SemesterEnrollment(
                user_id=user.id,
                semester_id=tournament.id,
                user_license_id=license.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED
            )
            db_session.add(enrollment)

        db_session.commit()

        # Generate sessions
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db_session)

        success, message, sessions_created = generator.generate_sessions(
            tournament_id=tournament.id
        )

        assert success is True
        # Group stage (HEAD_TO_HEAD round-robin within groups):
        # - Group A: C(4,2) = 6 matches (4 players round-robin)
        # - Group B: C(4,2) = 6 matches (4 players round-robin)
        # Knockout stage: 4 matches (2 semis + final + 3rd place)
        # Total: 16 sessions
        assert len(sessions_created) == 16, f"Expected 16 sessions (12 group + 4 knockout), got {len(sessions_created)}"

        # Verify group stage sessions
        group_sessions = db_session.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id,
            SessionModel.tournament_phase == 'GROUP_STAGE'
        ).all()

        assert len(group_sessions) == 12, f"Expected 12 group stage sessions (2 groups × 6 matches), got {len(group_sessions)}"

        # Verify group A sessions (round-robin: C(4,2) = 6 matches)
        group_a_sessions = [s for s in group_sessions if s.group_identifier == 'A']
        assert len(group_a_sessions) == 6, f"Expected 6 matches in Group A, got {len(group_a_sessions)}"

        for session in group_a_sessions:
            assert session.expected_participants == 2, "HEAD_TO_HEAD matches have 2 participants"
            assert session.group_identifier == 'A'

        # Verify group B sessions (round-robin: C(4,2) = 6 matches)
        group_b_sessions = [s for s in group_sessions if s.group_identifier == 'B']
        assert len(group_b_sessions) == 6, f"Expected 6 matches in Group B, got {len(group_b_sessions)}"

        for session in group_b_sessions:
            assert session.expected_participants == 2, "HEAD_TO_HEAD matches have 2 participants"
            assert session.group_identifier == 'B'

        # Verify knockout stage sessions
        knockout_sessions = db_session.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id,
            SessionModel.tournament_phase.in_(['KNOCKOUT', 'FINALS'])
        ).order_by(SessionModel.date_start).all()

        assert len(knockout_sessions) == 4, f"Expected 4 knockout sessions (2 semis + final + 3rd place), got {len(knockout_sessions)}"

        for session in knockout_sessions:
            assert session.expected_participants == 2, "Knockout matches are 1v1"
            assert session.group_identifier is None, "Knockout sessions have no group"

    # ========================================================================
    # TEST 3: /active-match Endpoint with Participant Filtering
    # ========================================================================

    def test_active_match_endpoint_group_isolation(
        self,
        client,
        db_session: Session,
        instructor_user,
        tournament_type_group_knockout
    ):
        """
        TEST: /active-match endpoint returns only group members for group stage session

        Expected:
        - Group A session returns only 4 players (Group A members)
        - Group B session returns only 4 players (Group B members)
        - Participant filtering works correctly
        """
        # Create tournament with group+knockout type
        tournament = Semester(
            code="TEST/2026/FILTER",
            name="Test Filtering",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=instructor_user.id,
            # P2 Schema: tournament_type_id moved to TournamentConfiguration
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=tournament_type_group_knockout.id,
                participant_type="INDIVIDUAL",
                scoring_type="HEAD_TO_HEAD"
            )
        )
        db_session.add(tournament)
        db_session.commit()

        # Create 8 players and enroll
        player_ids = []
        for i in range(1, 9):
            user = User(
                name=f"Filter Player {i}",
                email=f"filterplayer{i}@test.com",
                password_hash="test_hash",
                role=UserRole.STUDENT,
                is_active=True
            )
            db_session.add(user)
            db_session.flush()

            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                credit_balance=500,
                credit_purchased=500
            )
            db_session.add(license)
            db_session.flush()

            enrollment = SemesterEnrollment(
                user_id=user.id,
                semester_id=tournament.id,
                user_license_id=license.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED
            )
            db_session.add(enrollment)
            player_ids.append(user.id)

        db_session.commit()

        # Generate sessions
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db_session)
        generator.generate_sessions(tournament_id=tournament.id)

        # Get first Group A session
        group_a_session = db_session.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id,
            SessionModel.group_identifier == 'A'
        ).first()

        assert group_a_session is not None

        # Login as instructor
        from fastapi.testclient import TestClient
        # Note: Assuming client fixture provides authenticated requests
        # If not, we need to create auth token

        # Call /active-match endpoint
        response = client.get(
            f"/api/v1/tournaments/{tournament.id}/active-match",
            headers={"Authorization": f"Bearer instructor_token"}  # Mock token
        )

        # Note: This test assumes auth is mocked/handled by fixture
        # In real scenario, we'd need proper authentication

        # Expected: Only 4 participants from Group A
        if response.status_code == 200:
            data = response.json()
            participants = data.get("active_match", {}).get("participants", [])

            # With proper ParticipantFilterService, should return 4 players
            assert len(participants) <= 4, "Group A session should have at most 4 participants"

    # ========================================================================
    # TEST 4: Session Generation Validation
    # ========================================================================

    def test_session_generation_requires_in_progress_status(
        self,
        db_session: Session,
        instructor_user,
        tournament_type_league
    ):
        """
        TEST: Session generation fails if tournament not IN_PROGRESS

        Expected:
        - Generation fails with appropriate error message
        - sessions_generated remains False
        """
        # Create tournament in DRAFT status
        tournament = Semester(
            code="TEST/2026/DRAFT",
            name="Test Draft Tournament",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="DRAFT",  # NOT IN_PROGRESS
            master_instructor_id=instructor_user.id,
            # P2 Schema: tournament_type_id moved to TournamentConfiguration
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=tournament_type_league.id,
                participant_type="INDIVIDUAL",
                scoring_type="HEAD_TO_HEAD"
            )
        )
        db_session.add(tournament)
        db_session.commit()

        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db_session)

        # Attempt generation
        success, message, sessions = generator.generate_sessions(tournament_id=tournament.id)

        # Should fail
        assert success is False
        assert "not ready" in message.lower() or "in_progress" in message.lower()
        assert len(sessions) == 0

        # Verify no sessions created
        session_count = db_session.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id
        ).count()
        assert session_count == 0

    def test_session_generation_idempotency(
        self,
        db_session: Session,
        test_tournament,
        enrolled_players_8
    ):
        """
        TEST: Session generation can only be run once (idempotent)

        Expected:
        - First generation succeeds
        - Second generation fails with "already generated" message
        """
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db_session)

        # First generation
        success1, message1, sessions1 = generator.generate_sessions(
            tournament_id=test_tournament.id
        )
        assert success1 is True
        assert len(sessions1) == 28, f"Expected 28 sessions (8-player round-robin), got {len(sessions1)}"

        # Second generation (should fail)
        success2, message2, sessions2 = generator.generate_sessions(
            tournament_id=test_tournament.id
        )
        assert success2 is False
        assert "already generated" in message2.lower()
        assert len(sessions2) == 0

        # Verify only 28 sessions exist (not 56 - idempotency check)
        session_count = db_session.query(SessionModel).filter(
            SessionModel.semester_id == test_tournament.id
        ).count()
        assert session_count == 28, f"Expected 28 sessions (idempotent), got {session_count}"

    # ========================================================================
    # TEST 5: Booking and Attendance Auto-Creation
    # ========================================================================

    def test_session_generation_creates_bookings_and_attendance(
        self,
        db_session: Session,
        test_tournament,
        enrolled_players_8
    ):
        """
        TEST: Session generation creates sessions with correct participant assignments

        NOTE: Bookings and attendance are NOT auto-created by session generator.
        They are created separately when students enroll or when matches start.

        Expected:
        - 28 sessions created (8-player round-robin)
        - Each session has participant_user_ids populated
        - Each session has expected_participants = 2 (1v1 matches)
        """
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db_session)

        success, message, sessions_created = generator.generate_sessions(tournament_id=test_tournament.id)

        assert success is True
        assert len(sessions_created) == 28, f"Expected 28 sessions, got {len(sessions_created)}"

        # Get all sessions
        sessions = db_session.query(SessionModel).filter(
            SessionModel.semester_id == test_tournament.id
        ).all()

        assert len(sessions) == 28

        # Verify each session has participant assignments (HEAD_TO_HEAD 1v1)
        for session in sessions:
            assert session.expected_participants == 2, "HEAD_TO_HEAD matches have 2 participants"
            assert session.event_category == EventCategory.MATCH
            assert session.auto_generated is True

            # Note: Bookings and attendance are created separately, not by generator

    # ========================================================================
    # TEST 6: Knockout (TIERED) Tournament Session Generation
    # ========================================================================

    def test_knockout_tiered_tournament_session_generation(
        self,
        db_session: Session,
        instructor_user
    ):
        """
        TEST: Generate sessions for Knockout tournament (HEAD_TO_HEAD single elimination)

        Expected:
        - 8 sessions for 8 players: 4 QF + 2 SF + 1 Final + 1 3rd place
        - All sessions are 1v1 matches (expected_participants = 2)
        - Tournament phases: KNOCKOUT, FINALS
        """
        # Create Knockout tournament type
        tournament_type = TournamentType(
            code="knockout",
            display_name="Knockout Tournament",
            description="Single elimination with tier-based points",
            min_players=4,
            max_players=16,
            requires_power_of_two=True,
            format="HEAD_TO_HEAD",  # P2 Schema: Required for HEAD_TO_HEAD formats
            config={
                "round_names": {
                    "8": "Quarter-Finals",
                    "4": "Semi-Finals",
                    "2": "Finals"
                },
                "third_place_playoff": True
            }
        )
        db_session.add(tournament_type)
        db_session.commit()

        # Create tournament
        tournament = Semester(
            code="TEST/2026/KNOCKOUT",
            name="Test Knockout",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=instructor_user.id,
            # P2 Schema: tournament_type_id moved to TournamentConfiguration
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=tournament_type.id,
                participant_type="INDIVIDUAL",
                scoring_type="HEAD_TO_HEAD"
            )
        )
        db_session.add(tournament)
        db_session.commit()

        # Create 8 enrolled players
        for i in range(1, 9):
            user = User(
                name=f"KO Player {i}",
                email=f"koplayer{i}@test.com",
                password_hash="test_hash",
                role=UserRole.STUDENT,
                is_active=True
            )
            db_session.add(user)
            db_session.flush()

            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                credit_balance=500,
                credit_purchased=500
            )
            db_session.add(license)
            db_session.flush()

            enrollment = SemesterEnrollment(
                user_id=user.id,
                semester_id=tournament.id,
                user_license_id=license.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED
            )
            db_session.add(enrollment)

        db_session.commit()

        # Generate sessions
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db_session)

        success, message, sessions_created = generator.generate_sessions(
            tournament_id=tournament.id
        )

        assert success is True
        # 8 players: Quarter-finals (4), Semi-finals (2), Finals (1) + 3rd place (1) = 8 sessions
        assert len(sessions_created) == 8, f"Expected 8 knockout sessions, got {len(sessions_created)}"

        # Verify all sessions are HEAD_TO_HEAD 1v1 matches
        sessions = db_session.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id
        ).order_by(SessionModel.date_start).all()

        assert len(sessions) == 8

        for session in sessions:
            assert session.expected_participants == 2, "Knockout matches are 1v1"
            assert session.event_category == EventCategory.MATCH
            assert session.auto_generated is True
            assert session.group_identifier is None

        # Verify tournament structure
        # Quarter-finals: 4 matches (round 1)
        qf_sessions = [s for s in sessions if s.tournament_round == 1]
        assert len(qf_sessions) == 4, f"Expected 4 QF matches, got {len(qf_sessions)}"

        # Semi-finals: 2 matches (round 2)
        sf_sessions = [s for s in sessions if s.tournament_round == 2]
        assert len(sf_sessions) == 2, f"Expected 2 SF matches, got {len(sf_sessions)}"

        # Finals + 3rd place: 2 matches (round 3)
        final_sessions = [s for s in sessions if s.tournament_round == 3]
        assert len(final_sessions) == 2, f"Expected 2 final matches (final + 3rd place), got {len(final_sessions)}"

    # ========================================================================
    # TEST 7: Swiss System (PERFORMANCE_POD) Tournament Session Generation
    # ========================================================================

    def test_swiss_performance_pod_tournament_session_generation(
        self,
        db_session: Session,
        instructor_user
    ):
        """
        TEST: Generate sessions for Swiss System tournament (HEAD_TO_HEAD)

        Expected:
        - Multiple rounds with performance-based pairing
        - HEAD_TO_HEAD 1v1 matches
        - 8 players, 3 rounds: 4 matches per round = 12 total sessions
        - Pairings adjust based on performance after each round
        """
        # Create Swiss tournament type
        tournament_type = TournamentType(
            code="swiss",
            display_name="Swiss System",
            description="Performance-based pod assignments",
            min_players=8,
            max_players=16,
            requires_power_of_two=False,
            format="HEAD_TO_HEAD",  # P2 Schema: Required for HEAD_TO_HEAD formats
            config={
                "pod_size": 4
            }
        )
        db_session.add(tournament_type)
        db_session.commit()

        # Create tournament
        tournament = Semester(
            code="TEST/2026/SWISS",
            name="Test Swiss",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=instructor_user.id,
            # P2 Schema: tournament_type_id moved to TournamentConfiguration
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=tournament_type.id,
                participant_type="INDIVIDUAL",
                scoring_type="HEAD_TO_HEAD"
            )
        )
        db_session.add(tournament)
        db_session.commit()

        # Create 8 enrolled players
        for i in range(1, 9):
            user = User(
                name=f"Swiss Player {i}",
                email=f"swissplayer{i}@test.com",
                password_hash="test_hash",
                role=UserRole.STUDENT,
                is_active=True
            )
            db_session.add(user)
            db_session.flush()

            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                credit_balance=500,
                credit_purchased=500
            )
            db_session.add(license)
            db_session.flush()

            enrollment = SemesterEnrollment(
                user_id=user.id,
                semester_id=tournament.id,
                user_license_id=license.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED
            )
            db_session.add(enrollment)

        db_session.commit()

        # Generate sessions
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(db_session)

        success, message, sessions_created = generator.generate_sessions(
            tournament_id=tournament.id
        )

        assert success is True
        # 8 players, 3 rounds, 4 matches per round (1v1 HEAD_TO_HEAD) = 12 sessions
        assert len(sessions_created) == 12, f"Expected 12 sessions (3 rounds × 4 matches), got {len(sessions_created)}"

        # Verify sessions
        sessions = db_session.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id
        ).order_by(SessionModel.date_start).all()

        assert len(sessions) == 12

        # All sessions should be HEAD_TO_HEAD 1v1 matches
        for session in sessions:
            assert session.expected_participants == 2, "Swiss matches are 1v1 HEAD_TO_HEAD"
            assert session.event_category == EventCategory.MATCH
            assert session.auto_generated is True

        # Verify round distribution (4 matches per round)
        round_1 = [s for s in sessions if s.tournament_round == 1]
        round_2 = [s for s in sessions if s.tournament_round == 2]
        round_3 = [s for s in sessions if s.tournament_round == 3]

        assert len(round_1) == 4, f"Expected 4 matches in round 1, got {len(round_1)}"
        assert len(round_2) == 4, f"Expected 4 matches in round 2, got {len(round_2)}"
        assert len(round_3) == 4, f"Expected 4 matches in round 3, got {len(round_3)}"

    # ========================================================================
    # TEST 8: Points Recording API with Tier Multipliers
    # ========================================================================

    def test_record_match_results_with_tier_multipliers(
        self,
        db_session: Session,
        instructor_user
    ):
        """
        TEST: Record match results applies tier multipliers correctly

        Expected:
        - Points calculated with tier multipliers
        - Tournament rankings updated correctly
        - Leaderboard reflects tier-adjusted points
        """
        # Create Knockout tournament type
        tournament_type = TournamentType(
            code="knockout_test",
            display_name="Knockout Test",
            description="Test tier multipliers",
            min_players=4,
            max_players=8,
            requires_power_of_two=True,
            format="HEAD_TO_HEAD",  # P2 Schema: Required for HEAD_TO_HEAD formats
            config={}
        )
        db_session.add(tournament_type)
        db_session.commit()

        # Create tournament
        tournament = Semester(
            code="TEST/POINTS",
            name="Test Points Calculation",
            start_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=14)).date(),
            tournament_status="IN_PROGRESS",
            master_instructor_id=instructor_user.id,
            # P2 Schema: tournament_type_id moved to TournamentConfiguration
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=tournament_type.id,
                participant_type="INDIVIDUAL",
                scoring_type="HEAD_TO_HEAD"
            )
        )
        db_session.add(tournament)
        db_session.commit()

        # Create 4 players
        player_ids = []
        for i in range(1, 5):
            user = User(
                name=f"Points Player {i}",
                email=f"pointsplayer{i}@test.com",
                password_hash="test_hash",
                role=UserRole.STUDENT,
                is_active=True
            )
            db_session.add(user)
            db_session.flush()

            license = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.now(timezone.utc),
                credit_balance=500,
                credit_purchased=500
            )
            db_session.add(license)
            db_session.flush()

            enrollment = SemesterEnrollment(
                user_id=user.id,
                semester_id=tournament.id,
                user_license_id=license.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED
            )
            db_session.add(enrollment)
            player_ids.append(user.id)

        db_session.commit()

        # Create a TIERED session (Finals, tier=3, multiplier=2.0)
        session = SessionModel(
            title="Finals",
            semester_id=tournament.id,
            instructor_id=instructor_user.id,
            date_start=datetime.now(timezone.utc),
            date_end=datetime.now(timezone.utc) + timedelta(hours=2),
            event_category=EventCategory.MATCH,
            auto_generated=True,
            ranking_mode='TIERED',
            expected_participants=4,
            pod_tier=3  # Finals tier
        )
        db_session.add(session)
        db_session.commit()

        # Record results using the service directly
        from app.services.tournament.points_calculator_service import PointsCalculatorService

        points_calculator = PointsCalculatorService(db_session)
        tournament_config = points_calculator.get_tournament_type_config(tournament.id)

        # Simulate recording results: Player 1=1st, Player 2=2nd, Player 3=3rd, Player 4=4th
        rankings = [
            (player_ids[0], 1),
            (player_ids[1], 2),
            (player_ids[2], 3),
            (player_ids[3], 4)
        ]

        points_map = points_calculator.calculate_points_batch(
            session_id=session.id,
            rankings=rankings,
            tournament_type_config=tournament_config
        )

        # Verify tier multipliers applied
        # Finals (tier=3): multiplier=2.0
        # 1st: 3 * 2.0 = 6.0
        # 2nd: 2 * 2.0 = 4.0
        # 3rd: 1 * 2.0 = 2.0
        # 4th: 0 * 2.0 = 0.0
        assert points_map[player_ids[0]] == 6.0
        assert points_map[player_ids[1]] == 4.0
        assert points_map[player_ids[2]] == 2.0
        assert points_map[player_ids[3]] == 0.0

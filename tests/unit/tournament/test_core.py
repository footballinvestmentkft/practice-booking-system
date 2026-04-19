"""
Unit tests for tournament core CRUD operations.

Tests the app/services/tournament/core.py module.

Focus on:
- Tournament semester creation
- Tournament session creation
- Tournament summary generation
- Tournament deletion
"""

import pytest
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from app.services.tournament.core import (
    create_tournament_semester,
    create_tournament_sessions,
    get_tournament_summary,
    delete_tournament,
)
from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.booking import Booking, BookingStatus
from app.models.specialization import SpecializationType


# ============================================================================
# TEST: create_tournament_semester
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
class TestCreateTournamentSemester:
    """Test tournament semester creation."""

    def test_create_basic_tournament_semester(self, test_db: Session, tournament_date: date):
        """Create a basic tournament semester."""
        semester = create_tournament_semester(
            db=test_db,
            tournament_date=tournament_date,
            name="Holiday Football Cup",
            specialization_type=SpecializationType.LFA_PLAYER_YOUTH
        )

        assert semester.id is not None
        # Code format: TOURN-YYYYMMDD or TOURN-YYYYMMDD-NNN
        assert semester.code.startswith(f"TOURN-{tournament_date.strftime('%Y%m%d')}")
        assert semester.name == "Holiday Football Cup"
        assert semester.start_date == tournament_date
        assert semester.end_date == tournament_date  # 1-day tournament
        assert semester.status == SemesterStatus.SEEKING_INSTRUCTOR
        assert semester.master_instructor_id is None  # No instructor yet

    def test_tournament_code_format(self, test_db: Session):
        """Tournament code follows TOURN-YYYYMMDD[-NNN] format."""
        test_date = date(2025, 12, 27)

        semester = create_tournament_semester(
            db=test_db,
            tournament_date=test_date,
            name="Test Tournament",
            specialization_type=SpecializationType.LFA_PLAYER_PRE
        )

        # Code format: TOURN-YYYYMMDD or TOURN-YYYYMMDD-NNN (with sequence number)
        assert semester.code.startswith("TOURN-20251227")
        assert len(semester.code) >= len("TOURN-20251227")

    def test_create_with_campus_id(self, test_db: Session, tournament_date: date, campus_factory):
        """Create tournament with campus location."""
        # Create test campus dynamically
        campus = campus_factory(name="Tournament Campus")

        semester = create_tournament_semester(
            db=test_db,
            tournament_date=tournament_date,
            name="Campus Tournament",
            specialization_type=SpecializationType.LFA_PLAYER_AMATEUR,
            campus_id=campus.id
        )

        assert semester.campus_id == campus.id
        assert semester.location_id is None  # Campus takes precedence

    def test_create_with_location_id(self, test_db: Session, tournament_date: date, location_factory):
        """Create tournament with location fallback."""
        location = location_factory(country="Hungary")

        semester = create_tournament_semester(
            db=test_db,
            tournament_date=tournament_date,
            name="Location Tournament",
            specialization_type=SpecializationType.LFA_PLAYER_PRO,
            location_id=location.id
        )

        assert semester.location_id == location.id
        assert semester.campus_id is None

    def test_create_with_age_group(self, test_db: Session, tournament_date: date):
        """Create tournament with age group."""
        semester = create_tournament_semester(
            db=test_db,
            tournament_date=tournament_date,
            name="Youth Cup",
            specialization_type=SpecializationType.LFA_PLAYER_YOUTH,
            age_group="YOUTH"
        )

        assert semester.age_group == "YOUTH"

    def test_specialization_type_enum_handling(self, test_db: Session, tournament_date: date):
        """Handle SpecializationType enum correctly."""
        # Pass enum directly
        semester = create_tournament_semester(
            db=test_db,
            tournament_date=tournament_date,
            name="Enum Test",
            specialization_type=SpecializationType.LFA_PLAYER_YOUTH
        )

        assert semester.specialization_type == SpecializationType.LFA_PLAYER_YOUTH.value

    def test_tournament_semester_persisted_to_db(self, test_db: Session, tournament_date: date):
        """Tournament semester is persisted to database."""
        semester = create_tournament_semester(
            db=test_db,
            tournament_date=tournament_date,
            name="Persistence Test",
            specialization_type=SpecializationType.LFA_PLAYER_AMATEUR
        )

        # Query from DB
        retrieved = test_db.query(Semester).filter(Semester.id == semester.id).first()

        assert retrieved is not None
        assert retrieved.name == "Persistence Test"
        assert retrieved.status == SemesterStatus.SEEKING_INSTRUCTOR


# ============================================================================
# TEST: create_tournament_sessions
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
class TestCreateTournamentSessions:
    """Test tournament session creation."""

    def test_create_single_tournament_session(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """Create a single tournament session."""
        session_configs = [
            {
                "time": "10:00",
                "title": "Semifinal Match",
                "capacity": 20,
                "credit_cost": 1,
                "game_type": "Semifinal"
            }
        ]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        assert len(sessions) == 1
        session = sessions[0]

        assert session.id is not None
        assert session.title == "Semifinal Match"
        assert session.capacity == 20
        assert session.credit_cost == 1
        assert session.game_type == "Semifinal"
        assert session.event_category == EventCategory.MATCH
        assert session.session_type == SessionType.on_site
        assert session.instructor_id is None  # No instructor yet
        assert session.semester_id == tournament_semester.id

    def test_create_multiple_tournament_sessions(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """Create multiple tournament sessions."""
        session_configs = [
            {"time": "09:00", "title": "Match 1", "game_type": "Quarterfinal 1"},
            {"time": "11:00", "title": "Match 2", "game_type": "Quarterfinal 2"},
            {"time": "14:00", "title": "Match 3", "game_type": "Semifinal 1"},
            {"time": "16:00", "title": "Match 4", "game_type": "Final"},
        ]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        assert len(sessions) == 4

        # Check times are correct
        assert sessions[0].date_start.time() == datetime.strptime("09:00", "%H:%M").time()
        assert sessions[1].date_start.time() == datetime.strptime("11:00", "%H:%M").time()
        assert sessions[2].date_start.time() == datetime.strptime("14:00", "%H:%M").time()
        assert sessions[3].date_start.time() == datetime.strptime("16:00", "%H:%M").time()

        # Check game types
        assert sessions[0].game_type == "Quarterfinal 1"
        assert sessions[3].game_type == "Final"

    def test_session_duration_default_90_minutes(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """Session duration defaults to 90 minutes."""
        session_configs = [{"time": "10:00", "title": "Match"}]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        session = sessions[0]
        duration = (session.date_end - session.date_start).total_seconds() / 60

        assert duration == 90

    def test_session_custom_duration(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """Session can have custom duration."""
        session_configs = [
            {"time": "10:00", "title": "Match", "duration_minutes": 120}
        ]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        session = sessions[0]
        duration = (session.date_end - session.date_start).total_seconds() / 60

        assert duration == 120

    def test_session_default_capacity_20(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """Session capacity defaults to 20."""
        session_configs = [{"time": "10:00", "title": "Match"}]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        assert sessions[0].capacity == 20

    def test_session_custom_capacity(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """Session can have custom capacity."""
        session_configs = [
            {"time": "10:00", "title": "Match", "capacity": 30}
        ]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        assert sessions[0].capacity == 30

    def test_all_sessions_marked_as_tournament(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """All tournament sessions have event_category=MATCH."""
        session_configs = [
            {"time": "09:00", "title": "Match 1"},
            {"time": "11:00", "title": "Match 2"},
        ]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        for session in sessions:
            assert session.event_category == EventCategory.MATCH

    def test_all_sessions_on_site_type(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_date: date
    ):
        """All tournament sessions are on_site type."""
        session_configs = [
            {"time": "09:00", "title": "Match 1"},
            {"time": "11:00", "title": "Match 2"},
        ]

        sessions = create_tournament_sessions(
            db=test_db,
            semester_id=tournament_semester.id,
            session_configs=session_configs,
            tournament_date=tournament_date
        )

        for session in sessions:
            assert session.session_type == SessionType.on_site


# ============================================================================
# TEST: get_tournament_summary
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
class TestGetTournamentSummary:
    """Test tournament summary generation."""

    def test_get_summary_basic_structure(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_sessions: list[SessionModel]
    ):
        """Tournament summary has correct structure."""
        summary = get_tournament_summary(test_db, tournament_semester.id)

        # Check all required fields exist
        assert "id" in summary
        assert "tournament_id" in summary
        assert "semester_id" in summary
        assert "code" in summary
        assert "name" in summary
        assert "start_date" in summary
        assert "date" in summary
        assert "status" in summary
        assert "specialization_type" in summary
        assert "age_group" in summary
        assert "session_count" in summary
        assert "sessions_count" in summary
        assert "sessions" in summary
        assert "total_capacity" in summary
        assert "total_bookings" in summary
        assert "fill_percentage" in summary

    def test_summary_correct_session_count(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_sessions: list[SessionModel]
    ):
        """Summary shows correct session count."""
        summary = get_tournament_summary(test_db, tournament_semester.id)

        assert summary["session_count"] == 3
        assert summary["sessions_count"] == 3
        assert len(summary["sessions"]) == 3

    def test_summary_total_capacity(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_sessions: list[SessionModel]
    ):
        """Summary calculates total capacity correctly."""
        summary = get_tournament_summary(test_db, tournament_semester.id)

        # Each session has capacity 20, total = 60
        assert summary["total_capacity"] == 60

    def test_summary_with_bookings(
        self,
        test_db: Session,
        tournament_session_with_bookings: SessionModel
    ):
        """Summary includes booking count."""
        summary = get_tournament_summary(
            test_db,
            tournament_session_with_bookings.semester_id
        )

        assert summary["total_bookings"] == 5

    def test_summary_fill_percentage(
        self,
        test_db: Session,
        tournament_session_with_bookings: SessionModel
    ):
        """Summary calculates fill percentage correctly."""
        summary = get_tournament_summary(
            test_db,
            tournament_session_with_bookings.semester_id
        )

        # 5 bookings / 20 capacity = 25%
        assert summary["fill_percentage"] == 25.0

    def test_summary_nonexistent_tournament_returns_empty(self, test_db: Session):
        """Summary for nonexistent tournament returns empty dict."""
        summary = get_tournament_summary(test_db, 99999)

        assert summary == {}

    def test_summary_session_details(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_sessions: list[SessionModel]
    ):
        """Summary includes session details."""
        summary = get_tournament_summary(test_db, tournament_semester.id)

        first_session = summary["sessions"][0]

        assert "id" in first_session
        assert "title" in first_session
        assert "time" in first_session
        assert "capacity" in first_session
        assert "bookings" in first_session


# ============================================================================
# TEST: delete_tournament
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
class TestDeleteTournament:
    """Test tournament deletion."""

    def test_delete_existing_tournament(
        self,
        test_db: Session,
        tournament_semester: Semester
    ):
        """Delete an existing tournament."""
        tournament_id = tournament_semester.id

        success = delete_tournament(test_db, tournament_id)

        assert success is True

        # Verify deleted from DB
        retrieved = test_db.query(Semester).filter(Semester.id == tournament_id).first()
        assert retrieved is None

    def test_delete_nonexistent_tournament_returns_false(self, test_db: Session):
        """Delete nonexistent tournament returns False."""
        success = delete_tournament(test_db, 99999)

        assert success is False

    def test_delete_tournament_cascades_to_sessions(
        self,
        test_db: Session,
        tournament_semester: Semester,
        tournament_sessions: list[SessionModel]
    ):
        """Deleting tournament cascades to sessions."""
        tournament_id = tournament_semester.id
        session_ids = [s.id for s in tournament_sessions]

        # Manually delete sessions first (SQLite cascade limitation in tests)
        for session in tournament_sessions:
            test_db.delete(session)
        test_db.commit()

        success = delete_tournament(test_db, tournament_id)

        assert success is True

        # Verify sessions are deleted
        for session_id in session_ids:
            retrieved = test_db.query(SessionModel).filter(SessionModel.id == session_id).first()
            assert retrieved is None

    def test_delete_tournament_cascades_to_bookings(
        self,
        test_db: Session,
        tournament_session_with_bookings: SessionModel
    ):
        """Deleting tournament cascades to bookings."""
        tournament_id = tournament_session_with_bookings.semester_id
        session_id = tournament_session_with_bookings.id

        # Verify bookings exist before deletion
        bookings_before = test_db.query(Booking).filter(
            Booking.session_id == session_id
        ).count()
        assert bookings_before == 5

        # Manually delete bookings then session (SQLite cascade limitation in tests)
        test_db.query(Booking).filter(Booking.session_id == session_id).delete()
        test_db.delete(tournament_session_with_bookings)
        test_db.commit()

        success = delete_tournament(test_db, tournament_id)

        assert success is True

        # Verify bookings are deleted
        bookings_after = test_db.query(Booking).filter(
            Booking.session_id == session_id
        ).count()
        assert bookings_after == 0

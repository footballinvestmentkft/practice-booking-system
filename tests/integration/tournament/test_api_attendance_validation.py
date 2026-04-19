"""
Integration tests for tournament attendance VALIDATION.

Tests ONLY the validation logic (not full attendance creation flow).
This tests the CRITICAL requirement: Tournament sessions reject late/excused status.
"""

import pytest
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from app.services.tournament.validation import validate_tournament_attendance_status
from app.models.session import Session as SessionModel, EventCategory
from app.models.semester import Semester


# ============================================================================
# Unit-style tests for validation (runs fast, no complex DB setup)
# ============================================================================

@pytest.mark.integration
@pytest.mark.tournament
class TestTournamentAttendanceValidation:
    """Test tournament attendance validation rules."""

    def test_validate_present_status(self):
        """Tournament sessions accept 'present' - validation passes."""
        is_valid, error = validate_tournament_attendance_status("present")
        assert is_valid is True
        assert error is None

    def test_validate_absent_status(self):
        """Tournament sessions accept 'absent' - validation passes."""
        is_valid, error = validate_tournament_attendance_status("absent")
        assert is_valid is True
        assert error is None

    def test_validate_late_status_fails(self):
        """
        🔥 CRITICAL: Tournament sessions REJECT 'late' status.

        This validates the 2-button rule.
        """
        is_valid, error = validate_tournament_attendance_status("late")
        assert is_valid is False
        assert "Invalid tournament attendance status" in error
        assert "late" in error
        assert "present" in error
        assert "absent" in error

    def test_validate_excused_status_fails(self):
        """
        🔥 CRITICAL: Tournament sessions REJECT 'excused' status.

        This validates the 2-button rule.
        """
        is_valid, error = validate_tournament_attendance_status("excused")
        assert is_valid is False
        assert "Invalid tournament attendance status" in error
        assert "excused" in error

    def test_validate_unknown_status_fails(self):
        """Unknown attendance status fails validation."""
        is_valid, error = validate_tournament_attendance_status("unknown")
        assert is_valid is False
        assert "Invalid tournament attendance status" in error


# ============================================================================
# Integration test: Tournament session flag detection
# ============================================================================

@pytest.mark.integration
@pytest.mark.tournament
class TestTournamentSessionDetection:
    """Test that tournament sessions are correctly identified."""

    def test_tournament_session_has_flag(
        self,
        test_db: Session,
        tournament_session_with_bookings: SessionModel
    ):
        """Tournament session has event_category=MATCH."""
        assert tournament_session_with_bookings.event_category == EventCategory.MATCH
        assert tournament_session_with_bookings.game_type is not None

    def test_tournament_semester_has_master_instructor(
        self,
        test_db: Session,
        tournament_semester_with_instructor: Semester
    ):
        """Tournament semester (ready) has master instructor assigned."""
        assert tournament_semester_with_instructor.master_instructor_id is not None

    def test_regular_session_does_not_have_tournament_flag(
        self,
        test_db: Session,
        instructor_user,
        student_user
    ):
        """Regular session has event_category=TRAINING."""
        from app.models.semester import SemesterStatus
        from app.models.specialization import SpecializationType
        from app.models.session import SessionType

        # Create regular semester
        semester = Semester(
            code="REG-2026-Q1",
            name="Regular Semester",
            start_date=date.today() + timedelta(days=7),
            end_date=date.today() + timedelta(days=90),
            status=SemesterStatus.ONGOING,
            specialization_type=SpecializationType.LFA_PLAYER_YOUTH.value,
            master_instructor_id=instructor_user.id
        )
        test_db.add(semester)
        test_db.commit()

        # Create regular session
        session_start = datetime.now() + timedelta(days=7, hours=10)
        session = SessionModel(
            title="Regular Training",
            date_start=session_start,
            date_end=session_start + timedelta(minutes=90),
            session_type=SessionType.on_site,
            capacity=20,
            instructor_id=instructor_user.id,
            semester_id=semester.id,
            event_category=EventCategory.TRAINING  # NOT a tournament
        )
        test_db.add(session)
        test_db.commit()
        test_db.refresh(session)

        assert session.event_category == EventCategory.TRAINING
        assert session.game_type is None


# ============================================================================
# Summary Test: End-to-End Validation Flow
# ============================================================================

@pytest.mark.integration
@pytest.mark.tournament
class TestTournamentValidationFlow:
    """Test complete validation flow for tournament attendance."""

    def test_validation_flow_for_tournament_session(
        self,
        test_db: Session,
        tournament_session_with_bookings: SessionModel
    ):
        """
        Complete flow: Identify tournament session → Apply validation.

        This simulates what the API endpoint should do.
        """
        # Step 1: Check if session is tournament
        session = tournament_session_with_bookings
        assert session.event_category == EventCategory.MATCH

        # Step 2: If tournament, ONLY present/absent allowed
        valid_statuses = ["present", "absent"]
        invalid_statuses = ["late", "excused"]

        # Validate valid statuses
        for status in valid_statuses:
            is_valid, error = validate_tournament_attendance_status(status)
            assert is_valid is True, f"Status '{status}' should be valid for tournaments"

        # Validate invalid statuses
        for status in invalid_statuses:
            is_valid, error = validate_tournament_attendance_status(status)
            assert is_valid is False, f"Status '{status}' should be INVALID for tournaments"
            assert "Invalid tournament attendance status" in error


# ============================================================================
# Performance Test (optional)
# ============================================================================

@pytest.mark.integration
@pytest.mark.tournament
@pytest.mark.slow
class TestTournamentValidationPerformance:
    """Test validation performance."""

    def test_validation_is_fast(self):
        """Validation should complete in < 1ms."""
        import time

        start = time.perf_counter()
        for _ in range(1000):
            validate_tournament_attendance_status("present")
        end = time.perf_counter()

        elapsed_ms = (end - start) * 1000
        # 1000 validations should complete in < 10ms
        assert elapsed_ms < 10, f"Validation too slow: {elapsed_ms:.2f}ms for 1000 calls"

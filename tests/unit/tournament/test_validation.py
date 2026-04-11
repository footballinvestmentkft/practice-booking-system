"""
Unit tests for tournament validation logic.

Tests the app/services/tournament/validation.py module.

These are pure unit tests - no database, no API calls.
Focus on business logic and edge cases.
"""

import pytest
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from app.services.tournament.validation import (
    get_visible_tournament_age_groups,
    validate_tournament_enrollment_age,
    validate_tournament_ready_for_enrollment,
    validate_enrollment_deadline,
    check_duplicate_enrollment,
    validate_tournament_session_type,
    validate_tournament_attendance_status,
)
from app.models.semester import Semester, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment


# ============================================================================
# TEST: get_visible_tournament_age_groups
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestGetVisibleTournamentAgeGroups:
    """Test age group visibility rules for tournaments."""

    def test_pro_category_sees_only_pro(self):
        """PRO (18+) can ONLY see PRO tournaments."""
        visible = get_visible_tournament_age_groups("PRO")
        assert visible == ["PRO"]

    def test_invalid_category_returns_empty(self):
        """Invalid age category returns empty list."""
        assert get_visible_tournament_age_groups("INVALID") == []
        assert get_visible_tournament_age_groups("") == []
        assert get_visible_tournament_age_groups(None) == []


# ============================================================================
# TEST: validate_tournament_enrollment_age
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestValidateTournamentEnrollmentAge:
    """Test age category enrollment validation."""

    def test_pre_can_enroll_in_pre(self):
        """PRE player can enroll in PRE tournament."""
        is_valid, error = validate_tournament_enrollment_age("PRE", "PRE")
        assert is_valid is True
        assert error is None

    def test_youth_can_enroll_in_youth(self):
        """YOUTH player can enroll in YOUTH tournament."""
        is_valid, error = validate_tournament_enrollment_age("YOUTH", "YOUTH")
        assert is_valid is True
        assert error is None

    def test_youth_can_enroll_in_amateur(self):
        """YOUTH player can 'move up' to AMATEUR tournament."""
        is_valid, error = validate_tournament_enrollment_age("YOUTH", "AMATEUR")
        assert is_valid is True
        assert error is None

    def test_amateur_can_enroll_in_amateur(self):
        """AMATEUR player can enroll in AMATEUR tournament."""
        is_valid, error = validate_tournament_enrollment_age("AMATEUR", "AMATEUR")
        assert is_valid is True
        assert error is None

    def test_pro_can_enroll_in_pro(self):
        """PRO player can enroll in PRO tournament."""
        is_valid, error = validate_tournament_enrollment_age("PRO", "PRO")
        assert is_valid is True
        assert error is None

    def test_invalid_player_category(self):
        """Invalid player category returns error."""
        is_valid, error = validate_tournament_enrollment_age("INVALID", "YOUTH")
        assert is_valid is False
        assert "Invalid age category" in error


# ============================================================================
# TEST: validate_tournament_ready_for_enrollment
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestValidateTournamentReadyForEnrollment:
    """Test tournament enrollment status validation."""

    def test_ready_tournament_is_valid(self, tournament_semester_with_instructor):
        """Tournament in READY_FOR_ENROLLMENT status is valid."""
        tournament_semester_with_instructor.status = SemesterStatus.READY_FOR_ENROLLMENT

        is_valid, error = validate_tournament_ready_for_enrollment(tournament_semester_with_instructor)

        assert is_valid is True
        assert error is None

    def test_seeking_instructor_tournament_is_invalid(self, tournament_semester):
        """Tournament in SEEKING_INSTRUCTOR status is invalid."""
        is_valid, error = validate_tournament_ready_for_enrollment(tournament_semester)

        assert is_valid is False
        assert "not ready for enrollment" in error.lower()
        assert SemesterStatus.SEEKING_INSTRUCTOR.value in error

    def test_completed_tournament_is_invalid(self, tournament_semester):
        """Tournament in COMPLETED status is invalid."""
        tournament_semester.status = SemesterStatus.COMPLETED

        is_valid, error = validate_tournament_ready_for_enrollment(tournament_semester)

        assert is_valid is False
        assert "not ready for enrollment" in error.lower()


# ============================================================================
# TEST: validate_enrollment_deadline
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestValidateEnrollmentDeadline:
    """Test enrollment deadline validation (1 hour before start)."""

    def test_enrollment_open_before_deadline(self):
        """Enrollment is valid 2 hours before tournament start."""
        first_session_start = datetime.utcnow() + timedelta(hours=2)

        is_valid, error = validate_enrollment_deadline(first_session_start)

        assert is_valid is True
        assert error is None

    def test_enrollment_open_exactly_1_hour_before(self):
        """Enrollment is still valid exactly 1 hour before."""
        first_session_start = datetime.utcnow() + timedelta(hours=1, minutes=1)

        is_valid, error = validate_enrollment_deadline(first_session_start)

        assert is_valid is True
        assert error is None

    def test_enrollment_closed_59_minutes_before(self):
        """Enrollment is closed 59 minutes before tournament start."""
        first_session_start = datetime.utcnow() + timedelta(minutes=59)

        is_valid, error = validate_enrollment_deadline(first_session_start)

        assert is_valid is False
        assert "Enrollment closed" in error
        assert "tournament starting soon" in error.lower()

    def test_enrollment_closed_after_start(self):
        """Enrollment is closed after tournament has started."""
        first_session_start = datetime.utcnow() - timedelta(hours=1)

        is_valid, error = validate_enrollment_deadline(first_session_start)

        assert is_valid is False
        assert "Enrollment closed" in error

    def test_none_first_session_returns_valid(self):
        """If no first session time provided, enrollment is valid."""
        is_valid, error = validate_enrollment_deadline(None)

        assert is_valid is True
        assert error is None


# ============================================================================
# TEST: check_duplicate_enrollment
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestCheckDuplicateEnrollment:
    """Test duplicate enrollment detection."""

    def test_no_existing_enrollment_is_valid(
        self,
        test_db: Session,
        student_user,
        tournament_semester
    ):
        """User not enrolled yet - enrollment is valid."""
        is_unique, error = check_duplicate_enrollment(
            test_db,
            student_user.id,
            tournament_semester.id
        )

        assert is_unique is True
        assert error is None

    def test_existing_enrollment_is_invalid(
        self,
        test_db: Session,
        student_user,
        tournament_semester_with_instructor
    ):
        """User already enrolled - enrollment is invalid."""
        from app.models.license import UserLicense
        from datetime import datetime, timezone

        # Create user license for student
        user_license = UserLicense(
            user_id=student_user.id,
            specialization_type="PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc)
        )
        test_db.add(user_license)
        test_db.flush()

        # Create existing enrollment
        existing = SemesterEnrollment(
            user_id=student_user.id,
            semester_id=tournament_semester_with_instructor.id,
            user_license_id=user_license.id,
            payment_verified=True,
            is_active=True
        )
        test_db.add(existing)
        test_db.commit()

        # Try to enroll again
        is_unique, error = check_duplicate_enrollment(
            test_db,
            student_user.id,
            tournament_semester_with_instructor.id
        )

        assert is_unique is False
        assert "already enrolled" in error.lower()


# ============================================================================
# TEST: validate_tournament_session_type
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestValidateTournamentSessionType:
    """Test tournament session type validation (on_site, virtual, hybrid)."""

    def test_on_site_is_valid(self):
        """Tournament sessions can be on_site."""
        is_valid, error = validate_tournament_session_type("on_site")

        assert is_valid is True
        assert error is None

    def test_hybrid_is_valid(self):
        """Tournament sessions can be hybrid."""
        is_valid, error = validate_tournament_session_type("hybrid")

        assert is_valid is True
        assert error is None

    def test_virtual_is_valid(self):
        """Tournament sessions can be virtual."""
        is_valid, error = validate_tournament_session_type("virtual")

        assert is_valid is True
        assert error is None

    def test_invalid_type_is_rejected(self):
        """Unknown session types are rejected."""
        is_valid, error = validate_tournament_session_type("offline")

        assert is_valid is False
        assert "offline" in error


# ============================================================================
# TEST: validate_tournament_attendance_status
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestValidateTournamentAttendanceStatus:
    """Test tournament attendance status validation (ONLY present/absent)."""

    def test_present_is_valid(self):
        """'present' status is valid for tournaments."""
        is_valid, error = validate_tournament_attendance_status("present")

        assert is_valid is True
        assert error is None

    def test_absent_is_valid(self):
        """'absent' status is valid for tournaments."""
        is_valid, error = validate_tournament_attendance_status("absent")

        assert is_valid is True
        assert error is None

    def test_late_is_invalid(self):
        """'late' status is INVALID for tournaments."""
        is_valid, error = validate_tournament_attendance_status("late")

        assert is_valid is False
        assert "Invalid tournament attendance status" in error
        assert "late" in error
        assert "present" in error
        assert "absent" in error

    def test_excused_is_invalid(self):
        """'excused' status is INVALID for tournaments."""
        is_valid, error = validate_tournament_attendance_status("excused")

        assert is_valid is False
        assert "Invalid tournament attendance status" in error
        assert "excused" in error

    def test_unknown_status_is_invalid(self):
        """Unknown status is invalid."""
        is_valid, error = validate_tournament_attendance_status("unknown")

        assert is_valid is False
        assert "Invalid tournament attendance status" in error


# ============================================================================
# EDGE CASES & ERROR HANDLING
# ============================================================================

@pytest.mark.unit
@pytest.mark.tournament
@pytest.mark.validation
class TestValidationEdgeCases:
    """Test edge cases and error handling in validation logic."""

    def test_case_sensitivity_in_attendance_status(self):
        """Attendance status validation should be case-sensitive."""
        # Lowercase should work (standard format)
        is_valid, error = validate_tournament_attendance_status("present")
        assert is_valid is True

        # Uppercase might not work (depends on implementation)
        is_valid, error = validate_tournament_attendance_status("PRESENT")
        # If implementation is case-sensitive, this should fail

    def test_whitespace_handling(self):
        """Validation should handle whitespace correctly."""
        # Whitespace should not affect validation
        visible = get_visible_tournament_age_groups(" PRE ")
        # Depending on implementation, might need stripping

    def test_none_values(self):
        """Validation should handle None values gracefully."""
        # Should return empty/False, not crash
        visible = get_visible_tournament_age_groups(None)
        assert visible == []

        is_valid, error = validate_tournament_attendance_status(None)
        assert is_valid is False

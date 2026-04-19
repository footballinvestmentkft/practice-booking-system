"""
Integration tests for tournament attendance API endpoints.

Tests the CRITICAL requirement: Tournament sessions ONLY accept
present/absent attendance statuses (NO late/excused).

This is the API-level validation that ensures the 2-button UI
is properly enforced at the backend.
"""

import pytest
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models.user import User
from app.models.semester import Semester
from app.models.session import Session as SessionModel, EventCategory
from app.models.booking import Booking, BookingStatus
from app.models.attendance import Attendance, AttendanceStatus


# ============================================================================
# CRITICAL: Tournament Attendance Validation (2 vs 4 buttons)
# ============================================================================

@pytest.mark.integration
@pytest.mark.tournament
@pytest.mark.api
class TestTournamentAttendanceAPI:
    """Test tournament attendance API endpoints enforce 2-button rule."""

    def test_tournament_attendance_present_succeeds(
        self,
        client: TestClient,
        instructor_token: str,
        tournament_session_with_bookings: SessionModel,
        test_db: Session
    ):
        """Tournament sessions accept 'present' status - should succeed."""
        # Get first booking
        booking = test_db.query(Booking).filter(
            Booking.session_id == tournament_session_with_bookings.id
        ).first()

        response = client.post(
            "/api/v1/attendance/",
            json={
                "booking_id": booking.id,
                "user_id": booking.user_id,
                "session_id": booking.session_id,
                "status": "present",
                "notes": "Test attendance"
            },
            headers={"Authorization": f"Bearer {instructor_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "present"
        assert data["booking_id"] == booking.id

    def test_tournament_attendance_absent_succeeds(
        self,
        client: TestClient,
        instructor_token: str,
        tournament_session_with_bookings: SessionModel,
        test_db: Session
    ):
        """Tournament sessions accept 'absent' status - should succeed."""
        # Get second booking
        bookings = test_db.query(Booking).filter(
            Booking.session_id == tournament_session_with_bookings.id
        ).all()
        booking = bookings[1] if len(bookings) > 1 else bookings[0]

        response = client.post(
            "/api/v1/attendance/",
            json={
                "booking_id": booking.id,
                "user_id": booking.user_id,
                "session_id": booking.session_id,
                "status": "absent"
            },
            headers={"Authorization": f"Bearer {instructor_token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "absent"

    def test_tournament_attendance_late_fails(
        self,
        client: TestClient,
        instructor_token: str,
        tournament_session_with_bookings: SessionModel,
        test_db: Session
    ):
        """
        🔥 CRITICAL TEST: Tournament sessions REJECT 'late' status.

        This is the main test that validates the 2-button rule at API level.
        """
        # Get third booking
        bookings = test_db.query(Booking).filter(
            Booking.session_id == tournament_session_with_bookings.id
        ).all()
        booking = bookings[2] if len(bookings) > 2 else bookings[0]

        response = client.post(
            "/api/v1/attendance/",
            json={
                "booking_id": booking.id,
                "user_id": booking.user_id,
                "session_id": booking.session_id,
                "status": "late"
            },
            headers={"Authorization": f"Bearer {instructor_token}"}
        )

        assert response.status_code == 400
        data = response.json()
        # Custom exception handler wraps errors in {"error": {"message": ...}}
        error_msg = data.get("error", {}).get("message") or data.get("detail", "")
        assert "Tournaments only support" in error_msg
        assert "present" in error_msg
        assert "absent" in error_msg
        assert "late" in error_msg

    def test_tournament_attendance_excused_fails(
        self,
        client: TestClient,
        instructor_token: str,
        tournament_session_with_bookings: SessionModel,
        test_db: Session
    ):
        """
        🔥 CRITICAL TEST: Tournament sessions REJECT 'excused' status.

        This validates that excused is also blocked (only present/absent allowed).
        """
        # Get fourth booking
        bookings = test_db.query(Booking).filter(
            Booking.session_id == tournament_session_with_bookings.id
        ).all()
        booking = bookings[3] if len(bookings) > 3 else bookings[0]

        response = client.post(
            "/api/v1/attendance/",
            json={
                "booking_id": booking.id,
                "user_id": booking.user_id,
                "session_id": booking.session_id,
                "status": "excused"
            },
            headers={"Authorization": f"Bearer {instructor_token}"}
        )

        assert response.status_code == 400
        data = response.json()
        # Custom exception handler wraps errors in {"error": {"message": ...}}
        error_msg = data.get("error", {}).get("message") or data.get("detail", "")
        assert "Tournaments only support" in error_msg
        assert "excused" in error_msg


# ============================================================================
# Regular Session Comparison (all 4 statuses work)
# ============================================================================

@pytest.mark.integration
@pytest.mark.api
class TestRegularSessionAttendanceAPI:
    """Test that regular sessions still accept all 4 attendance statuses."""

    @pytest.fixture
    def regular_session_with_booking(
        self,
        test_db: Session,
        instructor_user: User,
        student_user: User
    ) -> SessionModel:
        """Create a REGULAR (non-tournament) session with a booking."""
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
        test_db.refresh(semester)

        # Create regular session (is_tournament_game=False)
        session_start = datetime.now() + timedelta(days=7, hours=10)
        session = SessionModel(
            title="Regular Training",
            description="Normal training session",
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

        # Create booking
        booking = Booking(
            user_id=student_user.id,
            session_id=session.id,
            status=BookingStatus.CONFIRMED
        )
        test_db.add(booking)
        test_db.commit()

        return session

    def test_regular_session_accepts_all_statuses(
        self,
        client: TestClient,
        instructor_token: str,
        regular_session_with_booking: SessionModel,
        test_db: Session
    ):
        """Regular sessions accept ALL 4 attendance statuses."""
        booking = test_db.query(Booking).filter(
            Booking.session_id == regular_session_with_booking.id
        ).first()

        for status in ["present", "absent", "late", "excused"]:
            response = client.post(
                "/api/v1/attendance/",
                json={
                    "booking_id": booking.id,
                    "user_id": booking.user_id,
                    "session_id": booking.session_id,
                    "status": status
                },
                headers={"Authorization": f"Bearer {instructor_token}"}
            )

            # All statuses should work for regular sessions
            assert response.status_code == 200, f"Status '{status}' failed for regular session"
            data = response.json()
            assert data["status"] == status


# ============================================================================
# Edge Cases
# ============================================================================

@pytest.mark.integration
@pytest.mark.tournament
@pytest.mark.api
class TestTournamentAttendanceEdgeCases:
    """Test edge cases for tournament attendance."""

    def test_tournament_attendance_requires_authentication(
        self,
        client: TestClient,
        tournament_session_with_bookings: SessionModel,
        test_db: Session
    ):
        """Tournament attendance requires authentication."""
        booking = test_db.query(Booking).filter(
            Booking.session_id == tournament_session_with_bookings.id
        ).first()

        response = client.post(
            "/api/v1/attendance/",
            json={
                "booking_id": booking.id,
                "user_id": booking.user_id,
                "session_id": booking.session_id,
                "status": "present"
            }
            # No Authorization header
        )

        assert response.status_code == 401

    def test_tournament_attendance_requires_instructor_role(
        self,
        client: TestClient,
        student_token: str,
        tournament_session_with_bookings: SessionModel,
        test_db: Session
    ):
        """Tournament attendance requires instructor/admin role."""
        booking = test_db.query(Booking).filter(
            Booking.session_id == tournament_session_with_bookings.id
        ).first()

        response = client.post(
            "/api/v1/attendance/",
            json={
                "booking_id": booking.id,
                "user_id": booking.user_id,
                "session_id": booking.session_id,
                "status": "present"
            },
            headers={"Authorization": f"Bearer {student_token}"}
        )

        # Should fail - students can't mark attendance
        assert response.status_code == 403

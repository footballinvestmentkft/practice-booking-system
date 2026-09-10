"""
Contract tests — Booking schemas

Validates that Booking, BookingWithRelations, and BookingList preserve required
fields, types, and defaults. Breaks immediately on any field rename or removal
in app/schemas/booking.py without requiring a DB or server.
"""

from app.main import app
from app.schemas.booking import (
    Booking,
    BookingList,
    BookingWithRelations,
    PlayerSessionAvailability,
)


class TestBookingContract:
    """Contract: POST /api/v1/bookings/ response body (Booking schema)."""

    def test_booking_has_id_field(self):
        assert "id" in Booking.model_fields

    def test_booking_has_user_id_field(self):
        assert "user_id" in Booking.model_fields

    def test_booking_has_session_id_field(self):
        assert "session_id" in Booking.model_fields

    def test_booking_has_status_field(self):
        assert "status" in Booking.model_fields

    def test_booking_has_created_at_field(self):
        assert "created_at" in Booking.model_fields

    def test_booking_id_is_required(self):
        assert Booking.model_fields["id"].is_required()

    def test_booking_user_id_is_required(self):
        assert Booking.model_fields["user_id"].is_required()

    def test_booking_session_id_is_required(self):
        assert Booking.model_fields["session_id"].is_required()

    def test_booking_status_is_required(self):
        assert Booking.model_fields["status"].is_required()

    def test_booking_created_at_is_required(self):
        assert Booking.model_fields["created_at"].is_required()

    def test_booking_waitlist_position_is_optional(self):
        # waitlist_position has default=None (not required)
        assert not Booking.model_fields["waitlist_position"].is_required()

    def test_booking_cancelled_at_is_optional(self):
        assert not Booking.model_fields["cancelled_at"].is_required()


class TestBookingWithRelationsContract:
    """Contract: booking list item with embedded user/session (BookingWithRelations schema)."""

    def test_booking_with_relations_has_user_field(self):
        assert "user" in BookingWithRelations.model_fields

    def test_booking_with_relations_has_session_field(self):
        assert "session" in BookingWithRelations.model_fields

    def test_booking_with_relations_inherits_id(self):
        # Inherits from Booking — id must still be present
        assert "id" in BookingWithRelations.model_fields

    def test_booking_with_relations_inherits_status(self):
        assert "status" in BookingWithRelations.model_fields


class TestBookingListContract:
    """Contract: GET /api/v1/bookings/ paginated response (BookingList schema)."""

    def test_booking_list_has_bookings_field(self):
        assert "bookings" in BookingList.model_fields

    def test_booking_list_has_total_field(self):
        assert "total" in BookingList.model_fields

    def test_booking_list_has_page_field(self):
        assert "page" in BookingList.model_fields

    def test_booking_list_has_size_field(self):
        assert "size" in BookingList.model_fields

    def test_booking_list_bookings_is_required(self):
        assert BookingList.model_fields["bookings"].is_required()

    def test_booking_list_total_is_required(self):
        assert BookingList.model_fields["total"].is_required()

    def test_booking_list_page_is_required(self):
        assert BookingList.model_fields["page"].is_required()

    def test_booking_list_size_is_required(self):
        assert BookingList.model_fields["size"].is_required()


class TestPlayerParticipationContract:
    def test_availability_projection_has_canonical_lifecycle_fields(self):
        assert set(PlayerSessionAvailability.model_fields) == {
            "session_id", "title", "date_start", "date_end", "category",
            "capacity", "confirmed", "available", "waitlisted", "booking_id",
            "participation_status",
        }

    def test_openapi_exposes_shared_player_participation_routes(self):
        paths = app.openapi()["paths"]
        assert "get" in paths["/api/v1/sessions/player/available"]
        assert "post" in paths["/api/v1/bookings/"]
        assert "delete" in paths["/api/v1/bookings/{booking_id}"]

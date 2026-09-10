import XCTest
@testable import LFAEducationCenter

final class PlayerIdentityContractTests: XCTestCase {
    func testPlayerLicenseDecodesCanonicalSeasonAssignment() throws {
        let json = """
        {
          "id": 41,
          "user_id": 7,
          "specialization_type": "LFA_FOOTBALL_PLAYER",
          "canonical_program_id": "LFA_FOOTBALL_PLAYER",
          "current_level": 1,
          "is_active": true,
          "onboarding_completed": false,
          "started_at": "2026-09-10T10:00:00Z",
          "expires_at": "2026-10-10T10:00:00Z",
          "season_start": "2026-07-01",
          "season_end": "2027-06-30",
          "season_base_category": "YOUTH",
          "effective_category": "AMATEUR",
          "base_participation_retained": true
        }
        """.data(using: .utf8)!

        let license = try JSONDecoder().decode(LFAPlayerLicense.self, from: json)

        XCTAssertEqual(license.canonicalProgramId, "LFA_FOOTBALL_PLAYER")
        XCTAssertEqual(license.seasonStart, "2026-07-01")
        XCTAssertEqual(license.seasonEnd, "2027-06-30")
        XCTAssertEqual(license.seasonBaseCategory, "YOUTH")
        XCTAssertEqual(license.effectiveCategory, "AMATEUR")
        XCTAssertEqual(license.baseParticipationRetained, true)
    }

    func testHistoricalLicenseWithoutAssignmentRemainsDecodable() throws {
        let json = """
        {
          "id": 42,
          "user_id": 8,
          "specialization_type": "LFA_FOOTBALL_PLAYER",
          "canonical_program_id": "LFA_FOOTBALL_PLAYER",
          "current_level": 1,
          "is_active": true,
          "onboarding_completed": true,
          "started_at": null,
          "expires_at": null,
          "season_start": null,
          "season_end": null,
          "season_base_category": null,
          "effective_category": null,
          "base_participation_retained": null
        }
        """.data(using: .utf8)!

        let license = try JSONDecoder().decode(LFAPlayerLicense.self, from: json)

        XCTAssertNil(license.seasonBaseCategory)
        XCTAssertNil(license.effectiveCategory)
    }

    func testPlayerSessionAvailabilityDecodesCanonicalLifecycleProjection() throws {
        let json = """
        {
          "session_id": 91,
          "title": "Player training",
          "date_start": "2026-10-02T18:00:00",
          "date_end": "2026-10-02T19:30:00",
          "category": "AMATEUR",
          "capacity": 12,
          "confirmed": 8,
          "available": 4,
          "waitlisted": 0,
          "booking_id": null,
          "participation_status": "available"
        }
        """.data(using: .utf8)!

        let session = try JSONDecoder().decode(PlayerSessionAvailabilityDTO.self, from: json)

        XCTAssertEqual(session.sessionId, 91)
        XCTAssertEqual(session.category, "AMATEUR")
        XCTAssertEqual(session.available, 4)
        XCTAssertEqual(session.participationStatus, "available")
        XCTAssertEqual(PlayerParticipationAPI.availabilityPath, "/api/v1/sessions/player/available")
        XCTAssertEqual(PlayerParticipationAPI.bookingPath, "/api/v1/bookings/")
    }

    func testRepeatedCancellationResponseRemainsDecodable() throws {
        let json = """
        {
          "message": "Booking cancelled successfully",
          "cancelled_booking_id": 44,
          "session_id": 91,
          "replayed": true
        }
        """.data(using: .utf8)!

        let result = try JSONDecoder().decode(PlayerBookingCancellationDTO.self, from: json)

        XCTAssertEqual(result.cancelledBookingId, 44)
        XCTAssertEqual(result.sessionId, 91)
        XCTAssertTrue(result.replayed)
        XCTAssertNil(result.promotedBookingId)
    }
}

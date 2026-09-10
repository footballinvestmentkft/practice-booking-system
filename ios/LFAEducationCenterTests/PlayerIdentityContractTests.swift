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
}

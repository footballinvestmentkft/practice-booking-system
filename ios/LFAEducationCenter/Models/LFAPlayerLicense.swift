import Foundation

// Decoded from GET /api/v1/lfa-player/licenses/me.
//
// This replaces the old GET /api/v1/licenses/me for LFA Player flow.
// The old /licenses/me returns GānCuju COACH/PLAYER/INTERNSHIP licenses
// with a different schema (specialization_type, current_level etc.) —
// that system is separate from the LFA Football Player license.
struct LFAPlayerLicense: Decodable {
    let id:                  Int
    let userId:              Int
    let specializationType:  String   // "LFA_FOOTBALL_PLAYER"
    let canonicalProgramId:   String
    let currentLevel:        Int
    let isActive:            Bool
    let onboardingCompleted: Bool
    let startedAt:           String?
    let expiresAt:           String?  // ISO 8601 — nil means perpetual (no expiry set)
    let seasonStart:          String?
    let seasonEnd:            String?
    let seasonBaseCategory:   String?
    let effectiveCategory:    String?
    let baseParticipationRetained: Bool?

    // True when expiresAt is set and that date is in the past.
    // nil expiresAt is treated as perpetual — never expired.
    var isExpired: Bool {
        guard let s = expiresAt else { return false }
        let formats = [
            "yyyy-MM-dd'T'HH:mm:ssZZZZZ",
            "yyyy-MM-dd'T'HH:mm:ss.SSSSSSZZZZZ",
            "yyyy-MM-dd'T'HH:mm:ss",
        ]
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        for fmt in formats {
            f.dateFormat = fmt
            if let d = f.date(from: s) { return d < Date() }
        }
        return false
    }

    // Human-readable expiry label for UI display.
    // Returns nil when expires_at is not set (perpetual licence).
    var expiryDisplayString: String? {
        guard let s = expiresAt else { return nil }
        let formats = [
            "yyyy-MM-dd'T'HH:mm:ssZZZZZ",
            "yyyy-MM-dd'T'HH:mm:ss.SSSSSSZZZZZ",
            "yyyy-MM-dd'T'HH:mm:ss",
        ]
        let parser = DateFormatter()
        parser.locale = Locale(identifier: "en_US_POSIX")
        let display = DateFormatter()
        display.dateStyle = .medium
        display.timeStyle = .none
        for fmt in formats {
            parser.dateFormat = fmt
            if let d = parser.date(from: s) { return display.string(from: d) }
        }
        return nil
    }

    enum CodingKeys: String, CodingKey {
        case id
        case userId              = "user_id"
        case specializationType  = "specialization_type"
        case canonicalProgramId  = "canonical_program_id"
        case currentLevel        = "current_level"
        case isActive            = "is_active"
        case onboardingCompleted = "onboarding_completed"
        case startedAt           = "started_at"
        case expiresAt           = "expires_at"
        case seasonStart          = "season_start"
        case seasonEnd            = "season_end"
        case seasonBaseCategory   = "season_base_category"
        case effectiveCategory    = "effective_category"
        case baseParticipationRetained = "base_participation_retained"
    }
}

// Canonical Player participation contract. Policy decisions remain server-side;
// native clients consume the same eligibility projection and mutation endpoints.
struct PlayerSessionAvailabilityDTO: Decodable {
    let sessionId: Int
    let title: String
    let dateStart: String
    let dateEnd: String
    let category: String
    let capacity: Int?
    let confirmed: Int
    let available: Int?
    let waitlisted: Int
    let bookingId: Int?
    let participationStatus: String

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case title
        case dateStart = "date_start"
        case dateEnd = "date_end"
        case category, capacity, confirmed, available, waitlisted
        case bookingId = "booking_id"
        case participationStatus = "participation_status"
    }
}

struct PlayerBookingRequest: Encodable {
    let sessionId: Int
    let notes: String?

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case notes
    }
}

struct PlayerBookingDTO: Decodable {
    let id: Int
    let userId: Int
    let sessionId: Int
    let status: String
    let waitlistPosition: Int?
    let notes: String?
    let createdAt: String
    let updatedAt: String?
    let cancelledAt: String?
    let attendedStatus: String?

    enum CodingKeys: String, CodingKey {
        case id
        case userId = "user_id"
        case sessionId = "session_id"
        case status
        case waitlistPosition = "waitlist_position"
        case notes
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case cancelledAt = "cancelled_at"
        case attendedStatus = "attended_status"
    }
}

struct PlayerBookingCancellationDTO: Decodable {
    let message: String
    let cancelledBookingId: Int
    let sessionId: Int
    let replayed: Bool
    let promotedBookingId: Int?

    enum CodingKeys: String, CodingKey {
        case message
        case cancelledBookingId = "cancelled_booking_id"
        case sessionId = "session_id"
        case replayed
        case promotedBookingId = "promoted_booking_id"
    }
}

enum PlayerParticipationAPI {
    static let availabilityPath = "/api/v1/sessions/player/available"
    static let bookingPath = "/api/v1/bookings/"

    static func availableSessions(token: String) async throws -> [PlayerSessionAvailabilityDTO] {
        try await APIClient.get(path: availabilityPath, token: token)
    }

    static func book(sessionId: Int, notes: String? = nil, token: String) async throws -> PlayerBookingDTO {
        try await APIClient.post(
            path: bookingPath,
            body: PlayerBookingRequest(sessionId: sessionId, notes: notes),
            token: token
        )
    }

    static func cancel(bookingId: Int, token: String) async throws -> PlayerBookingCancellationDTO {
        try await APIClient.delete(path: "\(bookingPath)\(bookingId)", token: token)
    }
}

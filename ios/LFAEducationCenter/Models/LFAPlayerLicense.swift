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
    let currentLevel:        Int
    let isActive:            Bool
    let onboardingCompleted: Bool
    let startedAt:           String?
    let expiresAt:           String?  // ISO 8601 — nil means perpetual (no expiry set)

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
        case currentLevel        = "current_level"
        case isActive            = "is_active"
        case onboardingCompleted = "onboarding_completed"
        case startedAt           = "started_at"
        case expiresAt           = "expires_at"
    }
}

import Foundation

// Decoded from GET /api/v1/licenses/me — one entry per specialization the user owns.
// All fields Optional: response schema may evolve; missing fields degrade gracefully.
struct UserLicense: Decodable, Identifiable {
    var id:                 Int?
    let specializationCode: String?  // "lfa"
    let specializationName: String?  // "LFA Football Player"
    let isActive:           Bool?
    let isOnboarded:        Bool?
    let level:              Int?
    let xp:                 Int?

    enum CodingKeys: String, CodingKey {
        case id
        case specializationCode = "specialization_code"
        case specializationName = "specialization_name"
        case isActive           = "is_active"
        case isOnboarded        = "is_onboarded"
        case level
        case xp
    }

    // Fallback display name
    var displayName: String {
        specializationName ?? specializationCode?.capitalized ?? "License"
    }
}

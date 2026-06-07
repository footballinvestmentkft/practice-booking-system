import Foundation

// Decoded from GET /api/v1/specializations/me.
// Returns the user's currently selected specialization (or nil if not set).
struct SpecializationStatus: Decodable {
    let userId:            Int?
    let hasSpecialization: Bool
    let specialization:    Detail?

    struct Detail: Decodable {
        let code: String
        let name: String
        let icon: String
    }

    enum CodingKeys: String, CodingKey {
        case userId            = "user_id"
        case hasSpecialization = "has_specialization"
        case specialization
    }
}

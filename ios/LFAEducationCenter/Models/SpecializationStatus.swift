import Foundation

// Decoded from GET /api/v1/specializations/me.
// Returns the user's currently selected specialization (or nil if not set).
// hasSpecialization is Optional for resilience — backend always sends Bool, but
// if the schema shifts the view falls back to specialization != nil check.
struct SpecializationStatus: Decodable {
    let userId:            Int?
    let hasSpecialization: Bool?   // Optional: degrade gracefully if field missing
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

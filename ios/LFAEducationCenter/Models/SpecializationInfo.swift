import Foundation

// Decoded from GET /api/v1/specializations/ (public endpoint, no auth required).
// Returns the full catalog of available specializations.
struct SpecializationInfo: Decodable, Identifiable {
    var id: String { code }
    let code:        String
    let name:        String
    let description: String
    let features:    [String]
    let icon:        String
}

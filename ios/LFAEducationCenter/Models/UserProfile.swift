import Foundation

// Decoded from GET /api/v1/users/me.
// Phase D: adds creditBalance and role (Optional — degrade gracefully if absent).
// Phase E+: add dateOfBirth, nationality, xp, etc. as needed.
struct UserProfile: Decodable {
    let email:         String
    let firstName:     String
    let lastName:      String
    let creditBalance: Double?  // credit_balance — may be Float on backend
    let role:          String?  // "STUDENT", "INSTRUCTOR", etc.

    var displayName: String { "\(firstName) \(lastName)" }

    enum CodingKeys: String, CodingKey {
        case email
        case firstName     = "first_name"
        case lastName      = "last_name"
        case creditBalance = "credit_balance"
        case role
    }
}

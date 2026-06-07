import Foundation

struct LoginRequest: Encodable {
    let email:    String
    let password: String
}

// POST /api/v1/auth/refresh request body.
// Backend expects { "refresh_token": "..." } in JSON body (not Authorization header).
struct RefreshRequest: Encodable {
    let refreshToken: String
    enum CodingKeys: String, CodingKey {
        case refreshToken = "refresh_token"
    }
}

// Request body for POST /api/v1/auth/register-with-invitation.
// All fields required by backend — no Optional fields.
// date_of_birth: ISO 8601 string "yyyy-MM-dd'T'HH:mm:ss" (backend accepts datetime).
struct RegisterRequest: Encodable {
    let email:          String
    let password:       String
    let name:           String   // full name = "\(first_name) \(last_name)"
    let firstName:      String
    let lastName:       String
    let nickname:       String
    let phone:          String
    let dateOfBirth:    String   // "2000-05-15T00:00:00"
    let nationality:    String
    let gender:         String
    let streetAddress:  String
    let city:           String
    let postalCode:     String
    let country:        String
    let invitationCode: String

    enum CodingKeys: String, CodingKey {
        case email, password, name, nickname, phone, gender, city, country, nationality
        case firstName      = "first_name"
        case lastName       = "last_name"
        case dateOfBirth    = "date_of_birth"
        case streetAddress  = "street_address"
        case postalCode     = "postal_code"
        case invitationCode = "invitation_code"
    }
}

// POST /api/v1/invitation-codes/validate — public, no auth required.
// Pre-checks a code before registration; does NOT consume it.
struct InviteValidateRequest: Encodable {
    let code: String
}

struct InviteValidateResponse: Decodable {
    let success:      Bool
    let valid:        Bool
    let bonusCredits: Int?
    let invitedName:  String?
    enum CodingKeys: String, CodingKey {
        case success, valid
        case bonusCredits = "bonus_credits"
        case invitedName  = "invited_name"
    }
}

// Shared response for login and refresh — both endpoints return the same shape.
struct AuthResponse: Decodable {
    let accessToken:  String
    let refreshToken: String
    let tokenType:    String

    enum CodingKeys: String, CodingKey {
        case accessToken  = "access_token"
        case refreshToken = "refresh_token"
        case tokenType    = "token_type"
    }
}

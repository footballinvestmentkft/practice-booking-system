import Foundation

// Decoded from GET /api/v1/users/me.
//
// Backend sends "name" (full name, single field) — NOT "first_name"/"last_name".
// credit_balance is Int on the backend (not Float/Double).
// xp_balance added for Phase E display.
// date_of_birth is an ISO 8601 datetime string from the backend User schema.
struct UserProfile: Decodable {
    let id:                  Int?
    let name:                String
    let email:               String
    let role:                String?       // "STUDENT", "INSTRUCTOR", "ADMIN"
    let creditBalance:       Int?          // credit_balance
    let xpBalance:           Int?          // xp_balance — Phase E stat display
    let onboardingCompleted: Bool?
    let position:            String?       // football position
    let dateOfBirth:                 String?       // date_of_birth — ISO 8601
    let profilePhotoUrl:             String?       // profile_photo_url
    let profilePhotoProcessedUrl:    String?       // profile_photo_processed_url (BG-removed PNG)
    let profilePhotoStatus:          String?       // none/uploaded/processing/ready/failed
    // 🪪 Academy ID (Phase 2A) — only present on the owner's own authenticated response
    let lfaAcademyId:                String?       // lfa_academy_id — shown on card, not secret
    let publicToken:                 String?       // UUID for /verify/{token} QR — owner eyes only
    let licenses:                    [UserLicenseBrief]?

    // displayName maps directly to name — no first/last split in the backend schema.
    var displayName: String { name }

    // Age in full years calculated from dateOfBirth against today.
    // Returns nil if dateOfBirth is absent or cannot be parsed.
    var calculatedAge: Int? {
        guard let dob = dateOfBirth else { return nil }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        // Backend Pydantic datetime serialises as "YYYY-MM-DDTHH:mm:ss" (no offset).
        // Accept the date-only form as a fallback.
        for fmt in ["yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd"] {
            formatter.dateFormat = fmt
            if let birth = formatter.date(from: dob) {
                return Calendar.current.dateComponents([.year], from: birth, to: Date()).year
            }
        }
        return nil
    }

    enum CodingKeys: String, CodingKey {
        case id, name, email, role, position
        case creditBalance       = "credit_balance"
        case xpBalance           = "xp_balance"
        case onboardingCompleted = "onboarding_completed"
        case dateOfBirth              = "date_of_birth"
        case profilePhotoUrl          = "profile_photo_url"
        case profilePhotoProcessedUrl = "profile_photo_processed_url"
        case profilePhotoStatus       = "profile_photo_status"
        case lfaAcademyId             = "lfa_academy_id"
        case publicToken              = "public_token"
        case licenses
    }
}

// Embedded license summary inside GET /api/v1/users/me response.
// Full LFA Player license data: use LFAPlayerLicense from /api/v1/lfa-player/licenses/me.
struct UserLicenseBrief: Decodable, Identifiable {
    let id:                  Int
    let specializationType:  String
    let isActive:            Bool
    let paymentVerified:     Bool?
    let onboardingCompleted: Bool?

    enum CodingKeys: String, CodingKey {
        case id
        case specializationType  = "specialization_type"
        case isActive            = "is_active"
        case paymentVerified     = "payment_verified"
        case onboardingCompleted = "onboarding_completed"
    }
}

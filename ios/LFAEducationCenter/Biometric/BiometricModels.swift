import Foundation

// MARK: — Version constants

// Must match CURRENT_BIOMETRIC_DISCLOSURE_VERSION in backend app/config.py.
let kBiometricDisclosureVersion = "v1.0"
let kBiometricConsentVersion    = "v1.0"
let kBiometricChallengeVersion  = "v1.0"
// Only accepted source value for POST /me/biometric-liveness.
let kBiometricLivenessSource    = "onboarding_liveness"

// MARK: — Request models

struct BiometricDisclosureAcceptRequest: Encodable {
    let disclosureVersion: String
    enum CodingKeys: String, CodingKey {
        case disclosureVersion = "disclosure_version"
    }
}

struct BiometricConsentGrantRequest: Encodable {
    let consentVersion: String
    enum CodingKeys: String, CodingKey {
        case consentVersion = "consent_version"
    }
}

struct BiometricConsentRevokeRequest: Encodable {
    let reason: String?
}

// Exactly 5 allowed metadata fields — backend extra="forbid" rejects anything else.
// face_match_score, yaw, roll, device_model, ios_version, landmarks, frames: ABSENT by design.
struct BiometricLivenessMetadata: Encodable {
    let challengeVersion: String
    let stepsCompleted:   [String]
    let totalDurationMs:  Int
    let retryCount:       Int
    let failureReason:    String?

    enum CodingKeys: String, CodingKey {
        case challengeVersion = "challenge_version"
        case stepsCompleted   = "steps_completed"
        case totalDurationMs  = "total_duration_ms"
        case retryCount       = "retry_count"
        case failureReason    = "failure_reason"
    }
}

// POST /me/biometric-liveness — JSON body only.
// Backend has no image upload endpoint; photo_filename is a string basename placeholder.
// DEV/TEST MVP: no JPEG bytes are transmitted. Image upload is out of scope for PR-iOS-1.
struct BiometricLivenessSubmitRequest: Encodable {
    let source:           String
    let livenessMetadata: BiometricLivenessMetadata
    let photoFilename:    String?

    enum CodingKeys: String, CodingKey {
        case source
        case livenessMetadata = "liveness_metadata"
        case photoFilename    = "photo_filename"
    }
}

// POST /me/biometric-verify — JSON body only.
// Backend has no image upload endpoint; photo_filename is a string basename placeholder.
// DEV/TEST MVP: no JPEG bytes are transmitted. Image upload is out of scope for PR-iOS-1.
struct BiometricVerifyRequestBody: Encodable {
    let photoFilename: String?
    enum CodingKeys: String, CodingKey {
        case photoFilename = "photo_filename"
    }
}

// MARK: — Response models

// GET / POST / DELETE /me/biometric-disclosure
// face_match_score: ABSENT — mirrors backend BiometricDisclosureStatusOut structural enforcement.
struct BiometricDisclosureStatus: Decodable {
    let hasDisclosure:   Bool
    let isActive:        Bool
    let acceptedVersion: String?
    let acceptedAt:      String?
    let revokedAt:       String?

    enum CodingKeys: String, CodingKey {
        case hasDisclosure   = "has_disclosure"
        case isActive        = "is_active"
        case acceptedVersion = "accepted_version"
        case acceptedAt      = "accepted_at"
        case revokedAt       = "revoked_at"
    }
}

// GET / POST / DELETE /me/biometric-consent
// face_match_score: ABSENT — mirrors backend BiometricConsentStatusOut structural enforcement.
struct BiometricConsentStatus: Decodable {
    let hasConsent: Bool
    let grantedAt:  String?
    let version:    String?
    let revokedAt:  String?
    let isActive:   Bool

    enum CodingKeys: String, CodingKey {
        case hasConsent = "has_consent"
        case grantedAt  = "granted_at"
        case version
        case revokedAt  = "revoked_at"
        case isActive   = "is_active"
    }
}

// POST /me/biometric-liveness response
// face_match_score: ABSENT — mirrors backend BiometricVerificationStatusOut.
struct BiometricVerificationStatus: Decodable {
    let faceMatchStatus:          String?
    let faceReferencePhotoStatus: String?
    let hasBiometricConsent:      Bool
    let manualReviewRequired:     Bool

    enum CodingKeys: String, CodingKey {
        case faceMatchStatus          = "face_match_status"
        case faceReferencePhotoStatus = "face_reference_photo_status"
        case hasBiometricConsent      = "has_biometric_consent"
        case manualReviewRequired     = "manual_review_required"
    }
}

// POST /me/biometric-verify response
// result: "verified" | "manual_review_required" | "rejected"
// face_match_score: ABSENT — mirrors backend BiometricVerifyResponse structural enforcement.
// face_match_score must never appear in this struct, any view, or any log — not even in debug.
struct BiometricVerifyResult: Decodable {
    let result: String
    // face_match_score intentionally absent — backend Pydantic enforces this; iOS mirrors it.
}

// MARK: — Typed error

// Error detail strings sourced from backend app/services/biometric/*.py and endpoints.
// Only known backend detail values are mapped — unknown detail falls through to .unknown.
enum BiometricClientError: Error {
    case featureDisabled               // 503 — BIOMETRIC_*_ENABLED=false (long string detail)
    case rateLimiterUnavailable        // 503 "biometric_rate_limiter_unavailable"
    case rateLimited                   // 429 "rate_limited"
    case parentalConsentRequired       // 403 "parental_consent_required"
    case disclosureRequired            // 403 "biometric_disclosure_required"
    case disclosureUpdateRequired      // 403 "biometric_disclosure_update_required"
    case consentRequired               // 403 "biometric_consent_required"
    case disclosureAlreadyAccepted     // 409 "disclosure_already_accepted"
    case consentAlreadyActive          // 409 (consent service returns a sentence, not a key)
    case livenessAlreadySubmitted      // 409 "onboarding_liveness_already_submitted"
    case disclosureNotFound            // 404 "biometric_disclosure_not_found"
    case referenceNotFound             // 404 "biometric_reference_not_found"
    case pathTraversalRejected         // 400 "photo_filename_path_traversal"
    case unauthorized                  // 401
    case networkError(Error)
    case unknown(Int, String?)

    var userFacingMessage: String {
        switch self {
        case .featureDisabled:           return "A biometrikus ellenőrzés jelenleg nem elérhető."
        case .rateLimiterUnavailable:    return "A biometrikus rendszer átmenetileg nem elérhető."
        case .rateLimited:               return "Túl sok kísérlet. Kérjük, várj egy percet."
        case .parentalConsentRequired:   return "A biometrikus ellenőrzés 18 éves kor felett érhető el."
        case .disclosureRequired:        return "A tájékoztató elfogadása szükséges."
        case .disclosureUpdateRequired:  return "A tájékoztató megváltozott. Kérjük, fogadd el újra."
        case .consentRequired:           return "A hozzájárulás megadása szükséges."
        case .disclosureAlreadyAccepted: return "A tájékoztató már el lett fogadva."
        case .consentAlreadyActive:      return "A hozzájárulás már megadásra került."
        case .livenessAlreadySubmitted:  return "A liveness teszt már elvégzésre került."
        case .disclosureNotFound:        return "Nincs aktív tájékoztató visszavonni."
        case .referenceNotFound:         return "Nincs tárolt referencia kép. Végezd el a liveness tesztet."
        case .pathTraversalRejected:     return "Érvénytelen fájlnév."
        case .unauthorized:              return "A munkamenet lejárt. Kérjük, jelentkezz be újra."
        case .networkError:              return "Hálózati hiba. Ellenőrizd a kapcsolatot."
        case .unknown(let code, let d):  return d ?? "Ismeretlen hiba (\(code))."
        }
    }

    static func from(_ error: Error) -> BiometricClientError {
        switch error {
        case APIError.unauthorized:
            return .unauthorized
        case APIError.networkError(let e):
            return .networkError(e)
        case APIError.httpError(let code, let detail):
            switch (code, detail) {
            case (503, "biometric_rate_limiter_unavailable"): return .rateLimiterUnavailable
            case (503, _):                                    return .featureDisabled
            case (429, _):                                    return .rateLimited
            case (403, "parental_consent_required"):          return .parentalConsentRequired
            case (403, "biometric_disclosure_required"):      return .disclosureRequired
            case (403, "biometric_disclosure_update_required"): return .disclosureUpdateRequired
            case (403, "biometric_consent_required"):         return .consentRequired
            case (409, "disclosure_already_accepted"):        return .disclosureAlreadyAccepted
            case (409, "onboarding_liveness_already_submitted"): return .livenessAlreadySubmitted
            case (409, _):                                    return .consentAlreadyActive
            case (404, "biometric_disclosure_not_found"):     return .disclosureNotFound
            case (404, "biometric_reference_not_found"):      return .referenceNotFound
            case (400, "photo_filename_path_traversal"):      return .pathTraversalRejected
            case (401, _):                                    return .unauthorized
            default:                                          return .unknown(code, detail)
            }
        default:
            return .networkError(error)
        }
    }
}

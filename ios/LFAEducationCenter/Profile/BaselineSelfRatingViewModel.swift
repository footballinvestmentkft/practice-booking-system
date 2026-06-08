import Foundation

// MARK: — Request / Response

private struct SelfAssessmentRequest: Encodable {
    let skills: [String: Int]
}

private struct SelfAssessmentResponse: Decodable {
    let success:            Bool
    let selfAssessmentAverage: Double?
    enum CodingKeys: String, CodingKey {
        case success
        case selfAssessmentAverage = "self_assessment_average"
    }
}

// MARK: — State

enum SelfAssessmentState {
    case idle
    case saving
    case success
    case error(String)
}

// MARK: — ViewModel

// Manages the 44-skill baseline self-rating form.
// POSTs to POST /api/v1/lfa-player/self-assessment.
// Scale: 0–99 (integer), default: 60 (SYSTEM_BASELINE).
// Does NOT modify current_level, system_baseline, OVR, or onboarding_completed.
@MainActor
final class BaselineSelfRatingViewModel: ObservableObject {

    // One Double per skill key — Double for SwiftUI Slider compatibility.
    // Initialised to 60.0 (backend SYSTEM_BASELINE).
    @Published var ratings: [String: Double] = {
        Dictionary(uniqueKeysWithValues: SkillConfig.allKeys.map { ($0, 60.0) })
    }()

    @Published private(set) var state: SelfAssessmentState = .idle

    // MARK: — Save

    func save(using authManager: AuthManager) async {
        state = .saving

        let intRatings = ratings.mapValues { Int($0.rounded()) }
        let body = SelfAssessmentRequest(skills: intRatings)

        do {
            let response: SelfAssessmentResponse = try await authManager.authenticatedPost(
                path: "/api/v1/lfa-player/self-assessment",
                body: body
            )
            state = response.success ? .success : .error("Could not save. Please try again.")
        } catch APIError.httpError(let code, let detail) {
            switch code {
            case 400: state = .error(detail ?? "Invalid data. Please check your inputs.")
            case 401: state = .error("Your session has expired. Please sign in again.")
            case 404: state = .error("No active LFA Football Player license found.")
            default:  state = .error("Save failed (error \(code)). Please try again.")
            }
        } catch APIError.unauthorized {
            state = .error("Your session has expired. Please sign in again.")
        } catch {
            state = .error("Network error. Check your connection and try again.")
        }
    }

    func reset() { state = .idle }
}

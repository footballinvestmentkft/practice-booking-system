import Foundation

// MARK: — API types

// POST /specialization/lfa-player/onboarding-submit
// Auth: Bearer JWT (same as all authenticated API calls)
// Body: JSON  (endpoint uses await request.json())
private struct OnboardingRequest: Encodable {
    let position:      String
    let positions:     [String]
    let skills:        [String: Int]
    let footDominance: Double
    let heightCm:      Int
    let weightKg:      Int
    let preferredFoot: String
    let goals:         String
    let motivation:    String

    enum CodingKeys: String, CodingKey {
        case position, positions, skills, goals, motivation
        case footDominance = "foot_dominance"
        case heightCm      = "height_cm"
        case weightKg      = "weight_kg"
        case preferredFoot = "preferred_foot"
    }
}

private struct OnboardingResponse: Decodable {
    let success: Bool
    let message: String?
}

// MARK: — ViewModel

// Manages LFA Player minimum onboarding form state and API submission.
//
// Submit sends:
//   - primary + secondary positions (canonical snake_case)
//   - height_cm, weight_kg, preferred_foot, foot_dominance
//   - 44 skill keys × 50 (neutral default; no self-assessment UI in R3C)
//
// On success, call dashboardVM.reload() — lfaCardState transitions .setupPending → .active.
@MainActor
final class LFAOnboardingViewModel: ObservableObject {

    // MARK: — Position

    @Published var primaryPosition:    FootballPosition? = nil
    @Published var secondaryPositions: [FootballPosition] = []

    // MARK: — Physique

    @Published var heightCm:      Double = 175    // slider range 120-230
    @Published var weightKg:      Double = 72     // slider range 35-160
    @Published var preferredFoot: String = "right"   // "left" | "right" | "both"
    @Published var footDominance: Double = 50.0       // 0=full left, 100=full right

    // MARK: — Submit state

    enum SubmitState {
        case idle, loading, success, error(String)
    }
    @Published private(set) var submitState: SubmitState = .idle

    // MARK: — Validation

    var canSubmit: Bool { primaryPosition != nil }

    // MARK: — Submit

    func submit(using authManager: AuthManager) async {
        guard case .idle = submitState else { return }    // duplicate-tap guard
        guard let primary = primaryPosition else { return }

        submitState = .loading

        let allPositions = ([primary] + secondaryPositions).map { $0.id }
        let request = OnboardingRequest(
            position:      primary.id,
            positions:     allPositions,
            skills:        Self.defaultSkillPayload,
            footDominance: footDominance,
            heightCm:      Int(heightCm.rounded()),
            weightKg:      Int(weightKg.rounded()),
            preferredFoot: preferredFoot,
            goals:         "",
            motivation:    ""
        )

        do {
            let _: OnboardingResponse = try await authManager.authenticatedPost(
                path: "/specialization/lfa-player/onboarding-submit",
                body: request
            )
            submitState = .success

        } catch APIError.httpError(let code, let detail) where code == 422 {
            submitState = .error(detail ?? "Validation error. Please check your inputs.")

        } catch APIError.httpError(let code, let detail) where code == 500 {
            submitState = .error(detail ?? "Server error. Please try again.")

        } catch APIError.unauthorized {
            submitState = .error("Session expired. Please sign in again.")

        } catch {
            submitState = .error("Network error. Please check your connection and try again.")
        }
    }

    func reset() { submitState = .idle }

    // MARK: — Default 44×50 skill payload

    // The backend stores self_assessment separately from current_level (60.0 baseline).
    // Sending 50 for all skills means "neutral / not yet self-assessed" in R3C.
    // R3E (skill sliders) will replace this with user-assessed values.
    static let defaultSkillPayload: [String: Int] = Dictionary(
        uniqueKeysWithValues: skillKeys.map { ($0, 50) }
    )

    // 44 keys from app/skills_config.py get_all_skill_keys() — must match exactly.
    static let skillKeys: [String] = [
        "ball_control", "dribbling", "finishing", "shot_power", "long_shots",
        "volleys", "crossing", "passing", "heading", "tackle",
        "marking", "shooting", "technique", "creativity", "long_passing",
        "flair", "touch", "forward_runs", "throwing", "free_kicks",
        "corners", "penalties", "positioning_off", "positioning_def", "vision",
        "aggression", "reactions", "composure", "consistency", "tactical_awareness",
        "anticipation", "concentration", "decisions", "determination", "teamwork",
        "leadership", "acceleration", "sprint_speed", "agility", "jumping",
        "strength", "stamina", "balance", "work_rate",
    ]
}

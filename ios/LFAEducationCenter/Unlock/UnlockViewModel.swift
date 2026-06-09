import Foundation

// Decoded from POST /specialization/unlock (200 OK).
private struct UnlockResponse: Decodable {
    let success:        Bool
    let message:        String?
    let newBalance:     Int?
    let licenseId:      Int?
    let durationMonths: Int?
    let cost:           Int?
    let expiresAt:      String?   // ISO 8601 — nil only on legacy/error paths

    enum CodingKeys: String, CodingKey {
        case success, message
        case newBalance     = "new_balance"
        case licenseId      = "license_id"
        case durationMonths = "duration_months"
        case cost
        case expiresAt      = "expires_at"
    }
}

// Manages the specialization unlock POST flow.
//
// Endpoint: POST /specialization/unlock
//   Body: form-encoded  specialization=LFA_PLAYER&duration_months=<1|3|6|12>
//   Auth: Bearer token (same as all API calls)
//
// State machine:
//   .idle    → user sees confirm UI with duration selector
//   .loading → request in-flight, duplicate tap blocked
//   .success → dashboard reload triggered, view auto-dismisses
//   .error   → message shown, user can reset and retry
//
// 409 Conflict (already unlocked) is treated as success — the intent is fulfilled.
@MainActor
final class UnlockViewModel: ObservableObject {

    enum State {
        case idle
        case loading
        case success(newBalance: Int)
        case error(String)
    }

    @Published private(set) var state: State = .idle

    // MARK: — Perform unlock

    func performUnlock(using authManager: AuthManager, durationMonths: Int) async {
        guard case .idle = state else { return }    // duplicate-tap guard
        state = .loading

        do {
            let response: UnlockResponse = try await authManager.authenticatedFormPost(
                path:   "/specialization/unlock",
                fields: [
                    "specialization":  "LFA_PLAYER",
                    "duration_months": "\(durationMonths)",
                ]
            )
            state = .success(newBalance: response.newBalance ?? 0)

        } catch APIError.httpError(let code, _) where code == 409 {
            // Already unlocked — intent fulfilled, treat as success.
            state = .success(newBalance: 0)

        } catch APIError.httpError(let code, let detail) where code == 400 {
            state = .error(detail ?? "Insufficient credits. Please add more credits and try again.")

        } catch APIError.httpError(let code, let detail) where code == 403 {
            state = .error(detail ?? "Age requirement not met for LFA Football Player.")

        } catch APIError.unauthorized {
            state = .error("Session expired. Please sign in again.")

        } catch {
            state = .error("Network error. Please check your connection and try again.")
        }
    }

    // MARK: — Reset (allows retry from error state)

    func reset() { state = .idle }
}

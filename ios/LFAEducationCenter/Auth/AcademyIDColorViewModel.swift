import Foundation
import UIKit

// ViewModel for the Academy ID card colour picker.
//
// Loads the colour palette from GET /api/v1/users/me/academy-id/colors.
// Persists the active selection via POST /api/v1/users/me/academy-id/colors/select.
// Purchases premium colours via POST /api/v1/users/me/academy-id/colors/unlock.
//
// select(): optimistic update + rollback on error.
// unlock(): non-optimistic — waits for API response, then updates ownership
//           and auto-selects the colour on success.
//
// Error handling: pattern-match on APIError type/statusCode, never on
// localizedDescription strings — those don't contain the HTTP status code.

@MainActor
final class AcademyIDColorViewModel: ObservableObject {

    @Published private(set) var colors:        [AcademyIDColorTheme] = []
    @Published private(set) var activeColorId: String                = "official"
    @Published private(set) var isLoading:     Bool                  = false
    @Published private(set) var isUnlocking:   Bool                  = false
    @Published           var errorMessage:     String?               = nil

    // MARK: — Load (no-op if already loaded)

    func load(using authManager: AuthManager) async {
        guard colors.isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let response: AcademyIDColorsResponse = try await authManager.authenticatedGet(
                path: "/api/v1/users/me/academy-id/colors"
            )
            colors        = response.colors.sorted { $0.sortOrder < $1.sortOrder }
            activeColorId = response.activeColorId
        } catch {
            errorMessage = "Could not load card styles."
        }
    }

    // MARK: — Select (optimistic + rollback)

    func select(colorId: String, using authManager: AuthManager) async {
        guard colorId != activeColorId else { return }
        let previous  = activeColorId
        activeColorId = colorId
        errorMessage  = nil
        do {
            struct Payload:  Encodable  { let colorId: String
                enum CodingKeys: String, CodingKey { case colorId = "color_id" } }
            struct Response: Decodable  { let ok: Bool; let activeColorId: String
                enum CodingKeys: String, CodingKey { case ok; case activeColorId = "active_color_id" } }
            let _: Response = try await authManager.authenticatedPost(
                path: "/api/v1/users/me/academy-id/colors/select",
                body: Payload(colorId: colorId)
            )
        } catch let err as APIError {
            activeColorId = previous
            switch err {
            case .networkError:
                errorMessage = "Could not apply style. Check your connection."
            case .httpError(403, _):
                // color_not_owned — frontend should prevent this, but handle gracefully
                errorMessage = "Unlock this style first."
            default:
                errorMessage = "Could not apply style. Please try again."
            }
        } catch {
            activeColorId = previous
            errorMessage  = "Could not apply style. Please try again."
        }
    }

    // MARK: — Unlock (purchase + auto-select)
    //
    // Returns true on success so the caller (confirmation alert) can dismiss.
    // Error mapping is based on HTTP statusCode, never on localizedDescription.

    @discardableResult
    func unlock(colorId: String, using authManager: AuthManager) async -> Bool {
        guard !isUnlocking else { return false }
        isUnlocking  = true
        errorMessage = nil
        defer { isUnlocking = false }

        do {
            struct Payload: Encodable { let colorId: String
                enum CodingKeys: String, CodingKey { case colorId = "color_id" } }

            struct UnlockResponse: Decodable {
                let ok:             Bool
                let colorId:        String
                let alreadyOwned:   Bool
                let creditsCharged: Int
                let balanceAfter:   Int
                enum CodingKeys: String, CodingKey {
                    case ok
                    case colorId        = "color_id"
                    case alreadyOwned   = "already_owned"
                    case creditsCharged = "credits_charged"
                    case balanceAfter   = "balance_after"
                }
            }

            let result: UnlockResponse = try await authManager.authenticatedPost(
                path: "/api/v1/users/me/academy-id/colors/unlock",
                body: Payload(colorId: colorId)
            )

            guard result.ok else { return false }

            // Mark owned in local state
            colors = colors.map { theme in
                guard theme.id == colorId else { return theme }
                return AcademyIDColorTheme(
                    id:         theme.id,
                    label:      theme.label,
                    dotColor:   theme.dotColor,
                    isPremium:  theme.isPremium,
                    creditCost: theme.creditCost,
                    isOwned:    true,
                    sortOrder:  theme.sortOrder
                )
            }

            // Auto-select the newly unlocked colour
            await select(colorId: colorId, using: authManager)
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            return true

        } catch let err as APIError {
            errorMessage = Self.unlockErrorMessage(for: err)
            return false
        } catch {
            // Non-HTTP errors (task cancellation, etc.)
            errorMessage = "Could not unlock style. Please try again."
            return false
        }
    }

    // MARK: — Error message mapping

    private static func unlockErrorMessage(for error: APIError) -> String {
        switch error {
        case .networkError:
            return "Could not unlock style. Check your connection."
        case .httpError(let status, _):
            switch status {
            case 402:
                return "Not enough credits. You need 300 CR to unlock this style."
            case 403:
                // color_not_owned returned by backend — shouldn't happen normally
                // since frontend checks ownership before showing the unlock alert
                return "This style is already locked. Unlock it first."
            case 404:
                // No LFA Football Player licence found
                return "Activate your Academy ID before unlocking premium styles."
            case 400:
                // color_is_free or color_unknown — UI should prevent these
                return "This style cannot be unlocked."
            default:
                return "Could not unlock style. Please try again."
            }
        case .unauthorized:
            return "Session expired. Please log in again."
        default:
            return "Could not unlock style. Please try again."
        }
    }

    // MARK: — Convenience

    func theme(for colorId: String) -> AcademyIDColorTheme? {
        colors.first { $0.id == colorId }
    }
}

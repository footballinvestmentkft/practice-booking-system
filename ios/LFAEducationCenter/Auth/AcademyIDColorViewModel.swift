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
        } catch {
            activeColorId = previous
            errorMessage  = "Could not apply style. Check your connection."
        }
    }

    // MARK: — Unlock (purchase + auto-select)
    //
    // Returns true on success so the caller (confirmation alert) can dismiss.

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

        } catch {
            let description = error.localizedDescription
            if description.contains("402") || description.contains("insufficient") {
                errorMessage = "Not enough credits to unlock this style."
            } else {
                errorMessage = "Could not unlock style. Check your connection."
            }
            return false
        }
    }

    // MARK: — Convenience

    func theme(for colorId: String) -> AcademyIDColorTheme? {
        colors.first { $0.id == colorId }
    }
}

import Foundation

// Dashboard load state.
enum DashboardLoadState: Equatable {
    case idle
    case loading
    case loaded
    case error(String)

    static func == (lhs: DashboardLoadState, rhs: DashboardLoadState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.loading, .loading), (.loaded, .loaded): return true
        case (.error(let a), .error(let b)):                           return a == b
        default:                                                        return false
        }
    }
}

// Fetches user profile + license data and drives DashboardView state.
//
// Requests are sequential (not concurrent) to avoid the dual-401 race condition:
// if both GET /users/me and GET /licenses/me get 401 concurrently, AuthManager's
// isRefreshing flag would block the second refresh attempt, causing it to throw
// .unauthorized before the first refresh completes. Sequential requests mean the
// second request always uses the refreshed token from the first. (Phase E: use an
// actor-based shared refresh task to support safe concurrent requests.)
//
// /api/v1/licenses/me is treated as non-fatal: if it fails the profile still shows.
// /api/v1/licenses/dashboard schema is not yet confirmed — deferred to Phase E.
@MainActor
final class DashboardViewModel: ObservableObject {

    @Published private(set) var loadState: DashboardLoadState = .idle
    @Published private(set) var profile:   UserProfile?       = nil
    @Published private(set) var licenses:  [UserLicense]      = []

    // MARK: — Load (initial, guarded)

    func load(using authManager: AuthManager) async {
        guard case .idle = loadState else { return }
        await fetchData(using: authManager)
    }

    // MARK: — Reload (manual retry, resets state)

    func reload(using authManager: AuthManager) async {
        loadState = .idle
        profile   = nil
        licenses  = []
        await fetchData(using: authManager)
    }

    // MARK: — Reset (called on logout so next login fetches fresh data)

    func reset() {
        loadState = .idle
        profile   = nil
        licenses  = []
    }

    // MARK: — Private

    private func fetchData(using authManager: AuthManager) async {
        loadState = .loading

        do {
            // Sequential: ensures the second request uses a refreshed token
            // if the first triggered a 401 → refresh cycle.
            profile = try await authManager.authenticatedGet(path: "/api/v1/users/me")

            // Non-fatal: licenses missing is tolerable — show empty state in UI.
            licenses = (try? await authManager.authenticatedGet(path: "/api/v1/licenses/me")) ?? []

            // /api/v1/licenses/dashboard schema not confirmed — deferred to Phase E.
            // It returns Dict[str, Any] from license_service.get_user_license_dashboard().
            // Placeholder card shown in DashboardView until schema is confirmed.

            loadState = .loaded

        } catch APIError.unauthorized {
            // AuthManager.performRefresh() already called logout() →
            // RootView reacts to isLoggedIn = false → LoginView appears.
            loadState = .idle

        } catch {
            loadState = .error("Could not load dashboard. Check your connection.")
        }
    }
}

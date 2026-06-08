import Foundation

// ViewModel for CreditsView.
//
// Credit balance comes from DashboardViewModel.profile.creditBalance (already loaded).
//
// Transaction history: GET /api/v1/users/me/credit-transactions?limit=50
//   User-level endpoint — works for all users regardless of LFA license status.
//   Returns CreditTransactionPage wrapper {"transactions":[...], "total_count", "credit_balance"}.
@MainActor
final class CreditsViewModel: ObservableObject {

    enum LoadState {
        case idle
        case loading
        case loaded(CreditTransactionPage)
        case error(String)
    }

    @Published private(set) var loadState: LoadState = .idle

    var transactions: [CreditTransaction] {
        if case .loaded(let page) = loadState { return page.transactions }
        return []
    }

    // MARK: — Load (guarded)

    func load(using authManager: AuthManager) async {
        guard case .idle = loadState else { return }
        await fetch(using: authManager)
    }

    func reload(using authManager: AuthManager) async {
        loadState = .idle
        await fetch(using: authManager)
    }

    // MARK: — Private

    private func fetch(using authManager: AuthManager) async {
        loadState = .loading
        do {
            let page: CreditTransactionPage = try await authManager.authenticatedGet(
                path: "/api/v1/users/me/credit-transactions?limit=50"
            )
            loadState = .loaded(page)
        } catch {
            loadState = .error("Could not load transaction history.")
        }
    }
}

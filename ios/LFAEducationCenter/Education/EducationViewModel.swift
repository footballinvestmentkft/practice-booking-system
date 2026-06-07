import Foundation

// Education Center data layer.
//
// Endpoint mapping:
//   /api/v1/specializations/me            → status  (fatal — determines what to show)
//   /api/v1/specializations/              → availableSpecs (non-fatal, public catalog)
//   /api/v1/specializations/progress/me  → progressData (non-fatal, silent-fail OK)
//   /api/v1/lfa-player/licenses/me        → lfaLicense (non-fatal, 404 = not onboarded)
//   /api/v1/progression/skill-profile     → skillProfile (non-fatal, 404 = no license)
//
// /api/v1/progression/progress is intentionally excluded: it returns mock/hardcoded data.
//
// Concurrent 401 race is handled by AuthManager.performRefresh() shared Task barrier.
@MainActor
final class EducationViewModel: ObservableObject {

    enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case error(String)

        static func == (lhs: LoadState, rhs: LoadState) -> Bool {
            switch (lhs, rhs) {
            case (.idle, .idle), (.loading, .loading), (.loaded, .loaded): return true
            case (.error(let a), .error(let b)):                           return a == b
            default:                                                        return false
            }
        }
    }

    @Published private(set) var loadState:     LoadState                            = .idle
    @Published private(set) var status:        SpecializationStatus?                = nil
    @Published private(set) var availableSpecs: [SpecializationInfo]               = []
    @Published private(set) var progressData:  [String: SpecializationProgressData] = [:]
    @Published private(set) var lfaLicense:    LFAPlayerLicense?                   = nil
    @Published private(set) var skillProfile:  SkillProfile?                        = nil

    // MARK: — Load (initial, guarded)

    func load(using authManager: AuthManager) async {
        guard case .idle = loadState else { return }
        await fetchData(using: authManager)
    }

    // MARK: — Reload (manual retry)

    func reload(using authManager: AuthManager) async {
        loadState      = .idle
        status         = nil
        availableSpecs = []
        progressData   = [:]
        lfaLicense     = nil
        skillProfile   = nil
        await fetchData(using: authManager)
    }

    // MARK: — Reset (called on logout)

    func reset() {
        loadState      = .idle
        status         = nil
        availableSpecs = []
        progressData   = [:]
        lfaLicense     = nil
        skillProfile   = nil
    }

    // MARK: — Private

    private func fetchData(using authManager: AuthManager) async {
        loadState = .loading

        do {
            // 1. Current specialization — fatal (can't render Education without it)
            status = try await authManager.authenticatedGet(
                path: "/api/v1/specializations/me"
            )

            // 2. Available specializations catalog — non-fatal
            availableSpecs = (try? await authManager.authenticatedGet(
                path: "/api/v1/specializations/"
            )) ?? []

            // 3. Specialization progress — non-fatal (silent-fail returns empty dict)
            let progressResp: SpecializationProgressResponse? = try? await authManager.authenticatedGet(
                path: "/api/v1/specializations/progress/me"
            )
            progressData = progressResp?.data ?? [:]

            // 4. LFA Player license — non-fatal (404 = user not yet onboarded)
            lfaLicense = try? await authManager.authenticatedGet(
                path: "/api/v1/lfa-player/licenses/me"
            )

            // 5. Skill profile — non-fatal (404 = no active LFA license yet)
            skillProfile = try? await authManager.authenticatedGet(
                path: "/api/v1/progression/skill-profile"
            )

            loadState = .loaded

        } catch APIError.unauthorized {
            // AuthManager already called logout() → RootView switches to LoginView.
            loadState = .idle
        } catch {
            loadState = .error("Could not load Education Center. Check your connection.")
        }
    }
}

import Foundation

// Education Center data layer.
//
// Endpoint mapping:
//   /api/v1/specializations/me            → status  (network error = fatal, other errors = empty state)
//   /api/v1/specializations/              → availableSpecs (non-fatal, public catalog)
//   /api/v1/specializations/progress/me  → progressData (non-fatal, silent-fail OK)
//   /api/v1/lfa-player/licenses/me        → lfaLicense (non-fatal, 404 = not onboarded)
//   /api/v1/progression/skill-profile     → skillProfile (non-fatal, 404 = no license)
//
// Error handling policy:
//   networkError  → .error (connection error shown)
//   unauthorized  → .idle  (AuthManager triggers logout)
//   httpError 4xx/5xx, decodingError on status → status = nil, continue to .loaded (empty state)
//   httpError/decodingError on non-fatal calls → try? swallows, field stays nil
//
// /api/v1/progression/progress is intentionally excluded: returns mock/hardcoded data.
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

    @Published private(set) var loadState:      LoadState                             = .idle
    @Published private(set) var status:         SpecializationStatus?                 = nil
    @Published private(set) var availableSpecs: [SpecializationInfo]                  = []
    @Published private(set) var progressData:   [String: SpecializationProgressData]  = [:]
    @Published private(set) var lfaLicense:     LFAPlayerLicense?                     = nil
    @Published private(set) var skillProfile:   SkillProfile?                          = nil

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

        // 1. Specialization status — precise per-error-type handling.
        //    networkError → fatal (connection error). Other errors → empty state (status = nil).
        //    This prevents misleading "connection error" when backend returns 4xx or schema drifts.
        do {
            status = try await authManager.authenticatedGet(path: "/api/v1/specializations/me")
        } catch APIError.unauthorized {
            loadState = .idle
            return
        } catch APIError.networkError(_) {
            loadState = .error("Could not reach Education Center. Check your connection.")
            return
        } catch {
            // HTTP 4xx/5xx or decodingError — not a connectivity problem.
            // Show empty/onboarding state rather than a misleading connection error.
            status = nil
        }

        // 2. Available specializations catalog — non-fatal (empty list if endpoint fails)
        availableSpecs = (try? await authManager.authenticatedGet(
            path: "/api/v1/specializations/"
        )) ?? []

        // 3. Specialization progress — non-fatal (silent-fail on empty dict or decode error)
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
    }
}

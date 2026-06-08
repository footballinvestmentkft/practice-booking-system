import Foundation

// LFA Football Player card state — drives MainHubView card badge/action.
//
// State machine (evaluated in order):
//   profile not loaded yet         → loading
//   profile.calculatedAge < 5      → ageLocked        (tap disabled)
//   no lfaLicense, credit < 100    → insufficientCredits (tap disabled)
//   no lfaLicense, credit ≥ 100    → unlockAvailable  (tap disabled; R3 adds confirm)
//   license, onboarding incomplete → setupPending      (tap disabled; R4 adds flow)
//   license, onboarding complete   → active            (tap opens LFASpecTabView)
enum LFACardState {
    case loading
    case ageLocked
    case insufficientCredits
    case unlockAvailable
    case setupPending
    case active
}

// Dashboard load state.
enum DashboardLoadState: Equatable {
    case idle
    case loading
    case unlocking  // post-unlock reload — prevents card from flashing unlockAvailable again
    case loaded
    case error(String)

    static func == (lhs: DashboardLoadState, rhs: DashboardLoadState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.loading, .loading), (.unlocking, .unlocking), (.loaded, .loaded): return true
        case (.error(let a), .error(let b)):                                                      return a == b
        default:                                                                                   return false
        }
    }
}

// Fetches user profile + LFA Player license + dashboard stats.
//
// Endpoint mapping (Phase D.1 corrected):
//   /api/v1/users/me                   → profile (fatal — dashboard can't render without it)
//   /api/v1/lfa-player/licenses/me     → lfaLicense (non-fatal — user may not have one yet)
//   /api/v1/licenses/dashboard          → dashboard (non-fatal — complex schema, graceful)
//   /api/v1/licenses/me                 → licenses  (GānCuju COACH/PLAYER/INTERNSHIP,
//                                                     reserved, not displayed in LFA UI)
//
// Requests are sequential to avoid concurrent 401 within the same load cycle.
// Cross-call concurrent 401 (e.g. simultaneous DashboardView + ProfileTab fetches)
// is handled by AuthManager.performRefresh() shared Task barrier.
@MainActor
final class DashboardViewModel: ObservableObject {

    @Published private(set) var loadState:          DashboardLoadState = .idle
    @Published private(set) var profile:            UserProfile?       = nil
    @Published private(set) var lfaLicense:         LFAPlayerLicense?  = nil
    @Published private(set) var dashboard:          LicenseDashboard?  = nil
    @Published private(set) var licenses:           [UserLicense]      = []  // GānCuju, reserved
    @Published private(set) var selfRatingCompleted: Bool              = false

    // MARK: — LFA Card State

    var lfaCardState: LFACardState {
        guard loadState == .loaded, let profile else { return .loading }
        if let age = profile.calculatedAge, age < 5 { return .ageLocked }
        if let license = lfaLicense {
            return (license.onboardingCompleted && license.isActive) ? .active : .setupPending
        }
        return (profile.creditBalance ?? 0) >= 100 ? .unlockAvailable : .insufficientCredits
    }

    // MARK: — Load (initial, guarded)

    func load(using authManager: AuthManager) async {
        guard case .idle = loadState else { return }
        await fetchData(using: authManager)
    }

    // MARK: — Reload (manual retry, resets state)

    func reload(using authManager: AuthManager) async {
        loadState           = .idle
        profile             = nil
        lfaLicense          = nil
        dashboard           = nil
        licenses            = []
        selfRatingCompleted = false
        await fetchData(using: authManager)
    }

    // MARK: — Reload after unlock
    // Called by UnlockViewModel on success. Sets .unlocking so the card does not
    // flash back to .unlockAvailable during the post-unlock dashboard refresh.

    func reloadAfterUnlock(using authManager: AuthManager) async {
        loadState           = .unlocking
        profile             = nil
        lfaLicense          = nil
        dashboard           = nil
        licenses            = []
        selfRatingCompleted = false
        await fetchData(using: authManager)
    }

    // MARK: — Reset (called on logout so next login fetches fresh data)

    func reset() {
        loadState           = .idle
        profile             = nil
        lfaLicense          = nil
        dashboard           = nil
        licenses            = []
        selfRatingCompleted = false
    }

    // MARK: — Private

    private func fetchData(using authManager: AuthManager) async {
        loadState = .loading

        do {
            // 1. User profile — fatal (dashboard cannot render without it).
            //    /users/me now correctly returns "name" (not first_name/last_name).
            profile = try await authManager.authenticatedGet(path: "/api/v1/users/me")

            // 2. LFA Player license — non-fatal (user may not have one yet).
            //    Uses /lfa-player/licenses/me — NOT /licenses/me (which is GānCuju).
            lfaLicense = try? await authManager.authenticatedGet(
                path: "/api/v1/lfa-player/licenses/me"
            )

            // 3. License dashboard — non-fatal. Shows overall_progress % if decode succeeds.
            //    Schema is Dict[str, Any] — LicenseDashboard decodes only what it needs.
            dashboard = try? await authManager.authenticatedGet(
                path: "/api/v1/licenses/dashboard"
            )

            // 4. GānCuju licenses (COACH/PLAYER/INTERNSHIP) — reserved for future tracks.
            //    Schema mismatch with UserLicense model is non-fatal (all fields Optional).
            //    Not displayed in the LFA Player UI.
            licenses = (try? await authManager.authenticatedGet(
                path: "/api/v1/licenses/me"
            )) ?? []

            // 5. Goals & Motivation completion — non-fatal.
            //    Returns {completed: bool} for the user's most recent license.
            //    404 (no license) or any decode error → false.
            struct MotivationCheck: Decodable { let completed: Bool }
            selfRatingCompleted = (try? await authManager.authenticatedGet(
                path: "/api/v1/licenses/motivation-assessment"
            ) as MotivationCheck)?.completed ?? false

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

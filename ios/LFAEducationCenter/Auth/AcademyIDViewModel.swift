import Foundation

// ViewModel for My Academy ID full-screen view.
//
// Load strategy (two paths):
//   Fast path — publicToken already in UserProfile (from /users/me cache):
//     qr_data is assembled locally from APIConfig.verifyBaseURL.
//     Works offline once the token is cached.
//   Slow path — publicToken == nil (first-ever access or fresh install):
//     Calls GET /api/v1/users/me/academy-id which lazy-assigns the ID on the
//     backend and returns the complete response including qr_data.
//
// Reload forces the slow path and re-fetches from backend.
@MainActor
final class AcademyIDViewModel: ObservableObject {

    enum LoadState {
        case idle
        case loading
        case loaded(AcademyIDResponse)
        case error(String)

        var isLoaded: Bool {
            if case .loaded = self { return true }
            return false
        }
        var response: AcademyIDResponse? {
            if case .loaded(let r) = self { return r }
            return nil
        }
    }

    @Published private(set) var loadState: LoadState = .idle

    // MARK: — Load (guarded — no-op if already loaded)

    func load(using authManager: AuthManager, profile: UserProfile?) async {
        guard case .idle = loadState else { return }
        await fetch(using: authManager, profile: profile, forceRemote: false)
    }

    // MARK: — Reload (forced — always hits backend)

    func reload(using authManager: AuthManager, profile: UserProfile?) async {
        loadState = .idle
        await fetch(using: authManager, profile: profile, forceRemote: true)
    }

    // MARK: — Private

    private func fetch(
        using authManager: AuthManager,
        profile: UserProfile?,
        forceRemote: Bool
    ) async {
        loadState = .loading

        // Fast path: token already known → assemble qr_data locally, no network needed.
        if !forceRemote,
           let token = profile?.publicToken,
           let aid   = profile?.lfaAcademyId {
            let qrData = APIConfig.verifyBaseURL + "/verify/" + token
            loadState = .loaded(AcademyIDResponse(
                lfaAcademyId: aid,
                publicToken:  token,
                qrUrl:        "/verify/" + token,
                qrData:       qrData
            ))
            return
        }

        // Slow path: call backend.
        do {
            let response: AcademyIDResponse = try await authManager.authenticatedGet(
                path: "/api/v1/users/me/academy-id"
            )
            // Rebuild qrData using APIConfig.verifyBaseURL so fast path and slow path
            // always produce the same QR URL on this iOS client, regardless of the
            // backend's VERIFY_BASE_URL default (which may be localhost in dev).
            let iosQrData = APIConfig.verifyBaseURL + "/verify/" + response.publicToken
            loadState = .loaded(AcademyIDResponse(
                lfaAcademyId: response.lfaAcademyId,
                publicToken:  response.publicToken,
                qrUrl:        response.qrUrl,
                qrData:       iosQrData
            ))
        } catch {
            loadState = .error("Could not load Academy ID. Check your connection.")
        }
    }
}

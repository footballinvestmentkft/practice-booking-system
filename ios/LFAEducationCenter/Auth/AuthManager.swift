import Foundation

// Central auth state and session lifecycle.
//
// Token flow:
//   login()              → save access_token + refresh_token → isLoggedIn = true
//   validateSession()    → GET /users/me; on 401: refresh; on refresh 401: logout
//   authenticatedGet/Post → inject Bearer; on 401: single refresh + retry; no infinite loop
//   performRefresh()     → POST /auth/refresh; rotating: save both tokens;
//                          on 401: logout(); on network error: preserve tokens
//   logout()             → clearAll Keychain → isLoggedIn = false
//
// Concurrent 401 race protection:
//   pendingRefresh is a shared Task<Bool, Never>?. If multiple concurrent callers
//   get 401 simultaneously, the first creates the Task; subsequent callers find
//   pendingRefresh != nil and await the same Task.value — no second refresh fires.
//   This prevents the dual-logout race where isRefreshing=true caused the second
//   caller to incorrectly receive .unauthorized and trigger logout prematurely.
//
// All @Published mutations run on the main actor.
// pendingRefresh is accessed only from the main actor — no data race possible.
@MainActor
final class AuthManager: ObservableObject {

    @Published private(set) var isLoggedIn:           Bool    = false
    @Published private(set) var isLoading:            Bool    = false
    @Published private(set) var isValidatingSession:  Bool    = true   // true until validateSession() completes
    @Published              var errorMessage:          String? = nil

    // Set to true after a successful registration — clears on Continue or logout.
    // Never set by login() or validateSession(), so it never fires for returning users.
    @Published private(set) var justRegistered:       Bool    = false
    // First name captured from the register form for immediate display in WelcomeSuccessView.
    private(set) var registeredUserName:              String? = nil

    // Shared-task barrier replacing the former isRefreshing: Bool flag.
    // A non-nil value means a refresh is in-flight; new callers join via .value.
    private var pendingRefresh: Task<Bool, Never>?

    init() {
        // Optimistic: set isLoggedIn from Keychain immediately.
        // isValidatingSession stays true so SplashView is shown until validateSession() finishes.
        isLoggedIn = KeychainService.load(account: KeychainService.accessTokenKey) != nil
    }

    // MARK: — Session restore (call once on app launch)

    // Validates the stored session against the backend.
    // Strategy:
    //   1. No tokens → stay logged out.
    //   2. Has access_token → GET /users/me to verify.
    //   3. 401 on /users/me → try refresh.
    //   4. Refresh 401 → logout (tokens invalid/expired beyond recovery).
    //   5. Network error at any step → stay logged in (offline tolerance).
    func validateSession() async {
        // isValidatingSession was true from init — clear it when we're done regardless of outcome.
        defer { isValidatingSession = false }

        guard let token = accessToken else {
            isLoggedIn = false
            return
        }

        do {
            let _: UserProfile = try await APIClient.get(
                path: "/api/v1/users/me",
                token: token
            )
            isLoggedIn = true

        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            if refreshed { isLoggedIn = true }

        } catch {
            // Network error on launch — remain optimistically logged in.
        }
    }

    // MARK: — Login

    func login(email: String, password: String) async {
        isLoading    = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            let response: AuthResponse = try await APIClient.post(
                path: "/api/v1/auth/login",
                body: LoginRequest(email: email, password: password)
            )
            saveTokens(response)
            isLoggedIn = true

        } catch APIError.httpError(let code, _) {
            errorMessage = (code == 401 || code == 422)
                ? "Invalid email or password."
                : "Server error (\(code)). Please try again."
        } catch APIError.networkError {
            errorMessage = "Network error. Check your connection and try again."
        } catch {
            errorMessage = "Something went wrong. Please try again."
        }
    }

    // MARK: — Register

    // Creates a new account via POST /api/v1/auth/register-with-invitation.
    // On success: saves tokens to Keychain and sets isLoggedIn = true.
    // errorMessage is set on any failure; the view observes and displays it.
    func register(
        email: String, password: String,
        firstName: String, lastName: String, nickname: String,
        phone: String, dateOfBirth: String,
        nationality: String, gender: String,
        streetAddress: String, city: String,
        postalCode: String, country: String,
        invitationCode: String
    ) async {
        isLoading    = true
        errorMessage = nil
        defer { isLoading = false }

        let body = RegisterRequest(
            email: email,
            password: password,
            name: "\(firstName) \(lastName)",
            firstName: firstName,
            lastName: lastName,
            nickname: nickname,
            phone: phone,
            dateOfBirth: dateOfBirth,
            nationality: nationality,
            gender: gender,
            streetAddress: streetAddress,
            city: city,
            postalCode: postalCode,
            country: country,
            invitationCode: invitationCode
        )

        do {
            let response: AuthResponse = try await APIClient.post(
                path: "/api/v1/auth/register-with-invitation",
                body: body
            )
            saveTokens(response)
            registeredUserName = firstName
            justRegistered     = true
            isLoggedIn         = true

        } catch APIError.httpError(let code, let detail) {
            switch code {
            case 400: errorMessage = detail ?? "Registration failed. Check your details."
            case 403: errorMessage = detail ?? "Invitation code restricted."
            case 404: errorMessage = "Invalid or expired invitation code."
            case 409: errorMessage = "Email already registered. Try signing in."
            default:  errorMessage = "Server error (\(code)). Please try again."
            }
        } catch APIError.networkError {
            errorMessage = "Network error. Check your connection and try again."
        } catch {
            errorMessage = "Something went wrong. Please try again."
        }
    }

    // MARK: — Post-registration context

    // Called by WelcomeSuccessView when the user taps Continue to Hub.
    func clearJustRegistered() {
        justRegistered     = false
        registeredUserName = nil
    }

    // MARK: — Logout

    func logout() {
        KeychainService.clearAll()
        justRegistered     = false
        registeredUserName = nil
        isLoggedIn         = false
    }

    // MARK: — Protected request wrappers

    // GET with automatic Bearer inject, single 401 refresh + retry, logout on refresh failure.
    func authenticatedGet<T: Decodable>(path: String) async throws -> T {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }

        do {
            return try await APIClient.get(path: path, token: token)
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.get(path: path, token: newToken)
        }
    }

    // POST with automatic Bearer inject, single 401 refresh + retry, logout on refresh failure.
    func authenticatedPost<B: Encodable, T: Decodable>(path: String, body: B) async throws -> T {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }

        do {
            return try await APIClient.post(path: path, body: body, token: token)
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.post(path: path, body: body, token: newToken)
        }
    }

    // Form-encoded POST with automatic Bearer inject, single 401 refresh + retry.
    // Used for FastAPI endpoints that declare Form(...) parameters.
    func authenticatedFormPost<T: Decodable>(path: String, fields: [String: String]) async throws -> T {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }

        do {
            return try await APIClient.formPost(path: path, fields: fields, token: token)
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.formPost(path: path, fields: fields, token: newToken)
        }
    }

    // MARK: — Token accessors

    var accessToken: String? {
        KeychainService.load(account: KeychainService.accessTokenKey)
    }

    var refreshToken: String? {
        KeychainService.load(account: KeychainService.refreshTokenKey)
    }

    // MARK: — Private

    // Concurrent-safe refresh coordinator.
    //
    // If pendingRefresh is already set, all callers await the same Task.value —
    // only one HTTP request fires regardless of how many 401s arrive concurrently.
    // After completion, pendingRefresh is cleared so the next error cycle starts fresh.
    //
    // On 401 from the refresh endpoint: calls logout() (session unrecoverable).
    // On network error: preserves tokens (offline tolerance), returns false.
    @discardableResult
    private func performRefresh() async -> Bool {
        if let pending = pendingRefresh {
            return await pending.value   // join the in-flight refresh
        }

        // Task inherits @MainActor context — saveTokens/logout run on main actor.
        let task = Task<Bool, Never> { await self.runRefresh() }
        pendingRefresh = task
        let result = await task.value
        pendingRefresh = nil
        return result
    }

    // Executes the actual token refresh HTTP call.
    // Called only from performRefresh() Task — always on @MainActor.
    private func runRefresh() async -> Bool {
        guard let rt = refreshToken else { logout(); return false }

        do {
            let response: AuthResponse = try await APIClient.post(
                path: "/api/v1/auth/refresh",
                body: RefreshRequest(refreshToken: rt)
            )
            // Rotating refresh: backend issues a new refresh_token every time.
            // Always save BOTH tokens after a successful refresh.
            saveTokens(response)
            return true

        } catch APIError.httpError(401, _) {
            // Refresh token expired or revoked — session is unrecoverable.
            logout()
            return false

        } catch {
            // Network error — preserve existing tokens, let caller decide.
            return false
        }
    }

    private func saveTokens(_ response: AuthResponse) {
        KeychainService.save(response.accessToken,  account: KeychainService.accessTokenKey)
        KeychainService.save(response.refreshToken, account: KeychainService.refreshTokenKey)
    }
}

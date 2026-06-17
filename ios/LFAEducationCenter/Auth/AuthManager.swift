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
    // Cached from GET /api/v1/users/me after successful validateSession / login.
    // Used by annotation screens to scope local storage without an extra network call.
    @Published private(set) var currentUserId:        Int?    = nil

    // Shared-task barrier replacing the former isRefreshing: Bool flag.
    // A non-nil value means a refresh is in-flight; new callers join via .value.
    private var pendingRefresh: Task<Bool, Never>?

    init() {
        // Optimistic: set isLoggedIn from Keychain immediately.
        // isValidatingSession stays true so SplashView is shown until validateSession() finishes.
        isLoggedIn = KeychainService.load(account: KeychainService.accessTokenKey) != nil
        if isLoggedIn {
            // Restore the cached id so currentUserId is never nil while isLoggedIn is
            // optimistically true, even before validateSession() completes.
            currentUserId = Self.cachedUserId()
        }
    }

    // MARK: — Session restore (call once on app launch)

    // Validates the stored session against the backend.
    // Strategy:
    //   1. No tokens → stay logged out.
    //   2. Has access_token → GET /users/me to verify and capture currentUserId.
    //   3. 401 on /users/me → try refresh, then retry /users/me.
    //   4. Refresh 401 → logout (tokens invalid/expired beyond recovery).
    //   5. Network error at any step → fall back to the cached currentUserId
    //      (offline tolerance). If no cache exists, the session cannot be
    //      trusted — log out rather than leave currentUserId nil while
    //      isLoggedIn is true.
    func validateSession() async {
        // isValidatingSession was true from init — clear it when we're done regardless of outcome.
        defer { isValidatingSession = false }

        guard let token = accessToken else {
            isLoggedIn = false
            return
        }

        if let id = await fetchUserId(token: token) {
            currentUserId = id
            Self.cacheUserId(id)
            isLoggedIn = true
            return
        }

        // /users/me failed. Distinguish 401 (needs refresh) from other errors
        // by retrying the request once more after a successful refresh.
        let refreshed = await performRefresh()
        if refreshed {
            // performRefresh() guarantees currentUserId on success (see runRefresh).
            isLoggedIn = currentUserId != nil
            return
        }

        // Refresh did not run/succeed (e.g. network error, no 401 occurred).
        // Fall back to the cached id for offline tolerance.
        if let cached = Self.cachedUserId() {
            currentUserId = cached
            isLoggedIn = true
        } else {
            isLoggedIn = false
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

            // currentUserId must be established before the session is marked
            // ready — annotation flows rely on a valid, positive userId.
            guard let id = await fetchUserId(token: response.accessToken) else {
                errorMessage = "Could not load user profile. Please try again."
                return
            }
            currentUserId = id
            Self.cacheUserId(id)
            isLoggedIn = true

        } catch APIError.httpError(let code, _) {
            errorMessage = (code == 401 || code == 422)
                ? "Invalid email or password."
                : "Server error (\(code)). Please try again."
        } catch APIError.networkError(let underlyingError) {
            #if DEBUG
            let urlErr = underlyingError as? URLError
            print("[AuthManager] ✖ login networkError type=\(type(of: underlyingError)) urlCode=\(urlErr?.code.rawValue ?? -99999)")
            #endif
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

            // currentUserId must be established before the session is marked
            // ready — annotation flows rely on a valid, positive userId.
            guard let id = await fetchUserId(token: response.accessToken) else {
                errorMessage = "Could not load user profile. Please try again."
                return
            }
            currentUserId      = id
            Self.cacheUserId(id)
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
        Self.clearCachedUserId()
        justRegistered     = false
        registeredUserName = nil
        currentUserId      = nil
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

    // Multipart POST with automatic Bearer inject, single 401 refresh + retry.
    // Used for file upload endpoints (e.g. POST /api/v1/users/me/profile-photo).
    func authenticatedMultipartPost<T: Decodable>(
        path:      String,
        imageData: Data,
        mimeType:  String,
        fieldName: String = "photo"
    ) async throws -> T {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }
        do {
            return try await APIClient.multipartPost(
                path: path, imageData: imageData,
                mimeType: mimeType, fieldName: fieldName, token: token
            )
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.multipartPost(
                path: path, imageData: imageData,
                mimeType: mimeType, fieldName: fieldName, token: newToken
            )
        }
    }

    // File upload with multipart/form-data — Bearer inject, single 401 refresh + retry.
    // Streams the video file from fileURL without loading it fully into memory.
    // Uses APIClient.multipartUploadFromFile which writes the multipart envelope
    // to a temp file and streams it via URLSession.uploadTask(with:fromFile:).
    func authenticatedMultipartUploadFile<T: Decodable>(
        path:      String,
        fileURL:   URL,
        mimeType:  String,
        fieldName: String = "file"
    ) async throws -> T {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }
        do {
            return try await APIClient.multipartUploadFromFile(
                path: path, fileURL: fileURL,
                mimeType: mimeType, fieldName: fieldName, token: token
            )
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.multipartUploadFromFile(
                path: path, fileURL: fileURL,
                mimeType: mimeType, fieldName: fieldName, token: newToken
            )
        }
    }

    // DELETE with no response body (204) — Bearer inject, single 401 refresh + retry.
    func authenticatedDeleteNoContent(path: String) async throws {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }
        do {
            try await APIClient.deleteNoContent(path: path, token: token)
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            try await APIClient.deleteNoContent(path: path, token: newToken)
        }
    }

    // GET (binary) with automatic Bearer inject, single 401 refresh + retry.
    // Used for juggling thumbnail and media endpoints that return JPEG/MP4 binary.
    func authenticatedFetchData(path: String) async throws -> Data {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }
        do {
            return try await APIClient.fetchData(path: path, token: token)
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.fetchData(path: path, token: newToken)
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

    // GET (raw) — inject Bearer, returns (Data, URLResponse) without a 2xx check.
    // No 401 refresh: getRaw never throws on non-2xx, so a 401 body passes through
    // for the caller to inspect (e.g. ContactTaxonomyStore falls back to bundled data).
    func authenticatedGetRaw(
        path:         String,
        extraHeaders: [String: String] = [:]
    ) async throws -> (Data, URLResponse) {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }
        return try await APIClient.getRaw(path: path, token: token, extraHeaders: extraHeaders)
    }

    // POST (raw) — inject Bearer, single 401 refresh + retry, returns (Data, statusCode).
    // Used by annotation create/batch endpoints to distinguish 200 (duplicate) from 201 (created)
    // and to surface 409 conflict bodies to the caller via APIError.httpError.
    func authenticatedPostRaw<B: Encodable>(path: String, body: B) async throws -> (Data, Int) {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }
        do {
            return try await APIClient.postRaw(path: path, body: body, token: token)
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.postRaw(path: path, body: body, token: newToken)
        }
    }

    // PATCH (raw) — inject Bearer, single 401 refresh + retry, returns (Data, statusCode).
    func authenticatedPatchRaw<B: Encodable>(path: String, body: B) async throws -> (Data, Int) {
        guard let token = accessToken else { logout(); throw APIError.unauthorized }
        do {
            return try await APIClient.patchRaw(path: path, body: body, token: token)
        } catch APIError.httpError(401, _) {
            let refreshed = await performRefresh()
            guard refreshed, let newToken = accessToken else { throw APIError.unauthorized }
            return try await APIClient.patchRaw(path: path, body: body, token: newToken)
        }
    }

    // MARK: — Token accessors

    // Test-only override: set before test, clear in tearDown.
    // nil in production — Keychain is always used when this is nil.
    var _testAccessToken: String? = nil

    var accessToken: String? {
        _testAccessToken ?? KeychainService.load(account: KeychainService.accessTokenKey)
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
    //
    // Visibility is internal (not private) so AnnotationVideoLoader can call it
    // directly after receiving a 401 on a streaming download task that bypasses
    // the authenticatedFetchData wrapper. The pendingRefresh barrier prevents
    // double-refresh regardless of how many callers enter concurrently.
    @discardableResult
    func performRefresh() async -> Bool {
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

            // Guarantee currentUserId after a successful refresh — never leave
            // the session in a state where tokens are valid but currentUserId is nil.
            if currentUserId == nil {
                if let id = await fetchUserId(token: response.accessToken) {
                    currentUserId = id
                    Self.cacheUserId(id)
                } else if let cached = Self.cachedUserId() {
                    currentUserId = cached
                } else {
                    logout()
                    return false
                }
            }
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

    // GET /api/v1/users/me and extract a usable (positive) user id.
    // Swallows all errors — returns nil on 401, network error, or a profile
    // with a nil/non-positive id. Callers decide how to react to nil.
    private func fetchUserId(token: String) async -> Int? {
        do {
            let profile: UserProfile = try await APIClient.get(path: "/api/v1/users/me", token: token)
            guard let id = profile.id, id > 0 else { return nil }
            return id
        } catch {
            return nil
        }
    }

    // MARK: — currentUserId persistence (UserDefaults)
    //
    // AuthResponse (login/refresh) carries no user id, so currentUserId can
    // only be obtained via GET /users/me. This cache lets currentUserId be
    // restored immediately on relaunch (init) and survive transient network
    // errors during validateSession()/refresh without leaving currentUserId nil
    // while isLoggedIn is true.

    private static let currentUserIdDefaultsKey = "lfa_current_user_id"

    private static func cachedUserId() -> Int? {
        let value = UserDefaults.standard.integer(forKey: currentUserIdDefaultsKey)
        return value > 0 ? value : nil
    }

    private static func cacheUserId(_ id: Int) {
        UserDefaults.standard.set(id, forKey: currentUserIdDefaultsKey)
    }

    private static func clearCachedUserId() {
        UserDefaults.standard.removeObject(forKey: currentUserIdDefaultsKey)
    }
}

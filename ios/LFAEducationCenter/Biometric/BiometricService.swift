import Foundation

// Service layer for all biometric backend endpoints.
// All calls use AuthManager's authenticated wrappers for Bearer inject and 401 refresh retry.
//
// Backend contract (PR-iOS-1):
//   POST /me/biometric-liveness: JSON body only — no image bytes, no multipart.
//   POST /me/biometric-verify:   JSON body only — no image bytes, no multipart.
//   photo_filename is a UUID-based string basename (dev/test placeholder only).
//   Image upload requires a future backend multipart endpoint — out of scope here.
//
// Privacy rules (enforced structurally):
//   face_match_score: no model field, no return value, no log — ever.
//   Bearer token: not logged.
//   user_id: not logged.
//   Raw error body: not logged — only typed BiometricClientError is propagated.
//
// DELETE endpoints for disclosure and consent return 200 + JSON body (not 204).
// AuthManager.authenticatedDeleteNoContent handles 204 only — a local wrapper handles
// these two endpoints without modifying AuthManager.
@MainActor
final class BiometricService: ObservableObject {

    private let auth: AuthManager

    init(auth: AuthManager) {
        self.auth = auth
    }

    // MARK: — Disclosure

    func getDisclosureStatus() async throws -> BiometricDisclosureStatus {
        do {
            return try await auth.authenticatedGet(path: "/api/v1/users/me/biometric-disclosure")
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    func acceptDisclosure() async throws -> BiometricDisclosureStatus {
        let body = BiometricDisclosureAcceptRequest(disclosureVersion: kBiometricDisclosureVersion)
        do {
            return try await auth.authenticatedPost(path: "/api/v1/users/me/biometric-disclosure", body: body)
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    func revokeDisclosure() async throws -> BiometricDisclosureStatus {
        do {
            return try await deleteWithBody(path: "/api/v1/users/me/biometric-disclosure")
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    // MARK: — Consent

    func getConsentStatus() async throws -> BiometricConsentStatus {
        do {
            return try await auth.authenticatedGet(path: "/api/v1/users/me/biometric-consent")
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    func grantConsent() async throws -> BiometricConsentStatus {
        let body = BiometricConsentGrantRequest(consentVersion: kBiometricConsentVersion)
        do {
            return try await auth.authenticatedPost(path: "/api/v1/users/me/biometric-consent", body: body)
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    func revokeConsent(reason: String? = nil) async throws -> BiometricConsentStatus {
        let body = BiometricConsentRevokeRequest(reason: reason)
        do {
            return try await deleteWithBody(path: "/api/v1/users/me/biometric-consent", body: body)
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    // MARK: — Liveness

    // Submits completed liveness challenge result.
    // photo_filename: safe UUID basename, e.g. "liveness_<uuid>.jpg".
    // No image bytes are sent — JSON body only. Image upload is out of scope for PR-iOS-1.
    func submitLiveness(
        metadata: BiometricLivenessMetadata,
        photoFilename: String?
    ) async throws -> BiometricVerificationStatus {
        let body = BiometricLivenessSubmitRequest(
            source:           kBiometricLivenessSource,
            livenessMetadata: metadata,
            photoFilename:    photoFilename
        )
        do {
            return try await auth.authenticatedPost(
                path: "/api/v1/users/me/biometric-liveness",
                body: body
            )
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    // MARK: — Verify

    // Submits face verify request.
    // photo_filename: safe UUID basename, e.g. "verify_<uuid>.jpg".
    // No image bytes are sent — JSON body only. Image upload is out of scope for PR-iOS-1.
    func verify(photoFilename: String?) async throws -> BiometricVerifyResult {
        let body = BiometricVerifyRequestBody(photoFilename: photoFilename)
        do {
            return try await auth.authenticatedPost(
                path: "/api/v1/users/me/biometric-verify",
                body: body
            )
        } catch {
            throw BiometricClientError.from(error)
        }
    }

    // MARK: — Private: DELETE + JSON response

    // The disclosure and consent DELETE endpoints return 200 + JSON body.
    // AuthManager.authenticatedDeleteNoContent expects 204 only, so we use a local
    // single-attempt DELETE wrapper. On 401 the caller gets .unauthorized — acceptable
    // for dev/test MVP without modifying AuthManager.
    private func deleteWithBody<T: Decodable>(path: String) async throws -> T {
        guard let token = auth.accessToken else { throw APIError.unauthorized }
        return try await performDelete(path: path, body: Optional<EmptyBody>.none, token: token)
    }

    private func deleteWithBody<B: Encodable, T: Decodable>(
        path: String, body: B
    ) async throws -> T {
        guard let token = auth.accessToken else { throw APIError.unauthorized }
        return try await performDelete(path: path, body: body, token: token)
    }

    private func performDelete<B: Encodable, T: Decodable>(
        path: String, body: B?, token: String
    ) async throws -> T {
        guard let url = URL(string: APIConfig.baseURL + path) else {
            throw APIError.invalidURL
        }
        var req = URLRequest(url: url)
        req.httpMethod = "DELETE"
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body = body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONEncoder().encode(body)
        }
        let (data, response) = try await withCheckedThrowingContinuation {
            (cont: CheckedContinuation<(Data, URLResponse), Error>) in
            URLSession.shared.dataTask(with: req) { d, r, e in
                if let e = e { cont.resume(throwing: APIError.networkError(e)); return }
                guard let d = d, let r = r else {
                    cont.resume(throwing: APIError.networkError(URLError(.unknown))); return
                }
                cont.resume(returning: (d, r))
            }.resume()
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }
        guard (200...299).contains(http.statusCode) else {
            let d = try? JSONDecoder().decode(_BiometricDeleteErrorBody.self, from: data)
            throw APIError.httpError(statusCode: http.statusCode, detail: d?.detail)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw APIError.decodingError
        }
    }
}

private struct EmptyBody: Encodable {}

private struct _BiometricDeleteErrorBody: Decodable {
    let detail: String?
}

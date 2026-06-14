import Foundation

// MARK: — JugglingAnnotationAPIClient
//
// All 7 AN-1 annotation endpoints.
// Never stores a raw access token — all calls go through AuthManager wrappers.
// AuthManager handles 401 → refresh → retry internally (single refresh barrier).
//
// Error classification (caller uses this to set SyncStatus):
//   retryable:  AnnotationAPIError.retryable   (network, 502, 503, 504)
//   permanent:  AnnotationAPIError.permanent    (403, 404, 409 idempotency, 422)
//   conflict:   AnnotationAPIError.versionConflict  (409 version_conflict on PATCH)
//   idempotencyConflict: (409 idempotency_conflict on POST)
//   unauthorized: APIError.unauthorized (session expired beyond recovery)

// MARK: — JugglingAnnotationAPIClientProtocol
//
// Abstraction over the 5 sync-relevant endpoints (taxonomy fetch is handled
// separately by ContactTaxonomyStore). AnnotationSyncEngine depends on this
// protocol so tests can inject a mock without any networking.

@MainActor
protocol JugglingAnnotationAPIClientProtocol: AnyObject {
    func listContacts(videoId: String) async throws -> ContactEventListOut
    func createContact(videoId: String, request: ContactEventCreateRequest) async throws -> CreateContactResult
    func patchContact(videoId: String, eventId: UUID, request: ContactEventPatchRequest) async throws -> ContactEventOut
    func deleteContact(videoId: String, eventId: UUID) async throws -> DeleteContactResult
    func finishAnnotation(videoId: String, confirmZero: Bool) async throws -> FinishAnnotationOut
    // B-2: storage release — 204 or 410 are both success; throws VideoDeleteError on failure
    func deleteVideo(videoId: String) async throws
}

@MainActor
final class JugglingAnnotationAPIClient: JugglingAnnotationAPIClientProtocol {

    private let authManager: AuthManager
    private let isoDecoder: JSONDecoder

    init(authManager: AuthManager) {
        self.authManager = authManager
        isoDecoder = JSONDecoder()
        isoDecoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let str = try container.decode(String.self)
            // Try with fractional seconds first (FastAPI default), then without
            let fmtFrac = ISO8601DateFormatter()
            fmtFrac.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let d = fmtFrac.date(from: str) { return d }
            let fmt = ISO8601DateFormatter()
            fmt.formatOptions = [.withInternetDateTime]
            if let d = fmt.date(from: str) { return d }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Cannot decode date: \(str)")
        }
    }

    // MARK: — Taxonomy

    // Returns raw (Data, HTTPURLResponse) so ContactTaxonomyStore can inspect ETag + 304.
    func fetchTaxonomyRaw(eTag: String?) async throws -> (Data, HTTPURLResponse) {
        var extraHeaders: [String: String] = [:]
        if let e = eTag { extraHeaders["If-None-Match"] = e }
        let (data, response) = try await authManager.authenticatedGetRaw(
            path: "/api/v1/users/me/juggling/taxonomy",
            extraHeaders: extraHeaders
        )
        guard let http = response as? HTTPURLResponse else {
            throw APIError.networkError(URLError(.badServerResponse))
        }
        return (data, http)
    }

    // MARK: — Contact list

    func listContacts(videoId: String) async throws -> ContactEventListOut {
        let response: ContactEventListOut = try await authManager.authenticatedGet(
            path: "/api/v1/users/me/juggling/videos/\(videoId)/contacts"
        )
        return response
    }

    // MARK: — Single create
    //
    // HTTP 201 → ContactEventOut, isNew = true
    // HTTP 200 → ContactEventOut, isNew = false (exact duplicate)
    // HTTP 409 + "idempotency_conflict" → throws AnnotationAPIError.idempotencyConflict
    // HTTP 409 (other) → throws AnnotationAPIError.permanent
    // HTTP 403 / 404 / 422 → throws AnnotationAPIError.permanent
    // HTTP 502/503/504 / network → throws AnnotationAPIError.retryable

    func createContact(videoId: String, request: ContactEventCreateRequest) async throws -> CreateContactResult {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts"
        do {
            let (data, statusCode) = try await authManager.authenticatedPostRaw(path: path, body: request)
            let event = try decodeEvent(data)
            return CreateContactResult(event: event, isNew: statusCode == 201)
        } catch let apiErr as APIError {
            throw classifyAPIError(apiErr, path: "createContact")
        }
    }

    // MARK: — Batch submit
    //
    // HTTP 201 → all new; HTTP 200 → all dup; HTTP 207 → mixed/conflict
    // All three are success responses — caller processes ContactEventBatchResult.results.

    func batchSubmit(videoId: String, request: ContactEventBatchRequest) async throws -> ContactEventBatchResult {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/batch"
        do {
            let (data, _) = try await authManager.authenticatedPostRaw(path: path, body: request)
            return try isoDecoder.decode(ContactEventBatchResult.self, from: data)
        } catch let apiErr as APIError {
            throw classifyAPIError(apiErr, path: "batchSubmit")
        }
    }

    // MARK: — PATCH
    //
    // HTTP 200 → ContactEventOut (updated)
    // HTTP 409 + "version_conflict" → throws AnnotationAPIError.versionConflict
    // HTTP 409 (other) → permanent
    // Network timeout: caller must set needsReconciliation

    func patchContact(videoId: String, eventId: UUID, request: ContactEventPatchRequest) async throws -> ContactEventOut {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/\(eventId.uuidString.lowercased())"
        do {
            let (data, _) = try await authManager.authenticatedPatchRaw(path: path, body: request)
            return try decodeEvent(data)
        } catch let apiErr as APIError {
            throw classifyAPIError(apiErr, path: "patchContact")
        }
    }

    // MARK: — DELETE (soft-delete)
    //
    // HTTP 204 → success (.deleted)
    // HTTP 404 → ownership ambiguous — caller must reconcile (GET /contacts)
    // Network timeout → caller sets .needsReconciliation

    func deleteContact(videoId: String, eventId: UUID) async throws -> DeleteContactResult {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/\(eventId.uuidString.lowercased())"
        do {
            try await authManager.authenticatedDeleteNoContent(path: path)
            return .deleted
        } catch APIError.httpError(let code, _) where code == 404 {
            return .notFoundAmbiguous   // caller must reconcile, not auto-treat as success
        } catch let apiErr as APIError {
            throw classifyAPIError(apiErr, path: "deleteContact")
        }
    }

    // MARK: — Finish

    func finishAnnotation(videoId: String, confirmZero: Bool) async throws -> FinishAnnotationOut {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/finish"
        let body = FinishAnnotationRequest(confirmZeroContacts: confirmZero)
        do {
            let result: FinishAnnotationOut = try await authManager.authenticatedPost(path: path, body: body)
            return result
        } catch let apiErr as APIError {
            throw classifyAPIError(apiErr, path: "finishAnnotation")
        }
    }

    // MARK: — Video media delete (B-2)
    //
    // DELETE /api/v1/users/me/juggling/videos/{videoId}
    // 204 → success (files deleted, status set to media_deleted on server)
    // 410 → idempotent success (media already deleted — no-throw)
    // 404 → VideoDeleteError.notFound
    // 401 → VideoDeleteError.unauthorized
    // 403 → VideoDeleteError.permissionDenied
    // network → VideoDeleteError.networkError

    func deleteVideo(videoId: String) async throws {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)"
        do {
            try await authManager.authenticatedDeleteNoContent(path: path)
        } catch let err as APIError {
            switch err {
            case .httpError(statusCode: 410, _):
                return   // already media_deleted — idempotent success
            case .httpError(statusCode: 404, _):
                throw VideoDeleteError.notFound
            case .httpError(statusCode: 401, _), .unauthorized:
                throw VideoDeleteError.unauthorized
            case .httpError(statusCode: 403, let detail):
                throw VideoDeleteError.permissionDenied(detail: detail ?? "forbidden")
            case .networkError(let e):
                throw VideoDeleteError.networkError(e)
            default:
                throw VideoDeleteError.networkError(URLError(.badServerResponse))
            }
        }
    }

    // MARK: — Private helpers

    private func decodeEvent(_ data: Data) throws -> ContactEventOut {
        do {
            return try isoDecoder.decode(ContactEventOut.self, from: data)
        } catch {
            throw AnnotationAPIError.decodeFailed(error)
        }
    }

    private func classifyAPIError(_ error: APIError, path: String) -> AnnotationAPIError {
        switch error {
        case .httpError(let code, let detail):
            switch code {
            case 401: return .unauthorized
            case 403: return .permanent(code: code, detail: detail ?? "consent_blocked")
            case 404: return .permanent(code: code, detail: detail ?? "not_found")
            case 409:
                let d = detail ?? ""
                if d.contains("idempotency_conflict") {
                    return .idempotencyConflict(detail: d)
                } else if d.contains("version_conflict") {
                    return .versionConflict(detail: d)
                }
                return .permanent(code: code, detail: d)
            case 422: return .permanent(code: code, detail: detail ?? "validation_error")
            case 502, 503, 504: return .retryable(code: code)
            default:  return .permanent(code: code, detail: detail ?? "http_\(code)")
            }
        case .networkError: return .retryable(code: nil)
        case .unauthorized: return .unauthorized
        case .decodingError: return .decodeFailed(URLError(.cannotDecodeContentData))
        case .invalidURL:    return .permanent(code: nil, detail: "invalid_url")
        }
    }
}

// MARK: — Result types

struct CreateContactResult {
    let event: ContactEventOut
    let isNew:  Bool   // true = 201, false = 200 dup
}

enum DeleteContactResult {
    case deleted           // 204 — server confirmed soft-delete
    case notFoundAmbiguous // 404 — unclear: already deleted vs. ownership error → reconcile
}

// MARK: — AnnotationAPIError

enum AnnotationAPIError: Error, LocalizedError {
    case retryable(code: Int?)             // network, 502, 503, 504
    case permanent(code: Int?, detail: String) // 403, 404, 409 idempotency, 422
    case idempotencyConflict(detail: String)   // 409 same device_event_id, different payload
    case versionConflict(detail: String)       // 409 PATCH version mismatch
    case unauthorized                          // 401 after refresh — session expired
    case decodeFailed(Error)

    var errorDescription: String? {
        switch self {
        case .retryable(let code):           return "Retryable error (HTTP \(code.map(String.init) ?? "network"))"
        case .permanent(let code, let d):    return "Permanent error (HTTP \(code.map(String.init) ?? "?")): \(d)"
        case .idempotencyConflict(let d):    return "Idempotency conflict: \(d)"
        case .versionConflict(let d):        return "Version conflict: \(d)"
        case .unauthorized:                  return "Session expired. Please log in again."
        case .decodeFailed(let e):           return "Decode failed: \(e)"
        }
    }

    var isRetryable: Bool {
        if case .retryable = self { return true }
        return false
    }
}

// MARK: — VideoDeleteError

enum VideoDeleteError: Error, LocalizedError, Equatable {
    case notFound                          // 404
    case unauthorized                      // 401 / session expired
    case permissionDenied(detail: String)  // 403
    case networkError(Error)               // URLError or transport failure

    var errorDescription: String? {
        switch self {
        case .notFound:                    return "Video not found."
        case .unauthorized:                return "Session expired. Please log in again."
        case .permissionDenied(let d):     return "Permission denied: \(d)"
        case .networkError:                return "Network error. Please try again."
        }
    }

    static func == (lhs: VideoDeleteError, rhs: VideoDeleteError) -> Bool {
        switch (lhs, rhs) {
        case (.notFound, .notFound):                           return true
        case (.unauthorized, .unauthorized):                   return true
        case (.permissionDenied(let a), .permissionDenied(let b)): return a == b
        case (.networkError, .networkError):                   return true
        default:                                               return false
        }
    }
}

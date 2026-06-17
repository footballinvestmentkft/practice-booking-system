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
    // B-3: three-step video upload pipeline (init → upload → complete)
    func uploadInit(sourceType: String, uploadSource: String) async throws -> JugglingUploadInitResponse
    func uploadVideoFile(videoId: String, fileURL: URL, mimeType: String) async throws -> JugglingUploadFileResponse
    func completeUpload(videoId: String) async throws -> JugglingCompleteResponse
    // AN-3B2C-1: ball detection
    func fetchBallDetection(videoId: String, eventId: UUID) async throws -> BallDetectionOut
    func postBallDetection(videoId: String, eventId: UUID, request: BallDetectionManualRequest) async throws -> BallDetectionOut
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

    // MARK: — Video upload pipeline (B-3)
    //
    // Step 1 — uploadInit: POST /upload-init → {video_id, status, upload_url}
    //   201 → JugglingUploadInitResponse
    //   403 → JugglingUploadError.noConsent (service consent not given)
    //   401 → JugglingUploadError.unauthorized (session expired)
    //   network → JugglingUploadError.networkError
    //
    // Step 2 — uploadVideoFile: POST /videos/{id}/upload (multipart, field="file")
    //   200 → JugglingUploadFileResponse
    //   409 → JugglingUploadError.invalidState (video not in pending_upload)
    //   413 → JugglingUploadError.fileTooLarge (>100 MB)
    //   415 → JugglingUploadError.unsupportedFormat (not mp4/mov/m4v)
    //   401 → JugglingUploadError.unauthorized
    //   network → JugglingUploadError.networkError
    //
    // Step 3 — completeUpload: POST /videos/{id}/complete
    //   200 → JugglingCompleteResponse (analysis queued)
    //   409 → JugglingUploadError.invalidState (not in 'uploaded' status)
    //   401 → JugglingUploadError.unauthorized
    //   network → JugglingUploadError.networkError

    func uploadInit(sourceType: String, uploadSource: String) async throws -> JugglingUploadInitResponse {
        let path = "/api/v1/users/me/juggling/videos/upload-init"
        let body = JugglingUploadInitBody(sourceType: sourceType, uploadSource: uploadSource)
        do {
            return try await authManager.authenticatedPost(path: path, body: body)
        } catch let err as APIError {
            throw JugglingAnnotationAPIClient.mapUploadInitError(err)
        }
    }

    func uploadVideoFile(videoId: String, fileURL: URL, mimeType: String) async throws -> JugglingUploadFileResponse {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/upload"
        do {
            return try await authManager.authenticatedMultipartUploadFile(
                path: path, fileURL: fileURL, mimeType: mimeType, fieldName: "file"
            )
        } catch let err as APIError {
            throw JugglingAnnotationAPIClient.mapUploadFileError(err)
        }
    }

    func completeUpload(videoId: String) async throws -> JugglingCompleteResponse {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/complete"
        do {
            return try await authManager.authenticatedPost(path: path, body: EmptyBody())
        } catch let err as APIError {
            throw JugglingAnnotationAPIClient.mapCompleteError(err)
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
                return
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

    // MARK: — Phase 2A: Pose Snapshot (non-throwing — upload failure must not block annotation)
    //
    // uploadPoseSnapshot: POST /contacts/{event_id}/pose-snapshot
    //   201 → new snapshot (isNew = true)
    //   200 → updated snapshot (isNew = false, idempotent retry)
    //   503 → POSE_SNAPSHOT_ENABLED=false on server; silently ignored
    //   Any other error → logged, not re-thrown
    //
    // fetchPoseSnapshots: GET /pose-snapshots
    //   200 → [PoseSnapshotOut] ordered by timestamp_ms
    //   503 → feature flag off; returns []
    //   network failure → returns []

    func uploadPoseSnapshot(
        videoId:  String,
        eventId:  UUID,
        request:  PoseSnapshotUploadRequest
    ) async {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/\(eventId.uuidString.lowercased())/pose-snapshot"
        do {
            let (_, statusCode) = try await authManager.authenticatedPostRaw(path: path, body: request)
            guard statusCode == 200 || statusCode == 201 else {
                print("[PoseSnapshot] upload unexpected status \(statusCode)")
                return
            }
        } catch APIError.httpError(503, _) {
            // POSE_SNAPSHOT_ENABLED=false — expected in most deployments, silent
        } catch {
            print("[PoseSnapshot] upload failed (non-blocking): \(error)")
        }
    }

    // MARK: — User rotation persistence (non-throwing — display preference, never blocks annotation)
    //
    // patchRotation: PATCH /videos/{video_id}/rotation
    //   200 → success
    //   422 → invalid degrees value (impossible if caller validates)
    //   Any error → logged, not re-thrown

    func patchRotation(videoId: String, degrees: Int) async {
        struct RotationBody: Encodable {
            let rotationDegrees: Int
            enum CodingKeys: String, CodingKey { case rotationDegrees = "rotation_degrees" }
        }
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/rotation"
        do {
            let (_, statusCode) = try await authManager.authenticatedPatchRaw(
                path: path, body: RotationBody(rotationDegrees: degrees)
            )
            guard statusCode == 200 else {
                print("[Rotation] patchRotation unexpected status \(statusCode) for videoId=\(videoId)")
                return
            }
        } catch {
            print("[Rotation] patchRotation failed (non-blocking): \(error)")
        }
    }

    func fetchPoseSnapshots(videoId: String) async -> [PoseSnapshotOut] {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/pose-snapshots"
        do {
            let snapshots: [PoseSnapshotOut] = try await authManager.authenticatedGet(path: path)
            return snapshots
        } catch APIError.httpError(503, _) {
            return []   // feature flag off
        } catch {
            print("[PoseSnapshot] fetchPoseSnapshots failed: \(error)")
            return []
        }
    }

    // MARK: — Phase 2C-1: Ball Detection
    //
    // fetchBallDetection: GET /contacts/{event_id}/ball-detection
    //   200 → BallDetectionOut
    //   404 → AnnotationAPIError.permanent(code: 404) — no detection yet
    //   503 → AnnotationAPIError.permanent(code: 503) — BALL_DETECTION_ENABLED=false
    //
    // postBallDetection: POST /contacts/{event_id}/ball-detection
    //   200/201 → BallDetectionOut (idempotent upsert)
    //   422 → AnnotationAPIError.permanent — invalid coords (validator)
    //   503 → AnnotationAPIError.permanent — feature flag off

    func fetchBallDetection(videoId: String, eventId: UUID) async throws -> BallDetectionOut {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/\(eventId.uuidString.lowercased())/ball-detection"
        do {
            return try await authManager.authenticatedGet(path: path)
        } catch let apiErr as APIError {
            throw classifyAPIError(apiErr, path: "fetchBallDetection")
        }
    }

    func postBallDetection(
        videoId: String,
        eventId: UUID,
        request: BallDetectionManualRequest
    ) async throws -> BallDetectionOut {
        let path = "/api/v1/users/me/juggling/videos/\(videoId)/contacts/\(eventId.uuidString.lowercased())/ball-detection"
        do {
            let (data, _) = try await authManager.authenticatedPostRaw(path: path, body: request)
            return try isoDecoder.decode(BallDetectionOut.self, from: data)
        } catch let apiErr as APIError {
            throw classifyAPIError(apiErr, path: "postBallDetection")
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

    // MARK: — Upload error mapping (internal static for unit testing)

    internal static func mapUploadInitError(_ error: APIError) -> JugglingUploadError {
        switch error {
        case .httpError(401, _), .unauthorized:    return .unauthorized
        case .httpError(403, _):                   return .noConsent
        case .networkError(let e):                 return .networkError(e)
        default:                                   return .networkError(URLError(.badServerResponse))
        }
    }

    internal static func mapUploadFileError(_ error: APIError) -> JugglingUploadError {
        switch error {
        case .httpError(401, _), .unauthorized:    return .unauthorized
        case .httpError(403, _):                   return .noConsent
        case .httpError(409, let d):               return .invalidState(d)
        case .httpError(413, _):                   return .fileTooLarge
        case .httpError(415, _):                   return .unsupportedFormat
        case .networkError(let e):                 return .networkError(e)
        default:                                   return .networkError(URLError(.badServerResponse))
        }
    }

    internal static func mapCompleteError(_ error: APIError) -> JugglingUploadError {
        switch error {
        case .httpError(401, _), .unauthorized:    return .unauthorized
        case .httpError(409, let d):               return .invalidState(d)
        case .networkError(let e):                 return .networkError(e)
        default:                                   return .networkError(URLError(.badServerResponse))
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

// MARK: — Upload request / response types (B-3)

private struct EmptyBody: Encodable {}

struct JugglingUploadInitBody: Encodable {
    let sourceType:   String
    let uploadSource: String
    enum CodingKeys: String, CodingKey {
        case sourceType   = "source_type"
        case uploadSource = "upload_source"
    }
}

struct JugglingUploadInitResponse: Decodable {
    let videoId:   String
    let status:    String
    let uploadUrl: String
    enum CodingKeys: String, CodingKey {
        case videoId   = "video_id"
        case status
        case uploadUrl = "upload_url"
    }
}

struct JugglingUploadFileResponse: Decodable {
    let videoId:        String
    let status:         String
    let fileSizeBytes:  Int
    let checksumSha256: String
    enum CodingKeys: String, CodingKey {
        case videoId        = "video_id"
        case status
        case fileSizeBytes  = "file_size_bytes"
        case checksumSha256 = "checksum_sha256"
    }
}

struct JugglingCompleteResponse: Decodable {
    let videoId: String
    let status:  String
    let message: String
    enum CodingKeys: String, CodingKey {
        case videoId = "video_id"
        case status
        case message
    }
}

// MARK: — JugglingUploadError

enum JugglingUploadError: Error, LocalizedError, Equatable {
    case noConsent                    // 403 — service consent not given
    case invalidState(String?)        // 409 — video not in expected status
    case fileTooLarge                 // 413 — exceeds 100 MB server limit (applies to exported output)
    case unsupportedFormat            // 415 — MIME not in {mp4, quicktime, m4v}
    case unauthorized                 // 401 / session expired beyond recovery
    case networkError(Error)          // URLError or transport failure

    // Client-side 360p export (Commit 2) — see JugglingVideoExportService.
    case exportUnsupported            // source has no compatible re-encoding preset
    case exportFailed(String)         // AVAssetExportSession finished with status == .failed
    case exportCancelled              // export cancelled by the user
    case invalidExportOutput          // exported file failed post-export validation
    case insufficientStorage          // export failed due to lack of free disk space

    var errorDescription: String? {
        switch self {
        case .noConsent:              return "Juggling service consent required."
        case .invalidState(let d):   return "Cannot upload: video in wrong state. \(d ?? "")".trimmingCharacters(in: .whitespaces)
        case .fileTooLarge:          return "A videófájl túl nagy. A maximális méret 100 MB."
        case .unsupportedFormat:     return "Unsupported video format. Use MP4 or MOV."
        case .unauthorized:          return "Session expired. Please log in again."
        case .networkError:          return "Network error. Please try again."
        case .exportUnsupported:     return "A videó formátuma nem támogatja a tömörítést. Válassz másik videót."
        case .exportFailed:          return "A videó tömörítése sikertelen. Próbáld újra."
        case .exportCancelled:       return "A feldolgozás megszakítva."
        case .invalidExportOutput:   return "A tömörített videó érvénytelen. Próbálj másik videót."
        case .insufficientStorage:   return "Nincs elég szabad tárhely a videó feldolgozásához."
        }
    }

    static func == (lhs: JugglingUploadError, rhs: JugglingUploadError) -> Bool {
        switch (lhs, rhs) {
        case (.noConsent,       .noConsent):       return true
        case (.fileTooLarge,    .fileTooLarge):    return true
        case (.unsupportedFormat, .unsupportedFormat): return true
        case (.unauthorized,    .unauthorized):    return true
        case (.invalidState(let a), .invalidState(let b)): return a == b
        case (.networkError,    .networkError):    return true
        case (.exportUnsupported, .exportUnsupported): return true
        case (.exportFailed,    .exportFailed):    return true
        case (.exportCancelled, .exportCancelled): return true
        case (.invalidExportOutput, .invalidExportOutput): return true
        case (.insufficientStorage, .insufficientStorage): return true
        default:                                   return false
        }
    }
}

// MARK: — Ball Detection types (AN-3B2C-1)

struct BallDetectionOut: Decodable, Equatable {
    let id:                   UUID
    let contactEventId:       UUID
    let videoId:              UUID
    // "mobilenet_ssd_v1" (automatic) | "manual" (user override)
    let detectionSource:      String
    let ballX:                Double?      // nil when noBallDetected=true
    let ballY:                Double?
    let confidence:           Double?
    let worldXM:              Double?      // nil in Phase 2C-1 (pitch config not yet applied)
    let worldYM:              Double?
    let modelVersion:         String?
    let noBallDetected:       Bool
    let excludedFromTraining: Bool
    // Opció A: original auto state frozen on first manual override; nil when manual-first
    let autoBallX:            Double?
    let autoBallY:            Double?
    // AN-3B2C-1 follow-up: model confidence at auto detection time; nil for pre-migration rows
    let autoBallConfidence:   Double?
    let createdAt:            Date
    let updatedAt:            Date

    enum CodingKeys: String, CodingKey {
        case id, confidence
        case contactEventId       = "contact_event_id"
        case videoId              = "video_id"
        case detectionSource      = "detection_source"
        case ballX                = "ball_x"
        case ballY                = "ball_y"
        case worldXM              = "world_x_m"
        case worldYM              = "world_y_m"
        case modelVersion         = "model_version"
        case noBallDetected       = "no_ball_detected"
        case excludedFromTraining = "excluded_from_training"
        case autoBallX            = "auto_ball_x"
        case autoBallY            = "auto_ball_y"
        case autoBallConfidence   = "auto_confidence"
        case createdAt            = "created_at"
        case updatedAt            = "updated_at"
    }
}

struct BallDetectionManualRequest: Encodable {
    let ballX:          Double?    // nil when noBallDetected=true
    let ballY:          Double?
    let confidence:     Double?
    let noBallDetected: Bool

    enum CodingKeys: String, CodingKey {
        case ballX          = "ball_x"
        case ballY          = "ball_y"
        case confidence
        case noBallDetected = "no_ball_detected"
    }
}

enum BallDetectionState: Equatable {
    case notFetched                    // onAppear not yet called
    case fetching                      // GET in-flight
    case loaded(BallDetectionOut)      // successful fetch
    case notFound                      // 404 — Celery not yet run; polling active
    case featureDisabled               // 503 — BALL_DETECTION_ENABLED=false
    case networkError(String)          // transport failure

    static func == (lhs: BallDetectionState, rhs: BallDetectionState) -> Bool {
        switch (lhs, rhs) {
        case (.notFetched,       .notFetched):      return true
        case (.fetching,         .fetching):        return true
        case (.loaded(let a),    .loaded(let b)):   return a == b
        case (.notFound,         .notFound):        return true
        case (.featureDisabled,  .featureDisabled): return true
        case (.networkError(let a), .networkError(let b)): return a == b
        default:                                    return false
        }
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

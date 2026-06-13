import Foundation

// MARK: — ContactTaxonomyStore
//
// Source-of-truth hierarchy (AN-2):
//   1. Backend GET /api/v1/users/me/juggling/taxonomy  (primary, ETag cached)
//   2. Bundled contact_types_v1.json                    (offline fallback)
//
// The bundled JSON is a deterministic copy of datasets/juggling/contact_types_v1.json.
// A test (AN2-T01) verifies the bundled copy matches the dataset source by checksum.
// CI must fail if they diverge (scripts/check_taxonomy_bundle_drift.py).
//
// taxonomy() is always available synchronously from the bundled fallback;
// refreshFromBackend() updates it without blocking annotation sessions.

@MainActor
final class ContactTaxonomyStore: ObservableObject {

    @Published private(set) var document: TaxonomyDocument?
    @Published private(set) var loadError: TaxonomyError?

    private var cachedETag: String?
    private let cacheFileURL: URL
    private let authManager: AuthManager

    // Checksum of the bundled file as shipped. Updated by the drift-guard script.
    // If bundled content changes, this constant must be updated in the same commit.
    static let bundledChecksum = "7198de62733493537450275dadc6f445"  // MD5 of contact_types_v1.json

    init(authManager: AuthManager, cacheDirectory: URL? = nil) {
        self.authManager = authManager
        let dir = cacheDirectory ?? FileManager.default
            .urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("juggling_taxonomy", isDirectory: true)
        self.cacheFileURL = dir.appendingPathComponent("taxonomy_cache.json")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    // MARK: — Public API

    // Load bundled taxonomy synchronously. Call on app launch before any annotation session.
    func loadBundled() {
        do {
            let doc = try Self.decodeBundled()
            try validate(doc)
            self.document = doc
            self.loadError = nil
        } catch let e as TaxonomyError {
            self.loadError = e
        } catch {
            self.loadError = .decodeFailed(error)
        }
    }

    // Try to update from backend. Falls back silently on any error.
    // Sets document to updated version only if decode + validation pass.
    func refreshFromBackend() async {
        do {
            let updated = try await fetchFromBackend()
            self.document = updated
            self.loadError = nil
        } catch {
            // Backend unavailable or invalid — existing document (bundled or cached) remains.
        }
    }

    // Returns all contact type keys. Returns empty if not loaded (callers must loadBundled first).
    var allKeys: [String] { document?.allKeys ?? [] }
    var stableKeys: [String] { document?.stableKeys ?? [] }
    var groups: [TaxonomyGroup] { document?.groups ?? [] }

    // MARK: — Bundled decode (static — usable in tests)

    static func decodeBundled() throws -> TaxonomyDocument {
        guard let url = Bundle.main.url(forResource: "contact_types_v1", withExtension: "json") else {
            throw TaxonomyError.bundleFileMissing
        }
        let data = try Data(contentsOf: url)
        return try decodeAndValidate(data)
    }

    // MARK: — Private

    private func fetchFromBackend() async throws -> TaxonomyDocument {
        let path = "/api/v1/users/me/juggling/taxonomy"
        let headers: [String: String]? = cachedETag.map { ["If-None-Match": $0] }

        // Use raw token inject rather than authenticated* wrapper so we can inspect status code.
        // 304 is not an error — it means our ETag is still valid.
        guard let token = authManager.accessToken else { throw APIError.unauthorized }

        let (data, response) = try await APIClient.getRaw(path: path, token: token, extraHeaders: headers ?? [:])

        guard let http = response as? HTTPURLResponse else { throw APIError.networkError(URLError(.badServerResponse)) }

        switch http.statusCode {
        case 304:
            // ETag still valid — decode from cache file if present, else use current in-memory doc
            if let cached = try? loadFromCacheFile() { return cached }
            if let doc = document { return doc }
            throw TaxonomyError.decodeFailed(URLError(.cannotDecodeContentData))

        case 200:
            let doc = try Self.decodeAndValidate(data)
            // Persist to cache file
            try? saveToCacheFile(data: data)
            // Save ETag for next request
            cachedETag = http.allHeaderFields["Etag"] as? String
                      ?? http.allHeaderFields["etag"] as? String
            return doc

        case 401:
            // Let AuthManager refresh and retry once via caller
            throw APIError.httpError(statusCode: 401, detail: nil)

        default:
            throw APIError.httpError(statusCode: http.statusCode, detail: nil)
        }
    }

    // Internal (not private) so AN2-T04 can exercise validation with malformed fixtures.
    static func decodeAndValidate(_ data: Data) throws -> TaxonomyDocument {
        let decoder = JSONDecoder()
        let doc: TaxonomyDocument
        do {
            doc = try decoder.decode(TaxonomyDocument.self, from: data)
        } catch {
            throw TaxonomyError.decodeFailed(error)
        }
        try validate(doc)
        return doc
    }

    static func validate(_ doc: TaxonomyDocument) throws {
        guard doc.taxonomyVersion == "v1"     else { throw TaxonomyError.wrongVersion(doc.taxonomyVersion) }
        guard doc.totalCount == 18            else { throw TaxonomyError.invalidTotalCount(doc.totalCount) }
        guard doc.stableCount == 17           else { throw TaxonomyError.invalidStableCount(doc.stableCount) }
        guard doc.allKeys.contains("custom_other") else { throw TaxonomyError.missingCustomOther }
        guard !doc.allKeys.contains("thigh")  else { throw TaxonomyError.forbiddenThighKey }
    }

    // Non-static wrapper for instance calls
    private func validate(_ doc: TaxonomyDocument) throws {
        try Self.validate(doc)
    }

    private func loadFromCacheFile() throws -> TaxonomyDocument {
        let data = try Data(contentsOf: cacheFileURL)
        return try Self.decodeAndValidate(data)
    }

    private func saveToCacheFile(data: Data) throws {
        // Atomic write via temp file + replace
        let tmp = cacheFileURL.deletingLastPathComponent()
            .appendingPathComponent("taxonomy_cache_tmp.json")
        try data.write(to: tmp, options: .atomic)
        _ = try FileManager.default.replaceItemAt(cacheFileURL, withItemAt: tmp)
    }
}

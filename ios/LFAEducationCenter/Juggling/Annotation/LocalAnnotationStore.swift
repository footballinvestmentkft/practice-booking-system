import Foundation
import CryptoKit

// MARK: — AnnotationSessionFile
//
// On-disk format for one video's annotation session.
// schemaVersion allows future migration without discarding data.
// checksum covers drafts array only (for integrity validation on load).

struct AnnotationSessionFile: Codable {
    var schemaVersion:   Int              // current: 1
    var userId:          Int              // isolation key — drafts are user-scoped
    var videoId:         String
    var taxonomyVersion: String           // "v1"
    var lastUpdatedAt:   Date
    var drafts:          [ContactEventDraft]
    var checksum:        String           // SHA256 hex of drafts JSON bytes (integrity check)
}

// MARK: — LocalAnnotationStore
//
// File-based Codable store. One JSON file per (userId, videoId) pair.
// File path: {documents}/juggling_annotations/{userId}/{videoId}.json
//
// Write strategy: atomic via temp-file + FileManager.replaceItemAt.
// On corruption: quarantine original → new empty store → attempt server re-sync signal.
// The draft is NEVER silently discarded — quarantine preserves bytes for recovery.
//
// User isolation: file path includes userId so user A never reads user B's drafts.
// On logout or user switch: caller is responsible for not opening sessions with wrong userId.

@MainActor
final class LocalAnnotationStore {

    private let baseDirectory: URL
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(baseDirectory: URL? = nil) {
        let dir = baseDirectory ?? FileManager.default
            .urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("juggling_annotations", isDirectory: true)
        self.baseDirectory = dir
        self.decoder = JSONDecoder()
        self.decoder.dateDecodingStrategy = .iso8601
        self.encoder = JSONEncoder()
        self.encoder.dateEncodingStrategy = .iso8601
        self.encoder.outputFormatting = .sortedKeys
    }

    // MARK: — Public interface

    // Load session. On corruption: quarantine the bad file + return .quarantined
    // (the caller decides whether/how to start a fresh session).
    func load(userId: Int, videoId: String) -> AnnotationLoadResult {
        let url = fileURL(userId: userId, videoId: videoId)
        guard FileManager.default.fileExists(atPath: url.path) else {
            return .notFound
        }
        do {
            let data = try Data(contentsOf: url)
            let session = try decoder.decode(AnnotationSessionFile.self, from: data)
            // Integrity check
            let expectedChecksum = try checksumOf(drafts: session.drafts)
            guard session.checksum == expectedChecksum else {
                let qURL = try quarantine(fileAt: url, reason: "checksum_mismatch")
                return .quarantined(quarantineURL: qURL, hasLocalOnlyEvents: false)
            }
            return .loaded(session)
        } catch {
            // Corrupt / undecodable
            let qURL = (try? quarantine(fileAt: url, reason: "decode_failure")) ?? url
            let hasLocalOnly = hasLocalOnlyEventsInRawBytes(
                at: url.deletingLastPathComponent()
                    .appendingPathComponent("quarantine")
                    .appendingPathComponent(url.lastPathComponent + "_quarantine")
            )
            return .quarantined(quarantineURL: qURL, hasLocalOnlyEvents: hasLocalOnly)
        }
    }

    // Save session atomically. Throws on write failure (caller decides how to handle).
    func save(session: inout AnnotationSessionFile) throws {
        session.lastUpdatedAt = Date()
        session.checksum = try checksumOf(drafts: session.drafts)

        let url = fileURL(userId: session.userId, videoId: session.videoId)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        let data = try encoder.encode(session)

        // Write to temp file first
        let tmpURL = url.deletingLastPathComponent()
            .appendingPathComponent(".\(session.videoId)_tmp.json")
        try data.write(to: tmpURL, options: [.atomic])

        // Atomic replace (preserves previous-good as backup via replaceItemAt's built-in backup)
        _ = try FileManager.default.replaceItemAt(url, withItemAt: tmpURL,
                                                   backupItemName: "\(session.videoId).bak.json",
                                                   options: [])
    }

    // Delete session file (e.g. after successful finish + server confirm).
    func delete(userId: Int, videoId: String) {
        let url = fileURL(userId: userId, videoId: videoId)
        try? FileManager.default.removeItem(at: url)
    }

    // MARK: — Diagnostics (read-only; no side effects)

    // Exposes the on-disk session path for a (userId, videoId) pair without
    // touching the filesystem. Used by AnnotationDebugOverlay (DEBUG only).
    func sessionFileURL(userId: Int, videoId: String) -> URL {
        fileURL(userId: userId, videoId: videoId)
    }

    // Read-only existence check — does not create, read, or modify the file.
    func sessionFileExists(userId: Int, videoId: String) -> Bool {
        FileManager.default.fileExists(atPath: fileURL(userId: userId, videoId: videoId).path)
    }

    // The quarantine directory for a (userId, videoId) pair. Does not create it.
    func quarantineDirectory(userId: Int, videoId: String) -> URL {
        fileURL(userId: userId, videoId: videoId)
            .deletingLastPathComponent()
            .appendingPathComponent("quarantine", isDirectory: true)
    }

    // MARK: — Session factory

    func emptySession(userId: Int, videoId: String, taxonomyVersion: String = "v1") -> AnnotationSessionFile {
        AnnotationSessionFile(
            schemaVersion:   1,
            userId:          userId,
            videoId:         videoId,
            taxonomyVersion: taxonomyVersion,
            lastUpdatedAt:   Date(),
            drafts:          [],
            checksum:        ""
        )
    }

    // MARK: — Private helpers

    private func fileURL(userId: Int, videoId: String) -> URL {
        baseDirectory
            .appendingPathComponent("\(userId)", isDirectory: true)
            .appendingPathComponent("\(videoId).json")
    }

    private func checksumOf(drafts: [ContactEventDraft]) throws -> String {
        let data = try encoder.encode(drafts)
        let digest = SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    // Move file to quarantine subdirectory preserving original bytes.
    // Returns the quarantine URL.
    @discardableResult
    private func quarantine(fileAt url: URL, reason: String) throws -> URL {
        let quarantineDir = url.deletingLastPathComponent()
            .appendingPathComponent("quarantine", isDirectory: true)
        try FileManager.default.createDirectory(at: quarantineDir, withIntermediateDirectories: true)
        let timestamp = Int(Date().timeIntervalSince1970)
        let qName = "\(url.lastPathComponent)_\(reason)_\(timestamp)"
        let qURL  = quarantineDir.appendingPathComponent(qName)
        try FileManager.default.moveItem(at: url, to: qURL)
        return qURL
    }

    // Best-effort check: scan quarantined bytes for "localOnly" status marker.
    // Used only to determine if the blocking recovery signal should fire.
    private func hasLocalOnlyEventsInRawBytes(at url: URL) -> Bool {
        guard let data = try? Data(contentsOf: url),
              let raw = String(data: data, encoding: .utf8) else { return false }
        return raw.contains("\"localOnly\"")
    }
}

// MARK: — AnnotationLoadResult

enum AnnotationLoadResult {
    // No session file exists on disk yet — safe to create a fresh empty session.
    case notFound
    case loaded(AnnotationSessionFile)
    // File existed but was corrupt/checksum-mismatched and has been moved to
    // quarantine/ (original bytes preserved). The path is now free, but the
    // caller must surface loadWarning to the user before starting fresh.
    case quarantined(quarantineURL: URL, hasLocalOnlyEvents: Bool)
}

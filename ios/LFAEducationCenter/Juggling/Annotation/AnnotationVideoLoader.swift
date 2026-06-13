import Foundation

// MARK: — Testability seams

/// Minimal protocol wrapping URLSessionDownloadTask so tests can inject a mock.
/// AVFoundation's URLSessionDownloadTask has no public initializer, so we
/// abstract only the three properties / methods we actually use.
protocol URLSessionTaskProtocol: AnyObject {
    func resume()
    func cancel()
    var progress: Progress { get }
}
extension URLSessionDownloadTask: URLSessionTaskProtocol {}

/// Protocol wrapping URLSession.downloadTask for injection in unit tests.
protocol URLSessionDownloadProtocol: AnyObject {
    func annotationDownloadTask(
        with request: URLRequest,
        completionHandler: @escaping (URL?, URLResponse?, Error?) -> Void
    ) -> URLSessionTaskProtocol
}

extension URLSession: URLSessionDownloadProtocol {
    func annotationDownloadTask(
        with request: URLRequest,
        completionHandler: @escaping (URL?, URLResponse?, Error?) -> Void
    ) -> URLSessionTaskProtocol {
        downloadTask(with: request, completionHandler: completionHandler)
    }
}

// MARK: — AnnotationVideoLoader

/// Downloads a juggling annotation video to a user/videoId-isolated temp directory.
///
/// File layout:
///   <tmp>/juggling_annotation/<userId>/<videoId>/video.mp4.partial  ← in-flight
///   <tmp>/juggling_annotation/<userId>/<videoId>/video.mp4          ← complete
///
/// Privacy contracts:
///   - Media URL is first-party only: /api/v1/users/me/juggling/videos/<id>/media
///   - Filenames contain no personal data (userId is opaque Int, no email/name).
///   - Protection: .completeUnlessOpen while partial → .complete after atomic rename.
///   - iCloud backup excluded via isExcludedFromBackup resource value.
///   - userId directory is removed on logout via cleanupAll(userId:).
@MainActor
final class AnnotationVideoLoader: ObservableObject {

    // MARK: — State

    enum LoadState: Equatable {
        case idle
        /// progress: fraction in [0, 1], or -1.0 when Content-Length is absent.
        case downloading(progress: Double)
        case ready(localURL: URL)
        case failed(LoadError)

        static func == (lhs: LoadState, rhs: LoadState) -> Bool {
            switch (lhs, rhs) {
            case (.idle, .idle): return true
            case (.downloading(let a), .downloading(let b)): return a == b
            case (.ready(let a), .ready(let b)): return a == b
            case (.failed(let a), .failed(let b)): return a == b
            default: return false
            }
        }
    }

    enum LoadError: Error, Equatable {
        case diskSpaceInsufficient(availableBytes: Int64)
        case unauthorized
        case httpError(Int)
        case networkError(String)
        case cancelled
    }

    @Published private(set) var state: LoadState = .idle

    // MARK: — Dependencies

    private let authManager: AuthManager
    private let session: URLSessionDownloadProtocol
    private let fileManager: FileManager

    private var currentTask: URLSessionTaskProtocol?
    private var progressObservation: NSKeyValueObservation?
    private var isCancelled = false

    // MARK: — Init

    init(
        authManager: AuthManager,
        session:     URLSessionDownloadProtocol = URLSession.shared,
        fileManager: FileManager               = .default
    ) {
        self.authManager = authManager
        self.session     = session
        self.fileManager = fileManager
    }

    // MARK: — Public API

    /// Begin downloading the video for `videoId`, scoped to `userId`.
    /// No-op when not idle — call `reset()` first to re-trigger a completed or failed load.
    func load(videoId: String, userId: Int) async {
        guard case .idle = state else { return }

        // Disk-space preflight: require 200 MB free before starting.
        let available = diskSpaceAvailable()
        guard available >= 200 * 1_048_576 else {
            state = .failed(.diskSpaceInsufficient(availableBytes: available))
            return
        }

        let dir        = videoDirectory(userId: userId, videoId: videoId)
        let partialURL = dir.appendingPathComponent("video.mp4.partial")
        let finalURL   = dir.appendingPathComponent("video.mp4")

        try? fileManager.createDirectory(at: dir, withIntermediateDirectories: true)

        guard let token = authManager.accessToken else {
            state = .failed(.unauthorized)
            return
        }

        isCancelled = false
        state = .downloading(progress: -1.0)

        let path = "/api/v1/users/me/juggling/videos/\(videoId)/media"

        // First attempt.
        var result = await performDownload(path: path, token: token, to: partialURL)

        // Single 401 refresh + retry (mirrors authenticatedGet pattern in AuthManager).
        if case .failure(.unauthorized) = result, !isCancelled {
            let refreshed = await authManager.performRefresh()
            if refreshed, let newToken = authManager.accessToken {
                result = await performDownload(path: path, token: newToken, to: partialURL)
            } else {
                cleanupPartial(partialURL)
                state = .failed(.unauthorized)
                return
            }
        }

        if isCancelled {
            cleanupPartial(partialURL)
            state = .failed(.cancelled)
            return
        }

        switch result {
        case .success:
            do {
                // Atomic rename: remove stale final file if present.
                if fileManager.fileExists(atPath: finalURL.path) {
                    try fileManager.removeItem(at: finalURL)
                }
                try fileManager.moveItem(at: partialURL, to: finalURL)

                // Upgrade protection: file is now fully readable.
                try fileManager.setAttributes(
                    [.protectionKey: FileProtectionType.complete],
                    ofItemAtPath: finalURL.path
                )

                // Exclude from iCloud / iTunes backup.
                var rv = URLResourceValues()
                rv.isExcludedFromBackup = true
                var mutableURL = finalURL
                try mutableURL.setResourceValues(rv)

                state = .ready(localURL: finalURL)
            } catch {
                cleanupPartial(partialURL)
                state = .failed(.networkError(error.localizedDescription))
            }

        case .failure(let err):
            cleanupPartial(partialURL)
            state = .failed(err)
        }
    }

    /// Cancel an in-flight download. No-op when not downloading.
    func cancel() {
        guard case .downloading = state else { return }
        isCancelled = true
        progressObservation?.invalidate()
        progressObservation = nil
        currentTask?.cancel()
        currentTask = nil
    }

    /// Remove the user's entire annotation video cache directory and reset to idle.
    /// Call on logout to comply with the temp-file privacy contract.
    func cleanupAll(userId: Int) {
        try? fileManager.removeItem(at: userDirectory(userId: userId))
        reset()
    }

    /// Reset to idle without deleting files (e.g. before reloading a failed download).
    func reset() {
        state = .idle
        isCancelled = false
    }

    /// Delete all `.partial` files left by interrupted downloads for `userId`.
    /// Safe to call at app-launch sweep — skips directories and non-partial files.
    static func sweepStalePartials(
        userId:      Int,
        fileManager: FileManager = .default
    ) {
        let base = fileManager.temporaryDirectory
            .appendingPathComponent("juggling_annotation")
            .appendingPathComponent("\(userId)")
        guard let enumerator = fileManager.enumerator(
            at: base,
            includingPropertiesForKeys: nil,
            options: .skipsHiddenFiles
        ) else { return }

        for case let url as URL in enumerator
        where url.pathExtension == "partial" {
            try? fileManager.removeItem(at: url)
        }
    }

    // MARK: — Private download core

    private func performDownload(
        path:    String,
        token:   String,
        to partialURL: URL
    ) async -> Result<Void, LoadError> {
        progressObservation?.invalidate()
        progressObservation = nil

        let request = buildRequest(path: path, token: token)
        let fm      = fileManager   // capture on main actor before suspending

        return await withCheckedContinuation { continuation in
            let task = session.annotationDownloadTask(with: request) { tempURL, response, error in
                if let error = error {
                    let nsErr = error as NSError
                    if nsErr.code == NSURLErrorCancelled {
                        continuation.resume(returning: .failure(.cancelled))
                    } else {
                        continuation.resume(returning: .failure(.networkError(error.localizedDescription)))
                    }
                    return
                }

                guard let http = response as? HTTPURLResponse else {
                    continuation.resume(returning: .failure(.networkError("No HTTP response")))
                    return
                }
                guard (200..<300).contains(http.statusCode) else {
                    if http.statusCode == 401 {
                        continuation.resume(returning: .failure(.unauthorized))
                    } else {
                        continuation.resume(returning: .failure(.httpError(http.statusCode)))
                    }
                    return
                }
                guard let tempURL = tempURL else {
                    continuation.resume(returning: .failure(.networkError("Missing temp file")))
                    return
                }

                do {
                    if fm.fileExists(atPath: partialURL.path) {
                        try fm.removeItem(at: partialURL)
                    }
                    try fm.moveItem(at: tempURL, to: partialURL)
                    // Protect mid-download file (readable during active download only).
                    try fm.setAttributes(
                        [.protectionKey: FileProtectionType.completeUnlessOpen],
                        ofItemAtPath: partialURL.path
                    )
                    continuation.resume(returning: .success(()))
                } catch {
                    continuation.resume(returning: .failure(.networkError(error.localizedDescription)))
                }
            }

            // Observe NSProgress from the download task; dispatch state updates to main actor.
            progressObservation = task.progress.observe(\.fractionCompleted) { [weak self] progress, _ in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    guard case .downloading = self.state else { return }
                    let fraction = progress.totalUnitCount > 0 ? progress.fractionCompleted : -1.0
                    self.state = .downloading(progress: fraction)
                }
            }

            currentTask = task
            task.resume()
        }
    }

    // MARK: — Helpers

    private func buildRequest(path: String, token: String) -> URLRequest {
        let url = URL(string: APIConfig.baseURL + path)!
        var req = URLRequest(url: url)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return req
    }

    private func diskSpaceAvailable() -> Int64 {
        guard let attrs = try? fileManager.attributesOfFileSystem(
            forPath: fileManager.temporaryDirectory.path
        ) else { return 0 }
        return (attrs[.systemFreeSize] as? Int64) ?? 0
    }

    private func videoDirectory(userId: Int, videoId: String) -> URL {
        userDirectory(userId: userId).appendingPathComponent(videoId)
    }

    private func userDirectory(userId: Int) -> URL {
        fileManager.temporaryDirectory
            .appendingPathComponent("juggling_annotation")
            .appendingPathComponent("\(userId)")
    }

    private func cleanupPartial(_ url: URL) {
        try? fileManager.removeItem(at: url)
    }
}

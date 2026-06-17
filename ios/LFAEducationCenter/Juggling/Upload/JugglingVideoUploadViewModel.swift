import Foundation
import Combine

// MARK: — JugglingVideoUploadViewModel
//
// State machine: idle → selecting → preparing → exporting(progress) → uploading(progress) → completing → success
//                                  ↘ failure (any step)
//
// Temp-file lifecycle: coordinator copies picked video → controlled sourceTempURL.
// This ViewModel exports sourceTempURL to a smaller exportedOutputURL via
// JugglingVideoExportService BEFORE any network call. The picker's original
// source file is NEVER uploaded — only exportedOutputURL (and its actual
// codec/MIME) ever reach the API client. All cleanup is deterministic; no
// orphan files after any terminal transition (see cleanup matrix below):
//
//   export success            -> sourceTempURL deleted immediately
//   export failure/cancel      -> sourceTempURL deleted (partial output is
//                                  cleaned up by the export service itself)
//   output too large / invalid -> sourceTempURL + exportedOutputURL deleted
//   full upload success        -> exportedOutputURL deleted
//   cancel (any point)         -> sourceTempURL + exportedOutputURL deleted
//   upload network/API failure -> exportedOutputURL MAY remain (for retry)
//   retry after upload failure -> reuses exportedOutputURL, no re-export
//   export failure              -> retry requires a brand-new picker selection

@MainActor
final class JugglingVideoUploadViewModel: ObservableObject {

    // MARK: — State

    enum UploadState: Equatable {
        case idle
        case selecting
        case preparing
        case exporting(progress: Double)
        case uploading(progress: Double)
        case completing
        case success
        case failure(JugglingUploadError)

        static func == (lhs: UploadState, rhs: UploadState) -> Bool {
            switch (lhs, rhs) {
            case (.idle, .idle), (.selecting, .selecting), (.preparing, .preparing),
                 (.completing, .completing), (.success, .success):
                return true
            case (.exporting(let a), .exporting(let b)):
                return a == b
            case (.uploading(let a), .uploading(let b)):
                return a == b
            case (.failure(let a), .failure(let b)):
                return a == b
            default:
                return false
            }
        }
    }

    // MARK: — Published

    @Published private(set) var state: UploadState = .idle {
        didSet {
            #if DEBUG
            log("state: \(oldValue) -> \(state)")
            #endif
        }
    }

    // MARK: — Callback (fired exactly once, on the main actor, after completeUpload succeeds)

    var onSuccess: (() -> Void)?

    // MARK: — Computed

    var isActive: Bool {
        switch state {
        case .preparing, .exporting, .uploading, .completing: return true
        default: return false
        }
    }

    var errorMessage: String? {
        guard case .failure(let err) = state else { return nil }
        return err.errorDescription
    }

    // MARK: — Internal (exposed via @testable for unit tests)

    private(set) var uploadTask: Task<Void, Never>?

    // MARK: — Private

    private let apiClient: JugglingAnnotationAPIClientProtocol
    private let exportService: JugglingVideoExportServiceProtocol
    private let maxFileSizeBytes: Int64

    // Picker's original file. Never passed to the API client; deleted as soon
    // as the export step finishes (success or failure).
    private var sourceTempURL: URL?

    // JugglingVideoExportService's output. The ONLY file the API client ever
    // sees. Survives upload-stage (network/API) failures so retry() can reuse
    // it without re-exporting.
    private var exportedOutputURL: URL?
    private var exportedMimeType: String?

    private var currentVideoId: String?

    // MIME types accepted for the EXPORTED output (the export service always
    // produces "video/mp4", but this is validated rather than assumed).
    private static let supportedMIMETypes: Set<String> = [
        "video/mp4", "video/quicktime", "video/x-m4v"
    ]

    // MARK: — Init

    init(
        apiClient: JugglingAnnotationAPIClientProtocol,
        exportService: JugglingVideoExportServiceProtocol = JugglingVideoExportService(),
        maxFileSizeBytes: Int64 = 100 * 1024 * 1024
    ) {
        self.apiClient = apiClient
        self.exportService = exportService
        self.maxFileSizeBytes = maxFileSizeBytes
        #if DEBUG
        print("[B3-DIAG][ViewModel] init — maxFileSizeBytes=\(maxFileSizeBytes) bytes")
        #endif
    }

    // MARK: — Picker lifecycle

    func startPicker() {
        guard state == .idle else { return }
        state = .selecting
    }

    func pickerCancelled() {
        #if DEBUG
        log("pickerCancelled() called — currentState=\(state)")
        #endif
        guard case .selecting = state else {
            #if DEBUG
            log("pickerCancelled() IGNORED — state is not .selecting")
            #endif
            return
        }
        state = .idle
    }

    // Called by JugglingVideoPHPicker.Coordinator after copying the picked video to a
    // controlled temp URL. Ownership of tempURL transfers here; this ViewModel is
    // responsible for deleting it in all terminal paths (directly, or via export()).
    // `mimeType` describes the SOURCE file and is informational only — the export
    // step determines the MIME type that is actually uploaded.
    func pickerDidSelect(tempURL: URL, mimeType: String) {
        #if DEBUG
        log("pickerDidSelect(tempFile=\(tempURL.lastPathComponent), sourceMime=\(mimeType)) — currentState=\(state)")
        #endif
        guard case .selecting = state else {
            // A late/spurious callback (state moved on before this arrived) must
            // not leave the picker's temp file behind — this ViewModel is its
            // only owner once pickerDidSelect is called.
            try? FileManager.default.removeItem(at: tempURL)
            #if DEBUG
            log("pickerDidSelect IGNORED — state is not .selecting; deleted orphaned tempFile=\(tempURL.lastPathComponent)")
            #endif
            return
        }
        state = .preparing
        uploadTask = Task { [self] in
            await exportAndUpload(sourceURL: tempURL)
        }
    }

    // MARK: — Upload control

    func cancel() {
        #if DEBUG
        log("cancel() called — currentState=\(state)")
        #endif
        uploadTask?.cancel()
        exportService.cancelExport()
        uploadTask = nil
        cleanupAllTempFiles(reason: "cancel()")
        currentVideoId = nil
        state = .idle
    }

    // Retry behavior depends on what survived the failed attempt:
    //  - If exportedOutputURL is still present, the export already succeeded;
    //    re-run the upload pipeline against it WITHOUT re-exporting.
    //  - Otherwise the export step itself never produced a usable output
    //    (export failure/cancel/invalid-output) — reset to .idle so the
    //    caller must invoke startPicker() for a brand-new selection + export.
    func retry() {
        #if DEBUG
        log("retry() called — currentState=\(state), hasExportedOutput=\(exportedOutputURL != nil)")
        #endif
        guard case .failure = state else { return }
        uploadTask = nil

        if exportedOutputURL != nil {
            state = .preparing
            uploadTask = Task { [self] in
                await runUploadPipeline()
            }
        } else {
            currentVideoId = nil
            state = .idle
        }
    }

    // MARK: — Pipeline: export

    private func exportAndUpload(sourceURL: URL) async {
        #if DEBUG
        log("exportAndUpload started — sourceFile=\(sourceURL.lastPathComponent), fileExists=\(FileManager.default.fileExists(atPath: sourceURL.path))")
        #endif

        guard !Task.isCancelled else {
            try? FileManager.default.removeItem(at: sourceURL)
            return
        }

        sourceTempURL = sourceURL
        state = .exporting(progress: 0)

        let exportResult = await exportService.export(sourceURL: sourceURL) { [weak self] progress in
            Task { @MainActor in
                guard let self = self else { return }
                guard case .exporting = self.state else { return }
                self.state = .exporting(progress: progress)
            }
        }

        guard !Task.isCancelled else {
            #if DEBUG
            log("exportAndUpload — cancelled while/after export() was running")
            #endif
            if case .success(let exported) = exportResult {
                try? FileManager.default.removeItem(at: exported.outputURL)
            }
            cleanupSourceTempFile(reason: "cancelled during export")
            return
        }

        switch exportResult {
        case .failure(let exportError):
            #if DEBUG
            log("exportAndUpload — export failed: \(exportError)")
            #endif
            cleanupSourceTempFile(reason: "export failed")
            state = .failure(mapExportError(exportError))

        case .success(let exported):
            #if DEBUG
            log("exportAndUpload — export succeeded: output=\(exported.outputURL.lastPathComponent), size=\(exported.fileSizeBytes), dims=\(exported.width)x\(exported.height), codec=\(exported.codec), mime=\(exported.mimeType)")
            #endif
            // The source is never uploaded; once we have an exported output
            // (valid or not), the source has served its purpose.
            cleanupSourceTempFile(reason: "export success")

            guard isValidExportResult(exported) else {
                #if DEBUG
                log("exportAndUpload — exported output failed metadata validation")
                #endif
                try? FileManager.default.removeItem(at: exported.outputURL)
                state = .failure(.invalidExportOutput)
                return
            }

            guard exported.fileSizeBytes <= maxFileSizeBytes else {
                #if DEBUG
                log("exportAndUpload — exported output too large: \(exported.fileSizeBytes) > \(maxFileSizeBytes)")
                #endif
                try? FileManager.default.removeItem(at: exported.outputURL)
                state = .failure(.fileTooLarge)
                return
            }

            exportedOutputURL = exported.outputURL
            exportedMimeType = exported.mimeType
            await runUploadPipeline()
        }
    }

    private func mapExportError(_ error: JugglingVideoExportError) -> JugglingUploadError {
        switch error {
        case .sourceUnreadable, .exportUnsupported:
            return .exportUnsupported
        case .cancelled:
            return .exportCancelled
        case .exportFailed(let message):
            if message.localizedCaseInsensitiveContains("space") {
                return .insufficientStorage
            }
            return .exportFailed(message)
        }
    }

    // Validates the exported output before it is allowed anywhere near the
    // upload pipeline: file existence, actual file size, resolution, codec,
    // and supported file type/MIME.
    private func isValidExportResult(_ result: JugglingVideoExportResult) -> Bool {
        guard FileManager.default.fileExists(atPath: result.outputURL.path) else { return false }
        guard result.fileSizeBytes > 0 else { return false }
        guard result.width > 0, result.height > 0 else { return false }
        guard !result.codec.isEmpty, result.codec != "unknown" else { return false }
        guard result.fileType == "mp4" else { return false }
        guard Self.supportedMIMETypes.contains(result.mimeType) else { return false }
        return true
    }

    // MARK: — Pipeline: upload (operates exclusively on exportedOutputURL)

    private func runUploadPipeline() async {
        guard let outputURL = exportedOutputURL, let mimeType = exportedMimeType else { return }

        do {
            // Step 1 — upload-init
            guard !Task.isCancelled else { return }
            #if DEBUG
            log("uploadInit starting — sourceType=uploaded_video, uploadSource=gallery")
            #endif
            let initResp = try await apiClient.uploadInit(
                sourceType: "uploaded_video", uploadSource: "gallery"
            )
            #if DEBUG
            log("uploadInit succeeded — status=\(initResp.status)")
            #endif

            guard !Task.isCancelled else { return }
            currentVideoId = initResp.videoId
            state = .uploading(progress: 0)

            // Step 2 — multipart file upload (exported output only; actual export MIME)
            _ = try await apiClient.uploadVideoFile(
                videoId: initResp.videoId, fileURL: outputURL, mimeType: mimeType
            )

            // Step 3 — complete (triggers server-side analysis queue)
            guard !Task.isCancelled else { return }
            state = .completing

            _ = try await apiClient.completeUpload(videoId: initResp.videoId)

            guard !Task.isCancelled else { return }
            cleanupExportedOutput(reason: "upload pipeline success")
            currentVideoId = nil
            state = .success
            onSuccess?()

        } catch is CancellationError {
            #if DEBUG
            log("upload pipeline threw CancellationError")
            #endif
            // cancel() already performed full cleanup + state reset.
        } catch let err as JugglingUploadError {
            #if DEBUG
            log("upload pipeline failed — JugglingUploadError: \(err)")
            #endif
            // exportedOutputURL is intentionally KEPT here so retry() can
            // re-run the upload without re-exporting.
            state = .failure(err)
        } catch {
            #if DEBUG
            log("upload pipeline failed — error: \(error.localizedDescription)")
            #endif
            state = .failure(.networkError(error))
        }
    }

    // MARK: — Cleanup

    private func cleanupSourceTempFile(reason: String) {
        #if DEBUG
        log("cleanupSourceTempFile — reason=\(reason), hadFile=\(sourceTempURL != nil)")
        #endif
        guard let url = sourceTempURL else { return }
        try? FileManager.default.removeItem(at: url)
        sourceTempURL = nil
    }

    private func cleanupExportedOutput(reason: String) {
        #if DEBUG
        log("cleanupExportedOutput — reason=\(reason), hadFile=\(exportedOutputURL != nil)")
        #endif
        guard let url = exportedOutputURL else { return }
        try? FileManager.default.removeItem(at: url)
        exportedOutputURL = nil
        exportedMimeType = nil
    }

    private func cleanupAllTempFiles(reason: String) {
        cleanupSourceTempFile(reason: reason)
        cleanupExportedOutput(reason: reason)
    }

    #if DEBUG
    private func log(_ message: String) {
        print("[B3-DIAG][ViewModel] \(message)")
    }
    #endif
}

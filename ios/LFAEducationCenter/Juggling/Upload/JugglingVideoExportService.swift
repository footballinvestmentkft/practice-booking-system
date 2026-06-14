import Foundation
import AVFoundation

// MARK: — JugglingVideoExportService
//
// Client-side pre-upload 360p export. Re-encodes a picked source video to a
// smaller MP4/H.264 output before any upload happens. The original (large,
// high-resolution) source file is never returned to the caller and never
// touched/modified by this service.
//
// Preset policy: AVAssetExportPreset640x480 is a bounding-box preset — for a
// 16:9 source it yields ~640x360 (360p landscape); for a 9:16 source it
// yields ~270x480 (360p-equivalent portrait). Aspect ratio and orientation
// (preferredTransform) are preserved automatically because no
// AVVideoComposition is supplied.
//
// No passthrough fallback: if the asset is not compatible with any preset in
// candidatePresets, export fails with .exportUnsupported rather than
// re-uploading the original (unshrunk) file under a different name.

protocol JugglingVideoExportServiceProtocol: AnyObject {
    /// Exports `sourceURL` to a new, smaller temp MP4 file. The source file is
    /// read-only to this call and is never modified or deleted.
    /// `progressHandler` may be called from a background queue with values in [0, 1].
    func export(
        sourceURL: URL,
        progressHandler: @escaping (Double) -> Void
    ) async -> Result<JugglingVideoExportResult, JugglingVideoExportError>

    /// Cancels an in-flight export started by `export`. The corresponding
    /// `export` call resolves with `.failure(.cancelled)` and any partial
    /// output file is deleted. A no-op if no export is in flight.
    func cancelExport()
}

struct JugglingVideoExportResult: Equatable {
    let outputURL: URL
    let fileSizeBytes: Int64
    let width: Int
    let height: Int
    let codec: String
    let fileType: String
    let mimeType: String
}

enum JugglingVideoExportError: Error, Equatable {
    /// Source has no readable video track.
    case sourceUnreadable
    /// No candidate re-encoding preset is compatible with the source asset
    /// (and passthrough is intentionally not used as a fallback).
    case exportUnsupported
    /// AVAssetExportSession finished with status == .failed.
    case exportFailed(String)
    /// cancelExport() was called before completion.
    case cancelled
}

final class JugglingVideoExportService: JugglingVideoExportServiceProtocol {

    // Ordered list of re-encoding presets to try, most-preferred first.
    // AVAssetExportPreset640x480 is a bounding-box preset (preserves aspect
    // ratio + orientation): 16:9 source -> ~640x360, 9:16 source -> ~270x480.
    // Intentionally does NOT include AVAssetExportPresetPassthrough — that
    // would not guarantee any size reduction.
    static let candidatePresets: [String] = [AVAssetExportPreset640x480]

    private let outputDirectory: URL
    private var currentSession: AVAssetExportSession?
    private var progressTimer: Timer?

    init(outputDirectory: URL = FileManager.default.temporaryDirectory) {
        self.outputDirectory = outputDirectory
    }

    // Pure preset-selection logic, exposed for unit testing without needing
    // an AVAsset. Returns nil if no candidate preset is compatible.
    static func selectPreset(from compatiblePresets: [String]) -> String? {
        candidatePresets.first(where: compatiblePresets.contains)
    }

    func export(
        sourceURL: URL,
        progressHandler: @escaping (Double) -> Void
    ) async -> Result<JugglingVideoExportResult, JugglingVideoExportError> {
        #if DEBUG
        log("export start — source=\(sourceURL.lastPathComponent)")
        #endif

        let asset = AVAsset(url: sourceURL)
        guard asset.tracks(withMediaType: .video).first != nil else {
            #if DEBUG
            log("export — source has no readable video track")
            #endif
            return .failure(.sourceUnreadable)
        }

        let compatiblePresets = AVAssetExportSession.exportPresets(compatibleWith: asset)
        guard let preset = Self.selectPreset(from: compatiblePresets) else {
            #if DEBUG
            log("export — no compatible re-encoding preset; compatible=\(compatiblePresets)")
            #endif
            return .failure(.exportUnsupported)
        }

        guard let session = AVAssetExportSession(asset: asset, presetName: preset) else {
            #if DEBUG
            log("export — AVAssetExportSession init failed for preset=\(preset)")
            #endif
            return .failure(.exportUnsupported)
        }

        guard session.supportedFileTypes.contains(.mp4) else {
            #if DEBUG
            log("export — preset=\(preset) does not support mp4 output; supported=\(session.supportedFileTypes)")
            #endif
            return .failure(.exportUnsupported)
        }

        let outputURL = outputDirectory
            .appendingPathComponent("juggling-export-\(UUID().uuidString)")
            .appendingPathExtension("mp4")

        session.outputURL = outputURL
        session.outputFileType = .mp4
        session.shouldOptimizeForNetworkUse = true

        currentSession = session

        #if DEBUG
        log("export — preset=\(preset), output=\(outputURL.lastPathComponent)")
        #endif

        startProgressPolling(session: session, handler: progressHandler)

        let status: AVAssetExportSession.Status = await withCheckedContinuation { continuation in
            session.exportAsynchronously {
                continuation.resume(returning: session.status)
            }
        }

        stopProgressPolling()
        currentSession = nil

        switch status {
        case .completed:
            #if DEBUG
            log("export — completed")
            #endif
            progressHandler(1.0)
            return buildResult(outputURL: outputURL)

        case .cancelled:
            #if DEBUG
            log("export — cancelled")
            #endif
            cleanupPartialOutput(outputURL)
            return .failure(.cancelled)

        default:
            let message = session.error?.localizedDescription ?? "export failed (status=\(status.rawValue))"
            #if DEBUG
            log("export — failed: \(message)")
            #endif
            cleanupPartialOutput(outputURL)
            return .failure(.exportFailed(message))
        }
    }

    func cancelExport() {
        #if DEBUG
        log("cancelExport() called — hasActiveSession=\(currentSession != nil)")
        #endif
        currentSession?.cancelExport()
    }

    // MARK: — Progress polling

    // AVAssetExportSession has no completion-driven progress callback on
    // iOS 14; poll `.progress` on a timer instead.
    private func startProgressPolling(session: AVAssetExportSession, handler: @escaping (Double) -> Void) {
        let timer = Timer(timeInterval: 0.1, repeats: true) { [weak session] _ in
            guard let session = session else { return }
            handler(Double(session.progress))
        }
        progressTimer = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    private func stopProgressPolling() {
        progressTimer?.invalidate()
        progressTimer = nil
    }

    // MARK: — Output metadata

    private func buildResult(outputURL: URL) -> Result<JugglingVideoExportResult, JugglingVideoExportError> {
        let outputAsset = AVAsset(url: outputURL)
        guard let track = outputAsset.tracks(withMediaType: .video).first else {
            cleanupPartialOutput(outputURL)
            return .failure(.exportFailed("exported output has no readable video track"))
        }

        let displaySize = track.naturalSize.applying(track.preferredTransform)
        let width = Int(abs(displaySize.width).rounded())
        let height = Int(abs(displaySize.height).rounded())

        var codec = "unknown"
        if let formatDescription = track.formatDescriptions.first {
            let mediaSubType = CMFormatDescriptionGetMediaSubType(formatDescription as! CMFormatDescription)
            codec = Self.fourCCToString(mediaSubType)
        }

        guard let attrs = try? FileManager.default.attributesOfItem(atPath: outputURL.path),
              let fileSize = attrs[.size] as? Int64 else {
            cleanupPartialOutput(outputURL)
            return .failure(.exportFailed("could not read exported output file attributes"))
        }

        #if DEBUG
        log("export — output metadata: size=\(fileSize) bytes, dims=\(width)x\(height), codec=\(codec)")
        #endif

        return .success(JugglingVideoExportResult(
            outputURL: outputURL,
            fileSizeBytes: fileSize,
            width: width,
            height: height,
            codec: codec,
            fileType: "mp4",
            mimeType: "video/mp4"
        ))
    }

    private static func fourCCToString(_ code: FourCharCode) -> String {
        let bytes: [UInt8] = [
            UInt8((code >> 24) & 0xFF),
            UInt8((code >> 16) & 0xFF),
            UInt8((code >> 8) & 0xFF),
            UInt8(code & 0xFF)
        ]
        let scalars = bytes.map { Character(UnicodeScalar($0)) }
        return String(scalars)
    }

    // MARK: — Cleanup

    private func cleanupPartialOutput(_ url: URL) {
        try? FileManager.default.removeItem(at: url)
    }

    #if DEBUG
    private func log(_ message: String) {
        print("[B3-EXPORT-DIAG] \(message)")
    }
    #endif
}

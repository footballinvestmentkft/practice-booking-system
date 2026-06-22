import AVFoundation
import CoreGraphics

// MARK: — Permission

protocol PermissionProvider: Sendable {
    func cameraAuthorizationStatus() -> AVAuthorizationStatus
    func microphoneAuthorizationStatus() -> AVAuthorizationStatus
    func requestCameraAccess() async -> Bool
    func requestMicrophoneAccess() async -> Bool
}

struct SystemPermissionProvider: PermissionProvider {
    func cameraAuthorizationStatus() -> AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .video) }
    func microphoneAuthorizationStatus() -> AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .audio) }
    func requestCameraAccess() async -> Bool { await AVCaptureDevice.requestAccess(for: .video) }
    func requestMicrophoneAccess() async -> Bool { await AVCaptureDevice.requestAccess(for: .audio) }
}

// MARK: — File store

struct PendingCapture {
    let sessionUUID: String
    let deviceId: String
    let url: URL
    let size: UInt64
}

enum CaptureFileValidation: Equatable {
    case valid(duration: TimeInterval, resolution: CGSize, displayOrientation: String, hasAudio: Bool, transform: String)
    case invalid(reason: String)
}

protocol CaptureFileStore {
    func capturesDirectory() -> URL
    func ensureDirectoryExists() throws
    func availableStorageBytes() -> UInt64
    func outputURL(sessionUUID: String, deviceId: Int) -> URL
    func fileSize(at url: URL) -> UInt64
    func removeItem(at url: URL) throws
    func listPendingCaptures() -> [PendingCapture]
    func removeZeroByteFiles(sessionUUID: String)
    func validateCaptureFile(url: URL) async -> CaptureFileValidation
}

// MARK: — System file store

final class SystemCaptureFileStore: CaptureFileStore {

    func capturesDirectory() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("multicamera_captures", isDirectory: true)
    }

    func ensureDirectoryExists() throws {
        let dir = capturesDirectory()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var mutable = dir
        try mutable.setResourceValues(values)
    }

    func availableStorageBytes() -> UInt64 {
        let attrs = try? FileManager.default.attributesOfFileSystem(forPath: NSHomeDirectory())
        return (attrs?[.systemFreeSize] as? UInt64) ?? 0
    }

    func outputURL(sessionUUID: String, deviceId: Int) -> URL {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withFullDate, .withFullTime, .withTimeZone]
        let ts = formatter.string(from: Date()).replacingOccurrences(of: ":", with: "")
        let name = "session_\(sessionUUID)_device_\(deviceId)_\(ts).mov"
        return capturesDirectory().appendingPathComponent(name)
    }

    func fileSize(at url: URL) -> UInt64 {
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        return (attrs?[.size] as? UInt64) ?? 0
    }

    func removeItem(at url: URL) throws {
        try FileManager.default.removeItem(at: url)
    }

    func listPendingCaptures() -> [PendingCapture] {
        let dir = capturesDirectory()
        guard let files = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey]) else { return [] }
        return files.compactMap { url in
            let name = url.deletingPathExtension().lastPathComponent
            let parts = name.split(separator: "_")
            guard parts.count >= 4, parts[0] == "session" else { return nil }
            let uuid = String(parts[1])
            let devId = String(parts.count > 3 ? parts[3] : "0")
            return PendingCapture(sessionUUID: uuid, deviceId: devId, url: url, size: fileSize(at: url))
        }
    }

    func removeZeroByteFiles(sessionUUID: String) {
        let files = listPendingCaptures().filter { $0.sessionUUID == sessionUUID && $0.size == 0 }
        for f in files { try? removeItem(at: f.url) }
    }

    func validateCaptureFile(url: URL) async -> CaptureFileValidation {
        let asset = AVURLAsset(url: url)
        do {
            let duration: TimeInterval
            let videoTracks: [AVAssetTrack]
            let audioTracks: [AVAssetTrack]

            if #available(iOS 16, *) {
                duration = try await asset.load(.duration).seconds
                videoTracks = try await asset.loadTracks(withMediaType: .video)
                audioTracks = try await asset.loadTracks(withMediaType: .audio)
            } else {
                duration = asset.duration.seconds
                videoTracks = asset.tracks(withMediaType: .video)
                audioTracks = asset.tracks(withMediaType: .audio)
            }

            guard !videoTracks.isEmpty else { return .invalid(reason: "Nincs video track") }
            guard !audioTracks.isEmpty else { return .invalid(reason: "Nincs audio track") }
            guard duration >= 1.0 else { return .invalid(reason: "Felvétel túl rövid (\(String(format: "%.1f", duration))s)") }
            guard fileSize(at: url) > 0 else { return .invalid(reason: "0 byte fájl") }

            let naturalSize: CGSize
            let transform: CGAffineTransform
            if #available(iOS 16, *) {
                naturalSize = try await videoTracks[0].load(.naturalSize)
                transform = try await videoTracks[0].load(.preferredTransform)
            } else {
                naturalSize = videoTracks[0].naturalSize
                transform = videoTracks[0].preferredTransform
            }

            let orientation = Self.displayOrientation(from: transform)
            let transformStr = "a=\(transform.a) b=\(transform.b) c=\(transform.c) d=\(transform.d) tx=\(transform.tx) ty=\(transform.ty)"

            return .valid(duration: duration, resolution: naturalSize, displayOrientation: orientation, hasAudio: true, transform: transformStr)
        } catch {
            return .invalid(reason: "Asset loading: \(error.localizedDescription)")
        }
    }

    static func displayOrientation(from t: CGAffineTransform) -> String {
        let angle = atan2(t.b, t.a)
        switch angle {
        case _ where abs(angle - .pi / 2) < 0.01: return "portrait"
        case _ where abs(angle + .pi / 2) < 0.01: return "portraitUpsideDown"
        case _ where abs(angle - .pi) < 0.01 || abs(angle + .pi) < 0.01: return "landscapeLeft"
        case _ where abs(angle) < 0.01: return "landscapeRight"
        default: return "unknown(\(String(format: "%.2f", angle))rad)"
        }
    }
}

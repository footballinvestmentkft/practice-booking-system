import AVFoundation

// MARK: — Capture Quality Policy (docs/MEDIA_PIPELINE_PLAN.md baseline)
//
// Primary: 1280x720 @ 30fps. Fallback: 640x360 @ 30fps, if the primary
// resolution/frame-rate combination isn't available on the active device's
// camera. Preview (CameraFramePublisher, GoProStreamProbe) is a SEPARATE,
// lower-fidelity profile and is not affected by this selector.

enum CaptureProfile {
    case hd720
    case sd360

    var targetWidth: Int32 {
        switch self {
        case .hd720: return 1280
        case .sd360: return 640
        }
    }

    var targetHeight: Int32 {
        switch self {
        case .hd720: return 720
        case .sd360: return 360
        }
    }

    var targetFPS: Double { 30 }

    var label: String {
        switch self {
        case .hd720: return "1280x720"
        case .sd360: return "640x360"
        }
    }
}

/// A plain-data mirror of the AVCaptureDevice.Format properties this
/// selector cares about — lets the selection logic be unit tested without
/// a physical camera (AVCaptureDevice.Format itself cannot be constructed
/// in a test target).
struct FormatDescriptor: Equatable {
    let width: Int32
    let height: Int32
    let maxFrameRate: Double
}

enum CaptureFormatSelector {

    /// Picks the best matching descriptor for `profile.targetWidth/Height`
    /// at >= `profile.targetFPS`. Falls back to `.sd360` if `.hd720` isn't
    /// satisfiable, then returns nil if NEITHER is — callers must treat nil
    /// as an explicit failure (no silent fallback to some other resolution).
    static func select(from descriptors: [FormatDescriptor]) -> (descriptor: FormatDescriptor, profile: CaptureProfile)? {
        if let match = bestMatch(for: .hd720, in: descriptors) {
            return (match, .hd720)
        }
        if let match = bestMatch(for: .sd360, in: descriptors) {
            return (match, .sd360)
        }
        return nil
    }

    private static func bestMatch(for profile: CaptureProfile, in descriptors: [FormatDescriptor]) -> FormatDescriptor? {
        descriptors
            .filter { $0.width == profile.targetWidth && $0.height == profile.targetHeight && $0.maxFrameRate >= profile.targetFPS }
            .min(by: { $0.maxFrameRate < $1.maxFrameRate }) // tightest fit above target, not the highest-fps variant
    }

    /// Maps an `AVCaptureDevice`'s real formats to `FormatDescriptor`s, runs
    /// `select`, then returns the matching real `AVCaptureDevice.Format` so
    /// the caller can assign it to `activeFormat`.
    static func selectRealFormat(for device: AVCaptureDevice) -> (format: AVCaptureDevice.Format, profile: CaptureProfile)? {
        let entries: [(AVCaptureDevice.Format, FormatDescriptor)] = device.formats.map { format in
            let dims = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
            let maxFPS = format.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0
            return (format, FormatDescriptor(width: dims.width, height: dims.height, maxFrameRate: maxFPS))
        }
        guard let selected = select(from: entries.map(\.1)) else { return nil }
        guard let realFormat = entries.first(where: { $0.1 == selected.descriptor })?.0 else { return nil }
        return (realFormat, selected.profile)
    }
}

import AVFoundation
import UIKit

// MARK: — Orientation/Aspect handling
//
// Replaces the hardcoded `.portrait` connection.videoOrientation that
// existed in CapturePreviewLayer, SessionCaptureManager, and
// CameraFramePublisher (all three set it once, unconditionally, regardless
// of actual device orientation). Capture/preview connections now read the
// CURRENT interface orientation each time they're (re)configured.
//
// NOTE: AVCaptureVideoOrientation.landscapeLeft/Right are defined opposite
// to UIInterfaceOrientation.landscapeLeft/Right by Apple's own convention
// (a well-known AVFoundation gotcha) — the mapping below accounts for that.
// This has NOT been physically verified on a rotated device yet; the
// capture_metadata_diag.json `orientation` field exists specifically so a
// physical test can confirm the recorded file's actual orientation matches
// the device's actual orientation at record time.
enum OrientationMapper {

    static func captureOrientation(for interfaceOrientation: UIInterfaceOrientation) -> AVCaptureVideoOrientation {
        switch interfaceOrientation {
        case .portrait: return .portrait
        case .portraitUpsideDown: return .portraitUpsideDown
        case .landscapeLeft: return .landscapeRight
        case .landscapeRight: return .landscapeLeft
        default: return .portrait
        }
    }

    static var currentInterfaceOrientation: UIInterfaceOrientation {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }?.interfaceOrientation
            ?? UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first?.interfaceOrientation
            ?? .portrait
    }

    /// Coarse label for metadata/diagnostics — "portrait" or "landscape".
    static var currentOrientationLabel: String {
        switch currentInterfaceOrientation {
        case .portrait, .portraitUpsideDown: return "portrait"
        case .landscapeLeft, .landscapeRight: return "landscape"
        default: return "unknown"
        }
    }

    /// Applies the current interface orientation to a capture connection,
    /// if the connection supports orientation control. Call this at setup
    /// time AND whenever the device rotates (see `UIDevice.orientationDidChangeNotification`
    /// observers added by the call sites), since a single one-time
    /// assignment is exactly the bug this type replaces.
    static func applyCurrentOrientation(to connection: AVCaptureConnection?) {
        guard let connection, connection.isVideoOrientationSupported else { return }
        connection.videoOrientation = captureOrientation(for: currentInterfaceOrientation)
    }
}

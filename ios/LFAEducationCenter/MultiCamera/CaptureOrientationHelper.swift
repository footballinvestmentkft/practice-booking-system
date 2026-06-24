import AVFoundation
import UIKit

#if DEBUG
enum CaptureOrientationHelper {

    static func avCaptureOrientation(for interfaceOrientation: UIInterfaceOrientation) -> AVCaptureVideoOrientation {
        switch interfaceOrientation {
        case .landscapeLeft:      return .landscapeLeft
        case .landscapeRight:     return .landscapeRight
        case .portraitUpsideDown: return .portraitUpsideDown
        case .portrait:           return .portrait
        case .unknown:            return .portrait
        @unknown default:         return .portrait
        }
    }

    // Returns the interface orientation of the currently active foreground window scene.
    // Falls back to .portrait for faceUp / faceDown / unknown device positions.
    static func currentInterfaceOrientation() -> UIInterfaceOrientation {
        UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }?
            .interfaceOrientation ?? .portrait
    }

    static func currentAVCaptureOrientation() -> AVCaptureVideoOrientation {
        avCaptureOrientation(for: currentInterfaceOrientation())
    }
}

extension AVCaptureVideoOrientation {
    var name: String {
        switch self {
        case .portrait:           return "portrait"
        case .portraitUpsideDown: return "portraitUpsideDown"
        case .landscapeLeft:      return "landscapeLeft"
        case .landscapeRight:     return "landscapeRight"
        @unknown default:         return "unknown"
        }
    }
}
#endif

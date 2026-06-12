import Foundation

// Accepts a gesture only after it has been held continuously for `holdDuration`.
//
// Designed for 60 FPS ARKit callbacks: update(detected:) is called every ~16ms.
// If detection drops below the threshold for even one frame, the timer resets.
// This prevents a fleeting over-threshold blendshape spike from confirming a gesture.
//
// onConfirmed fires exactly once per gesture cycle; call reset() before the next step.
@MainActor
final class GestureStabilizer {

    private let holdDuration: TimeInterval   // seconds
    private var detectedSince: Date?
    private(set) var isConfirmed: Bool = false

    var onConfirmed: (() -> Void)?

    init(holdDurationMs: Int) {
        holdDuration = Double(holdDurationMs) / 1000.0
    }

    // MARK: — State queries

    /// Progress toward confirmation [0.0 … 1.0].
    /// 0 = not detecting; 1 = confirmed or at threshold.
    var holdProgress: Double {
        if isConfirmed { return 1.0 }
        guard let since = detectedSince else { return 0.0 }
        return min(Date().timeIntervalSince(since) / holdDuration, 1.0)
    }

    var isDetecting: Bool { detectedSince != nil && !isConfirmed }

    // MARK: — Feed per-frame detection result

    func update(detected: Bool) {
        guard !isConfirmed else { return }

        if detected {
            if detectedSince == nil {
                detectedSince = Date()
            } else if Date().timeIntervalSince(detectedSince!) >= holdDuration {
                isConfirmed = true
                onConfirmed?()
            }
        } else {
            detectedSince = nil    // reset — must hold continuously
        }
    }

    // MARK: — Reset for next step

    func reset() {
        detectedSince = nil
        isConfirmed   = false
    }
}

import ARKit
import Foundation

// State machine for the ARKit auto-capture spike liveness flow.
//
// Privacy rules (enforced structurally):
//   - Raw angle/blendshape values are @Published only in #if DEBUG builds.
//   - In Release builds, the debugValues property does not exist; the fields
//     are absent from binary and cannot be read or logged.
//   - No data is sent to the backend from this ViewModel.
//   - No images are stored on disk.
//
// Backend upload and image persistence are Phase 3 items — out of scope for spike.
@MainActor
final class SpikeLivenessViewModel: ObservableObject {

    // MARK: — Step state

    enum StepState: Equatable {
        case detecting                  // face visible, not yet at threshold
        case stabilizing(Double)        // threshold met, holding [0.0…1.0]
        case confirmed                  // gesture accepted — showing visual confirmation
        case timedOut                   // step timeout reached
        case complete                   // all gestures done
        case noFace                     // ARSession running but no face anchor present

        static func == (lhs: StepState, rhs: StepState) -> Bool {
            switch (lhs, rhs) {
            case (.detecting, .detecting),
                 (.confirmed, .confirmed),
                 (.timedOut, .timedOut),
                 (.complete, .complete),
                 (.noFace, .noFace):
                return true
            case (.stabilizing(let a), .stabilizing(let b)):
                return abs(a - b) < 0.01
            default:
                return false
            }
        }
    }

    // MARK: — Published state

    @Published private(set) var currentStepIndex: Int       = 0
    @Published private(set) var stepState:         StepState = .noFace
    @Published private(set) var retryCount:         Int      = 0

    // Raw sensor values — only compiled into DEBUG builds.
    // These power the calibration overlay and are never logged in Release.
#if DEBUG
    struct DebugValues {
        var yaw:        Float = 0
        var pitch:      Float = 0
        var blinkLeft:  Float = 0
        var blinkRight: Float = 0
        var smileLeft:  Float = 0
        var smileRight: Float = 0
        var squintLeft: Float = 0
        var squintRight: Float = 0
        var detected:   Bool  = false
    }
    @Published private(set) var debugValues = DebugValues()
#endif

    // MARK: — Callbacks

    var onFlowComplete: (() -> Void)?

    // MARK: — Internals

    private let sequence:   [FaceGestureType]
    private let detector:   FaceGestureDetector
    private let stabilizer: GestureStabilizer
    private var timeoutTask: Task<Void, Never>?

    static let defaultSequence: [FaceGestureType] = [
        .neutral, .headLeft, .headRight, .chinUp, .blinkRight, .blinkLeft, .smile
    ]

    init(
        sequence:   [FaceGestureType]  = SpikeLivenessViewModel.defaultSequence,
        thresholds: FacePoseThresholds = .production
    ) {
        self.sequence   = sequence
        self.detector   = FaceGestureDetector(thresholds: thresholds)
        self.stabilizer = GestureStabilizer(holdDurationMs: thresholds.holdDurationMs)
        stabilizer.onConfirmed = { [weak self] in self?.onGestureConfirmed() }
    }

    // MARK: — Public interface

    var currentGesture: FaceGestureType? {
        guard currentStepIndex < sequence.count else { return nil }
        return sequence[currentStepIndex]
    }

    var totalSteps: Int { sequence.count }

    func start() {
        currentStepIndex = 0
        retryCount       = 0
        stabilizer.reset()
        stepState        = .noFace
        startTimeout()
    }

    // Called by ARFaceTrackingView on every ARSession delegate update (~60 FPS).
    // This is the only entry point for sensor data — nothing is stored.
    func update(with anchor: FaceAnchorInput) {
        guard let gesture = currentGesture else { return }
        guard stepState != .confirmed, stepState != .complete else { return }

        let detected = detector.detect(gesture: gesture, from: anchor)

#if DEBUG
        updateDebugValues(from: anchor, detected: detected)
#endif

        stabilizer.update(detected: detected)

        switch (detected, stabilizer.isDetecting, stabilizer.isConfirmed) {
        case (_, _, true):
            break   // stabilizer fires onConfirmed — handled in onGestureConfirmed()
        case (_, true, _):
            stepState = .stabilizing(stabilizer.holdProgress)
        case (true, false, _):
            stepState = .stabilizing(0.0)
        default:
            stepState = .detecting
        }
    }

    // Called when ARSession loses face tracking.
    func faceTrackingLost() {
        guard stepState != .confirmed, stepState != .complete else { return }
        stepState = .noFace
        stabilizer.reset()
    }

    // Manual retry after timeout — user taps the retry button.
    func retryCurrentStep() {
        guard stepState == .timedOut else { return }
        retryCount += 1
        stabilizer.reset()
        stepState = .noFace
        startTimeout()
    }

    // MARK: — Private

    private func onGestureConfirmed() {
        timeoutTask?.cancel()
        stepState = .confirmed
        Task {
            try? await Task.sleep(nanoseconds: 700_000_000)  // 700 ms visual confirmation
            guard !Task.isCancelled else { return }
            advanceStep()
        }
    }

    private func advanceStep() {
        currentStepIndex += 1
        if currentStepIndex >= sequence.count {
            stepState = .complete
            onFlowComplete?()
        } else {
            stabilizer.reset()
            stepState = .noFace
            startTimeout()
        }
    }

    private func startTimeout() {
        timeoutTask?.cancel()
        let ms = detector.thresholds.stepTimeoutMs
        timeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(ms) * 1_000_000)
            guard !Task.isCancelled else { return }
            self?.stepState = .timedOut
        }
    }

    // MARK: — Debug values (DEBUG only)

#if DEBUG
    private func updateDebugValues(from anchor: FaceAnchorInput, detected: Bool) {
        let e = anchor.faceEulerAngles
        let bs = anchor.faceBlendShapes
        debugValues = DebugValues(
            yaw:         e.x,
            pitch:        e.y,
            blinkLeft:   bs[.eyeBlinkLeft]?.floatValue   ?? 0,
            blinkRight:  bs[.eyeBlinkRight]?.floatValue  ?? 0,
            smileLeft:   bs[.mouthSmileLeft]?.floatValue  ?? 0,
            smileRight:  bs[.mouthSmileRight]?.floatValue ?? 0,
            squintLeft:  bs[.cheekSquintLeft]?.floatValue  ?? 0,
            squintRight: bs[.cheekSquintRight]?.floatValue ?? 0,
            detected:    detected
        )
    }
#endif
}

import Foundation
import UIKit

// Liveness challenge state machine.
//
// Challenge spec: CENTER → LEFT → CENTER → RIGHT → CENTER → CAPTURE (6 steps).
// challenge_version: "v1.0" (kBiometricChallengeVersion).
//
// DEV/TEST MVP — manual step advancement only.
// The user taps "Tovább" to progress through each step.
// No automatic head-position detection is implemented in PR-iOS-1.
// Automatic Vision/MediaPipe-based detection is out of scope and requires a separate PR.
//
// photo_filename: UUID-based safe basename. No JPEG bytes are transmitted.
// The backend POST /me/biometric-liveness accepts JSON only — no multipart endpoint exists.
// Image upload support is out of scope for PR-iOS-1.
@MainActor
final class BiometricLivenessViewModel: ObservableObject {

    enum Step: Int, CaseIterable {
        case center1, left, center2, right, center3, capture

        var displayName: String {
            switch self {
            case .center1, .center2, .center3: return "CENTER"
            case .left:                        return "LEFT"
            case .right:                       return "RIGHT"
            case .capture:                     return "CAPTURE"
            }
        }

        var instruction: String {
            switch self {
            case .center1: return "Look straight at the camera"
            case .left:    return "Slowly turn your head LEFT"
            case .center2: return "Turn your head back STRAIGHT"
            case .right:   return "Slowly turn your head RIGHT"
            case .center3: return "Turn your head back STRAIGHT"
            case .capture: return "Stay straight — capturing photo"
            }
        }

        var isCapture: Bool { self == .capture }
    }

    @Published private(set) var currentStepIndex: Int = 0
    @Published private(set) var isLoading:         Bool = false
    @Published private(set) var livenessResult:    BiometricVerificationStatus?
    @Published              var error:             BiometricClientError?

    // Fired on successful liveness submit — parent shows BiometricVerifyView.
    var onLivenessComplete: ((String?) -> Void)?

    private let service:     BiometricService
    private var startedAt:   Date = Date()
    private var retryCount:  Int  = 0
    private var capturedFilename: String?

    var currentStep: Step { Step(rawValue: currentStepIndex) ?? .center1 }
    var totalSteps:  Int  { Step.allCases.count }

    init(service: BiometricService) {
        self.service = service
    }

    // MARK: — Step control

    func start() {
        startedAt = Date()
        currentStepIndex = 0
        capturedFilename = nil
        error = nil
    }

    // Called by the "Tovább" button for non-capture steps.
    func advanceStep() {
        guard currentStepIndex < Step.allCases.count - 1 else { return }
        currentStepIndex += 1
    }

    // Called by CameraImagePicker on successful photo capture.
    // image: captured UIImage — NOT stored, NOT logged, NOT transmitted.
    // Only a UUID-based basename is derived for the photo_filename field.
    // No JPEG bytes are sent in PR-iOS-1.
    func onPhotoCaptured(_ image: UIImage) {
        // Derive safe basename — no path separators, no PII.
        let uuid = UUID().uuidString.lowercased()
        capturedFilename = "liveness_\(uuid).jpg"
        // image is intentionally discarded — no storage, no log, no upload in PR-iOS-1.
        _ = image
        Task { await submitLiveness() }
    }

    func retry() {
        retryCount += 1
        currentStepIndex = 0
        capturedFilename  = nil
        error             = nil
        startedAt         = Date()
    }

    // MARK: — Submit

    private func submitLiveness() async {
        isLoading = true
        defer { isLoading = false }
        let metadata = buildMetadata()
        do {
            let result = try await service.submitLiveness(
                metadata:      metadata,
                photoFilename: capturedFilename
            )
            livenessResult = result
            onLivenessComplete?(capturedFilename)
        } catch BiometricClientError.livenessAlreadySubmitted {
            // Treat as success — reference already captured in a previous session.
            onLivenessComplete?(capturedFilename)
        } catch let e as BiometricClientError {
            error = e
        } catch {
            self.error = .networkError(error)
        }
    }

    private func buildMetadata() -> BiometricLivenessMetadata {
        let completedSteps = Step.allCases
            .prefix(currentStepIndex + 1)
            .map { $0.displayName }
        let durationMs = Int(Date().timeIntervalSince(startedAt) * 1000)
        return BiometricLivenessMetadata(
            challengeVersion: kBiometricChallengeVersion,
            stepsCompleted:   completedSteps,
            totalDurationMs:  max(0, durationMs),
            retryCount:       retryCount,
            failureReason:    nil
        )
        // Structural guarantee: yaw, roll, device_model, ios_version, landmarks, frames
        // are absent — not collected, not included. Backend extra="forbid" enforces this.
    }
}

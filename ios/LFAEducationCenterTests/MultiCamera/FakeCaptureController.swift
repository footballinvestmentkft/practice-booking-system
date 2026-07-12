import Combine
import Foundation
@testable import LFAEducationCenter

@MainActor
final class FakeCaptureController: CaptureController {
    private let subject = CurrentValueSubject<CaptureState, Never>(.idle)
    var captureStatePublisher: AnyPublisher<CaptureState, Never> { subject.eraseToAnyPublisher() }
    private(set) var startCallCount = 0
    private(set) var stopCallCount  = 0
    private(set) var rearmCallCount = 0
    /// When false, startCapture() is a silent no-op that never reaches
    /// `.capturing` — reproduces the 2026-07-04 physical failure mode
    /// (camera lost while `.ready`) for the PCO watchdog tests.
    var startAdvancesToCapturing = true
    func startCapture() {
        startCallCount += 1
        if startAdvancesToCapturing { subject.send(.capturing) }
    }
    func stopCapture()  {
        stopCallCount += 1
        subject.send(.stopping)
        subject.send(.completed(fileURL: URL(fileURLWithPath: "/dev/null")))
    }
    func rearmForNextCycle() { rearmCallCount += 1; subject.send(.ready) }
    func simulateState(_ s: CaptureState) { subject.send(s) }
}

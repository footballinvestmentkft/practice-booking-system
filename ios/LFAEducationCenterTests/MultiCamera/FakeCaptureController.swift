import Combine
@testable import LFAEducationCenter

@MainActor
final class FakeCaptureController: CaptureController {
    private let subject = CurrentValueSubject<CaptureState, Never>(.idle)
    var captureStatePublisher: AnyPublisher<CaptureState, Never> { subject.eraseToAnyPublisher() }
    private(set) var startCallCount = 0
    private(set) var stopCallCount  = 0
    private(set) var rearmCallCount = 0
    func startCapture() { startCallCount += 1; subject.send(.capturing) }
    func stopCapture()  { stopCallCount  += 1; subject.send(.stopping) }
    func rearmForNextCycle() { rearmCallCount += 1; subject.send(.ready) }
    func simulateState(_ s: CaptureState) { subject.send(s) }
}

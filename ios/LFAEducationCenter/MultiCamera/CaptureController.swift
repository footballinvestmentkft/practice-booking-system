import Foundation
import Combine

@MainActor
protocol CaptureController: AnyObject {
    var captureStatePublisher: AnyPublisher<CaptureState, Never> { get }
    func startCapture()
    func stopCapture()
}

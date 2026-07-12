import AVFoundation
import Combine
import UIKit

// MARK: — CameraFramePublisher
//
// Publishes low-fidelity preview JPEG frames from the PLAYER device to the
// instructor dashboard over CameraStreamService (MultipeerConnectivity).
//
// SINGLE-SESSION CAMERA OWNERSHIP (2026-07-04 tricamera physical run RCA):
// this type previously owned its own AVCaptureSession on the same back camera
// that SessionCaptureManager records from. iOS hands the camera to whichever
// session starts last and silently interrupts the other — on the failed run
// the publisher won, SessionCaptureManager stayed a lying `.ready`, and the
// player never reached confirmed_start. The publisher now owns NO session:
// it configures one AVCaptureVideoDataOutput and attaches it to the
// SessionCaptureManager's session via attachPreviewOutput(). Recording is
// authoritative; preview is a side branch of the same camera pipeline, so
// both run concurrently without contention.
@MainActor
final class CameraFramePublisher: NSObject, ObservableObject {

    @Published private(set) var frameIndex: Int = 0
    @Published private(set) var publishedFPS: Double = 0

    private let videoOutput = AVCaptureVideoDataOutput()
    private let processingQueue = DispatchQueue(label: "com.lfa.frame-publisher", qos: .userInitiated)

    private weak var captureManager: SessionCaptureManager?
    private var streamService: CameraStreamService?
    private var managerStateSubscription: AnyCancellable?
    private var isOutputConfigured = false
    private var isAttached = false
    private var attachInFlight = false
    private nonisolated(unsafe) var lastSentTime: CFTimeInterval = 0
    private var publishCount = 0
    private var fpsWindowStart = Date()
    private var orientationObserver: NSObjectProtocol?

    deinit {
        if let token = orientationObserver {
            NotificationCenter.default.removeObserver(token)
        }
    }

    /// Starts publishing preview frames by tapping `captureManager`'s capture
    /// session. Attach is deferred until the manager's session is actually
    /// configured and running (`.ready` / `.capturing`) — its state publisher
    /// replays the current value, so a manager that is already ready attaches
    /// immediately.
    func startCapture(sharing captureManager: SessionCaptureManager, streamService: CameraStreamService) {
        self.captureManager = captureManager
        self.streamService = streamService
        frameIndex = 0
        publishCount = 0
        fpsWindowStart = Date()
        configureOutputIfNeeded()

        managerStateSubscription = captureManager.captureStatePublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] captureState in
                switch captureState {
                case .ready, .capturing:
                    self?.attachIfNeeded()
                default:
                    break
                }
            }
        MC1Log.notice("[FramePublisher] start requested: waiting for shared capture session (state=\(captureManager.state))")
    }

    func stopCapture() {
        managerStateSubscription?.cancel()
        managerStateSubscription = nil
        if isAttached {
            captureManager?.detachPreviewOutput()
            isAttached = false
        }
        streamService = nil
        captureManager = nil
        MC1Log.notice("[FramePublisher] capture stopped (preview output detached)")
    }

    // MARK: — Private

    private func configureOutputIfNeeded() {
        guard !isOutputConfigured else { return }
        isOutputConfigured = true

        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        videoOutput.setSampleBufferDelegate(self, queue: processingQueue)

        UIDevice.current.beginGeneratingDeviceOrientationNotifications()
        orientationObserver = NotificationCenter.default.addObserver(
            forName: UIDevice.orientationDidChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            OrientationMapper.applyCurrentOrientation(to: self.videoOutput.connection(with: .video))
        }
    }

    private func attachIfNeeded() {
        guard !isAttached, !attachInFlight, let manager = captureManager else { return }
        attachInFlight = true
        manager.attachPreviewOutput(videoOutput) { [weak self] attached in
            guard let self else { return }
            self.attachInFlight = false
            self.isAttached = attached
            MC1Log.notice("[FramePublisher] preview output attach \(attached ? "OK" : "FAILED") — target=\(Int(1.0 / Self.targetInterval))fps, orientation=\(OrientationMapper.currentOrientationLabel)")
        }
    }
}

// MARK: — AVCaptureVideoDataOutputSampleBufferDelegate

extension CameraFramePublisher: AVCaptureVideoDataOutputSampleBufferDelegate {
    private nonisolated static let targetInterval: CFTimeInterval = 1.0 / 12.0
    /// Longest edge of a published preview frame. The shared session runs at the
    /// RECORDING profile (1280x720 — see CaptureFormatSelector), unlike the old
    /// dedicated preview session's `.medium` preset; a full 720p JPEG can exceed
    /// what MCSession `.unreliable` reliably delivers, so downscale to roughly
    /// the previous preview fidelity before encoding.
    private nonisolated static let maxPreviewEdge: CGFloat = 640

    nonisolated func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = CACurrentMediaTime()
        guard now - lastSentTime >= Self.targetInterval else { return }
        lastSentTime = now

        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        var ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let longestEdge = max(ciImage.extent.width, ciImage.extent.height)
        if longestEdge > Self.maxPreviewEdge {
            let scale = Self.maxPreviewEdge / longestEdge
            ciImage = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        }
        let context = CIContext()
        guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else { return }
        let uiImage = UIImage(cgImage: cgImage)
        guard let jpegData = uiImage.jpegData(compressionQuality: 0.3) else { return }

        Task { @MainActor [weak self] in
            guard let self, let stream = self.streamService else { return }
            stream.sendFrame(jpegData)
            self.frameIndex += 1
            self.publishCount += 1
            let elapsed = Date().timeIntervalSince(self.fpsWindowStart)
            if elapsed >= 1.0 {
                self.publishedFPS = Double(self.publishCount) / elapsed
                self.publishCount = 0
                self.fpsWindowStart = Date()
            }
        }
    }
}

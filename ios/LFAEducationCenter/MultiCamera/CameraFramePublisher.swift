import AVFoundation
import UIKit

@MainActor
final class CameraFramePublisher: NSObject, ObservableObject {

    @Published private(set) var frameIndex: Int = 0
    @Published private(set) var publishedFPS: Double = 0

    private let captureSession = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let processingQueue = DispatchQueue(label: "com.lfa.frame-publisher", qos: .userInitiated)

    private var streamService: CameraStreamService?
    private nonisolated(unsafe) var lastSentTime: CFTimeInterval = 0
    private var publishCount = 0
    private var fpsWindowStart = Date()
    private var orientationObserver: NSObjectProtocol?

    var previewSession: AVCaptureSession { captureSession }

    deinit {
        if let token = orientationObserver {
            NotificationCenter.default.removeObserver(token)
        }
    }

    func configure() {
        captureSession.beginConfiguration()
        captureSession.sessionPreset = .medium // preview profile — intentionally separate/lower-fidelity than archival recording

        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: camera),
              captureSession.canAddInput(input) else {
            captureSession.commitConfiguration()
            print("[FramePublisher] camera setup failed")
            return
        }
        captureSession.addInput(input)

        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        videoOutput.setSampleBufferDelegate(self, queue: processingQueue)

        guard captureSession.canAddOutput(videoOutput) else {
            captureSession.commitConfiguration()
            print("[FramePublisher] cannot add video output")
            return
        }
        captureSession.addOutput(videoOutput)

        OrientationMapper.applyCurrentOrientation(to: videoOutput.connection(with: .video))

        captureSession.commitConfiguration()
        print("[FramePublisher] configured: preset=medium, target=\(Int(1.0/Self.targetInterval))fps, "
              + "orientation=\(OrientationMapper.currentOrientationLabel)")

        UIDevice.current.beginGeneratingDeviceOrientationNotifications()
        orientationObserver = NotificationCenter.default.addObserver(
            forName: UIDevice.orientationDidChangeNotification, object: nil, queue: .main
        ) { [weak self] _ in
            guard let self else { return }
            OrientationMapper.applyCurrentOrientation(to: self.videoOutput.connection(with: .video))
        }
    }

    func startCapture(streamService: CameraStreamService) {
        self.streamService = streamService
        frameIndex = 0
        publishCount = 0
        fpsWindowStart = Date()
        processingQueue.async { [weak self] in
            self?.captureSession.startRunning()
            DispatchQueue.main.async { print("[FramePublisher] capture started") }
        }
    }

    func stopCapture() {
        processingQueue.async { [weak self] in
            self?.captureSession.stopRunning()
            DispatchQueue.main.async { print("[FramePublisher] capture stopped") }
        }
        streamService = nil
    }
}

// MARK: — AVCaptureVideoDataOutputSampleBufferDelegate

extension CameraFramePublisher: AVCaptureVideoDataOutputSampleBufferDelegate {
    private static let targetInterval: CFTimeInterval = 1.0 / 12.0

    nonisolated func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        let now = CACurrentMediaTime()
        guard now - lastSentTime >= Self.targetInterval else { return }
        lastSentTime = now

        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }

        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
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

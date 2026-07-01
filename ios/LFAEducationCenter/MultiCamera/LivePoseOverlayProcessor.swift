import AVFoundation
import UIKit
import Vision

// Dual-mode live pose processor for multi-camera dashboard panels.
//
// Mode A (local camera): attach(to:) adds an AVCaptureVideoDataOutput to the
//   running AVCaptureSession; frames arrive on poseQueue via the delegate.
// Mode B (push): feed(_ image:) accepts UIImage frames from remote/GoPro panels.
//
// Both modes throttle to 5fps, run VNDetectHumanBodyPoseRequest on poseQueue,
// and publish a DensePoseFrame on the main actor. The frame is consumed directly
// by ContinuousSkeletonOverlayView — same visual as the annotation screen.
//
// Coordinate transform: Vision y=0 is bottom → overlay y=0 is top → 1.0 - vy.
// Joint list mirrors DensePoseExtractor.jointList exactly (19 joints).

@MainActor
final class LivePoseOverlayProcessor: NSObject, ObservableObject {

    @Published var frame: DensePoseFrame? = nil

    // MARK: — Per-panel diagnostics (2026-07-01 flow audit)
    //
    // Before this, there was no way to tell — from an artifact, after the fact — whether
    // a given dashboard panel's skeleton overlay ever received a source frame, whether
    // any of those frames made it past the 5fps throttle into Vision, or whether Vision
    // ever actually found a body. A "no skeleton visible" physical-test failure was
    // indistinguishable from "frames never arrived" vs "frames arrived but no body found"
    // vs "body found but all joints below the 0.3 confidence threshold" without re-running
    // the test. These counters make that distinction from a single exported JSON.
    @Published private(set) var framesReceived: Int = 0           // every feed()/delegate call, pre-throttle
    @Published private(set) var framesProcessed: Int = 0          // passed the 5fps throttle, sent to Vision
    @Published private(set) var visionDetectionSuccesses: Int = 0 // Vision found a body (obs != nil)
    @Published private(set) var framesWithSkeletonPoints: Int = 0 // landmarks non-empty (frame actually published)
    @Published private(set) var lastFrameReceivedAt: Date? = nil

    var diagnosticSnapshot: [String: Any] {
        [
            "framesReceived": framesReceived,
            "framesProcessed": framesProcessed,
            "visionDetectionSuccesses": visionDetectionSuccesses,
            "framesWithSkeletonPoints": framesWithSkeletonPoints,
            "lastFrameReceivedAt": lastFrameReceivedAt.map { ISO8601DateFormatter().string(from: $0) } ?? NSNull(),
        ]
    }

    private let poseQueue = DispatchQueue(label: "com.lfa.live-pose-overlay", qos: .userInitiated)

    // nonisolated(unsafe): accessed only from poseQueue (serial), never from two queues simultaneously.
    private nonisolated(unsafe) let seqHandler = VNSequenceRequestHandler()
    private nonisolated(unsafe) var lastTs: CFAbsoluteTime = 0
    private nonisolated(unsafe) var _captureOutput: AVCaptureVideoDataOutput?

    static let minInterval: CFAbsoluteTime = 1.0 / 5.0   // 5 fps cap

    // MARK: — Attach / detach (local camera)

    func attach(to session: AVCaptureSession) {
        guard _captureOutput == nil else { return }
        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
        output.setSampleBufferDelegate(self, queue: poseQueue)
        session.beginConfiguration()
        guard session.canAddOutput(output) else {
            session.commitConfiguration()
            print("[LivePose] cannot add output to session — skeleton overlay unavailable for local panel")
            return
        }
        session.addOutput(output)
        session.commitConfiguration()
        _captureOutput = output
        print("[LivePose] attached to local AVCaptureSession")
    }

    func detach(from session: AVCaptureSession) {
        guard let output = _captureOutput else { return }
        session.beginConfiguration()
        session.removeOutput(output)
        session.commitConfiguration()
        _captureOutput = nil
        frame = nil
        print("[LivePose] detached from local AVCaptureSession")
    }

    // MARK: — Push mode (remote device / GoPro)

    func feed(_ image: UIImage) {
        guard let cgImage = image.cgImage else { return }
        framesReceived += 1
        lastFrameReceivedAt = Date()
        poseQueue.async { [weak self] in
            guard let self else { return }
            let now = CFAbsoluteTimeGetCurrent()
            guard now - self.lastTs >= Self.minInterval else { return }
            self.lastTs = now
            Task { @MainActor [weak self] in self?.framesProcessed += 1 }
            self._runVisionCGImage(cgImage)
        }
    }

    // MARK: — Private (all called on poseQueue)

    nonisolated private func _runVisionPixelBuffer(_ pb: CVPixelBuffer) {
        let request = VNDetectHumanBodyPoseRequest()
        try? seqHandler.perform([request], on: pb, orientation: .up)
        _publish(request.results?.first)
    }

    nonisolated private func _runVisionCGImage(_ cg: CGImage) {
        let request = VNDetectHumanBodyPoseRequest()
        let handler = VNImageRequestHandler(cgImage: cg, orientation: .up, options: [:])
        try? handler.perform([request])
        _publish(request.results?.first)
    }

    nonisolated private func _publish(_ obs: VNHumanBodyPoseObservation?) {
        guard let obs else {
            Task { @MainActor [weak self] in self?.frame = nil }
            return
        }
        Task { @MainActor [weak self] in self?.visionDetectionSuccesses += 1 }
        let landmarks: [BodyLandmarkDTO] = Self.jointList.compactMap { (joint, name) in
            guard let pt = try? obs.recognizedPoint(joint), pt.confidence >= 0.3 else { return nil }
            // y-flip: Vision origin is bottom-left, overlay origin is top-left
            return BodyLandmarkDTO(name: name, x: Double(pt.location.x), y: Double(1.0 - pt.location.y), confidence: Double(pt.confidence))
        }
        guard !landmarks.isEmpty else {
            Task { @MainActor [weak self] in self?.frame = nil }
            return
        }
        let kp = PoseKeypointsDTO(schemaVersion: "1", body: landmarks, leftHand: [], rightHand: [])
        let denseFrame = DensePoseFrame(
            timestampMs: Int(CFAbsoluteTimeGetCurrent() * 1000),
            keypoints: kp,
            confidence: nil,
            syntheticFeet: nil
        )
        Task { @MainActor [weak self] in
            self?.framesWithSkeletonPoints += 1
            self?.frame = denseFrame
        }
    }

    // MARK: — Joint list (identical to DensePoseExtractor.jointList)

    static let jointList: [(VNHumanBodyPoseObservation.JointName, String)] = [
        (.nose,          "nose"),
        (.leftEye,       "left_eye"),   (.rightEye,      "right_eye"),
        (.leftEar,       "left_ear"),   (.rightEar,      "right_ear"),
        (.neck,          "neck"),
        (.leftShoulder,  "left_shoulder"),  (.rightShoulder, "right_shoulder"),
        (.leftElbow,     "left_elbow"),     (.rightElbow,    "right_elbow"),
        (.leftWrist,     "left_wrist"),     (.rightWrist,    "right_wrist"),
        (.root,          "root"),
        (.leftHip,       "left_hip"),   (.rightHip,      "right_hip"),
        (.leftKnee,      "left_knee"),  (.rightKnee,     "right_knee"),
        (.leftAnkle,     "left_ankle"), (.rightAnkle,    "right_ankle"),
    ]
}

// MARK: — AVCaptureVideoDataOutputSampleBufferDelegate

extension LivePoseOverlayProcessor: AVCaptureVideoDataOutputSampleBufferDelegate {
    // Called on poseQueue by AVFoundation.
    nonisolated func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        Task { @MainActor [weak self] in
            self?.framesReceived += 1
            self?.lastFrameReceivedAt = Date()
        }
        let now = CFAbsoluteTimeGetCurrent()
        guard now - lastTs >= Self.minInterval else { return }
        lastTs = now
        guard let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        Task { @MainActor [weak self] in self?.framesProcessed += 1 }
        _runVisionPixelBuffer(pb)
    }
}

// MARK: — Per-panel diagnostics export (2026-07-01 flow audit)
//
// Writes Documents/pose_overlay_diag.json with one entry per dashboard panel
// (instructor/player/gopro), pulled by the regression script via the
// established devicectl appDataContainer copy pattern (same as
// capture_metadata_diag.json / gopro_stream_diag.json). "sourceFramesSeen" is
// measured at the upstream source (local capture delegate call count for the
// instructor panel, CameraStreamService.totalFramesReceived for the player
// panel via MultiPeer, GoProStreamProbe.decodeSuccesses for the GoPro panel)
// so a gap between "source produced a frame" and "frame reached the pose
// overlay processor" (e.g. a malformed JPEG failing `UIImage(data:)`) is
// visible instead of silently invisible.
enum PoseOverlayDiagWriter {
    static let fileName = "pose_overlay_diag.json"

    @MainActor
    static func write(
        instructor: LivePoseOverlayProcessor,
        player: LivePoseOverlayProcessor, playerSourceFramesSeen: Int,
        gopro: LivePoseOverlayProcessor, goProSourceFramesSeen: Int
    ) {
        func panelDict(_ p: LivePoseOverlayProcessor, sourceFramesSeen: Int) -> [String: Any] {
            var d = p.diagnosticSnapshot
            d["sourceFramesSeen"] = sourceFramesSeen
            return d
        }
        let diag: [String: Any] = [
            "timestamp": ISO8601DateFormatter().string(from: Date()),
            "instructor": panelDict(instructor, sourceFramesSeen: instructor.framesReceived),
            "player": panelDict(player, sourceFramesSeen: playerSourceFramesSeen),
            "gopro": panelDict(gopro, sourceFramesSeen: goProSourceFramesSeen),
        ]
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              JSONSerialization.isValidJSONObject(diag),
              let data = try? JSONSerialization.data(withJSONObject: diag, options: [.prettyPrinted]) else { return }
        try? data.write(to: docs.appendingPathComponent(fileName), options: .atomic)
        print("[POSE-OVERLAY-DIAG] wrote \(fileName): \(diag)")
    }
}

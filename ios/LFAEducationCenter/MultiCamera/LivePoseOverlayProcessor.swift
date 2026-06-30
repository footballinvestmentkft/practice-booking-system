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
        poseQueue.async { [weak self] in
            guard let self else { return }
            let now = CFAbsoluteTimeGetCurrent()
            guard now - self.lastTs >= Self.minInterval else { return }
            self.lastTs = now
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
        Task { @MainActor [weak self] in self?.frame = denseFrame }
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
        let now = CFAbsoluteTimeGetCurrent()
        guard now - lastTs >= Self.minInterval else { return }
        lastTs = now
        guard let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        _runVisionPixelBuffer(pb)
    }
}

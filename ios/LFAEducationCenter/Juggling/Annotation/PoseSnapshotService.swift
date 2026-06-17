import AVFoundation
import Vision

// MARK: — PoseSnapshotService (Phase 2A)
//
// Captures a Vision body pose snapshot from a video asset at a given
// timestamp, then uploads it to the backend via JugglingAnnotationAPIClient.
//
// Key design decisions:
//   - Non-blocking: errors are logged, never re-thrown to callers.
//     Contact event creation (the primary action) always completes first.
//   - Uses AVAssetImageGenerator for frame extraction — the same mechanism
//     as EventStillFrameGenerator so no new dependency is introduced.
//   - appliesPreferredTrackTransform = true: handles portrait/landscape
//     orientation automatically (same flag as EventStillFrameGenerator).
//   - toleranceAfter = 1 frame (33ms at 30fps): avoids an expensive seek
//     to a non-keyframe while keeping visual accuracy within 1 frame.
//   - VNDetectHumanBodyPoseRequest runs on the extracted CGImage in a
//     background thread (VNImageRequestHandler is not MainActor).
//   - Confidence threshold 0.3: joints below this are omitted from upload.
//   - No mirror transform (unlike live-camera BodyPoseDetector which
//     mirrors x for front-camera display). Video frames are not mirrored.
//   - Coordinate transform: screen_y = 1 - vision_y (Vision origin = bottom-left).

// PoseSnapshotService has no MainActor isolation at the type level so that
// extractFrame and runPoseDetection can be called from Task.detached without
// an actor hop. captureAndUpload is @MainActor because it calls the API client.
enum PoseSnapshotService {

    // MARK: — Confidence threshold

    static let confidenceThreshold: Float = 0.3

    // MARK: — Joint name mapping (Apple Vision → JSON string)
    // Maps VNHumanBodyPoseObservation.JointName raw values to compact snake_case names.

    private static let jointNameMap: [String: String] = {
        var m: [String: String] = [:]
        // Face region
        m["nose"]          = "nose"
        m["leftEye"]       = "left_eye"
        m["rightEye"]      = "right_eye"
        m["leftEar"]       = "left_ear"
        m["rightEar"]      = "right_ear"
        // Upper body
        m["neck1"]         = "neck"
        m["leftShoulder1"] = "left_shoulder"
        m["rightShoulder1"] = "right_shoulder"
        m["leftElbow1"]    = "left_elbow"
        m["rightElbow1"]   = "right_elbow"
        m["leftWrist1"]    = "left_wrist"
        m["rightWrist1"]   = "right_wrist"
        // Lower body
        m["root"]          = "root"
        m["leftHip1"]      = "left_hip"
        m["rightHip1"]     = "right_hip"
        m["leftKnee1"]     = "left_knee"
        m["rightKnee1"]    = "right_knee"
        m["leftAnkle1"]    = "left_ankle"
        m["rightAnkle1"]   = "right_ankle"
        return m
    }()

    // MARK: — captureAndUpload
    //
    // Full pipeline: asset → frame → Vision → upload.
    // Must be called from a non-MainActor context (or a detached Task)
    // because VNImageRequestHandler.perform() blocks.
    // Upload happens via the non-throwing API client wrapper.

    @MainActor
    static func captureAndUpload(
        at timestampMs:  Int,
        videoId:         String,
        eventId:         UUID,
        asset:           AVAsset,
        apiClient:       JugglingAnnotationAPIClient
    ) async {
        guard let (cgImage, imageSize) = await extractFrame(from: asset, atMs: timestampMs) else {
            print("[PoseSnapshot] frame extraction returned nil at \(timestampMs)ms — skipping upload")
            return
        }

        let (keypoints, confidence) = await Task.detached(priority: .utility) {
            runPoseDetection(on: cgImage)
        }.value

        let request = PoseSnapshotUploadRequest(
            keypoints:           keypoints,
            modelVersion:        "apple_vision_v1",
            captureSource:       "ios_realtime",
            capturedAtMs:        timestampMs,
            imageWidthPx:        Int(imageSize.width),
            imageHeightPx:       Int(imageSize.height),
            inferenceConfidence: confidence.map { Double($0) }
        )
        await apiClient.uploadPoseSnapshot(videoId: videoId, eventId: eventId, request: request)
    }

    // MARK: — Frame extraction (internal for testing)
    //
    // Returns (CGImage, CGSize) or nil on failure.
    // toleranceBefore=.zero, toleranceAfter=1 frame: accurate but not forced
    // to seek a non-keyframe, keeping extraction fast.

    static func extractFrame(
        from asset:   AVAsset,
        atMs timestampMs: Int
    ) async -> (CGImage, CGSize)? {
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore  = .zero
        generator.requestedTimeToleranceAfter   = CMTime(value: 1, timescale: 30)

        let requestedTime = EventStillFrameGenerator.clampedTime(
            forMs: timestampMs,
            duration: asset.duration
        )

        return await withCheckedContinuation { continuation in
            generator.generateCGImagesAsynchronously(
                forTimes: [NSValue(time: requestedTime)]
            ) { _, cgImage, _, result, _ in
                switch result {
                case .succeeded:
                    if let cg = cgImage {
                        let size = CGSize(width: cg.width, height: cg.height)
                        continuation.resume(returning: (cg, size))
                    } else {
                        continuation.resume(returning: nil)
                    }
                case .cancelled, .failed:
                    continuation.resume(returning: nil)
                @unknown default:
                    continuation.resume(returning: nil)
                }
            }
        }
    }

    // MARK: — Vision pose detection (internal for testing)
    //
    // Runs synchronously on the calling thread — must be called from a
    // non-MainActor context (use Task.detached).
    // Returns (PoseKeypointsDTO, overallConfidence?).

    static func runPoseDetection(on cgImage: CGImage) -> (PoseKeypointsDTO, Float?) {
        let handler = VNImageRequestHandler(cgImage: cgImage, orientation: .up)
        let request = VNDetectHumanBodyPoseRequest()

        do {
            try handler.perform([request])
        } catch {
            print("[PoseSnapshot] Vision request failed: \(error)")
            return (.empty(), nil)
        }

        guard let observation = request.results?.first else {
            return (.empty(), nil)
        }

        let allPoints: [VNHumanBodyPoseObservation.JointName: VNRecognizedPoint]
        do {
            allPoints = try observation.recognizedPoints(.all)
        } catch {
            return (.empty(), Float(observation.confidence))
        }

        let landmarks: [BodyLandmarkDTO] = allPoints.compactMap { (key, point) in
            guard point.confidence >= confidenceThreshold else { return nil }
            let jsonName = jointNameMap[key.rawValue.rawValue] ?? key.rawValue.rawValue
            return BodyLandmarkDTO(
                name:       jsonName,
                x:          Double(point.location.x),
                y:          Double(1.0 - point.location.y),   // Vision y-flip
                confidence: Double(point.confidence)
            )
        }

        let keypoints = PoseKeypointsDTO(
            schemaVersion: "1",
            body:          landmarks,
            leftHand:      [],
            rightHand:     []
        )
        return (keypoints, Float(observation.confidence))
    }
}

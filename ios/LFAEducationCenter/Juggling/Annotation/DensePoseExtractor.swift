import AVFoundation
import Vision

// MARK: — DensePoseExtractor (AN-3B2D-2)
//
// Streaming Vision body pose extraction from a video asset.
// Uses AVAssetReader for efficient sequential frame access (not AVAssetImageGenerator).
// Processes every Nth frame (default: ~10 FPS from a 30fps source).
//
// Thread model:
//   - extract() dispatches to a utility-QoS background queue
//   - Progress/frame/completion callbacks dispatched to main
//   - VNImageRequestHandler runs on the background queue (no actor hop)
//
// Cancellation: cancel() stops the AVAssetReader.

enum DensePoseExtractorError: Error {
    case cannotCreateReader
    case noVideoTrack
    case readerFailed(String)
}

final class DensePoseExtractor {

    struct Config {
        let samplingFPS: Double
        let confidenceThreshold: Float
        let syntheticFootEnabled: Bool
        let syntheticFootExtension: Double
    }

    static let defaultConfig = Config(
        samplingFPS: 10,
        confidenceThreshold: 0.3,
        syntheticFootEnabled: true,
        syntheticFootExtension: 0.25
    )

    private(set) var isRunning = false
    private(set) var isCancelled = false
    private var reader: AVAssetReader?
    private let queue = DispatchQueue(label: "com.lfa.densePose", qos: .utility)

    func extract(
        from asset: AVAsset,
        config: Config = defaultConfig,
        onProgress: @escaping (Double) -> Void,
        onFrame: @escaping (DensePoseFrame) -> Void,
        onComplete: @escaping (Result<[DensePoseFrame], Error>) -> Void
    ) {
        guard !isRunning else { return }
        isRunning = true
        isCancelled = false

        queue.async { [weak self] in
            guard let self = self else { return }
            self.performExtraction(
                asset: asset, config: config,
                onProgress: onProgress, onFrame: onFrame, onComplete: onComplete
            )
        }
    }

    func cancel() {
        isCancelled = true
        reader?.cancelReading()
    }

    // MARK: — Core extraction (runs on background queue)

    private func performExtraction(
        asset: AVAsset,
        config: Config,
        onProgress: @escaping (Double) -> Void,
        onFrame: @escaping (DensePoseFrame) -> Void,
        onComplete: @escaping (Result<[DensePoseFrame], Error>)  -> Void
    ) {
        guard let reader = try? AVAssetReader(asset: asset) else {
            finish(.failure(DensePoseExtractorError.cannotCreateReader), onComplete)
            return
        }
        self.reader = reader

        let tracks = asset.tracks(withMediaType: .video)
        guard let videoTrack = tracks.first else {
            finish(.failure(DensePoseExtractorError.noVideoTrack), onComplete)
            return
        }

        let outputSettings: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        let output = AVAssetReaderTrackOutput(track: videoTrack, outputSettings: outputSettings)
        output.alwaysCopiesSampleData = false
        reader.add(output)

        guard reader.startReading() else {
            finish(.failure(DensePoseExtractorError.readerFailed(
                reader.error?.localizedDescription ?? "unknown"
            )), onComplete)
            return
        }

        let fps = Double(videoTrack.nominalFrameRate)
        let skipInterval = max(1, Int(round(fps / config.samplingFPS)))
        let durationMs = Int(CMTimeGetSeconds(asset.duration) * 1000)

        var frames: [DensePoseFrame] = []
        var frameIndex = 0

        while let sampleBuffer = output.copyNextSampleBuffer() {
            if isCancelled { break }

            if frameIndex % skipInterval != 0 {
                frameIndex += 1
                continue
            }

            let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
            let timestampMs = Int(CMTimeGetSeconds(presentationTime) * 1000)

            guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
                frameIndex += 1
                continue
            }

            let denseFrame = runPoseDetection(
                pixelBuffer: pixelBuffer,
                timestampMs: timestampMs,
                config: config
            )
            frames.append(denseFrame)

            let pct = durationMs > 0 ? min(Double(timestampMs) / Double(durationMs), 1.0) : 0.0
            DispatchQueue.main.async {
                onProgress(pct)
                onFrame(denseFrame)
            }

            frameIndex += 1
        }

        isRunning = false
        finish(.success(frames), onComplete)
    }

    private func finish(
        _ result: Result<[DensePoseFrame], Error>,
        _ onComplete: @escaping (Result<[DensePoseFrame], Error>) -> Void
    ) {
        isRunning = false
        DispatchQueue.main.async { onComplete(result) }
    }

    // MARK: — Vision pose detection per frame

    private func runPoseDetection(
        pixelBuffer: CVPixelBuffer,
        timestampMs: Int,
        config: Config
    ) -> DensePoseFrame {
        let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, orientation: .up)
        let request = VNDetectHumanBodyPoseRequest()

        do {
            try handler.perform([request])
        } catch {
            return DensePoseFrame(timestampMs: timestampMs, keypoints: .empty(), confidence: nil, syntheticFeet: nil)
        }

        guard let observation = request.results?.first else {
            return DensePoseFrame(timestampMs: timestampMs, keypoints: .empty(), confidence: nil, syntheticFeet: nil)
        }

        let allPoints: [VNHumanBodyPoseObservation.JointName: VNRecognizedPoint]
        do {
            allPoints = try observation.recognizedPoints(.all)
        } catch {
            return DensePoseFrame(
                timestampMs: timestampMs, keypoints: .empty(),
                confidence: Float(observation.confidence), syntheticFeet: nil
            )
        }

        let landmarks: [BodyLandmarkDTO] = allPoints.compactMap { (key, point) in
            guard point.confidence >= config.confidenceThreshold else { return nil }
            let jsonName = Self.jointNameMap[key.rawValue.rawValue] ?? key.rawValue.rawValue
            return BodyLandmarkDTO(
                name: jsonName,
                x: Double(point.location.x),
                y: Double(1.0 - point.location.y),
                confidence: Double(point.confidence)
            )
        }

        let keypoints = PoseKeypointsDTO(schemaVersion: "1", body: landmarks, leftHand: [], rightHand: [])

        let syntheticFeet: SyntheticFeetDTO?
        if config.syntheticFootEnabled {
            syntheticFeet = Self.estimateFeet(from: landmarks, extension: config.syntheticFootExtension)
        } else {
            syntheticFeet = nil
        }

        return DensePoseFrame(
            timestampMs: timestampMs,
            keypoints: keypoints,
            confidence: Float(observation.confidence),
            syntheticFeet: syntheticFeet
        )
    }

    // MARK: — Joint name mapping
    // Duplicated from PoseSnapshotService — intentionally independent.

    static let jointNameMap: [String: String] = {
        var m: [String: String] = [:]
        m["nose"] = "nose"
        m["leftEye"] = "left_eye"; m["rightEye"] = "right_eye"
        m["leftEar"] = "left_ear"; m["rightEar"] = "right_ear"
        m["neck1"] = "neck"
        m["leftShoulder1"] = "left_shoulder"; m["rightShoulder1"] = "right_shoulder"
        m["leftElbow1"] = "left_elbow"; m["rightElbow1"] = "right_elbow"
        m["leftWrist1"] = "left_wrist"; m["rightWrist1"] = "right_wrist"
        m["root"] = "root"
        m["leftHip1"] = "left_hip"; m["rightHip1"] = "right_hip"
        m["leftKnee1"] = "left_knee"; m["rightKnee1"] = "right_knee"
        m["leftAnkle1"] = "left_ankle"; m["rightAnkle1"] = "right_ankle"
        return m
    }()

    // MARK: — Synthetic foot estimation

    static func estimateFeet(
        from landmarks: [BodyLandmarkDTO],
        extension ext: Double
    ) -> SyntheticFeetDTO {
        let byName = Dictionary(uniqueKeysWithValues: landmarks.map { ($0.name, $0) })
        let left = estimateOneFoot(knee: byName["left_knee"], ankle: byName["left_ankle"], ext: ext)
        let right = estimateOneFoot(knee: byName["right_knee"], ankle: byName["right_ankle"], ext: ext)
        return SyntheticFeetDTO(leftFoot: left, rightFoot: right)
    }

    static func estimateOneFoot(
        knee: BodyLandmarkDTO?,
        ankle: BodyLandmarkDTO?,
        ext: Double
    ) -> SyntheticFootPoint? {
        guard let ankle = ankle else { return nil }
        guard let knee = knee else {
            let footY = min(ankle.y + 0.03, 0.98)
            return SyntheticFootPoint(
                x: ankle.x, y: footY,
                confidence: ankle.confidence * 0.5,
                ankleX: ankle.x, ankleY: ankle.y
            )
        }

        let dx = ankle.x - knee.x
        let dy = ankle.y - knee.y
        var footX = ankle.x + dx * ext
        var footY = ankle.y + dy * ext

        footX = max(0.0, min(footX, 1.0))
        footY = max(0.0, min(footY, 0.98))

        return SyntheticFootPoint(
            x: footX, y: footY,
            confidence: ankle.confidence * 0.7,
            ankleX: ankle.x, ankleY: ankle.y
        )
    }
}

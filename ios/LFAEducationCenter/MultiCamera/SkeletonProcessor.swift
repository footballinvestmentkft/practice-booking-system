import AVFoundation
import Vision
import UIKit

@MainActor
final class SkeletonProcessor: ObservableObject {

    enum State: Equatable {
        case idle
        case processing(progress: Int, total: Int)
        case completed(frameCount: Int, jointsDetected: Int)
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    private(set) var outputURL: URL?

    func process(videoURL: URL, sessionUuid: String, deviceId: String) async {
        state = .processing(progress: 0, total: 0)
        print("[Skeleton] starting processing: \(videoURL.lastPathComponent)")

        let asset = AVURLAsset(url: videoURL)
        guard let track = asset.tracks(withMediaType: .video).first else {
            state = .failed("no video track")
            return
        }

        let reader: AVAssetReader
        do {
            reader = try AVAssetReader(asset: asset)
        } catch {
            state = .failed("reader init: \(error.localizedDescription)")
            return
        }

        let outputSettings: [String: Any] = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
        ]
        let readerOutput = AVAssetReaderTrackOutput(track: track, outputSettings: outputSettings)
        readerOutput.alwaysCopiesSampleData = false
        guard reader.canAdd(readerOutput) else {
            state = .failed("cannot add reader output")
            return
        }
        reader.add(readerOutput)

        guard reader.startReading() else {
            state = .failed("startReading failed: \(reader.error?.localizedDescription ?? "unknown")")
            return
        }

        let fps = track.nominalFrameRate
        let duration = asset.duration.seconds
        let estimatedFrames = Int(Double(fps) * duration)
        let sampleEveryN = max(1, Int(fps / 5))

        var frames: [[String: Any]] = []
        var frameIndex = 0
        var detectedJoints = 0

        let request = VNDetectHumanBodyPoseRequest()

        while let sampleBuffer = readerOutput.copyNextSampleBuffer() {
            frameIndex += 1
            guard frameIndex % sampleEveryN == 0 else { continue }

            guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { continue }
            let timestamp = CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds

            let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, options: [:])
            do {
                try handler.perform([request])
            } catch {
                continue
            }

            guard let observations = request.results, !observations.isEmpty else {
                frames.append([
                    "frame_index": frameIndex,
                    "timestamp_s": timestamp,
                    "bodies_detected": 0,
                    "joints": [] as [[String: Any]]
                ])
                continue
            }

            var allJoints: [[String: Any]] = []
            for (bodyIdx, obs) in observations.enumerated() {
                guard let points = try? obs.recognizedPoints(.all) else { continue }
                for (key, point) in points where point.confidence > 0.3 {
                    allJoints.append([
                        "body": bodyIdx,
                        "joint": key.rawValue.rawValue,
                        "x": point.location.x,
                        "y": point.location.y,
                        "confidence": point.confidence
                    ])
                    detectedJoints += 1
                }
            }

            frames.append([
                "frame_index": frameIndex,
                "timestamp_s": timestamp,
                "bodies_detected": observations.count,
                "joints": allJoints
            ])

            if frameIndex % (sampleEveryN * 10) == 0 {
                state = .processing(progress: frameIndex, total: estimatedFrames)
            }
        }

        let result: [String: Any] = [
            "session_uuid": sessionUuid,
            "device_id": deviceId,
            "video_file": videoURL.lastPathComponent,
            "video_duration_s": duration,
            "video_fps": fps,
            "sample_every_n": sampleEveryN,
            "total_frames_read": frameIndex,
            "sampled_frames": frames.count,
            "total_joints_detected": detectedJoints,
            "frames": frames
        ]

        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first else {
            state = .failed("no Documents directory")
            return
        }
        let outputFile = docs.appendingPathComponent("skeleton_output.json")

        do {
            let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: outputFile, options: .atomic)
            outputURL = outputFile
            state = .completed(frameCount: frames.count, jointsDetected: detectedJoints)
            print("[SKELETON-RESULT] file=\(outputFile.path) frames=\(frames.count) joints=\(detectedJoints)")
        } catch {
            state = .failed("write: \(error.localizedDescription)")
        }
    }
}

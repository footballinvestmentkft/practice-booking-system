import Foundation

struct SkeletonFrameMetadata: Codable {
    let timestampMs: Double
    let frameIndex: Int
    let deviceId: String
    let captureSessionUuid: String
    let cycleIndex: Int
}

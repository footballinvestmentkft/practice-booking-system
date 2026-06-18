import Foundation

// MARK: — Dense Pose DTOs (AN-3B2D-2)
//
// Data types for continuous skeleton tracking across the full video.
// DensePoseFrame: one sampled frame with keypoints + optional synthetic feet.
// Reuses PoseKeypointsDTO/BodyLandmarkDTO from Phase 2A.

struct SyntheticFootPoint: Equatable {
    let x: Double
    let y: Double
    let confidence: Double
    let ankleX: Double
    let ankleY: Double
}

struct SyntheticFeetDTO: Equatable {
    let leftFoot: SyntheticFootPoint?
    let rightFoot: SyntheticFootPoint?
}

struct DensePoseFrame: Equatable {
    let timestampMs: Int
    let keypoints: PoseKeypointsDTO
    let confidence: Float?
    let syntheticFeet: SyntheticFeetDTO?
}

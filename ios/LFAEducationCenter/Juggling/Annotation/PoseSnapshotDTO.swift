import Foundation

// MARK: — Phase 2A: Pose Snapshot DTOs
//
// Mirror of backend PoseSnapshotCreateRequest + PoseSnapshotOut schemas.
// Used by PoseSnapshotService to upload Vision keypoints and by
// JugglingAnnotationAPIClient to fetch snapshots for overlay rendering.

// MARK: — BodyLandmarkDTO
// Single body keypoint in normalized screen coordinates.
// x: [0,1] left→right; y: [0,1] top→bottom (y = 1 - vision_y).
// Joints with confidence < 0.3 are omitted before upload.

struct BodyLandmarkDTO: Codable, Equatable {
    let name:       String
    let x:          Double
    let y:          Double
    let confidence: Double
}

// MARK: — PoseKeypointsDTO
// Wrapper matching the JSONB format stored in juggling_pose_snapshots.keypoints.

struct PoseKeypointsDTO: Codable, Equatable {
    let schemaVersion: String
    let body:          [BodyLandmarkDTO]
    let leftHand:      [BodyLandmarkDTO]
    let rightHand:     [BodyLandmarkDTO]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case body
        case leftHand      = "left_hand"
        case rightHand     = "right_hand"
    }

    static func empty() -> PoseKeypointsDTO {
        PoseKeypointsDTO(schemaVersion: "1", body: [], leftHand: [], rightHand: [])
    }
}

// MARK: — PoseSnapshotUploadRequest
// POST /contacts/{event_id}/pose-snapshot request body.

struct PoseSnapshotUploadRequest: Encodable {
    let keypoints:           PoseKeypointsDTO
    let modelVersion:        String
    let captureSource:       String
    let capturedAtMs:        Int
    let imageWidthPx:        Int?
    let imageHeightPx:       Int?
    let inferenceConfidence: Double?

    enum CodingKeys: String, CodingKey {
        case keypoints
        case modelVersion        = "model_version"
        case captureSource       = "capture_source"
        case capturedAtMs        = "captured_at_ms"
        case imageWidthPx        = "image_width_px"
        case imageHeightPx       = "image_height_px"
        case inferenceConfidence = "inference_confidence"
    }
}

// MARK: — PoseSnapshotOut
// Response from GET /pose-snapshots and POST /pose-snapshot.
// keypoints is decoded as PoseKeypointsDTO for type-safe overlay rendering.

struct PoseSnapshotOut: Codable, Identifiable, Equatable {
    let id:                  UUID
    let contactEventId:      UUID
    let videoId:             UUID
    let timestampMs:         Int
    let keypoints:           PoseKeypointsDTO
    let modelVersion:        String
    let captureSource:       String
    let inferenceConfidence: Double?
    let imageWidthPx:        Int?
    let imageHeightPx:       Int?
    let createdAt:           String

    enum CodingKeys: String, CodingKey {
        case id
        case contactEventId     = "contact_event_id"
        case videoId            = "video_id"
        case timestampMs        = "timestamp_ms"
        case keypoints
        case modelVersion       = "model_version"
        case captureSource      = "capture_source"
        case inferenceConfidence = "inference_confidence"
        case imageWidthPx       = "image_width_px"
        case imageHeightPx      = "image_height_px"
        case createdAt          = "created_at"
    }
}

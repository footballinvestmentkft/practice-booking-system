import Foundation

struct Skeleton3DJoint: Codable, Equatable {
    let canonicalJointName: String
    let sourceJointName: String
    let sourceModel: String
    let sourceConfidence: Double
    let imageX: Double
    let imageY: Double
    let imageConfidence: Double
    let isSynthetic: Bool
    let worldX: Double?
    let worldY: Double?
    let worldZ: Double?
    let worldConfidence: Double?
    let reprojectionErrorPx: Double?
    let sourceViewIds: [String]
    let triangulationStatus: String

    enum CodingKeys: String, CodingKey {
        case canonicalJointName = "canonical_joint_name"
        case sourceJointName = "source_joint_name"
        case sourceModel = "source_model"
        case sourceConfidence = "source_confidence"
        case imageX = "image_x"
        case imageY = "image_y"
        case imageConfidence = "image_confidence"
        case isSynthetic = "is_synthetic"
        case worldX = "world_x"
        case worldY = "world_y"
        case worldZ = "world_z"
        case worldConfidence = "world_confidence"
        case reprojectionErrorPx = "reprojection_error_px"
        case sourceViewIds = "source_view_ids"
        case triangulationStatus = "triangulation_status"
    }

    func validate() -> [String] {
        var errors: [String] = []
        if imageX < 0 || imageX > 1 { errors.append("image_x out of [0,1]") }
        if imageY < 0 || imageY > 1 { errors.append("image_y out of [0,1]") }
        if sourceConfidence < 0 || sourceConfidence > 1 { errors.append("source_confidence out of [0,1]") }
        let worldFields = [worldX, worldY, worldZ]
        let nonNil = worldFields.compactMap { $0 }.count
        if nonNil != 0 && nonNil != 3 { errors.append("world_x/y/z must be all nil or all filled") }
        if triangulationStatus == TriangulationStatus.triangulated.rawValue {
            if nonNil != 3 { errors.append("triangulated requires world coords") }
            if sourceViewIds.count < 2 { errors.append("triangulated requires >= 2 views") }
        }
        if triangulationStatus == TriangulationStatus.singleViewOnly.rawValue {
            if nonNil != 0 { errors.append("single_view_only must have nil world") }
        }
        if let re = reprojectionErrorPx, re < 0 { errors.append("reproj error negative") }
        return errors
    }
}

struct Skeleton3DFrame: Codable, Equatable {
    let schemaVersion: String
    let sessionId: String
    let captureId: String
    let cameraId: String
    let calibrationId: String?
    let frameId: String
    let sourceTimestampNs: Int64
    let synchronizedTimestampNs: Int64?
    let personId: Int
    let joints: [Skeleton3DJoint]
    let coordinateSystem: String
    let triangulationMethod: String?
    let processingVersion: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case sessionId = "session_id"
        case captureId = "capture_id"
        case cameraId = "camera_id"
        case calibrationId = "calibration_id"
        case frameId = "frame_id"
        case sourceTimestampNs = "source_timestamp_ns"
        case synchronizedTimestampNs = "synchronized_timestamp_ns"
        case personId = "person_id"
        case joints
        case coordinateSystem = "coordinate_system"
        case triangulationMethod = "triangulation_method"
        case processingVersion = "processing_version"
    }
}

struct CapturePresetDTO: Codable, Equatable {
    let resolution: String
    let fps: Int
    let lensMode: String
    let stabilization: String

    enum CodingKeys: String, CodingKey {
        case resolution, fps
        case lensMode = "lens_mode"
        case stabilization
    }
}

struct IntrinsicCalibrationDTO: Codable, Equatable {
    let cameraId: String
    let intrinsicMatrix: [[Double]]
    let distortionCoeffs: [Double]
    let imageWidthPx: Int
    let imageHeightPx: Int
    let reprojectionError: Double
    let capturePreset: CapturePresetDTO

    enum CodingKeys: String, CodingKey {
        case cameraId = "camera_id"
        case intrinsicMatrix = "intrinsic_matrix"
        case distortionCoeffs = "distortion_coeffs"
        case imageWidthPx = "image_width_px"
        case imageHeightPx = "image_height_px"
        case reprojectionError = "reprojection_error"
        case capturePreset = "capture_preset"
    }
}

struct StereoCalibrationDTO: Codable, Equatable {
    let cameraAId: String
    let cameraBId: String
    let rotationMatrix: [[Double]]
    let translationVector: [Double]
    let fundamentalMatrix: [[Double]]
    let essentialMatrix: [[Double]]
    let reprojectionError: Double
    let calibrationId: String

    enum CodingKeys: String, CodingKey {
        case cameraAId = "camera_a_id"
        case cameraBId = "camera_b_id"
        case rotationMatrix = "rotation_matrix"
        case translationVector = "translation_vector"
        case fundamentalMatrix = "fundamental_matrix"
        case essentialMatrix = "essential_matrix"
        case reprojectionError = "reprojection_error"
        case calibrationId = "calibration_id"
    }
}

struct SyncMetadataDTO: Codable, Equatable {
    let sessionId: String
    let syncMethod: String
    let initialOffsetMs: Double
    let driftRateMsPerS: Double
    let syncReferenceStartNs: Int64?
    let syncReferenceEndNs: Int64?
    let matchedFrameCount: Int
    let droppedFrameCount: Int
    let medianAlignmentMs: Double
    let p95AlignmentMs: Double?
    let syncQuality: String

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case syncMethod = "sync_method"
        case initialOffsetMs = "initial_offset_ms"
        case driftRateMsPerS = "drift_rate_ms_per_s"
        case syncReferenceStartNs = "sync_reference_start_ns"
        case syncReferenceEndNs = "sync_reference_end_ns"
        case matchedFrameCount = "matched_frame_count"
        case droppedFrameCount = "dropped_frame_count"
        case medianAlignmentMs = "median_alignment_ms"
        case p95AlignmentMs = "p95_alignment_ms"
        case syncQuality = "sync_quality"
    }
}

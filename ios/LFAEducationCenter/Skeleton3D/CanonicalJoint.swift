import Foundation

enum CanonicalJoint: String, CaseIterable, Codable {
    case nose = "nose"
    case leftEye = "left_eye"
    case rightEye = "right_eye"
    case leftEar = "left_ear"
    case rightEar = "right_ear"
    case neck = "neck"
    case leftShoulder = "left_shoulder"
    case rightShoulder = "right_shoulder"
    case leftElbow = "left_elbow"
    case rightElbow = "right_elbow"
    case leftWrist = "left_wrist"
    case rightWrist = "right_wrist"
    case root = "root"
    case leftHip = "left_hip"
    case rightHip = "right_hip"
    case leftKnee = "left_knee"
    case rightKnee = "right_knee"
    case leftAnkle = "left_ankle"
    case rightAnkle = "right_ankle"
}

enum TriangulationStatus: String, Codable {
    case triangulated = "triangulated"
    case singleViewOnly = "single_view_only"
    case belowConfidence = "below_confidence"
    case jointMissing = "joint_missing"
}

enum SyncMethod: String, Codable {
    case audioClap = "audio_clap"
    case softwareStart = "software_start"
    case manual = "manual"
}

enum SyncQuality: String, Codable {
    case high = "high"
    case acceptable = "acceptable"
    case degraded = "degraded"
    case failed = "failed"
}

struct AppleVisionJointMapping {
    static let map: [(visionName: String, canonical: CanonicalJoint, sourceRawValue: String)] = [
        ("nose",           .nose,           "nose"),
        ("left_eye",       .leftEye,        "leftEye"),
        ("right_eye",      .rightEye,       "rightEye"),
        ("left_ear",       .leftEar,        "leftEar"),
        ("right_ear",      .rightEar,       "rightEar"),
        ("neck",           .neck,           "neck1"),
        ("left_shoulder",  .leftShoulder,   "leftShoulder1"),
        ("right_shoulder", .rightShoulder,  "rightShoulder1"),
        ("left_elbow",     .leftElbow,      "leftElbow1"),
        ("right_elbow",    .rightElbow,     "rightElbow1"),
        ("left_wrist",     .leftWrist,      "leftWrist1"),
        ("right_wrist",    .rightWrist,     "rightWrist1"),
        ("root",           .root,           "root"),
        ("left_hip",       .leftHip,        "leftHip1"),
        ("right_hip",      .rightHip,       "rightHip1"),
        ("left_knee",      .leftKnee,       "leftKnee1"),
        ("right_knee",     .rightKnee,      "rightKnee1"),
        ("left_ankle",     .leftAnkle,      "leftAnkle1"),
        ("right_ankle",    .rightAnkle,     "rightAnkle1"),
    ]
}

import Foundation

enum SessionStatus: String, Codable, CaseIterable {
    case lobby
    case devicesReady = "devices_ready"
    case recordingPending = "recording_pending"
    case recording
    case stopped
    case finalizing
    case completed
    case cancelled
    case active
}

enum ParticipantRole: String, Codable, CaseIterable {
    case instructor
    case player
    case observer
}

enum MCDeviceType: String, Codable, CaseIterable {
    case iphone
    case ipad
    case gopro
}

enum MCDeviceRole: String, Codable, CaseIterable {
    case playerPrimary = "player_primary"
    case playerSecondary = "player_secondary"
    case instructorPrimary = "instructor_primary"
    case auxiliaryCamera = "auxiliary_camera"
}

enum MCDeviceStatus: String, Codable, CaseIterable {
    case registered
    case ready
    case recording
    case stopped
    case disconnected
    case error
}

enum MCStreamType: String, Codable, CaseIterable {
    case video
    case skeleton2d = "skeleton_2d"
    case skeleton3d = "skeleton_3d"
    case audio
    case telemetry
}

struct CalibrationPlaceholderDTO: Codable, Equatable {
    let schemaVersion: Int
    let calibrationId: String?
    let worldOriginCameraId: Int?
    let intrinsicCameras: [IntrinsicCalibrationDTO]
    let stereoPairs: [StereoCalibrationDTO]
    let syncMetadata: SyncMetadataDTO?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case calibrationId = "calibration_id"
        case worldOriginCameraId = "world_origin_camera_id"
        case intrinsicCameras = "intrinsic_cameras"
        case stereoPairs = "stereo_pairs"
        case syncMetadata = "sync_metadata"
    }
}

struct SessionParticipantDTO: Codable, Equatable {
    let id: Int
    let sessionId: Int
    let userId: Int
    let role: ParticipantRole
    let revision: Int
    let joinedAt: String
    let leftAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case sessionId = "session_id"
        case userId = "user_id"
        case role, revision
        case joinedAt = "joined_at"
        case leftAt = "left_at"
    }
}

struct SessionDeviceDTO: Codable, Equatable {
    let id: Int
    let sessionId: Int
    let deviceId: Int
    let participantId: Int?
    let managedByDeviceId: Int?
    let deviceRole: MCDeviceRole
    let status: MCDeviceStatus
    let revision: Int
    let lastHeartbeat: String?
    let registeredAt: String
    let removedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case sessionId = "session_id"
        case deviceId = "device_id"
        case participantId = "participant_id"
        case managedByDeviceId = "managed_by_device_id"
        case deviceRole = "device_role"
        case status, revision
        case lastHeartbeat = "last_heartbeat"
        case registeredAt = "registered_at"
        case removedAt = "removed_at"
    }
}

struct CaptureStreamDTO: Codable, Equatable {
    let id: Int
    let sessionDeviceId: Int
    let streamType: MCStreamType
    let presetJson: [String: AnyCodable]
    let revision: Int
    let createdAt: String
    let startedAt: String?
    let stoppedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case sessionDeviceId = "session_device_id"
        case streamType = "stream_type"
        case presetJson = "preset_json"
        case revision
        case createdAt = "created_at"
        case startedAt = "started_at"
        case stoppedAt = "stopped_at"
    }
}

struct MultiCameraSessionDTO: Codable, Equatable {
    let id: Int
    let sessionUuid: String
    let status: SessionStatus
    let createdByUserId: Int
    let maxParticipants: Int
    let maxDevices: Int
    let revision: Int
    let calibration: CalibrationPlaceholderDTO?
    let scheduledStartAt: String?
    let createdAt: String
    let startedAt: String?
    let stoppedAt: String?
    let finalizedAt: String?
    let cancelledAt: String?
    let participants: [SessionParticipantDTO]
    let devices: [SessionDeviceDTO]
    let streams: [CaptureStreamDTO]

    enum CodingKeys: String, CodingKey {
        case id
        case sessionUuid = "session_uuid"
        case status
        case createdByUserId = "created_by_user_id"
        case maxParticipants = "max_participants"
        case maxDevices = "max_devices"
        case revision, calibration
        case scheduledStartAt = "scheduled_start_at"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case stoppedAt = "stopped_at"
        case finalizedAt = "finalized_at"
        case cancelledAt = "cancelled_at"
        case participants, devices, streams
    }
}

struct AnyCodable: Codable, Equatable {
    let value: Any

    init(_ value: Any) { self.value = value }
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { value = i }
        else if let d = try? c.decode(Double.self) { value = d }
        else if let s = try? c.decode(String.self) { value = s }
        else if let b = try? c.decode(Bool.self) { value = b }
        else { value = "null" }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        if let i = value as? Int { try c.encode(i) }
        else if let d = value as? Double { try c.encode(d) }
        else if let s = value as? String { try c.encode(s) }
        else if let b = value as? Bool { try c.encode(b) }
        else { try c.encodeNil() }
    }
    static func == (lhs: AnyCodable, rhs: AnyCodable) -> Bool {
        String(describing: lhs.value) == String(describing: rhs.value)
    }
}

// MARK: — Capture Cycle models

enum CycleStatus: String, Codable, CaseIterable {
    case preparing
    case recordingPending = "recording_pending"
    case recording
    case stopping
    case completed
    case failed
    case aborted
}

enum CycleDeviceRecordingStatus: String, Codable, CaseIterable {
    case pending
    case confirmedStart = "confirmed_start"
    case confirmedStop = "confirmed_stop"
    case failed
}

enum CycleResult: String, Codable, CaseIterable {
    case success
    case partial
    case failed
}

struct CaptureCycleDeviceDTO: Codable, Equatable {
    let id: Int
    let captureCycleId: Int
    let sessionDeviceId: Int
    let required: Bool
    let recordingStatus: CycleDeviceRecordingStatus
    let startedAt: String?
    let stoppedAt: String?
    let failureReason: String?
    let revision: Int

    enum CodingKeys: String, CodingKey {
        case id
        case captureCycleId = "capture_cycle_id"
        case sessionDeviceId = "session_device_id"
        case required
        case recordingStatus = "recording_status"
        case startedAt = "started_at"
        case stoppedAt = "stopped_at"
        case failureReason = "failure_reason"
        case revision
    }
}

struct CaptureCycleDTO: Codable, Equatable {
    let id: Int
    let sessionId: Int
    let cycleIndex: Int
    let status: CycleStatus
    let result: CycleResult?
    let scheduledStartAt: String?
    let recordingStartedAt: String?
    let stopRequestedAt: String?
    let recordingStoppedAt: String?
    let completedAt: String?
    let failureReason: String?
    let createdByParticipantId: Int
    let idempotencyKey: String
    let revision: Int
    let createdAt: String
    let updatedAt: String
    let cycleDevices: [CaptureCycleDeviceDTO]

    enum CodingKeys: String, CodingKey {
        case id
        case sessionId = "session_id"
        case cycleIndex = "cycle_index"
        case status, result
        case scheduledStartAt = "scheduled_start_at"
        case recordingStartedAt = "recording_started_at"
        case stopRequestedAt = "stop_requested_at"
        case recordingStoppedAt = "recording_stopped_at"
        case completedAt = "completed_at"
        case failureReason = "failure_reason"
        case createdByParticipantId = "created_by_participant_id"
        case idempotencyKey = "idempotency_key"
        case revision
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case cycleDevices = "cycle_devices"
    }
}

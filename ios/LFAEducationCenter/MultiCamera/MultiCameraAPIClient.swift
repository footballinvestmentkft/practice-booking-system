import Foundation

// MARK: — Request types

struct CreateSessionRequest: Codable {
    let maxParticipants: Int
    let maxDevices: Int
    enum CodingKeys: String, CodingKey {
        case maxParticipants = "max_participants"
        case maxDevices = "max_devices"
    }
}

struct JoinSessionRequest: Codable {
    let role: ParticipantRole
}

struct TransitionRequest: Codable {
    let targetStatus: SessionStatus
    let revision: Int
    enum CodingKeys: String, CodingKey {
        case targetStatus = "target_status"
        case revision
    }
}

struct RegisterDeviceRequest: Codable {
    let deviceUuid: String?
    let deviceType: MCDeviceType?
    let deviceName: String?
    let bleIdentifier: String?
    let deviceRole: MCDeviceRole
    let participantId: Int?
    let managedByDeviceId: Int?
    enum CodingKeys: String, CodingKey {
        case deviceUuid = "device_uuid"
        case deviceType = "device_type"
        case deviceName = "device_name"
        case bleIdentifier = "ble_identifier"
        case deviceRole = "device_role"
        case participantId = "participant_id"
        case managedByDeviceId = "managed_by_device_id"
    }
}

struct HeartbeatResponse: Codable, Equatable {
    let sessionDeviceId: Int
    let lastHeartbeat: String
    enum CodingKeys: String, CodingKey {
        case sessionDeviceId = "session_device_id"
        case lastHeartbeat = "last_heartbeat"
    }
}

// MARK: — API Protocol (injectable for tests)

protocol MultiCameraAPIClientProtocol: Sendable {
    func transitionSession(token: String, uuid: String, target: SessionStatus, revision: Int) async throws -> MultiCameraSessionDTO
    func getSession(token: String, uuid: String) async throws -> MultiCameraSessionDTO
    func registerDevice(token: String, uuid: String, request: RegisterDeviceRequest) async throws -> SessionDeviceDTO
    func updateDeviceStatus(token: String, uuid: String, sessionDeviceId: Int,
                            targetStatus: MCDeviceStatus, deviceRevision: Int) async throws -> SessionDeviceDTO
}

struct SystemMultiCameraAPIClient: MultiCameraAPIClientProtocol {
    func transitionSession(token: String, uuid: String, target: SessionStatus, revision: Int) async throws -> MultiCameraSessionDTO {
        try await MultiCameraAPIClient.transitionSession(token: token, uuid: uuid, target: target, revision: revision)
    }
    func getSession(token: String, uuid: String) async throws -> MultiCameraSessionDTO {
        try await MultiCameraAPIClient.getSession(token: token, uuid: uuid)
    }
    func registerDevice(token: String, uuid: String, request: RegisterDeviceRequest) async throws -> SessionDeviceDTO {
        try await MultiCameraAPIClient.registerDevice(token: token, uuid: uuid, request: request)
    }
    func updateDeviceStatus(token: String, uuid: String, sessionDeviceId: Int,
                            targetStatus: MCDeviceStatus, deviceRevision: Int) async throws -> SessionDeviceDTO {
        try await MultiCameraAPIClient.updateDeviceStatus(
            token: token, uuid: uuid, sessionDeviceId: sessionDeviceId,
            targetStatus: targetStatus, deviceRevision: deviceRevision
        )
    }
}

// MARK: — API Client

enum MultiCameraAPIClient {
    private static let base = "/api/v1/multicamera"

    static func createSession(token: String, maxP: Int = 2, maxD: Int = 4) async throws -> MultiCameraSessionDTO {
        try await APIClient.post(
            path: "\(base)/sessions",
            body: CreateSessionRequest(maxParticipants: maxP, maxDevices: maxD),
            token: token
        )
    }

    static func getSession(token: String, uuid: String) async throws -> MultiCameraSessionDTO {
        try await APIClient.get(path: "\(base)/sessions/\(uuid)", token: token)
    }

    static func joinSession(token: String, uuid: String, role: ParticipantRole) async throws -> SessionParticipantDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/join",
            body: JoinSessionRequest(role: role),
            token: token
        )
    }

    static func transitionSession(token: String, uuid: String, target: SessionStatus, revision: Int) async throws -> MultiCameraSessionDTO {
        try await APIClient.patch(
            path: "\(base)/sessions/\(uuid)/status",
            body: TransitionRequest(targetStatus: target, revision: revision),
            token: token
        )
    }

    static func registerDevice(token: String, uuid: String, request: RegisterDeviceRequest) async throws -> SessionDeviceDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/devices",
            body: request,
            token: token
        )
    }

    static func heartbeat(token: String, uuid: String, sessionDeviceId: Int) async throws -> HeartbeatResponse {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/devices/\(sessionDeviceId)/heartbeat",
            body: EmptyBody(),
            token: token
        )
    }

    static func updateDeviceStatus(token: String, uuid: String, sessionDeviceId: Int,
                                    targetStatus: MCDeviceStatus, deviceRevision: Int) async throws -> SessionDeviceDTO {
        try await APIClient.patch(
            path: "\(base)/sessions/\(uuid)/devices/\(sessionDeviceId)/status",
            body: DeviceStatusUpdateBody(targetStatus: targetStatus, deviceRevision: deviceRevision),
            token: token
        )
    }

    static func createCaptureStream(token: String, uuid: String, sessionDeviceId: Int,
                                     streamType: MCStreamType, presetJson: [String: AnyCodable]) async throws -> CaptureStreamDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/devices/\(sessionDeviceId)/streams",
            body: CreateStreamBody(streamType: streamType, presetJson: presetJson),
            token: token
        )
    }

    static func updateCaptureStream(token: String, uuid: String, sessionDeviceId: Int, streamId: Int,
                                     startedAt: String? = nil, stoppedAt: String? = nil,
                                     captureResult: String? = nil, streamRevision: Int) async throws -> CaptureStreamDTO {
        try await APIClient.patch(
            path: "\(base)/sessions/\(uuid)/devices/\(sessionDeviceId)/streams/\(streamId)",
            body: UpdateStreamBody(startedAt: startedAt, stoppedAt: stoppedAt,
                                   captureResult: captureResult, streamRevision: streamRevision),
            token: token
        )
    }

    static func getSessionWithTiming(token: String, uuid: String) async throws -> TimedSessionResponse {
        let start = ProcessInfo.processInfo.systemUptime
        let session: MultiCameraSessionDTO = try await APIClient.get(path: "\(base)/sessions/\(uuid)", token: token)
        let elapsed = ProcessInfo.processInfo.systemUptime - start
        return TimedSessionResponse(session: session, requestDuration: elapsed)
    }
}

struct TimedSessionResponse {
    let session: MultiCameraSessionDTO
    let requestDuration: TimeInterval
}

private struct EmptyBody: Codable {}

private struct DeviceStatusUpdateBody: Codable {
    let targetStatus: MCDeviceStatus
    let deviceRevision: Int
    enum CodingKeys: String, CodingKey {
        case targetStatus = "target_status"
        case deviceRevision = "device_revision"
    }
}

private struct CreateStreamBody: Codable {
    let streamType: MCStreamType
    let presetJson: [String: AnyCodable]
    enum CodingKeys: String, CodingKey {
        case streamType = "stream_type"
        case presetJson = "preset_json"
    }
}

private struct UpdateStreamBody: Codable {
    let startedAt: String?
    let stoppedAt: String?
    let captureResult: String?
    let streamRevision: Int
    enum CodingKeys: String, CodingKey {
        case startedAt = "started_at"
        case stoppedAt = "stopped_at"
        case captureResult = "capture_result"
        case streamRevision = "stream_revision"
    }
}

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
}

private struct EmptyBody: Codable {}

// MARK: — Cycle request types

struct CreateCycleRequest: Codable {
    let idempotencyKey: String
    enum CodingKeys: String, CodingKey {
        case idempotencyKey = "idempotency_key"
    }
}

struct ScheduleCycleRequest: Codable {
    let revision: Int
}

struct StopCycleRequest: Codable {
    let revision: Int
}

struct ConfirmDeviceStartRequest: Codable {
    let startedAt: String
    let cycleDeviceRevision: Int
    enum CodingKeys: String, CodingKey {
        case startedAt = "started_at"
        case cycleDeviceRevision = "cycle_device_revision"
    }
}

struct ConfirmDeviceStopRequest: Codable {
    let stoppedAt: String
    let cycleDeviceRevision: Int
    enum CodingKeys: String, CodingKey {
        case stoppedAt = "stopped_at"
        case cycleDeviceRevision = "cycle_device_revision"
    }
}

// MARK: — Cycle API Client

extension MultiCameraAPIClient {

    static func activateSession(token: String, uuid: String, revision: Int) async throws -> MultiCameraSessionDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/activate",
            body: ScheduleCycleRequest(revision: revision),
            token: token
        )
    }

    static func createCycle(token: String, uuid: String, idempotencyKey: String) async throws -> CaptureCycleDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/cycles",
            body: CreateCycleRequest(idempotencyKey: idempotencyKey),
            token: token
        )
    }

    static func listCycles(token: String, uuid: String) async throws -> [CaptureCycleDTO] {
        try await APIClient.get(path: "\(base)/sessions/\(uuid)/cycles", token: token)
    }

    static func scheduleCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/cycles/\(cycleId)/schedule",
            body: ScheduleCycleRequest(revision: revision),
            token: token
        )
    }

    static func stopCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/cycles/\(cycleId)/stop",
            body: StopCycleRequest(revision: revision),
            token: token
        )
    }

    static func confirmDeviceStart(
        token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
        startedAt: String, cycleDeviceRevision: Int
    ) async throws -> CaptureCycleDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/cycles/\(cycleId)/devices/\(sessionDeviceId)/confirm-start",
            body: ConfirmDeviceStartRequest(startedAt: startedAt, cycleDeviceRevision: cycleDeviceRevision),
            token: token
        )
    }

    static func confirmDeviceStop(
        token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
        stoppedAt: String, cycleDeviceRevision: Int
    ) async throws -> CaptureCycleDTO {
        try await APIClient.post(
            path: "\(base)/sessions/\(uuid)/cycles/\(cycleId)/devices/\(sessionDeviceId)/confirm-stop",
            body: ConfirmDeviceStopRequest(stoppedAt: stoppedAt, cycleDeviceRevision: cycleDeviceRevision),
            token: token
        )
    }
}

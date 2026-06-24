import Foundation
import Combine
import UIKit
import os.log

private let vmLog = Logger(subsystem: "com.lovas-zoltan.lfa-education-center", category: "LobbyVM")

enum LobbyState: Equatable {
    case idle
    case creating
    case joining
    case inLobby(session: MultiCameraSessionDTO)
    case error(String)

    static func == (lhs: LobbyState, rhs: LobbyState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.creating, .creating), (.joining, .joining): return true
        case (.inLobby(let a), .inLobby(let b)): return a.sessionUuid == b.sessionUuid
        case (.error(let a), .error(let b)): return a == b
        default: return false
        }
    }
}

@MainActor
final class MultiCameraSessionViewModel: ObservableObject {

    @Published private(set) var state: LobbyState = .idle
    @Published private(set) var sessionDeviceId: Int?
    let orchestrator = SessionCaptureOrchestrator()

    private var pollingTask: Task<Void, Never>?
    private var heartbeatTask: Task<Void, Never>?
    private var isCreateInProgress = false
    private var hasArmedForCurrentSession = false

    private let authManager: AuthManager
    private let apiClient: MultiCameraAPIClientProtocol
    private let pollingInterval: UInt64
    private let heartbeatInterval: UInt64

    init(
        authManager: AuthManager,
        apiClient: (any MultiCameraAPIClientProtocol)? = nil,
        pollingIntervalSeconds: Double = 3.0,
        heartbeatIntervalSeconds: Double = 5.0
    ) {
        self.authManager = authManager
        self.apiClient = apiClient ?? SystemMultiCameraAPIClient()
        self.pollingInterval = UInt64(pollingIntervalSeconds * 1_000_000_000)
        self.heartbeatInterval = UInt64(heartbeatIntervalSeconds * 1_000_000_000)
    }

    deinit {
        pollingTask?.cancel()
        heartbeatTask?.cancel()
    }

    // MARK: — Public actions

    func createSession(maxP: Int = 2, maxD: Int = 4) {
        guard !isCreateInProgress else { return }
        guard case .idle = state else { return }
        isCreateInProgress = true
        state = .creating
        Task {
            defer { isCreateInProgress = false }
            do {
                guard let token = authManager.accessToken else { throw LobbyError.noAuth }
                let session = try await MultiCameraAPIClient.createSession(token: token, maxP: maxP, maxD: maxD)
                state = .inLobby(session: session)
                let myPartId = session.participants.first?.id
                await autoRegisterDevice(sessionUuid: session.sessionUuid, participantId: myPartId)
                startPolling(uuid: session.sessionUuid)
                startHeartbeat(uuid: session.sessionUuid)
            } catch {
                state = .error(mapError(error))
            }
        }
    }

    func joinSession(uuid: String, role: ParticipantRole = .player) {
        guard case .idle = state else { return }
        state = .joining
        Task {
            do {
                guard let token = authManager.accessToken else { throw LobbyError.noAuth }
                let joinedParticipant = try await MultiCameraAPIClient.joinSession(token: token, uuid: uuid, role: role)
                let session = try await MultiCameraAPIClient.getSession(token: token, uuid: uuid)
                state = .inLobby(session: session)
                await autoRegisterDevice(sessionUuid: session.sessionUuid, participantId: joinedParticipant.id)
                startPolling(uuid: session.sessionUuid)
                startHeartbeat(uuid: session.sessionUuid)
            } catch {
                state = .error(mapError(error))
            }
        }
    }

    func transitionToDevicesReady() {
        guard case .inLobby(let session) = state else { return }
        Task {
            do {
                guard let token = authManager.accessToken else { throw LobbyError.noAuth }
                let updated = try await MultiCameraAPIClient.transitionSession(
                    token: token, uuid: session.sessionUuid,
                    target: .devicesReady, revision: session.revision
                )
                state = .inLobby(session: updated)
            } catch {
                state = .error(mapError(error))
            }
        }
    }

    func armCapture() {
        guard case .inLobby(let session) = state, !hasArmedForCurrentSession else {
            let msg = "[LobbyVM] armCapture: skip — hasArmed=\(hasArmedForCurrentSession) sdId=\(sessionDeviceId?.description ?? "nil")"
            print(msg); vmLog.info("\(msg, privacy: .public)")
            return
        }
        guard let sdId = sessionDeviceId else {
            let msg = "[LobbyVM] armCapture: skip — sessionDeviceId=nil (device not registered)"
            print(msg); vmLog.error("\(msg, privacy: .public)")
            return
        }
        let msg0 = "[LobbyVM] armCapture: starting sdId=\(sdId)"
        print(msg0); vmLog.info("\(msg0, privacy: .public)")
        hasArmedForCurrentSession = true
        Task {
            await orchestrator.armCapture(sessionUUID: session.sessionUuid, deviceId: sdId)
            let orchStateStr = String(describing: orchestrator.orchestrationState)
            let msg1 = "[LobbyVM] armCapture: after arm — orchState=\(orchStateStr)"
            print(msg1); vmLog.info("\(msg1, privacy: .public)")
            if orchestrator.orchestrationState == .armed, let token = authManager.accessToken {
                _ = try? await MultiCameraAPIClient.updateDeviceStatus(
                    token: token, uuid: session.sessionUuid, sessionDeviceId: sdId,
                    targetStatus: .ready, deviceRevision: 1
                )
                let msg2 = "[LobbyVM] armCapture: PATCH status→ready sent"
                print(msg2); vmLog.info("\(msg2, privacy: .public)")
                let preset: [String: AnyCodable] = [
                    "resolution": AnyCodable("1920x1080"), "fps": AnyCodable(30),
                    "codec": AnyCodable("h264"), "camera": AnyCodable("rear_wide")
                ]
                await orchestrator.ensureStreamCreated(
                    token: token, uuid: session.sessionUuid, sdId: sdId, preset: preset
                )
            }
        }
    }

    func startCapture() {
        guard case .inLobby(let session) = state, isInstructor else {
            let inLobby: Bool; if case .inLobby = state { inLobby = true } else { inLobby = false }
            let msg = "[LobbyVM] startCapture: guard1 — inLobby=\(inLobby) isInstructor=\(isInstructor)"
            print(msg); vmLog.warning("\(msg, privacy: .public)")
            return
        }
        let appleDevices = session.devices.filter { $0.deviceRole != .auxiliaryCamera }
        let statusSummary = appleDevices.map { "id=\($0.id)/role=\($0.deviceRole.rawValue)/status=\($0.status.rawValue)" }.joined(separator: " ")
        let msg2 = "[LobbyVM] startCapture: \(appleDevices.count) apple devices: \(statusSummary)"
        print(msg2); vmLog.info("\(msg2, privacy: .public)")
        guard allAppleDevicesReady(session) else {
            let msg3 = "[LobbyVM] startCapture: BLOCKED — not all ready (see above)"
            print(msg3); vmLog.warning("\(msg3, privacy: .public)")
            return
        }
        Task {
            guard let token = authManager.accessToken else { return }
            do {
                let updated = try await transitionWithRetry(
                    token: token, uuid: session.sessionUuid,
                    target: .recordingPending, revision: session.revision
                )
                state = .inLobby(session: updated)
                if let schedStr = updated.scheduledStartAt, let schedDate = ISO8601DateFormatter().date(from: schedStr) {
                    orchestrator.scheduleStart(serverScheduledAt: schedDate)
                }
            } catch {
                state = .error(mapError(error))
            }
        }
    }

    func transitionWithRetry(token: String, uuid: String, target: SessionStatus, revision: Int) async throws -> MultiCameraSessionDTO {
        do {
            return try await apiClient.transitionSession(token: token, uuid: uuid, target: target, revision: revision)
        } catch {
            let nsError = error as NSError
            guard nsError.code == 409 else { throw error }
            let refreshed = try await apiClient.getSession(token: token, uuid: uuid)
            return try await apiClient.transitionSession(token: token, uuid: uuid, target: target, revision: refreshed.revision)
        }
    }

    func stopCapture() {
        guard case .inLobby(let session) = state else { return }
        orchestrator.stopCapture()
        Task {
            guard let token = authManager.accessToken else { return }
            let fresh = try? await MultiCameraAPIClient.getSession(token: token, uuid: session.sessionUuid)
            let current = fresh ?? session
            let target: SessionStatus
            switch SessionStatus(rawValue: current.status.rawValue) {
            case .recording:
                target = .stopped
            case .recordingPending:
                target = .cancelled
            default:
                target = .stopped
            }
            let updated = try? await transitionWithRetry(
                token: token, uuid: current.sessionUuid,
                target: target, revision: current.revision
            )
            if let updated { state = .inLobby(session: updated) }
        }
    }

    func allAppleDevicesReadyPublic(_ session: MultiCameraSessionDTO) -> Bool {
        allAppleDevicesReady(session)
    }

    private func allAppleDevicesReady(_ session: MultiCameraSessionDTO) -> Bool {
        let appleDevices = session.devices.filter { $0.deviceRole != .auxiliaryCamera }
        return !appleDevices.isEmpty && appleDevices.allSatisfy { $0.status == .ready }
    }

    private var hasTransitionedToRecording = false

    private func handlePolledSessionUpdate(_ session: MultiCameraSessionDTO) {
        if session.status == .devicesReady && orchestrator.orchestrationState == .idle {
            armCapture()
        }
        if session.status == .recordingPending && orchestrator.orchestrationState == .armed {
            if let schedStr = session.scheduledStartAt, let schedDate = ISO8601DateFormatter().date(from: schedStr) {
                orchestrator.scheduleStart(serverScheduledAt: schedDate)
            }
        }
        if session.status == .recordingPending && orchestrator.orchestrationState == .capturing
            && isInstructor && !hasTransitionedToRecording {
            hasTransitionedToRecording = true
            Task {
                guard let token = authManager.accessToken else { return }
                let updated = try? await transitionWithRetry(
                    token: token, uuid: session.sessionUuid,
                    target: .recording, revision: session.revision
                )
                if let updated { state = .inLobby(session: updated) }
            }
        }
        if session.status == .stopped && (orchestrator.orchestrationState == .capturing || orchestrator.orchestrationState == .starting) {
            orchestrator.stopCapture()
        }
    }

    func cancelSession() {
        guard case .inLobby(let session) = state else { return }
        orchestrator.stopCapture()
        Task {
            guard let token = authManager.accessToken else { reset(); return }
            _ = try? await transitionWithRetry(
                token: token, uuid: session.sessionUuid,
                target: .cancelled, revision: session.revision
            )
            reset()
        }
    }

    func reset() {
        pollingTask?.cancel()
        heartbeatTask?.cancel()
        pollingTask = nil
        heartbeatTask = nil
        sessionDeviceId = nil
        isCreateInProgress = false
        hasArmedForCurrentSession = false
        hasTransitionedToRecording = false
        orchestrator.teardown()
        state = .idle
    }

    // MARK: — Auto device register

    private func autoRegisterDevice(sessionUuid: String, participantId: Int? = nil) async {
        guard let token = authManager.accessToken else { return }
        #if targetEnvironment(simulator)
        let deviceType: MCDeviceType = .iphone
        #else
        let deviceType: MCDeviceType = UIDevice.current.userInterfaceIdiom == .pad ? .ipad : .iphone
        #endif
        #if DEBUG
        orchestrator.deviceType = deviceType.rawValue
        #endif
        let request = RegisterDeviceRequest(
            deviceUuid: nil, deviceType: deviceType,
            deviceName: UIDevice.current.name, bleIdentifier: nil,
            deviceRole: deviceType == .ipad ? .instructorPrimary : .playerPrimary,
            participantId: participantId, managedByDeviceId: nil
        )
        do {
            let sd = try await MultiCameraAPIClient.registerDevice(token: token, uuid: sessionUuid, request: request)
            sessionDeviceId = sd.id
            let msg = "[LobbyVM] autoRegisterDevice: OK — id=\(sd.id) type=\(deviceType.rawValue)"
            print(msg); vmLog.info("\(msg, privacy: .public)")
        } catch {
            let msg = "[LobbyVM] autoRegisterDevice: FAILED — \(error)"
            print(msg); vmLog.error("\(msg, privacy: .public)")
        }
    }

    // MARK: — Polling

    private func startPolling(uuid: String) {
        pollingTask?.cancel()
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: self?.pollingInterval ?? 3_000_000_000)
                guard !Task.isCancelled, let self else { return }
                guard let token = self.authManager.accessToken else { continue }
                do {
                    let session = try await MultiCameraAPIClient.getSession(token: token, uuid: uuid)
                    self.state = .inLobby(session: session)
                    self.handlePolledSessionUpdate(session)
                } catch {
                    // skip iteration, retry next cycle
                }
            }
        }
    }

    // MARK: — Heartbeat

    private func startHeartbeat(uuid: String) {
        heartbeatTask?.cancel()
        heartbeatTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: self?.heartbeatInterval ?? 5_000_000_000)
                guard !Task.isCancelled, let self else { return }
                guard let token = self.authManager.accessToken,
                      let sdId = self.sessionDeviceId else { continue }
                _ = try? await MultiCameraAPIClient.heartbeat(token: token, uuid: uuid, sessionDeviceId: sdId)
            }
        }
    }

    // MARK: — Helpers

    var isInstructor: Bool {
        guard case .inLobby(let session) = state else { return false }
        return session.participants.contains { $0.role == .instructor && $0.userId == (Self.cachedUserId ?? 0) }
    }

    private static var cachedUserId: Int? {
        let v = UserDefaults.standard.integer(forKey: "lfa_current_user_id")
        return v > 0 ? v : nil
    }

    var sessionUuid: String? {
        guard case .inLobby(let session) = state else { return nil }
        return session.sessionUuid
    }

    private func mapError(_ error: Error) -> String {
        let nsError = error as NSError
        if nsError.domain == "APIClient" {
            switch nsError.code {
            case 401: return "Nincs bejelentkezve"
            case 403: return "Nincs jogosultság"
            case 404: return "Session nem található"
            case 409: return "Session megtelt vagy verzióütközés"
            case 422: return "Érvénytelen művelet"
            default: return "Szerverhiba (\(nsError.code))"
            }
        }
        if error is LobbyError { return "Nincs bejelentkezve" }
        return "Hálózati hiba"
    }
}

private enum LobbyError: Error {
    case noAuth
}

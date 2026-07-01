import Foundation
import Combine
import UIKit

enum ClockSyncState: Equatable {
    case notSynced
    case syncing
    case synced(ClockSyncResult)
    case failed(retryCount: Int, message: String?)
}

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
    @Published private(set) var clockSyncState: ClockSyncState = .notSynced
    @Published private(set) var deviceRegisterError: String?

    private var pollingTask: Task<Void, Never>?
    private var heartbeatTask: Task<Void, Never>?
    private var clockSyncTask: Task<Void, Never>?
    private var isCreateInProgress = false

    let authManager: AuthManager
    private let clockSyncService: ClockSyncService
    private let pollingInterval: UInt64
    private let heartbeatInterval: UInt64
    private static let maxRetries = 3

    private let cycleOrchestrator: CycleCaptureOrchestrator?
    private let playerCycleListener: PlayerCycleListener?
    private let playerCaptureOrchestrator: PlayerCaptureOrchestrator?
    private let capturePreparable: (any CapturePreparable)?

    init(
        authManager: AuthManager,
        clockSyncService: ClockSyncService = ClockSyncService(),
        pollingIntervalSeconds: Double = 3.0,
        heartbeatIntervalSeconds: Double = 5.0,
        cycleOrchestrator: CycleCaptureOrchestrator? = nil,
        playerCycleListener: PlayerCycleListener? = nil,
        playerCaptureOrchestrator: PlayerCaptureOrchestrator? = nil,
        capturePreparable: (any CapturePreparable)? = nil
    ) {
        self.authManager = authManager
        self.clockSyncService = clockSyncService
        self.pollingInterval = UInt64(pollingIntervalSeconds * 1_000_000_000)
        self.heartbeatInterval = UInt64(heartbeatIntervalSeconds * 1_000_000_000)
        self.cycleOrchestrator = cycleOrchestrator
        self.playerCycleListener = playerCycleListener
        self.playerCaptureOrchestrator = playerCaptureOrchestrator
        self.capturePreparable = capturePreparable
    }

    deinit {
        pollingTask?.cancel()
        heartbeatTask?.cancel()
        clockSyncTask?.cancel()
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
                let myParticipantId = session.participants.first { $0.userId == (Self.cachedUserId ?? 0) }?.id
                await autoRegisterDevice(sessionUuid: session.sessionUuid, participantId: myParticipantId)
                startPolling(uuid: session.sessionUuid)
                startHeartbeat(uuid: session.sessionUuid)
                startClockSync()
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
                _ = try await MultiCameraAPIClient.joinSession(token: token, uuid: uuid, role: role)
                let session = try await MultiCameraAPIClient.getSession(token: token, uuid: uuid)
                state = .inLobby(session: session)
                let myParticipantId = session.participants.first { $0.userId == (Self.cachedUserId ?? 0) }?.id
                await autoRegisterDevice(sessionUuid: session.sessionUuid, participantId: myParticipantId)
                startPolling(uuid: session.sessionUuid)
                startHeartbeat(uuid: session.sessionUuid)
                startClockSync()
                playerCycleListener?.start(sessionUuid: session.sessionUuid)
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

    func cancelSession() {
        guard case .inLobby(let session) = state else { return }
        Task {
            do {
                guard let token = authManager.accessToken else { throw LobbyError.noAuth }
                _ = try await MultiCameraAPIClient.transitionSession(
                    token: token, uuid: session.sessionUuid,
                    target: .cancelled, revision: session.revision
                )
            } catch {
                // best-effort
            }
            reset()
        }
    }

    func reset() {
        pollingTask?.cancel()
        heartbeatTask?.cancel()
        clockSyncTask?.cancel()
        pollingTask = nil
        heartbeatTask = nil
        clockSyncTask = nil
        sessionDeviceId = nil
        isCreateInProgress = false
        clockSyncState = .notSynced
        cycleOrchestrator?.reset()
        playerCycleListener?.reset()
        playerCaptureOrchestrator?.reset()
        state = .idle
    }

    func beginCycle() {
        guard isController,
              canStartCapture,
              case .inLobby(let session) = state,
              let sdId = sessionDeviceId else { return }
        cycleOrchestrator?.startCycle(
            sessionUuid: session.sessionUuid,
            sessionDeviceId: sdId,
            sessionRevision: session.revision
        )
    }

    func endCycle() {
        guard isController, sessionUuid != nil else { return }
        Task { await cycleOrchestrator?.stopCycle() }
    }

    // MARK: — Auto device register

    private func autoRegisterDevice(sessionUuid: String, participantId: Int?) async {
        guard let token = authManager.accessToken else {
            deviceRegisterError = "No auth token"
            return
        }
        #if targetEnvironment(simulator)
        let deviceType: MCDeviceType = .iphone
        #else
        let deviceType: MCDeviceType = UIDevice.current.userInterfaceIdiom == .pad ? .ipad : .iphone
        #endif
        let myRole = self.resolvedParticipantRole
        let deviceRole: MCDeviceRole = myRole == .instructor ? .instructorPrimary : .playerPrimary
        let request = RegisterDeviceRequest(
            deviceUuid: nil, deviceType: deviceType,
            deviceName: UIDevice.current.name, bleIdentifier: nil,
            deviceRole: deviceRole,
            participantId: participantId, managedByDeviceId: nil
        )
        do {
            let sd = try await MultiCameraAPIClient.registerDevice(token: token, uuid: sessionUuid, request: request)
            sessionDeviceId = sd.id
            deviceRegisterError = nil
            print("[LobbyVM] autoRegisterDevice: OK sdId=\(sd.id)")
            // Attach PCO immediately after registration — must not be gated on updateDeviceStatus.
            // If updateDeviceStatus throws (revision conflict, network), PCO would never subscribe
            // to PCL state changes and the player would stay "pending" forever.
            //
            // Explicit POSITIVE role gate (2026-07-01 flow audit): only player-role devices may
            // attach a PlayerCaptureOrchestrator. Before this gate, attach() ran unconditionally
            // for EVERY device role including the instructor — so the instructor's own PCO
            // independently reacted to the same cycle its CycleCaptureOrchestrator was already
            // driving, racing it for confirmDeviceStart/Stop on the instructor's own device_id.
            // Whichever orchestrator's confirm call landed second got a stale-revision 409, and
            // CCO's error handler treats any confirm-start HTTP error as fatal — tearing down the
            // instructor's OWN capture even though the backend already showed confirmed_start=true.
            if Self.shouldAttachPlayerCaptureOrchestrator(deviceRole: sd.deviceRole),
               let listener = playerCycleListener, let orch = playerCaptureOrchestrator {
                orch.attach(listener: listener, sessionUuid: sessionUuid, playerSessionDeviceId: sd.id)
            }
            if Self.shouldAutoPrepare(deviceRole: sd.deviceRole) {
                await capturePreparable?.autoPrepare(sessionUUID: sessionUuid, deviceId: sd.id)
            }
            do {
                _ = try await MultiCameraAPIClient.updateDeviceStatus(
                    token: token, uuid: sessionUuid,
                    sessionDeviceId: sd.id, targetStatus: .ready,
                    deviceRevision: sd.revision
                )
                print("[LobbyVM] autoRegisterDevice: device \(sd.id) → ready")
            } catch {
                print("[LobbyVM] autoRegisterDevice: updateDeviceStatus FAILED (non-fatal) error=\(error)")
            }
        } catch {
            deviceRegisterError = "\(error)"
            print("[LobbyVM] autoRegisterDevice: FAILED error=\(error)")
        }
    }

    func retryDeviceRegistration() {
        guard case .inLobby(let session) = state else { return }
        deviceRegisterError = nil
        let myParticipantId = session.participants.first { $0.userId == (Self.cachedUserId ?? 0) }?.id
        Task { await autoRegisterDevice(sessionUuid: session.sessionUuid, participantId: myParticipantId) }
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
                    guard !Task.isCancelled else { return }
                    self.state = .inLobby(session: session)
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

    // MARK: — Clock sync

    func startClockSync() {
        clockSyncTask?.cancel()
        clockSyncTask = Task { [weak self] in
            guard let self else { return }
            self.clockSyncState = .syncing
            for attempt in 0...Self.maxRetries {
                guard !Task.isCancelled else { return }
                if attempt > 0 {
                    let delayNs = UInt64(pow(2.0, Double(attempt - 1))) * 1_000_000_000
                    try? await Task.sleep(nanoseconds: delayNs)
                    guard !Task.isCancelled else { return }
                }
                do {
                    let result = try await self.clockSyncService.sync()
                    guard !Task.isCancelled else { return }
                    self.clockSyncState = .synced(result)
                    return
                } catch {
                    if attempt == Self.maxRetries {
                        self.clockSyncState = .failed(
                            retryCount: Self.maxRetries,
                            message: error.localizedDescription
                        )
                    }
                }
            }
        }
    }

    func retryClockSync() {
        startClockSync()
    }

    var isClockSynced: Bool {
        if case .synced = clockSyncState { return true }
        return false
    }

    var canStartCapture: Bool {
        guard case .inLobby = state else { return false }
        guard isClockSynced else { return false }
        return true
    }

    // MARK: — Capture authority

    static func resolveDeviceRole(state: LobbyState, sessionDeviceId: Int?) -> MCDeviceRole? {
        guard case .inLobby(let session) = state, let sdId = sessionDeviceId else { return nil }
        return session.devices.first { $0.id == sdId && $0.removedAt == nil }?.deviceRole
    }

    static func resolveIsController(role: MCDeviceRole?) -> Bool {
        role == .instructorPrimary
    }

    var myDeviceRole: MCDeviceRole? {
        Self.resolveDeviceRole(state: state, sessionDeviceId: sessionDeviceId)
    }

    var isController: Bool {
        Self.resolveIsController(role: myDeviceRole)
    }

    static func shouldAutoPrepare(deviceRole: MCDeviceRole) -> Bool {
        switch deviceRole {
        case .instructorPrimary, .playerPrimary, .playerSecondary:
            return true
        case .auxiliaryCamera:
            return false
        }
    }

    /// Explicit POSITIVE allow-list — only these device roles may attach a
    /// PlayerCaptureOrchestrator to a PlayerCycleListener. Deliberately positive
    /// (not `!= .instructorPrimary`) so a future new MCDeviceRole case defaults to
    /// NOT attaching unless someone explicitly adds it here.
    static func shouldAttachPlayerCaptureOrchestrator(deviceRole: MCDeviceRole) -> Bool {
        switch deviceRole {
        case .playerPrimary, .playerSecondary:
            return true
        case .instructorPrimary, .auxiliaryCamera:
            return false
        }
    }

    // MARK: — Helpers

    var isInstructor: Bool {
        resolvedParticipantRole == .instructor
    }

    var resolvedParticipantRole: ParticipantRole {
        guard case .inLobby(let session) = state else { return .player }
        let isInst = session.participants.contains { $0.role == .instructor && $0.userId == (Self.cachedUserId ?? 0) }
        return isInst ? .instructor : .player
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
        if error is LobbyError { return "Nincs bejelentkezve" }
        if let apiErr = error as? APIError {
            switch apiErr {
            case .invalidURL:
                return "Invalid URL"
            case .httpError(let code, let detail):
                return "HTTP \(code): \(detail ?? "no detail")"
            case .decodingError:
                return "Decode error (response mismatch)"
            case .networkError(let underlying):
                return "Network: \(underlying.localizedDescription)"
            case .unauthorized:
                return "Unauthorized (token expired?)"
            }
        }
        return "Error: \(error)"
    }
}

private enum LobbyError: Error {
    case noAuth
}

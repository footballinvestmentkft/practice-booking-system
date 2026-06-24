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
    // Per-device readiness message; set when startCapture() is blocked by a specific device.
    @Published private(set) var deviceNotReadyMessage: String?
    let orchestrator = SessionCaptureOrchestrator()

    private var pollingTask: Task<Void, Never>?
    private var heartbeatTask: Task<Void, Never>?
    private var isCreateInProgress = false
    private var hasArmedForCurrentSession = false
    private var hasTransitionedToRecording = false
    // Revision from registerDevice() response; used for the ready-PATCH to avoid hardcoding 1.
    private(set) var sessionDeviceRevision: Int = 1

    private let authManager: AuthManager
    private let apiClient: MultiCameraAPIClientProtocol
    // Injectable token provider — defaults to authManager.accessToken; overridden in tests.
    private let tokenProvider: () -> String?
    private let pollingInterval: UInt64
    private let heartbeatInterval: UInt64

    init(
        authManager: AuthManager,
        apiClient: (any MultiCameraAPIClientProtocol)? = nil,
        tokenProvider: (() -> String?)? = nil,
        pollingIntervalSeconds: Double = 3.0,
        heartbeatIntervalSeconds: Double = 5.0
    ) {
        self.authManager = authManager
        self.apiClient = apiClient ?? SystemMultiCameraAPIClient()
        self.tokenProvider = tokenProvider ?? { authManager.accessToken }
        self.pollingInterval = UInt64(pollingIntervalSeconds * 1_000_000_000)
        self.heartbeatInterval = UInt64(heartbeatIntervalSeconds * 1_000_000_000)
    }

    deinit {
        pollingTask?.cancel()
        heartbeatTask?.cancel()
    }

    // MARK: — ISO8601 parser (shared; handles fractional seconds and plain Internet date)

    // Tries fractional seconds first (the server's typical format), falls back to plain.
    static func parseScheduledDate(_ string: String) -> Date? {
        fractionalFormatter.date(from: string) ?? plainFormatter.date(from: string)
    }

    private static let fractionalFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let plainFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    // MARK: — Public actions

    func createSession(maxP: Int = 2, maxD: Int = 4) {
        guard !isCreateInProgress else { return }
        guard case .idle = state else { return }
        isCreateInProgress = true
        state = .creating
        Task {
            defer { isCreateInProgress = false }
            do {
                guard let token = tokenProvider() else { throw LobbyError.noAuth }
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
                guard let token = tokenProvider() else { throw LobbyError.noAuth }
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
                guard let token = tokenProvider() else { throw LobbyError.noAuth }
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
            let msg = "[LobbyVM] armCapture: BLOCKED — sessionDeviceId=nil (device registration failed or pending)"
            print(msg); vmLog.error("\(msg, privacy: .public)")
            return
        }
        let msg0 = "[LobbyVM] armCapture: starting sdId=\(sdId) rev=\(sessionDeviceRevision)"
        print(msg0); vmLog.info("\(msg0, privacy: .public)")
        hasArmedForCurrentSession = true
        Task {
            await orchestrator.armCapture(sessionUUID: session.sessionUuid, deviceId: sdId)
            let orchStateStr = String(describing: orchestrator.orchestrationState)
            let msg1 = "[LobbyVM] armCapture: after arm — orchState=\(orchStateStr)"
            print(msg1); vmLog.info("\(msg1, privacy: .public)")
            guard orchestrator.orchestrationState == .armed, let token = tokenProvider() else { return }
            await _patchDeviceReady(token: token, sessionUuid: session.sessionUuid, sdId: sdId)
            guard case .inLobby = state else { return }  // _patchDeviceReady may have set .error
            let preset: [String: AnyCodable] = [
                "resolution": AnyCodable("1920x1080"), "fps": AnyCodable(30),
                "codec": AnyCodable("h264"), "camera": AnyCodable("rear_wide")
            ]
            await orchestrator.ensureStreamCreated(
                token: token, uuid: session.sessionUuid, sdId: sdId, preset: preset
            )
        }
    }

    // Extracted for unit-testability: sends the PATCH /devices/{id}/status ready call.
    // Uses sessionDeviceRevision (not hardcoded 1). Errors are surfaced — never swallowed.
    func _patchDeviceReady(token: String, sessionUuid: String, sdId: Int) async {
        do {
            let updated = try await apiClient.updateDeviceStatus(
                token: token, uuid: sessionUuid, sessionDeviceId: sdId,
                targetStatus: .ready, deviceRevision: sessionDeviceRevision
            )
            sessionDeviceRevision = updated.revision
            let msg = "[LobbyVM] _patchDeviceReady: OK — new rev=\(updated.revision)"
            print(msg); vmLog.info("\(msg, privacy: .public)")
        } catch {
            let msg = "[LobbyVM] _patchDeviceReady: FAILED — \(error)"
            print(msg); vmLog.error("\(msg, privacy: .public)")
            state = .error("Device status PATCH hiba (\(mapError(error))). Indíts új sessiont.")
        }
    }

    func startCapture() async {
        guard case .inLobby(let session) = state else {
            vmLog.warning("[LobbyVM] startCapture: not inLobby — skipping")
            return
        }
        if let instrErr = instructorIdentityError(for: session) {
            let msg = "[LobbyVM] startCapture: instructor check failed — \(instrErr)"
            print(msg); vmLog.warning("\(msg, privacy: .public)")
            state = .error(instrErr)
            return
        }
        guard let token = tokenProvider() else {
            state = .error("Nincs bejelentkezve")
            return
        }
        // Fresh GET — never rely on cached polling data for a gating decision.
        let fresh: MultiCameraSessionDTO
        do {
            fresh = try await apiClient.getSession(token: token, uuid: session.sessionUuid)
        } catch {
            let msg = "[LobbyVM] startCapture: fresh GET failed — \(error)"
            print(msg); vmLog.error("\(msg, privacy: .public)")
            state = .error("Session lekérés hiba: \(mapError(error))")
            return
        }
        state = .inLobby(session: fresh)

        // Duplicate device detection (same role registered more than once, active entries only).
        if let dupWarning = duplicateDeviceWarning(in: fresh) {
            let msg = "[LobbyVM] startCapture: DUPLICATE DEVICES — \(dupWarning)"
            print(msg); vmLog.warning("\(msg, privacy: .public)")
            state = .error(dupWarning)
            return
        }

        // Per-device readiness check — active non-auxiliary devices only.
        let appleDevices = fresh.devices.filter {
            $0.deviceRole != .auxiliaryCamera && $0.removedAt == nil
        }
        let summary = appleDevices.map {
            "id=\($0.id)/\($0.deviceRole.rawValue)/\($0.status.rawValue)"
        }.joined(separator: " ")
        let msg2 = "[LobbyVM] startCapture: \(appleDevices.count) active apple devices: \(summary)"
        print(msg2); vmLog.info("\(msg2, privacy: .public)")

        let notReady = appleDevices.filter { $0.status != .ready }
        if !notReady.isEmpty {
            let names = notReady.map {
                "\($0.deviceRole.rawValue) (id=\($0.id), status=\($0.status.rawValue))"
            }.joined(separator: ", ")
            let errMsg = "Nem kész eszközök: \(names)"
            let msg3 = "[LobbyVM] startCapture: BLOCKED — \(errMsg)"
            print(msg3); vmLog.warning("\(msg3, privacy: .public)")
            deviceNotReadyMessage = errMsg
            return
        }
        deviceNotReadyMessage = nil

        do {
            let updated = try await transitionWithRetry(
                token: token, uuid: fresh.sessionUuid,
                target: .recordingPending, revision: fresh.revision
            )
            state = .inLobby(session: updated)

            guard let schedStr = updated.scheduledStartAt else {
                let msg4 = "[LobbyVM] startCapture: scheduledStartAt nil after transition"
                print(msg4); vmLog.error("\(msg4, privacy: .public)")
                state = .error("Backend nem adott ütemezési időpontot (scheduled_start_at hiányzik)")
                return
            }
            guard let schedDate = Self.parseScheduledDate(schedStr) else {
                let msg5 = "[LobbyVM] startCapture: ISO8601 parse FAILED for '\(schedStr)'"
                print(msg5); vmLog.error("\(msg5, privacy: .public)")
                state = .error("Érvénytelen ütemezési időpont: \(schedStr)")
                return
            }
            let msg6 = "[LobbyVM] startCapture: scheduleStart at \(schedStr)"
            print(msg6); vmLog.info("\(msg6, privacy: .public)")
            orchestrator.scheduleStart(serverScheduledAt: schedDate)
        } catch {
            state = .error(mapError(error))
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
            guard let token = tokenProvider() else { return }
            let fresh = try? await MultiCameraAPIClient.getSession(token: token, uuid: session.sessionUuid)
            let current = fresh ?? session
            let target: SessionStatus
            switch current.status {
            case .recording: target = .stopped
            case .recordingPending: target = .cancelled
            default: target = .stopped
            }
            let updated = try? await transitionWithRetry(
                token: token, uuid: current.sessionUuid,
                target: target, revision: current.revision
            )
            if let updated { state = .inLobby(session: updated) }
        }
    }

    // MARK: — Device readiness helpers

    func allAppleDevicesReadyPublic(_ session: MultiCameraSessionDTO) -> Bool {
        let active = session.devices.filter {
            $0.deviceRole != .auxiliaryCamera && $0.removedAt == nil
        }
        return !active.isEmpty && active.allSatisfy { $0.status == .ready }
    }

    // Returns a user-visible warning when the same device role appears more than once
    // in active (non-removed) entries — a sign of duplicate registration from session reuse.
    func duplicateDeviceWarning(in session: MultiCameraSessionDTO) -> String? {
        let active = session.devices.filter {
            $0.deviceRole != .auxiliaryCamera && $0.removedAt == nil
        }
        var roleToIds: [MCDeviceRole: [Int]] = [:]
        for d in active { roleToIds[d.deviceRole, default: []].append(d.id) }
        let dups = roleToIds.filter { $0.value.count > 1 }
        guard !dups.isEmpty else { return nil }
        let msgs = dups.sorted { $0.key.rawValue < $1.key.rawValue }
                       .map { "\($0.key.rawValue): \($0.value.count)× (ids: \($0.value))" }
        return "Duplikált device rekordok — Indíts új sessiont! (\(msgs.joined(separator: ", ")))"
    }

    // Returns a user-visible error if instructor identity cannot be confirmed, nil if OK.
    func instructorIdentityError(for session: MultiCameraSessionDTO) -> String? {
        guard let uid = Self.cachedUserId else {
            return "Nincs bejelentkezve (lfa_current_user_id hiányzik a UserDefaults-ból)"
        }
        guard session.participants.contains(where: { $0.userId == uid && $0.role == .instructor }) else {
            return "A felhasználó (id=\(uid)) nem instructor ebben a sessionben"
        }
        return nil
    }

    var isInstructor: Bool {
        guard case .inLobby(let session) = state else { return false }
        return instructorIdentityError(for: session) == nil
    }

    // MARK: — Session state handler (polling)

    private func handlePolledSessionUpdate(_ session: MultiCameraSessionDTO) {
        if session.status == .devicesReady && orchestrator.orchestrationState == .idle {
            armCapture()
        }
        if session.status == .recordingPending && orchestrator.orchestrationState == .armed {
            if let schedStr = session.scheduledStartAt {
                if let schedDate = Self.parseScheduledDate(schedStr) {
                    orchestrator.scheduleStart(serverScheduledAt: schedDate)
                } else {
                    let msg = "[LobbyVM] handlePolledUpdate: ISO8601 parse FAILED for '\(schedStr)'"
                    print(msg); vmLog.error("\(msg, privacy: .public)")
                }
            }
        }
        if session.status == .recordingPending && orchestrator.orchestrationState == .capturing
            && isInstructor && !hasTransitionedToRecording {
            hasTransitionedToRecording = true
            Task {
                guard let token = tokenProvider() else { return }
                let updated = try? await transitionWithRetry(
                    token: token, uuid: session.sessionUuid,
                    target: .recording, revision: session.revision
                )
                if let updated { state = .inLobby(session: updated) }
            }
        }
        if session.status == .stopped
            && (orchestrator.orchestrationState == .capturing
                || orchestrator.orchestrationState == .starting) {
            orchestrator.stopCapture()
        }
    }

    func cancelSession() {
        guard case .inLobby(let session) = state else { return }
        orchestrator.stopCapture()
        Task {
            guard let token = tokenProvider() else { reset(); return }
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
        sessionDeviceRevision = 1
        deviceNotReadyMessage = nil
        isCreateInProgress = false
        hasArmedForCurrentSession = false
        hasTransitionedToRecording = false
        orchestrator.teardown()
        state = .idle
    }

    // MARK: — Auto device register (3-retry; never silent)

    // Internal visibility so unit tests can call it directly without going through
    // createSession()/joinSession() which use static API calls outside the injectable protocol.
    func autoRegisterDevice(sessionUuid: String, participantId: Int? = nil) async {
        guard let token = tokenProvider() else {
            let msg = "[LobbyVM] autoRegisterDevice: FAILED — no auth token"
            print(msg); vmLog.error("\(msg, privacy: .public)")
            return
        }
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
        for attempt in 1...3 {
            do {
                let sd = try await apiClient.registerDevice(token: token, uuid: sessionUuid, request: request)
                sessionDeviceId = sd.id
                sessionDeviceRevision = sd.revision
                let msg = "[LobbyVM] autoRegisterDevice: OK — attempt=\(attempt) id=\(sd.id) rev=\(sd.revision) type=\(deviceType.rawValue)"
                print(msg); vmLog.info("\(msg, privacy: .public)")
                return
            } catch {
                let msg = "[LobbyVM] autoRegisterDevice: attempt=\(attempt)/3 FAILED — \(error)"
                print(msg); vmLog.error("\(msg, privacy: .public)")
                if attempt < 3 { try? await Task.sleep(nanoseconds: 500_000_000) }
            }
        }
        let errMsg = "Device regisztráció sikertelen (3 kísérlet után). Ellenőrizd a hálózatot és indíts új sessiont."
        let msg = "[LobbyVM] autoRegisterDevice: ALL 3 FAILED — \(errMsg)"
        print(msg); vmLog.error("\(msg, privacy: .public)")
        state = .error(errMsg)
    }

    // MARK: — Polling

    private func startPolling(uuid: String) {
        pollingTask?.cancel()
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: self?.pollingInterval ?? 3_000_000_000)
                guard !Task.isCancelled, let self else { return }
                guard let token = self.tokenProvider() else { continue }
                do {
                    let session = try await MultiCameraAPIClient.getSession(token: token, uuid: uuid)
                    self.state = .inLobby(session: session)
                    self.handlePolledSessionUpdate(session)
                } catch { }
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
                guard let token = self.tokenProvider(),
                      let sdId = self.sessionDeviceId else { continue }
                _ = try? await MultiCameraAPIClient.heartbeat(token: token, uuid: uuid, sessionDeviceId: sdId)
            }
        }
    }

    // MARK: — Helpers

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
            case 409: return "Verzióütközés (409)"
            case 422: return "Érvénytelen művelet"
            default: return "Szerverhiba (\(nsError.code))"
            }
        }
        if error is LobbyError { return "Nincs bejelentkezve" }
        return "Hálózati hiba"
    }

    // MARK: — Test support

    // Sets the lobby state directly for unit tests that need the VM in .inLobby
    // without going through createSession()/joinSession() (which use static API calls).
    func _setLobbyStateForTesting(_ session: MultiCameraSessionDTO) {
        state = .inLobby(session: session)
    }
}

private enum LobbyError: Error {
    case noAuth
}

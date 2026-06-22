import Foundation
import Combine
import UIKit

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

    private var pollingTask: Task<Void, Never>?
    private var heartbeatTask: Task<Void, Never>?
    private var isCreateInProgress = false

    private let authManager: AuthManager
    private let pollingInterval: UInt64
    private let heartbeatInterval: UInt64

    init(
        authManager: AuthManager,
        pollingIntervalSeconds: Double = 3.0,
        heartbeatIntervalSeconds: Double = 5.0
    ) {
        self.authManager = authManager
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
                await autoRegisterDevice(sessionUuid: session.sessionUuid)
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
                _ = try await MultiCameraAPIClient.joinSession(token: token, uuid: uuid, role: role)
                let session = try await MultiCameraAPIClient.getSession(token: token, uuid: uuid)
                state = .inLobby(session: session)
                await autoRegisterDevice(sessionUuid: session.sessionUuid)
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
        pollingTask = nil
        heartbeatTask = nil
        sessionDeviceId = nil
        isCreateInProgress = false
        state = .idle
    }

    // MARK: — Auto device register

    private func autoRegisterDevice(sessionUuid: String) async {
        guard let token = authManager.accessToken else { return }
        #if targetEnvironment(simulator)
        let deviceType: MCDeviceType = .iphone
        #else
        let deviceType: MCDeviceType = UIDevice.current.userInterfaceIdiom == .pad ? .ipad : .iphone
        #endif
        let request = RegisterDeviceRequest(
            deviceUuid: nil, deviceType: deviceType,
            deviceName: UIDevice.current.name, bleIdentifier: nil,
            deviceRole: deviceType == .ipad ? .instructorPrimary : .playerPrimary,
            participantId: nil, managedByDeviceId: nil
        )
        do {
            let sd = try await MultiCameraAPIClient.registerDevice(token: token, uuid: sessionUuid, request: request)
            sessionDeviceId = sd.id
        } catch {
            // non-fatal — device can be registered later
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

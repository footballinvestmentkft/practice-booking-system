import Foundation
import Combine

// MARK: — PlayerListenerState

enum PlayerListenerState: Equatable {
    case idle
    case waitingForCycle
    case pendingCycleDetected(cycleId: Int)
    case recordingDetected(cycleId: Int)
    case stoppingDetected(cycleId: Int)
    /// Reserved for ORCH-3B+ escalation; not set by ORCH-3A polling (skip/log only).
    case failed(String)
}

// MARK: — CycleListClient

protocol CycleListClient {
    func listCycles(token: String, uuid: String) async throws -> [CaptureCycleDTO]
}

struct LiveCycleListClient: CycleListClient {
    func listCycles(token: String, uuid: String) async throws -> [CaptureCycleDTO] {
        try await MultiCameraAPIClient.listCycles(token: token, uuid: uuid)
    }
}

// MARK: — PlayerCycleListener

@MainActor
final class PlayerCycleListener: ObservableObject {

    @Published private(set) var state: PlayerListenerState = .idle

    private var pollingTask: Task<Void, Never>?
    var handledCycleIds: Set<Int> = []  // internal visibility for testability

    private let authManager: any AccessTokenProvider
    private let cycleListClient: CycleListClient
    private let pollingIntervalNs: UInt64
    private let sleepProvider: (UInt64) async throws -> Void

    init(
        authManager: any AccessTokenProvider,
        cycleListClient: CycleListClient = LiveCycleListClient(),
        pollingIntervalNs: UInt64 = 3_000_000_000,
        sleepProvider: @escaping (UInt64) async throws -> Void = { ns in
            try await Task.sleep(nanoseconds: ns)
        }
    ) {
        self.authManager = authManager
        self.cycleListClient = cycleListClient
        self.pollingIntervalNs = pollingIntervalNs
        self.sleepProvider = sleepProvider
    }

    // MARK: — Public API

    func start(sessionUuid: String) {
        guard pollingTask == nil else { return }
        state = .waitingForCycle
        pollingTask = Task { [weak self] in
            await self?.pollLoop(sessionUuid: sessionUuid)
        }
    }

    func stop() {
        pollingTask?.cancel()
        pollingTask = nil
        state = .idle
    }

    func reset() {
        stop()
        handledCycleIds = []
    }

    // MARK: — Polling loop

    private func pollLoop(sessionUuid: String) async {
        while !Task.isCancelled {
            do {
                try await sleepProvider(pollingIntervalNs)
            } catch {
                return  // CancellationError — exit cleanly
            }
            guard !Task.isCancelled else { return }
            guard let token = authManager.accessToken else { continue }
            do {
                let cycles = try await cycleListClient.listCycles(token: token, uuid: sessionUuid)
                let newState = classify(cycles)
                if newState != state {
                    state = newState
                }
            } catch {
                // ORCH-3A: single error → skip and retry next poll interval.
                // .failed state is reserved for ORCH-3B+ persistent-error escalation.
            }
        }
    }

    // MARK: — Cycle classification

    func classify(_ cycles: [CaptureCycleDTO]) -> PlayerListenerState {
        for cycle in cycles where isTerminal(cycle.status) {
            handledCycleIds.insert(cycle.id)
        }
        guard let cycle = cycles.first(where: {
            !isTerminal($0.status) && !handledCycleIds.contains($0.id)
        }) else {
            return .waitingForCycle
        }
        switch cycle.status {
        case .preparing:
            return .waitingForCycle
        case .recordingPending:
            return .pendingCycleDetected(cycleId: cycle.id)
        case .recording:
            return .recordingDetected(cycleId: cycle.id)
        case .stopping:
            return .stoppingDetected(cycleId: cycle.id)
        case .completed, .failed, .aborted:
            handledCycleIds.insert(cycle.id)
            return .waitingForCycle
        }
    }

    private func isTerminal(_ status: CycleStatus) -> Bool {
        switch status {
        case .completed, .failed, .aborted: return true
        default: return false
        }
    }
}

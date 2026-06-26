import Foundation
import Combine

// MARK: — PlayerOrchestratorState

enum PlayerOrchestratorState: Equatable {
    case idle
    case waitingForStart(cycleId: Int)  // clock wait in progress (or immediate for late-join)
    case capturing(cycleId: Int)        // startCapture() called; awaiting confirmDeviceStart
    case confirmed(cycleId: Int)        // confirmDeviceStart succeeded
    case failed(String)
}

// MARK: — PlayerCaptureOrchestrator

@MainActor
final class PlayerCaptureOrchestrator: ObservableObject {

    // MARK: — Constants
    private static let scheduledStartToleranceMs: Double = 2_000

    // MARK: — Published state
    @Published private(set) var state: PlayerOrchestratorState = .idle

    // MARK: — Dependencies
    private let authManager: any AccessTokenProvider
    private let clockSyncService: ClockSyncService
    private let captureController: CaptureController
    private let cycleAPIClient: CycleAPIClient
    private let sleepProvider: (UInt64) async throws -> Void

    // MARK: — Session context (set at attach time)
    private var sessionUuid: String?
    private var playerSessionDeviceId: Int?

    // MARK: — Tracking
    private var listenerSubscription: AnyCancellable?
    private var captureSubscription: AnyCancellable?
    private var startTask: Task<Void, Never>?
    private var handledCycleIds: Set<Int> = []  // prevents duplicate confirm-start

    // MARK: — Active cycle (stored when start begins; needed for confirmDeviceStart)
    private var activeCycle: CaptureCycleDTO?

    // MARK: — Init

    init(
        authManager: any AccessTokenProvider,
        clockSyncService: ClockSyncService,
        captureController: CaptureController,
        cycleAPIClient: CycleAPIClient = LiveCycleAPIClient(),
        sleepProvider: @escaping (UInt64) async throws -> Void = { ns in
            try await Task.sleep(nanoseconds: ns)
        }
    ) {
        self.authManager = authManager
        self.clockSyncService = clockSyncService
        self.captureController = captureController
        self.cycleAPIClient = cycleAPIClient
        self.sleepProvider = sleepProvider
    }

    // MARK: — Public API

    /// Call after autoRegisterDevice completes so playerSessionDeviceId is known.
    /// Subscribing to listener.$state delivers the current value immediately,
    /// so late-attach is handled without a separate "read current state" step.
    func attach(listener: PlayerCycleListener, sessionUuid: String, playerSessionDeviceId: Int) {
        self.sessionUuid = sessionUuid
        self.playerSessionDeviceId = playerSessionDeviceId

        listenerSubscription?.cancel()
        listenerSubscription = listener.$state
            .receive(on: DispatchQueue.main)
            .sink { [weak self, weak listener] newState in
                self?.handleListenerState(newState, currentCycle: listener?.currentCycle)
            }
    }

    func detach() {
        listenerSubscription?.cancel()
        listenerSubscription = nil
        startTask?.cancel()
        startTask = nil
        captureSubscription?.cancel()
        captureSubscription = nil
        activeCycle = nil
        sessionUuid = nil
        playerSessionDeviceId = nil
        state = .idle
    }

    func reset() {
        detach()
        handledCycleIds = []
    }

    // MARK: — Listener state handler (internal for testability)

    func handleListenerState(_ listenerState: PlayerListenerState, currentCycle: CaptureCycleDTO?) {
        switch listenerState {
        case .pendingCycleDetected(let cycleId):
            guard case .idle = state else { return }
            guard !handledCycleIds.contains(cycleId) else { return }
            guard let cycle = currentCycle else { return }
            beginStart(cycle: cycle, immediate: false)

        case .recordingDetected(let cycleId):
            guard case .idle = state else { return }
            guard !handledCycleIds.contains(cycleId) else { return }
            guard let cycle = currentCycle else { return }
            beginStart(cycle: cycle, immediate: true)

        default:
            break
        }
    }

    private func beginStart(cycle: CaptureCycleDTO, immediate: Bool) {
        state = .waitingForStart(cycleId: cycle.id)
        activeCycle = cycle
        startTask?.cancel()
        startTask = Task { [weak self] in
            await self?.performStart(cycle: cycle, immediate: immediate)
        }
    }

    // MARK: — Start orchestration

    private func performStart(cycle: CaptureCycleDTO, immediate: Bool) async {
        if !immediate {
            do {
                try await waitForScheduledStart(cycle: cycle)
            } catch is CancellationError {
                return
            } catch let failure as PlayerOrchestratorFailure {
                state = .failed(failure.description)
                return
            } catch {
                state = .failed(error.localizedDescription)
                return
            }
        }

        guard !Task.isCancelled else { return }

        subscribeToCaptureState()
        captureController.startCapture()
    }

    // MARK: — Scheduled start wait (mirrors CycleCaptureOrchestrator)

    private func waitForScheduledStart(cycle: CaptureCycleDTO) async throws {
        guard let scheduledStartAtStr = cycle.scheduledStartAt else {
            throw PlayerOrchestratorFailure.scheduledStartAtMissing
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let scheduledDate = formatter.date(from: scheduledStartAtStr) else {
            throw PlayerOrchestratorFailure.scheduledStartAtInvalid
        }
        guard let serverTimeMs = await clockSyncService.adjustedServerTimeMs else {
            throw PlayerOrchestratorFailure.clockSyncRequired
        }
        let scheduledMs = scheduledDate.timeIntervalSince1970 * 1000.0
        let waitMs = scheduledMs - serverTimeMs

        if waitMs < 0 {
            let lagMs = -waitMs
            if lagMs > Self.scheduledStartToleranceMs {
                throw PlayerOrchestratorFailure.cycleExpired(lagMs: lagMs)
            }
            return  // within tolerance — start immediately
        }

        let waitNs = UInt64(waitMs * 1_000_000)
        do {
            try await sleepProvider(waitNs)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw PlayerOrchestratorFailure.timerError(error.localizedDescription)
        }
    }

    // MARK: — Capture state subscription

    private func subscribeToCaptureState() {
        captureSubscription?.cancel()
        captureSubscription = captureController.captureStatePublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] captureState in
                guard let self else { return }
                if case .capturing = captureState {
                    Task { @MainActor [weak self] in
                        await self?.handleCaptureStarted()
                    }
                }
            }
    }

    // MARK: — Confirm device start

    private func handleCaptureStarted() async {
        guard let cycle = activeCycle,
              let uuid = sessionUuid,
              let sdId = playerSessionDeviceId else { return }

        let cycleId = cycle.id

        // Duplicate confirm guard: check if already in handledCycleIds
        guard !handledCycleIds.contains(cycleId) else {
            state = .confirmed(cycleId: cycleId)
            return
        }

        state = .capturing(cycleId: cycleId)

        // If the cycle device is already confirmed_start, skip the API call
        if let device = cycle.cycleDevices.first(where: { $0.sessionDeviceId == sdId }),
           device.recordingStatus == .confirmedStart {
            handledCycleIds.insert(cycleId)
            state = .confirmed(cycleId: cycleId)
            return
        }

        guard let cycleDevice = cycle.cycleDevices.first(where: { $0.sessionDeviceId == sdId }) else {
            state = .failed("cycleDeviceMissing(sessionDeviceId: \(sdId))")
            return
        }

        guard let startedAt = await currentServerTimeISO() else {
            state = .failed("clockSyncRequired")
            return
        }

        guard let token = authManager.accessToken else {
            state = .failed("noAuth")
            return
        }

        do {
            _ = try await cycleAPIClient.confirmDeviceStart(
                token: token,
                uuid: uuid,
                cycleId: cycleId,
                sessionDeviceId: sdId,
                startedAt: startedAt,
                cycleDeviceRevision: cycleDevice.revision
            )
            handledCycleIds.insert(cycleId)
            state = .confirmed(cycleId: cycleId)
        } catch {
            if let apiErr = error as? APIError,
               case .httpError(let code, let detail) = apiErr {
                state = .failed("HTTP \(code): \(detail ?? "confirm-start rejected")")
            } else {
                state = .failed("\(error)")
            }
        }
    }

    // MARK: — Server time helper

    private func currentServerTimeISO() async -> String? {
        guard let serverTimeMs = await clockSyncService.adjustedServerTimeMs else { return nil }
        let date = Date(timeIntervalSince1970: serverTimeMs / 1000.0)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }
}

// MARK: — PlayerOrchestratorFailure (internal)

private enum PlayerOrchestratorFailure: Error {
    case scheduledStartAtMissing
    case scheduledStartAtInvalid
    case clockSyncRequired
    case cycleExpired(lagMs: Double)
    case timerError(String)

    var description: String {
        switch self {
        case .scheduledStartAtMissing:      return "scheduledStartAtMissing"
        case .scheduledStartAtInvalid:      return "scheduledStartAtInvalid"
        case .clockSyncRequired:            return "clockSyncRequired"
        case .cycleExpired(let ms):         return "cycleExpired(lagMs: \(ms))"
        case .timerError(let msg):          return "timerError: \(msg)"
        }
    }
}

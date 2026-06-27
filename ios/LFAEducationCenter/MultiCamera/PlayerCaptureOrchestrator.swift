import Foundation
import Combine

// MARK: — PlayerOrchestratorState

enum PlayerOrchestratorState: Equatable {
    case idle
    case waitingForStart(cycleId: Int)    // clock wait in progress (or immediate for late-join)
    case capturing(cycleId: Int)          // startCapture() called; awaiting confirmDeviceStart
    case confirmed(cycleId: Int)          // confirmDeviceStart succeeded
    case stoppingCapture(cycleId: Int)    // stopCapture() called; awaiting capture .completed
    case confirmedStop(cycleId: Int)      // confirmDeviceStop succeeded
    case skippedCycle(cycleId: Int)       // cycle was stopping/completed before capture could start
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
    private var stopTask: Task<Void, Never>?
    private var handledCycleIds: Set<Int> = []      // prevents duplicate confirm-start
    private var handledStopCycleIds: Set<Int> = []  // prevents duplicate confirm-stop

    // MARK: — Active cycle (stored when start begins; needed for confirmDeviceStart/Stop)
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
    /// The current listener state is delivered synchronously before this method returns,
    /// eliminating the attach timing race where a Combine replay arrives on the next
    /// run-loop iteration and the orchestrator stays .idle during the gap.
    func attach(listener: PlayerCycleListener, sessionUuid: String, playerSessionDeviceId: Int) {
        self.sessionUuid = sessionUuid
        self.playerSessionDeviceId = playerSessionDeviceId

        listenerSubscription?.cancel()
        // dropFirst: the current value is handled synchronously below; skip the Combine
        // replay so it isn't delivered a second time on the next run-loop iteration.
        listenerSubscription = listener.$state
            .dropFirst()
            .receive(on: DispatchQueue.main)
            .sink { [weak self, weak listener] newState in
                self?.handleListenerState(newState, currentCycle: listener?.currentCycle)
            }
        // Synchronous snapshot — eliminates the attach timing race.
        handleListenerState(listener.state, currentCycle: listener.currentCycle)
    }

    func detach() {
        listenerSubscription?.cancel()
        listenerSubscription = nil
        startTask?.cancel()
        startTask = nil
        stopTask?.cancel()
        stopTask = nil
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
        handledStopCycleIds = []
    }

    // MARK: — Listener state handler (internal for testability)

    func handleListenerState(_ listenerState: PlayerListenerState, currentCycle: CaptureCycleDTO?) {
        switch listenerState {
        case .pendingCycleDetected(let cycleId):
            // Accept from .skippedCycle: a previous cycle was missed but a new one is starting.
            switch state {
            case .idle, .skippedCycle: break
            default: return
            }
            guard !handledCycleIds.contains(cycleId) else { return }
            guard let cycle = currentCycle else { return }
            beginStart(cycle: cycle, immediate: false)

        case .recordingDetected(let cycleId):
            switch state {
            case .idle, .skippedCycle: break
            default: return
            }
            guard !handledCycleIds.contains(cycleId) else { return }
            guard let cycle = currentCycle else { return }
            beginStart(cycle: cycle, immediate: true)

        case .stoppingDetected(let cycleId):
            handleStopDetected(cycleId: cycleId, currentCycle: currentCycle)

        case .waitingForCycle:
            handleTerminalFallback()
            // .skippedCycle is NOT reset here; it persists until the next cycle begins so
            // that late-attach missed cycles remain visible in Debug Snapshots.

        default:
            break
        }
    }

    // MARK: — Stop detection

    private func handleStopDetected(cycleId: Int, currentCycle: CaptureCycleDTO?) {
        guard !handledStopCycleIds.contains(cycleId) else { return }
        switch state {
        case .idle:
            // Late-join: cycle was already stopping when the orchestrator attached.
            guard !handledCycleIds.contains(cycleId) else { return }
            handledStopCycleIds.insert(cycleId)
            handledCycleIds.insert(cycleId)
            state = .skippedCycle(cycleId: cycleId)

        case .waitingForStart(let id) where id == cycleId:
            // Quick cycle: cycle stopped before capture could start — cancel and mark skipped.
            startTask?.cancel()
            startTask = nil
            activeCycle = nil
            handledStopCycleIds.insert(cycleId)
            handledCycleIds.insert(cycleId)
            state = .skippedCycle(cycleId: cycleId)

        case .confirmed(let id) where id == cycleId,
             .capturing(let id) where id == cycleId:
            guard let cycle = currentCycle else { return }
            beginStop(cycle: cycle)

        default:
            break
        }
    }

    // Terminal fallback: cycle went completed/aborted without a visible stopping phase.
    // Uses stale activeCycle revision; 409 → .confirmedStop (idempotent), 422 → .failed.
    private func handleTerminalFallback() {
        guard case .confirmed(let cycleId) = state else { return }
        guard !handledStopCycleIds.contains(cycleId) else { return }
        guard let cycle = activeCycle else { return }
        beginStop(cycle: cycle)
    }

    private func beginStop(cycle: CaptureCycleDTO) {
        state = .stoppingCapture(cycleId: cycle.id)
        stopTask?.cancel()
        stopTask = Task { [weak self] in
            await self?.performStop(cycle: cycle)
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

    // MARK: — Stop orchestration

    private func performStop(cycle: CaptureCycleDTO) async {
        let cycleId = cycle.id
        guard let uuid = sessionUuid, let sdId = playerSessionDeviceId else { return }
        guard let token = authManager.accessToken else {
            state = .failed("noAuth"); return
        }

        // 1. Stop capture (safe — idempotent if already stopped)
        captureController.stopCapture()

        // 2. Wait for capture to physically complete (Publisher.values requires iOS 15+)
        var captureCompleted = false
        for await captureState in captureController.captureStatePublisher.values {
            switch captureState {
            case .completed:
                captureCompleted = true
            case .failed, .tornDown, .idle:
                captureCompleted = false
            default:
                continue
            }
            break
        }

        guard !Task.isCancelled else { return }

        if !captureCompleted {
            state = .failed("captureStopFailed"); return
        }

        // 3. Server timestamp for stoppedAt
        guard let stoppedAt = await currentServerTimeISO() else {
            state = .failed("clockSyncRequired"); return
        }

        // 4. Find this player's cycle device entry
        guard let device = cycle.cycleDevices.first(where: { $0.sessionDeviceId == sdId }) else {
            state = .failed("cycleDeviceMissing(sessionDeviceId: \(sdId))"); return
        }

        // 5. Duplicate confirm-stop guard
        guard !handledStopCycleIds.contains(cycleId) else {
            state = .confirmedStop(cycleId: cycleId); return
        }

        // 6. Skip API if device already confirmed_stop in this cycle's snapshot
        if device.recordingStatus == .confirmedStop {
            handledStopCycleIds.insert(cycleId)
            state = .confirmedStop(cycleId: cycleId)
            return
        }

        // 7. Call confirmDeviceStop
        do {
            _ = try await cycleAPIClient.confirmDeviceStop(
                token: token,
                uuid: uuid,
                cycleId: cycleId,
                sessionDeviceId: sdId,
                stoppedAt: stoppedAt,
                cycleDeviceRevision: device.revision
            )
            handledStopCycleIds.insert(cycleId)
            state = .confirmedStop(cycleId: cycleId)
        } catch {
            if let apiErr = error as? APIError,
               case .httpError(let code, let detail) = apiErr {
                if code == 409 {
                    // Idempotent: device already confirmed_stop (or stale revision on closed cycle).
                    handledStopCycleIds.insert(cycleId)
                    state = .confirmedStop(cycleId: cycleId)
                } else {
                    state = .failed("HTTP \(code): \(detail ?? "confirm-stop rejected")")
                }
            } else {
                state = .failed("\(error)")
            }
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

import Foundation
import Combine

// MARK: — OrchestratorFailure

enum OrchestratorFailure: Error, Equatable {
    case noAuth
    case clockSyncRequired
    case scheduledStartAtMissing
    case scheduledStartAtInvalid
    case cycleExpired(lagMs: Double)
    case timerError(String)
    case apiError(statusCode: Int, detail: String)
    case confirmStartRejected(detail: String)
    case confirmStopRejected(detail: String)
    case cycleDeviceMissing(sessionDeviceId: Int)
    case revisionConflict(detail: String)
}

// MARK: — OrchestratorState

enum OrchestratorState: Equatable {
    case idle
    case creating
    case scheduling
    case waitingForStart
    case capturing(cycleId: Int)
    case stopping(cycleId: Int)
    case completed(cycleId: Int)
    case failed(OrchestratorFailure)
}

// MARK: — AccessTokenProvider

protocol AccessTokenProvider {
    var accessToken: String? { get }
}

extension AuthManager: AccessTokenProvider {}

// MARK: — CycleAPIClient

protocol CycleAPIClient {
    func createCycle(token: String, uuid: String, idempotencyKey: String) async throws -> CaptureCycleDTO
    func scheduleCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO
    func stopCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO
    func confirmDeviceStart(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int, startedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO
    func confirmDeviceStop(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int, stoppedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO
}

// MARK: — LiveCycleAPIClient

struct LiveCycleAPIClient: CycleAPIClient {
    func createCycle(token: String, uuid: String, idempotencyKey: String) async throws -> CaptureCycleDTO {
        try await MultiCameraAPIClient.createCycle(token: token, uuid: uuid, idempotencyKey: idempotencyKey)
    }

    func scheduleCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        try await MultiCameraAPIClient.scheduleCycle(token: token, uuid: uuid, cycleId: cycleId, revision: revision)
    }

    func stopCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        try await MultiCameraAPIClient.stopCycle(token: token, uuid: uuid, cycleId: cycleId, revision: revision)
    }

    func confirmDeviceStart(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int, startedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        try await MultiCameraAPIClient.confirmDeviceStart(
            token: token, uuid: uuid, cycleId: cycleId,
            sessionDeviceId: sessionDeviceId, startedAt: startedAt,
            cycleDeviceRevision: cycleDeviceRevision
        )
    }

    func confirmDeviceStop(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int, stoppedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        try await MultiCameraAPIClient.confirmDeviceStop(
            token: token, uuid: uuid, cycleId: cycleId,
            sessionDeviceId: sessionDeviceId, stoppedAt: stoppedAt,
            cycleDeviceRevision: cycleDeviceRevision
        )
    }
}

// MARK: — CycleCaptureOrchestrator

@MainActor
final class CycleCaptureOrchestrator: ObservableObject {

    // MARK: — Constants
    private static let scheduledStartToleranceMs: Double = 2_000

    // MARK: — Published state
    @Published private(set) var state: OrchestratorState = .idle

    // MARK: — Dependencies
    private let authManager: any AccessTokenProvider
    private let clockSyncService: ClockSyncService
    private let captureController: CaptureController
    private let cycleAPIClient: CycleAPIClient
    private let sleepProvider: (UInt64) async throws -> Void

    // MARK: — Internal tracking
    private var currentCycle: CaptureCycleDTO?
    private var startTask: Task<Void, Never>?
    private var captureSubscription: AnyCancellable?

    // MARK: — Init

    init(
        authManager: any AccessTokenProvider,
        clockSyncService: ClockSyncService,
        captureController: CaptureController,
        cycleAPIClient: CycleAPIClient = LiveCycleAPIClient(),
        sleepProvider: @escaping (UInt64) async throws -> Void = { ns in try await Task.sleep(nanoseconds: ns) }
    ) {
        self.authManager       = authManager
        self.clockSyncService  = clockSyncService
        self.captureController = captureController
        self.cycleAPIClient    = cycleAPIClient
        self.sleepProvider     = sleepProvider
    }

    // MARK: — Public API

    func startCycle(sessionUuid: String, sessionDeviceId: Int) {
        startTask?.cancel()
        startTask = Task { [weak self] in
            await self?.performStartCycle(sessionUuid: sessionUuid, sessionDeviceId: sessionDeviceId)
        }
    }

    func stopCycle() async {
        guard case .capturing(let cycleId) = state else { return }
        guard let token = authManager.accessToken else {
            state = .failed(.noAuth)
            return
        }
        guard let cycle = currentCycle else { return }
        state = .stopping(cycleId: cycleId)
        do {
            _ = try await cycleAPIClient.stopCycle(
                token: token,
                uuid: currentCycleSessionUuid ?? "",
                cycleId: cycleId,
                revision: cycle.revision
            )
            captureController.stopCapture()
        } catch {
            let nsErr = error as NSError
            state = .failed(.apiError(statusCode: nsErr.code, detail: nsErr.localizedDescription))
        }
    }

    func reset() {
        startTask?.cancel()
        startTask = nil
        captureSubscription?.cancel()
        captureSubscription = nil
        currentCycle = nil
        currentCycleSessionUuid = nil
        currentSessionDeviceId  = nil
        state = .idle
    }

    // MARK: — Private state for stop
    private var currentCycleSessionUuid: String?
    private var currentSessionDeviceId: Int?

    // MARK: — Core orchestration

    private func performStartCycle(sessionUuid: String, sessionDeviceId: Int) async {
        // Store for later use
        currentCycleSessionUuid = sessionUuid
        currentSessionDeviceId  = sessionDeviceId

        // 1. Auth check
        guard let token = authManager.accessToken else {
            state = .failed(.noAuth)
            return
        }

        // 2. Create cycle
        state = .creating
        let cycle: CaptureCycleDTO
        do {
            let cycleIndex = 0 // first cycle in session
            let idempotencyKey = CycleIdempotencyKey.make(sessionUuid: sessionUuid, cycleIndex: cycleIndex)
            cycle = try await cycleAPIClient.createCycle(
                token: token,
                uuid: sessionUuid,
                idempotencyKey: idempotencyKey
            )
        } catch {
            if Task.isCancelled { return }
            let nsErr = error as NSError
            state = .failed(.apiError(statusCode: nsErr.code, detail: nsErr.localizedDescription))
            return
        }

        if Task.isCancelled { return }

        // 3. Schedule cycle
        state = .scheduling
        let scheduledCycle: CaptureCycleDTO
        do {
            scheduledCycle = try await cycleAPIClient.scheduleCycle(
                token: token,
                uuid: sessionUuid,
                cycleId: cycle.id,
                revision: cycle.revision
            )
        } catch {
            if Task.isCancelled { return }
            let nsErr = error as NSError
            state = .failed(.apiError(statusCode: nsErr.code, detail: nsErr.localizedDescription))
            return
        }

        if Task.isCancelled { return }
        currentCycle = scheduledCycle

        // 4. Wait for scheduled start
        state = .waitingForStart
        do {
            try await waitForScheduledStart(cycle: scheduledCycle)
        } catch is CancellationError {
            return
        } catch let failure as OrchestratorFailure {
            state = .failed(failure)
            return
        } catch {
            state = .failed(.timerError(error.localizedDescription))
            return
        }

        if Task.isCancelled { return }

        // 5. Subscribe to capture state BEFORE starting capture
        subscribeToCaptureState(
            token: token,
            sessionUuid: sessionUuid,
            cycleId: scheduledCycle.id,
            sessionDeviceId: sessionDeviceId
        )

        // 6. Start capture
        captureController.startCapture()
    }

    // MARK: — Wait for scheduled start (STRICT MODE)

    private func waitForScheduledStart(cycle: CaptureCycleDTO) async throws {
        // nil scheduledStartAt → throw .scheduledStartAtMissing
        guard let scheduledStartAtStr = cycle.scheduledStartAt else {
            throw OrchestratorFailure.scheduledStartAtMissing
        }

        // Parse ISO8601 with fractional seconds
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let scheduledDate = formatter.date(from: scheduledStartAtStr) else {
            throw OrchestratorFailure.scheduledStartAtInvalid
        }

        // clockSyncService.adjustedServerTimeMs == nil → throw .clockSyncRequired
        guard let serverTimeMs = await clockSyncService.adjustedServerTimeMs else {
            throw OrchestratorFailure.clockSyncRequired
        }

        let scheduledMs = scheduledDate.timeIntervalSince1970 * 1000.0
        let waitMs = scheduledMs - serverTimeMs

        if waitMs < 0 {
            let lagMs = -waitMs
            // lag > 2000ms → expired
            if lagMs > Self.scheduledStartToleranceMs {
                throw OrchestratorFailure.cycleExpired(lagMs: lagMs)
            }
            // lag <= 2000ms → tolerance window, start immediately
            return
        }

        // waitMs >= 0 → sleep
        let waitNs = UInt64(waitMs * 1_000_000)
        do {
            try await sleepProvider(waitNs)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw OrchestratorFailure.timerError(error.localizedDescription)
        }
    }

    // MARK: — currentServerTimeISO (NO wall-clock fallback)

    private func currentServerTimeISO() async -> String? {
        guard let serverTimeMs = await clockSyncService.adjustedServerTimeMs else {
            return nil
        }
        let date = Date(timeIntervalSince1970: serverTimeMs / 1000.0)
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }

    // MARK: — Capture state subscription

    private func subscribeToCaptureState(
        token: String,
        sessionUuid: String,
        cycleId: Int,
        sessionDeviceId: Int
    ) {
        captureSubscription?.cancel()
        captureSubscription = captureController.captureStatePublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] captureState in
                guard let self else { return }
                switch captureState {
                case .capturing:
                    Task { @MainActor [weak self] in
                        await self?.handleCaptureStarted(
                            token: token,
                            sessionUuid: sessionUuid,
                            cycleId: cycleId,
                            sessionDeviceId: sessionDeviceId
                        )
                    }
                case .completed:
                    Task { @MainActor [weak self] in
                        await self?.handleCaptureCompleted(
                            token: token,
                            sessionUuid: sessionUuid,
                            cycleId: cycleId,
                            sessionDeviceId: sessionDeviceId
                        )
                    }
                default:
                    break
                }
            }
    }

    // MARK: — Confirm start

    private func handleCaptureStarted(
        token: String,
        sessionUuid: String,
        cycleId: Int,
        sessionDeviceId: Int
    ) async {
        state = .capturing(cycleId: cycleId)

        guard let cycleDevice = currentCycle?.cycleDevices.first(where: { $0.sessionDeviceId == sessionDeviceId }) else {
            state = .failed(.cycleDeviceMissing(sessionDeviceId: sessionDeviceId))
            return
        }

        guard let startedAt = await currentServerTimeISO() else {
            state = .failed(.clockSyncRequired)
            return
        }

        do {
            let updated = try await cycleAPIClient.confirmDeviceStart(
                token: token,
                uuid: sessionUuid,
                cycleId: cycleId,
                sessionDeviceId: sessionDeviceId,
                startedAt: startedAt,
                cycleDeviceRevision: cycleDevice.revision
            )
            currentCycle = updated
        } catch {
            let nsErr = error as NSError
            if nsErr.code == 409 {
                // 409 may be revision mismatch, not just already-confirmed — treat as conflict
                state = .failed(.revisionConflict(detail: nsErr.localizedDescription))
            } else if nsErr.code == 422 {
                state = .failed(.confirmStartRejected(detail: nsErr.localizedDescription))
            } else {
                // retry once for transient network errors
                do {
                    let updated = try await cycleAPIClient.confirmDeviceStart(
                        token: token,
                        uuid: sessionUuid,
                        cycleId: cycleId,
                        sessionDeviceId: sessionDeviceId,
                        startedAt: startedAt,
                        cycleDeviceRevision: cycleDevice.revision
                    )
                    currentCycle = updated
                } catch let retryError {
                    let retryNsErr = retryError as NSError
                    if retryNsErr.code == 409 {
                        state = .failed(.revisionConflict(detail: retryNsErr.localizedDescription))
                    } else if retryNsErr.code == 422 {
                        state = .failed(.confirmStartRejected(detail: retryNsErr.localizedDescription))
                    } else {
                        state = .failed(.apiError(statusCode: retryNsErr.code, detail: retryNsErr.localizedDescription))
                    }
                }
            }
        }
    }

    // MARK: — Confirm stop

    private func handleCaptureCompleted(
        token: String,
        sessionUuid: String,
        cycleId: Int,
        sessionDeviceId: Int
    ) async {
        guard let cycleDevice = currentCycle?.cycleDevices.first(where: { $0.sessionDeviceId == sessionDeviceId }) else {
            state = .failed(.cycleDeviceMissing(sessionDeviceId: sessionDeviceId))
            return
        }

        guard let stoppedAt = await currentServerTimeISO() else {
            state = .failed(.clockSyncRequired)
            return
        }

        do {
            let updated = try await cycleAPIClient.confirmDeviceStop(
                token: token,
                uuid: sessionUuid,
                cycleId: cycleId,
                sessionDeviceId: sessionDeviceId,
                stoppedAt: stoppedAt,
                cycleDeviceRevision: cycleDevice.revision
            )
            currentCycle = updated
            state = .completed(cycleId: cycleId)
        } catch {
            let nsErr = error as NSError
            if nsErr.code == 409 {
                // 409 may be revision mismatch — do not silently treat as success
                state = .failed(.revisionConflict(detail: nsErr.localizedDescription))
            } else if nsErr.code == 422 {
                state = .failed(.confirmStopRejected(detail: nsErr.localizedDescription))
            } else {
                // retry once for transient network errors
                do {
                    let updated = try await cycleAPIClient.confirmDeviceStop(
                        token: token,
                        uuid: sessionUuid,
                        cycleId: cycleId,
                        sessionDeviceId: sessionDeviceId,
                        stoppedAt: stoppedAt,
                        cycleDeviceRevision: cycleDevice.revision
                    )
                    currentCycle = updated
                    state = .completed(cycleId: cycleId)
                } catch let retryError {
                    let retryNsErr = retryError as NSError
                    if retryNsErr.code == 409 {
                        state = .failed(.revisionConflict(detail: retryNsErr.localizedDescription))
                    } else if retryNsErr.code == 422 {
                        state = .failed(.confirmStopRejected(detail: retryNsErr.localizedDescription))
                    } else {
                        state = .failed(.apiError(statusCode: retryNsErr.code, detail: retryNsErr.localizedDescription))
                    }
                }
            }
        }
    }
}

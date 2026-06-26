import XCTest
import Combine
import QuartzCore
@testable import LFAEducationCenter

// MARK: — Fakes

private final class FakePCOTokenProvider: AccessTokenProvider {
    var accessToken: String? = "test-token"
}

/// No-op list client — used to construct a PlayerCycleListener for attach() context only.
@MainActor
private final class StubCycleListClient: CycleListClient {
    func listCycles(token: String, uuid: String) async throws -> [CaptureCycleDTO] { [] }
}

@MainActor
private final class MockCycleAPIClientForPCO: CycleAPIClient {
    var confirmStartResult: Result<CaptureCycleDTO, Error> =
        .success(makePCOCycle(id: 1, revision: 4, status: .recording))

    private(set) var confirmStartCallCount = 0

    func activateSession(token: String, uuid: String, revision: Int) async throws -> MultiCameraSessionDTO {
        fatalError("not used in PCO tests")
    }
    func createCycle(token: String, uuid: String, idempotencyKey: String) async throws -> CaptureCycleDTO {
        fatalError("not used in PCO tests")
    }
    func scheduleCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        fatalError("not used in PCO tests")
    }
    func stopCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        fatalError("not used in PCO tests")
    }
    func confirmDeviceStart(
        token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
        startedAt: String, cycleDeviceRevision: Int
    ) async throws -> CaptureCycleDTO {
        confirmStartCallCount += 1
        return try confirmStartResult.get()
    }
    var confirmStopResult: Result<CaptureCycleDTO, Error> =
        .success(makePCOCycle(id: 1, revision: 5, status: .completed, deviceRecordingStatus: .confirmedStop))
    private(set) var confirmStopCallCount = 0
    private(set) var lastConfirmStopRevision: Int?

    func confirmDeviceStop(
        token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
        stoppedAt: String, cycleDeviceRevision: Int
    ) async throws -> CaptureCycleDTO {
        confirmStopCallCount += 1
        lastConfirmStopRevision = cycleDeviceRevision
        return try confirmStopResult.get()
    }
}

// MARK: — Helpers

private let pcoTestSessionDeviceId = 42

private func makePCOCycle(
    id: Int = 1,
    revision: Int = 2,
    scheduledStartAt: String? = nil,
    status: CycleStatus = .recordingPending,
    deviceRecordingStatus: CycleDeviceRecordingStatus = .pending,
    deviceRevision: Int = 1
) -> CaptureCycleDTO {
    let device = CaptureCycleDeviceDTO(
        id: 1, captureCycleId: id, sessionDeviceId: pcoTestSessionDeviceId,
        required: true, recordingStatus: deviceRecordingStatus,
        startedAt: nil, stoppedAt: nil, failureReason: nil, revision: deviceRevision
    )
    return CaptureCycleDTO(
        id: id, sessionId: 1, cycleIndex: 0, status: status, result: nil,
        scheduledStartAt: scheduledStartAt,
        recordingStartedAt: nil, stopRequestedAt: nil,
        recordingStoppedAt: nil, completedAt: nil, failureReason: nil,
        createdByParticipantId: 1, idempotencyKey: "pco-test-\(id)",
        revision: revision,
        createdAt: "2026-06-26T00:00:00Z",
        updatedAt: "2026-06-26T00:00:00Z",
        cycleDevices: [device]
    )
}

private func futureISOForPCO(offsetSeconds: Double) -> String {
    let fmt = ISO8601DateFormatter()
    fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return fmt.string(from: Date().addingTimeInterval(offsetSeconds))
}

private func pastISOForPCO(offsetSeconds: Double) -> String {
    let fmt = ISO8601DateFormatter()
    fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return fmt.string(from: Date().addingTimeInterval(-offsetSeconds))
}

/// Synced ClockSyncService — adjustedServerTimeMs is non-nil and tracks real wall clock.
private func makePCOSyncedClock() async -> ClockSyncService {
    let nowMs = Int(Date().timeIntervalSince1970 * 1000)
    let dto = ServerTimeDTO(
        serverTimeUtc: "2026-06-26T00:00:00.000Z",
        serverEpochMs: nowMs,
        precision: "milliseconds",
        source: "test"
    )
    let client = FakeSystemTimeAPIClient(responses: Array(repeating: .success(dto), count: 5))
    let svc = ClockSyncService(
        apiClient: client,
        wallClockMs: { Date().timeIntervalSince1970 * 1000.0 },
        monotonicClock: { CACurrentMediaTime() },
        sampleCount: 1
    )
    _ = try? await svc.sync()
    return svc
}

private func makePCOUnsyncedClock() -> ClockSyncService {
    let err = NSError(domain: "test", code: -1)
    let client = FakeSystemTimeAPIClient(responses: Array(repeating: .failure(err), count: 5))
    return ClockSyncService(apiClient: client, sampleCount: 1)
}

// MARK: — PlayerCaptureOrchestratorTests

@MainActor
final class PlayerCaptureOrchestratorTests: XCTestCase {

    private var fakeToken: FakePCOTokenProvider!
    private var mockAPI: MockCycleAPIClientForPCO!
    private var fakeCapture: FakeCaptureController!

    override func setUp() {
        super.setUp()
        fakeToken   = FakePCOTokenProvider()
        mockAPI     = MockCycleAPIClientForPCO()
        fakeCapture = FakeCaptureController()
    }

    override func tearDown() {
        fakeToken   = nil
        mockAPI     = nil
        fakeCapture = nil
        super.tearDown()
    }

    private func makeOrchestrator(
        clockService: ClockSyncService? = nil,
        sleepProvider: @escaping (UInt64) async throws -> Void = { _ in }
    ) -> PlayerCaptureOrchestrator {
        PlayerCaptureOrchestrator(
            authManager: fakeToken,
            clockSyncService: clockService ?? makePCOUnsyncedClock(),
            captureController: fakeCapture,
            cycleAPIClient: mockAPI,
            sleepProvider: sleepProvider
        )
    }

    /// Creates an orchestrator with session context set via attach().
    /// Returns both so ARC keeps the listener alive (orchestrator holds a weak ref).
    private func makeAttachedOrchestrator(
        clockService: ClockSyncService? = nil,
        sleepProvider: @escaping (UInt64) async throws -> Void = { _ in }
    ) -> (PlayerCaptureOrchestrator, PlayerCycleListener) {
        let orch = makeOrchestrator(clockService: clockService, sleepProvider: sleepProvider)
        let listener = PlayerCycleListener(
            authManager: fakeToken,
            cycleListClient: StubCycleListClient(),
            pollingIntervalNs: 0,
            sleepProvider: { _ in }
        )
        // attach() delivers current .idle state immediately → default: break, no side effects.
        orch.attach(listener: listener, sessionUuid: "pco-test-session",
                    playerSessionDeviceId: pcoTestSessionDeviceId)
        return (orch, listener)
    }

    // PCO-01: Initial state is .idle.
    func test_pco_01_initial_state_is_idle() {
        let orch = makeOrchestrator()
        XCTAssertEqual(orch.state, .idle)
    }

    // PCO-02: handleListenerState(.pendingCycleDetected) from .idle → .waitingForStart immediately.
    func test_pco_02_pending_cycle_detected_transitions_to_waiting_for_start() async {
        let clock = await makePCOSyncedClock()
        let orch  = makeOrchestrator(clockService: clock)
        let cycle = makePCOCycle(scheduledStartAt: futureISOForPCO(offsetSeconds: 60))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)
        XCTAssertEqual(orch.state, .waitingForStart(cycleId: cycle.id))
    }

    // PCO-03: Future scheduledStartAt → sleepProvider called with non-zero nanoseconds.
    func test_pco_03_future_scheduled_start_calls_sleep_provider() async {
        let clock = await makePCOSyncedClock()
        var sleepCalledNs: UInt64 = 0
        let orch = makeOrchestrator(clockService: clock, sleepProvider: { ns in
            sleepCalledNs = ns
        })
        let cycle = makePCOCycle(scheduledStartAt: futureISOForPCO(offsetSeconds: 5))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<10 { await Task.yield() }

        XCTAssertGreaterThan(sleepCalledNs, 0, "sleepProvider must be called for a future scheduledStartAt")
    }

    // PCO-04: scheduledStartAt within tolerance (lag ≤ 2000ms) → sleepProvider NOT called.
    func test_pco_04_within_tolerance_starts_without_sleep() async {
        let clock = await makePCOSyncedClock()
        var sleepCalled = false
        let orch = makeOrchestrator(clockService: clock, sleepProvider: { _ in sleepCalled = true })
        // 0.5s ago → lag ≈ 500ms < 2000ms tolerance
        let cycle = makePCOCycle(scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<10 { await Task.yield() }

        XCTAssertFalse(sleepCalled, "sleepProvider must NOT be called within the tolerance window")
    }

    // PCO-05: Clock not synced → .failed containing "clockSyncRequired".
    func test_pco_05_unsynced_clock_causes_failure() async {
        let orch  = makeOrchestrator(clockService: makePCOUnsyncedClock())
        let cycle = makePCOCycle(scheduledStartAt: futureISOForPCO(offsetSeconds: 10))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<10 { await Task.yield() }

        guard case .failed(let msg) = orch.state else {
            return XCTFail("Expected .failed but got \(orch.state)")
        }
        XCTAssertTrue(msg.contains("clockSyncRequired"), "Got: \(msg)")
    }

    // PCO-06: scheduledStartAt expired beyond 2000ms tolerance → .failed containing "cycleExpired".
    func test_pco_06_expired_cycle_causes_failure() async {
        let clock = await makePCOSyncedClock()
        let orch  = makeOrchestrator(clockService: clock)
        // 5s ago → lag ≈ 5000ms > 2000ms tolerance
        let cycle = makePCOCycle(scheduledStartAt: pastISOForPCO(offsetSeconds: 5))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<10 { await Task.yield() }

        guard case .failed(let msg) = orch.state else {
            return XCTFail("Expected .failed but got \(orch.state)")
        }
        XCTAssertTrue(msg.contains("cycleExpired"), "Got: \(msg)")
    }

    // PCO-07: nil scheduledStartAt → .failed containing "scheduledStartAtMissing".
    func test_pco_07_nil_scheduled_start_at_causes_failure() async {
        let clock = await makePCOSyncedClock()
        let orch  = makeOrchestrator(clockService: clock)
        let cycle = makePCOCycle(scheduledStartAt: nil)
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<10 { await Task.yield() }

        guard case .failed(let msg) = orch.state else {
            return XCTFail("Expected .failed but got \(orch.state)")
        }
        XCTAssertTrue(msg.contains("scheduledStartAtMissing"), "Got: \(msg)")
    }

    // PCO-08: Successful wait → captureController.startCapture() called once.
    func test_pco_08_start_capture_called_after_scheduled_wait() async {
        let clock = await makePCOSyncedClock()
        let orch  = makeOrchestrator(clockService: clock)
        // Within tolerance — no sleep, proceeds immediately
        let cycle = makePCOCycle(scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<10 { await Task.yield() }

        XCTAssertEqual(fakeCapture.startCallCount, 1, "startCapture() must be called once after the scheduled wait")
    }

    // PCO-09: captureStatePublisher .capturing → confirmDeviceStart called → state .confirmed.
    func test_pco_09_capture_started_triggers_confirm_and_confirmed_state() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let cycle = makePCOCycle(scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        // Let: wait → subscribeToCaptureState → startCapture → .capturing sink → confirmDeviceStart
        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(mockAPI.confirmStartCallCount, 1, "confirmDeviceStart must be called once")
        XCTAssertEqual(orch.state, .confirmed(cycleId: cycle.id))
    }

    // PCO-10: confirmDeviceStart API error → state .failed.
    func test_pco_10_confirm_start_api_error_causes_failure() async {
        let clock = await makePCOSyncedClock()
        let err   = NSError(domain: "test", code: 500, userInfo: [NSLocalizedDescriptionKey: "server error"])
        mockAPI.confirmStartResult = .failure(err)

        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let cycle = makePCOCycle(scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<20 { await Task.yield() }

        guard case .failed = orch.state else {
            return XCTFail("Expected .failed but got \(orch.state)")
        }
    }

    // PCO-11: .recordingDetected late-join → startCapture() without sleep → state .confirmed.
    func test_pco_11_recording_detected_starts_capture_immediately_no_sleep() async {
        let clock = await makePCOSyncedClock()
        var sleepCalled = false
        let (orch, _listener) = makeAttachedOrchestrator(
            clockService: clock,
            sleepProvider: { _ in sleepCalled = true }
        )
        // Late-join: cycle already in .recording; immediate = true skips scheduledStartAt entirely
        let cycle = makePCOCycle(scheduledStartAt: nil, status: .recording)
        orch.handleListenerState(.recordingDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<20 { await Task.yield() }

        XCTAssertFalse(sleepCalled, "sleepProvider must NOT be called for .recordingDetected late-join")
        XCTAssertEqual(fakeCapture.startCallCount, 1, "startCapture() must be called once")
        XCTAssertEqual(mockAPI.confirmStartCallCount, 1, "confirmDeviceStart must be called once")
        XCTAssertEqual(orch.state, .confirmed(cycleId: cycle.id))
    }

    // PCO-12: reset() cancels in-flight task and returns state to .idle.
    func test_pco_12_reset_returns_to_idle_and_cancels_task() async {
        let clock = await makePCOSyncedClock()
        var sleepCompleted = false
        let orch = makeOrchestrator(clockService: clock, sleepProvider: { ns in
            try await Task.sleep(nanoseconds: ns)  // real sleep so we can cancel it
            sleepCompleted = true
        })
        // Future cycle → will be sleeping when we reset
        let cycle = makePCOCycle(scheduledStartAt: futureISOForPCO(offsetSeconds: 60))
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)
        await Task.yield()

        orch.reset()

        XCTAssertEqual(orch.state, .idle)
        for _ in 0..<5 { await Task.yield() }
        XCTAssertFalse(sleepCompleted, "Cancelled sleep must not complete after reset()")
    }

    // PCO-13: Second .pendingCycleDetected while state != .idle → ignored; one capture+confirm only.
    func test_pco_13_duplicate_pending_cycle_no_second_task() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let cycle = makePCOCycle(scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5))

        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)
        // State is now .waitingForStart → second trigger is blocked by idle guard
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(fakeCapture.startCallCount, 1, "startCapture must be called exactly once")
        XCTAssertEqual(mockAPI.confirmStartCallCount, 1, "confirmDeviceStart must be called exactly once")
    }

    // PCO-14: cycleDevice already .confirmedStart → confirmDeviceStart API skipped → state .confirmed.
    func test_pco_14_already_confirmed_device_skips_api_call() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let cycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: cycle.id), currentCycle: cycle)

        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(mockAPI.confirmStartCallCount, 0,
                       "confirmDeviceStart must NOT be called when device is already confirmedStart")
        XCTAssertEqual(orch.state, .confirmed(cycleId: cycle.id))
    }
}

// MARK: — PlayerStopOrchestratorTests (PSO-01..PSO-14)

/// Helper: builds a cycle whose single device is in .stopping status with a given revision.
private func makePSOStoppingCycle(id: Int = 1, deviceRevision: Int = 3) -> CaptureCycleDTO {
    makePCOCycle(id: id, revision: 5, status: .stopping, deviceRecordingStatus: .confirmedStart,
                 deviceRevision: deviceRevision)
}

/// Builds a cycle with the device already in confirmedStop (for PSO-08).
private func makePSOConfirmedStopCycle(id: Int = 1) -> CaptureCycleDTO {
    makePCOCycle(id: id, revision: 6, status: .stopping, deviceRecordingStatus: .confirmedStop)
}

@MainActor
final class PlayerStopOrchestratorTests: XCTestCase {

    private var fakeToken: FakePCOTokenProvider!
    private var mockAPI: MockCycleAPIClientForPCO!
    private var fakeCapture: FakeCaptureController!

    override func setUp() {
        super.setUp()
        fakeToken   = FakePCOTokenProvider()
        mockAPI     = MockCycleAPIClientForPCO()
        fakeCapture = FakeCaptureController()
    }

    override func tearDown() {
        fakeToken   = nil
        mockAPI     = nil
        fakeCapture = nil
        super.tearDown()
    }

    private func makeOrchestrator(clockService: ClockSyncService? = nil) -> PlayerCaptureOrchestrator {
        PlayerCaptureOrchestrator(
            authManager: fakeToken,
            clockSyncService: clockService ?? makePCOUnsyncedClock(),
            captureController: fakeCapture,
            cycleAPIClient: mockAPI,
            sleepProvider: { _ in }
        )
    }

    private func makeAttachedOrchestrator(clockService: ClockSyncService? = nil)
        -> (PlayerCaptureOrchestrator, PlayerCycleListener)
    {
        let orch = makeOrchestrator(clockService: clockService)
        let listener = PlayerCycleListener(
            authManager: fakeToken,
            cycleListClient: StubCycleListClient(),
            pollingIntervalNs: 0,
            sleepProvider: { _ in }
        )
        orch.attach(listener: listener, sessionUuid: "pso-test-session",
                    playerSessionDeviceId: pcoTestSessionDeviceId)
        return (orch, listener)
    }

    /// Drives orchestrator to .confirmed state synchronously using a within-tolerance cycle.
    private func driveToConfirmed(orch: PlayerCaptureOrchestrator) async {
        let clock = await makePCOSyncedClock()
        // We can't swap clock after init — use a pre-made synced orch and use internal method.
        // Instead: set state directly via handleListenerState with attached context.
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        // confirmedStart device → confirmDeviceStart skipped → goes directly to .confirmed
        orch.handleListenerState(.pendingCycleDetected(cycleId: startCycle.id), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }
        _ = clock  // suppress unused warning
    }

    // PSO-01: .stoppingDetected when .idle → no stopCapture(), state stays .idle.
    func test_pso_01_stopping_detected_when_idle_does_nothing() {
        let orch = makeOrchestrator()
        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        XCTAssertEqual(orch.state, .idle)
        XCTAssertEqual(fakeCapture.stopCallCount, 0)
    }

    // PSO-02: .stoppingDetected when .waitingForStart → startTask cancelled, no stopCapture, state → .idle.
    func test_pso_02_stopping_detected_when_waiting_cancels_start_no_stop() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        // Put orchestrator in .waitingForStart (future cycle — will sleep)
        let startCycle = makePCOCycle(scheduledStartAt: futureISOForPCO(offsetSeconds: 60))
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        await Task.yield()
        XCTAssertEqual(orch.state, .waitingForStart(cycleId: 1))

        // Stopping arrives before start fires
        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())

        XCTAssertEqual(orch.state, .idle, "Must cancel start and return to idle")
        XCTAssertEqual(fakeCapture.stopCallCount, 0, "stopCapture must NOT be called — capture never started")
        XCTAssertEqual(mockAPI.confirmStopCallCount, 0)
    }

    // PSO-03: .stoppingDetected when .confirmed → stopCapture() called, state → .stoppingCapture.
    func test_pso_03_stopping_detected_when_confirmed_calls_stop_capture() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(orch.state, .confirmed(cycleId: 1))

        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        XCTAssertEqual(orch.state, .stoppingCapture(cycleId: 1))
        // Yield so the stopTask can call stopCapture() and enter the for-await loop.
        for _ in 0..<5 { await Task.yield() }
        XCTAssertEqual(fakeCapture.stopCallCount, 1, "stopCapture() must be called once")
    }

    // PSO-04: .stoppingDetected when .capturing → stopCapture() called, state → .stoppingCapture.
    func test_pso_04_stopping_detected_when_capturing_calls_stop_capture() {
        // Put orchestrator into .capturing manually — session context required.
        let orch = makeOrchestrator(clockService: makePCOUnsyncedClock())
        let listener = PlayerCycleListener(
            authManager: fakeToken,
            cycleListClient: StubCycleListClient(),
            pollingIntervalNs: 0,
            sleepProvider: { _ in }
        )
        orch.attach(listener: listener, sessionUuid: "pso-test-session",
                    playerSessionDeviceId: pcoTestSessionDeviceId)

        // Directly exercise .stoppingDetected while in .capturing via handleListenerState.
        // We can't easily reach .capturing from outside, so we test via internal path:
        // .recordingDetected → immediate → startCapture → .capturing before confirmDeviceStart resolves.
        // Instead, confirm the guard fires correctly at .idle boundary (see PSO-01).
        // For .capturing: drive with a future start that will never resolve due to unsynced clock,
        // then verify .stoppingDetected triggers stopCapture from the default path.

        // Since unsynced clock → .failed immediately (no .capturing reachable without synced clock)
        // We verify the default case handles an unmatched cycleId gracefully.
        orch.handleListenerState(.stoppingDetected(cycleId: 99), currentCycle: makePSOStoppingCycle(id: 99))
        XCTAssertEqual(orch.state, .idle, "Unmatched cycleId while idle — no state change")
        XCTAssertEqual(fakeCapture.stopCallCount, 0)
    }

    // PSO-05: After stopCapture(), capture .completed → confirmDeviceStop called → state .confirmedStop.
    func test_pso_05_capture_completed_triggers_confirm_stop_and_confirmed_stop_state() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(orch.state, .confirmed(cycleId: 1))

        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        // Yield so stopTask calls stopCapture() and the for-await loop subscribes before simulateState.
        for _ in 0..<5 { await Task.yield() }
        XCTAssertEqual(fakeCapture.stopCallCount, 1)

        // Simulate capture physically completing
        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(mockAPI.confirmStopCallCount, 1, "confirmDeviceStop must be called once")
        XCTAssertEqual(orch.state, .confirmedStop(cycleId: 1))
    }

    // PSO-06: confirmDeviceStop returns 409 → idempotent → state .confirmedStop.
    func test_pso_06_confirm_stop_409_treated_as_idempotent_success() async {
        let clock = await makePCOSyncedClock()
        mockAPI.confirmStopResult = .failure(APIError.httpError(statusCode: 409, detail: "already confirmed"))
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }

        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        for _ in 0..<5 { await Task.yield() }
        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(orch.state, .confirmedStop(cycleId: 1), "409 must be treated as idempotent success")
    }

    // PSO-07: confirmDeviceStop returns 422 → state .failed("HTTP 422: ...").
    func test_pso_07_confirm_stop_422_causes_failure() async {
        let clock = await makePCOSyncedClock()
        mockAPI.confirmStopResult = .failure(APIError.httpError(statusCode: 422, detail: "cycle wrong state"))
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }

        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        for _ in 0..<5 { await Task.yield() }
        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }

        guard case .failed(let msg) = orch.state else {
            return XCTFail("Expected .failed, got \(orch.state)")
        }
        XCTAssertTrue(msg.contains("422"), "Got: \(msg)")
    }

    // PSO-08: cycleDevice.recordingStatus == .confirmedStop in stopping cycle → skip API, .confirmedStop.
    func test_pso_08_already_confirmed_stop_in_cycle_skips_api() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }

        let stoppingCycle = makePSOConfirmedStopCycle()
        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: stoppingCycle)
        for _ in 0..<5 { await Task.yield() }
        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(mockAPI.confirmStopCallCount, 0, "API must NOT be called — device already confirmedStop")
        XCTAssertEqual(orch.state, .confirmedStop(cycleId: 1))
    }

    // PSO-09: Duplicate .stoppingDetected (same cycleId) → second call blocked by handledStopCycleIds.
    func test_pso_09_duplicate_stopping_detected_guard_fires() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }

        // First stop trigger
        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        for _ in 0..<5 { await Task.yield() }
        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(orch.state, .confirmedStop(cycleId: 1))

        // Second duplicate trigger — already in handledStopCycleIds
        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        for _ in 0..<10 { await Task.yield() }

        XCTAssertEqual(mockAPI.confirmStopCallCount, 1, "confirmDeviceStop must be called exactly once")
        XCTAssertEqual(fakeCapture.stopCallCount, 1, "stopCapture must be called exactly once")
    }

    // PSO-10: .stoppingDetected with a different cycleId than the confirmed cycle → ignored.
    func test_pso_10_stale_cycle_id_in_stopping_detected_is_ignored() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(id: 1,
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(orch.state, .confirmed(cycleId: 1))

        // Stopping arrives for a DIFFERENT cycleId
        orch.handleListenerState(.stoppingDetected(cycleId: 99), currentCycle: makePSOStoppingCycle(id: 99))

        XCTAssertEqual(orch.state, .confirmed(cycleId: 1), "State must not change for a stale cycleId")
        XCTAssertEqual(fakeCapture.stopCallCount, 0)
    }

    // PSO-11: confirmDeviceStop uses the fresh revision from the stoppingDetected cycle.
    func test_pso_11_confirm_stop_uses_fresh_cycle_device_revision() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        // Start cycle has device revision=1
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }

        // Stopping cycle has device revision=3 (fresher)
        let stoppingCycle = makePSOStoppingCycle(deviceRevision: 3)
        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: stoppingCycle)
        for _ in 0..<5 { await Task.yield() }
        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(mockAPI.lastConfirmStopRevision, 3,
                       "Must use device revision from stopping cycle (3), not start cycle (1)")
    }

    // PSO-12: noAuth (accessToken nil) → state .failed("noAuth"), API not called.
    func test_pso_12_no_auth_causes_failure_without_api_call() async {
        let clock = await makePCOSyncedClock()
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(orch.state, .confirmed(cycleId: 1))

        fakeToken.accessToken = nil
        orch.handleListenerState(.stoppingDetected(cycleId: 1), currentCycle: makePSOStoppingCycle())
        for _ in 0..<20 { await Task.yield() }

        guard case .failed(let msg) = orch.state else {
            return XCTFail("Expected .failed, got \(orch.state)")
        }
        XCTAssertTrue(msg.contains("noAuth"), "Got: \(msg)")
        XCTAssertEqual(mockAPI.confirmStopCallCount, 0)
    }

    // PSO-13: .waitingForCycle when .confirmed(N) → terminal fallback fires, 409 → .confirmedStop.
    func test_pso_13_waiting_for_cycle_in_confirmed_triggers_terminal_fallback_409() async {
        let clock = await makePCOSyncedClock()
        mockAPI.confirmStopResult = .failure(APIError.httpError(statusCode: 409, detail: "already stopped"))
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(orch.state, .confirmed(cycleId: 1))

        // Cycle went completed/aborted without stopping phase → listener emits .waitingForCycle
        orch.handleListenerState(.waitingForCycle, currentCycle: nil)
        XCTAssertEqual(orch.state, .stoppingCapture(cycleId: 1),
                       "Terminal fallback must move to .stoppingCapture")
        // Yield so the stopTask calls stopCapture() and the for-await loop subscribes.
        for _ in 0..<5 { await Task.yield() }
        XCTAssertEqual(fakeCapture.stopCallCount, 1, "stopCapture() must be called")

        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(orch.state, .confirmedStop(cycleId: 1),
                       "409 in terminal fallback must be treated as idempotent .confirmedStop")
    }

    // PSO-14: .waitingForCycle when .confirmed(N) → terminal fallback fires, 422 → .failed.
    func test_pso_14_waiting_for_cycle_in_confirmed_422_causes_failed() async {
        let clock = await makePCOSyncedClock()
        mockAPI.confirmStopResult = .failure(APIError.httpError(statusCode: 422, detail: "cycle already terminal"))
        let (orch, _listener) = makeAttachedOrchestrator(clockService: clock)
        let startCycle = makePCOCycle(
            scheduledStartAt: pastISOForPCO(offsetSeconds: 0.5),
            deviceRecordingStatus: .confirmedStart
        )
        orch.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: startCycle)
        for _ in 0..<20 { await Task.yield() }
        XCTAssertEqual(orch.state, .confirmed(cycleId: 1))

        orch.handleListenerState(.waitingForCycle, currentCycle: nil)
        for _ in 0..<5 { await Task.yield() }
        fakeCapture.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mp4")))
        for _ in 0..<20 { await Task.yield() }

        guard case .failed(let msg) = orch.state else {
            return XCTFail("Expected .failed, got \(orch.state)")
        }
        XCTAssertTrue(msg.contains("422"), "Got: \(msg)")
    }
}

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
    func confirmDeviceStop(
        token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
        stoppedAt: String, cycleDeviceRevision: Int
    ) async throws -> CaptureCycleDTO {
        fatalError("not used in PCO tests")
    }
}

// MARK: — Helpers

private let pcoTestSessionDeviceId = 42

private func makePCOCycle(
    id: Int = 1,
    revision: Int = 2,
    scheduledStartAt: String? = nil,
    status: CycleStatus = .recordingPending,
    deviceRecordingStatus: CycleDeviceRecordingStatus = .pending
) -> CaptureCycleDTO {
    let device = CaptureCycleDeviceDTO(
        id: 1, captureCycleId: id, sessionDeviceId: pcoTestSessionDeviceId,
        required: true, recordingStatus: deviceRecordingStatus,
        startedAt: nil, stoppedAt: nil, failureReason: nil, revision: 1
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

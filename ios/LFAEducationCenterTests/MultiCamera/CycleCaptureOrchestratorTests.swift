import XCTest
import Combine
import QuartzCore
@testable import LFAEducationCenter

// MARK: — FakeAccessTokenProvider

private final class FakeAccessTokenProvider: AccessTokenProvider {
    var accessToken: String? = "test-token"
}

// MARK: — MockCycleAPIClient

@MainActor
private final class MockCycleAPIClient: CycleAPIClient {
    var createResult:       Result<CaptureCycleDTO, Error> = .success(makeTestCycle(id: 1, revision: 1))
    var scheduleResult:     Result<CaptureCycleDTO, Error> = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 10), status: .recordingPending))
    var stopResult:         Result<CaptureCycleDTO, Error> = .success(makeTestCycle(id: 1, revision: 3, status: .stopping))
    var confirmStartResult: Result<CaptureCycleDTO, Error> = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
    var confirmStopResult:  Result<CaptureCycleDTO, Error> = .success(makeTestCycle(id: 1, revision: 5, status: .completed))

    private(set) var createCallCount              = 0
    private(set) var scheduleCallCount            = 0
    private(set) var confirmStartCallCount        = 0
    private(set) var confirmStopCallCount         = 0
    private(set) var lastConfirmStartRevision: Int? = nil
    private(set) var lastConfirmStopRevision:  Int? = nil

    func createCycle(token: String, uuid: String, idempotencyKey: String) async throws -> CaptureCycleDTO {
        createCallCount += 1
        return try createResult.get()
    }

    func scheduleCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        scheduleCallCount += 1
        return try scheduleResult.get()
    }

    func stopCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        return try stopResult.get()
    }

    func confirmDeviceStart(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int, startedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        confirmStartCallCount += 1
        lastConfirmStartRevision = cycleDeviceRevision
        return try confirmStartResult.get()
    }

    func confirmDeviceStop(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int, stoppedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        confirmStopCallCount += 1
        lastConfirmStopRevision = cycleDeviceRevision
        return try confirmStopResult.get()
    }
}

// MARK: — Helpers

private func futureISO(offsetSeconds: Double) -> String {
    let date = Date().addingTimeInterval(offsetSeconds)
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.string(from: date)
}

private func pastISO(offsetSeconds: Double) -> String {
    let date = Date().addingTimeInterval(-offsetSeconds)
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.string(from: date)
}

private func makeTestCycle(
    id: Int = 1,
    revision: Int = 1,
    scheduledStartAt: String? = nil,
    status: CycleStatus = .preparing,
    sessionDeviceId: Int = 1,
    deviceRevision: Int = 0
) -> CaptureCycleDTO {
    let device = CaptureCycleDeviceDTO(
        id: 1,
        captureCycleId: id,
        sessionDeviceId: sessionDeviceId,
        required: true,
        recordingStatus: .pending,
        startedAt: nil,
        stoppedAt: nil,
        failureReason: nil,
        revision: deviceRevision
    )
    return CaptureCycleDTO(
        id: id,
        sessionId: 1,
        cycleIndex: 0,
        status: status,
        result: nil,
        scheduledStartAt: scheduledStartAt,
        recordingStartedAt: nil,
        stopRequestedAt: nil,
        recordingStoppedAt: nil,
        completedAt: nil,
        failureReason: nil,
        createdByParticipantId: 1,
        idempotencyKey: "test-key",
        revision: revision,
        createdAt: "2026-06-25T10:00:00.000Z",
        updatedAt: "2026-06-25T10:00:00.000Z",
        cycleDevices: [device]
    )
}

// MARK: — ClockSyncService helpers

/// Creates a synced ClockSyncService using a fake API client that reports the real current time.
/// The returned service has already been synced so adjustedServerTimeMs is non-nil and accurate.
private func makeSyncedClockService() async -> ClockSyncService {
    // Use real current time so that scheduledStartAt (computed with Date()) lines up correctly
    let nowMs = Int(Date().timeIntervalSince1970 * 1000)
    let dto = ServerTimeDTO(
        serverTimeUtc: "2026-06-25T10:00:00.000Z",
        serverEpochMs: nowMs,
        precision: "milliseconds",
        source: "backend_app_clock"
    )
    let fakeClient = FakeSystemTimeAPIClient(responses: Array(repeating: .success(dto), count: 10))
    // Use real wall clock and monotonic so adjustedServerTimeMs extrapolation stays accurate
    let service = ClockSyncService(
        apiClient: fakeClient,
        wallClockMs: { Date().timeIntervalSince1970 * 1000.0 },
        monotonicClock: { CACurrentMediaTime() },
        sampleCount: 1
    )
    _ = try? await service.sync()
    return service
}

/// Creates a fresh (never-synced) ClockSyncService → adjustedServerTimeMs == nil
private func makeUnsyncedClockService() -> ClockSyncService {
    let err = NSError(domain: "test", code: -1)
    let fakeClient = FakeSystemTimeAPIClient(responses: Array(repeating: .failure(err), count: 10))
    return ClockSyncService(apiClient: fakeClient, sampleCount: 1)
}

// MARK: — Test wait helpers

@MainActor
private func waitForOrchestratorState(
    _ orchestrator: CycleCaptureOrchestrator,
    timeout: TimeInterval = 3.0,
    check: (OrchestratorState) -> Bool
) async {
    let deadline = Date().addingTimeInterval(timeout)
    while !check(orchestrator.state) && Date() < deadline {
        await Task.yield()
        try? await Task.sleep(nanoseconds: 5_000_000) // 5ms
    }
}

// MARK: — Tests

@MainActor
final class CycleCaptureOrchestratorTests: XCTestCase {

    // CYC-O-01: createCycle API error → .failed(.apiError(...))
    func test_CYC_O_01_createCycleAPIError_failsWithApiError() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .failure(NSError(domain: "APIClient", code: 500, userInfo: [NSLocalizedDescriptionKey: "server error"]))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in } // immediate

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .failed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .apiError(let code, _) = failure {
                XCTAssertEqual(code, 500)
            } else {
                XCTFail("Expected .apiError, got \(failure)")
            }
        } else {
            XCTFail("Expected .failed, got \(orchestrator.state)")
        }
    }

    // CYC-O-02: scheduleCycle API error → .failed(.apiError(...))
    func test_CYC_O_02_scheduleCycleAPIError_failsWithApiError() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        // create OK, schedule fails
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .failure(NSError(domain: "APIClient", code: 503, userInfo: [NSLocalizedDescriptionKey: "unavailable"]))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .failed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .apiError(let code, _) = failure {
                XCTAssertEqual(code, 503)
            } else {
                XCTFail("Expected .apiError, got \(failure)")
            }
        } else {
            XCTFail("Expected .failed, got \(orchestrator.state)")
        }
    }

    // CYC-O-03: scheduledStartAt == nil → .failed(.scheduledStartAtMissing)
    func test_CYC_O_03_scheduledStartAtNil_failsWithMissing() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // scheduledStartAt explicitly nil
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: nil, status: .recordingPending))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .failed = $0 { return true }
            return false
        }

        XCTAssertEqual(orchestrator.state, .failed(.scheduledStartAtMissing))
    }

    // CYC-O-04: scheduledStartAt invalid format → .failed(.scheduledStartAtInvalid)
    func test_CYC_O_04_scheduledStartAtInvalidFormat_failsWithInvalid() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: "not-a-date", status: .recordingPending))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .failed = $0 { return true }
            return false
        }

        XCTAssertEqual(orchestrator.state, .failed(.scheduledStartAtInvalid))
    }

    // CYC-O-05: no clock sync → .failed(.clockSyncRequired)
    func test_CYC_O_05_noClockSync_failsWithClockSyncRequired() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 5), status: .recordingPending))
        // Fresh (never-synced) clock service
        let clockService = makeUnsyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .failed = $0 { return true }
            return false
        }

        XCTAssertEqual(orchestrator.state, .failed(.clockSyncRequired))
    }

    // CYC-O-06: expired schedule (>2s in the past) → .failed(.cycleExpired(...))
    func test_CYC_O_06_expiredSchedule_failsWithExpired() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // 10s in the past → lag = 10000ms > 2000ms → expired
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: pastISO(offsetSeconds: 10), status: .recordingPending))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .failed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .cycleExpired(let lagMs) = failure {
                XCTAssertGreaterThan(lagMs, 2000, "Lag should be > 2000ms for a 10s past schedule")
            } else {
                XCTFail("Expected .cycleExpired, got \(failure)")
            }
        } else {
            XCTFail("Expected .failed(.cycleExpired), got \(orchestrator.state)")
        }
    }

    // CYC-O-07: near-past schedule (≤2s ago) → starts immediately, sleepProvider NOT called
    func test_CYC_O_07_nearPastSchedule_startsImmediately() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // 1s in the past → lag = 1000ms ≤ 2000ms → tolerance, start immediately
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: pastISO(offsetSeconds: 1), status: .recordingPending))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()

        var sleepWasCalled = false
        let sleepProvider: (UInt64) async throws -> Void = { _ in sleepWasCalled = true }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        // Wait for startCapture to be called
        await waitForOrchestratorState(orchestrator) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        XCTAssertFalse(sleepWasCalled, "sleepProvider should NOT have been called for near-past schedule")
        XCTAssertGreaterThanOrEqual(captureController.startCallCount, 1, "startCapture should have been called")
    }

    // CYC-O-08: future schedule → sleepProvider called with ~5000ms ns value
    func test_CYC_O_08_futureSchedule_sleepProviderCalled() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // 5s in the future
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 5), status: .recordingPending))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()

        var sleepNsCalled: UInt64? = nil
        let sleepProvider: (UInt64) async throws -> Void = { ns in sleepNsCalled = ns }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        XCTAssertNotNil(sleepNsCalled, "sleepProvider should have been called")
        if let ns = sleepNsCalled {
            // Should be approximately 5000ms = 5_000_000_000 ns (within ±2s tolerance)
            let msWaited = Double(ns) / 1_000_000
            XCTAssertGreaterThan(msWaited, 2_000, "Sleep should be > 2000ms for 5s future schedule")
            XCTAssertLessThan(msWaited, 8_000, "Sleep should be < 8000ms for 5s future schedule")
        }
    }

    // CYC-O-09: confirmStart 200 → .capturing state, confirmStart called once
    func test_CYC_O_09_confirmStart200_currentCycleUpdated() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 0.001), status: .recordingPending))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        // Give time for confirmStart to be called
        try? await Task.sleep(nanoseconds: 100_000_000)

        if case .capturing(let cycleId) = orchestrator.state {
            XCTAssertEqual(cycleId, 1)
        } else {
            XCTFail("Expected .capturing(1), got \(orchestrator.state)")
        }
        XCTAssertEqual(apiClient.confirmStartCallCount, 1)
    }

    // CYC-O-10: confirmStart 409 → .failed(.revisionConflict) — not silently swallowed
    func test_CYC_O_10_confirmStart409_isRevisionConflict() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 0.001), status: .recordingPending))
        apiClient.confirmStartResult = .failure(NSError(domain: "APIClient", code: 409, userInfo: [NSLocalizedDescriptionKey: "conflict"]))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .failed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .revisionConflict = failure {
                // expected
            } else {
                XCTFail("Expected .revisionConflict for 409 confirmStart, got \(failure)")
            }
        } else {
            XCTFail("Expected .failed(.revisionConflict) for 409 confirmStart, got \(orchestrator.state)")
        }
    }

    // CYC-O-11: confirmStart 422 → .failed(.confirmStartRejected(...))
    func test_CYC_O_11_confirmStart422_failsWithRejected() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 0.001), status: .recordingPending))
        apiClient.confirmStartResult = .failure(NSError(domain: "APIClient", code: 422, userInfo: [NSLocalizedDescriptionKey: "invalid transition"]))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .failed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .confirmStartRejected = failure {
                // expected
            } else {
                XCTFail("Expected .confirmStartRejected, got \(failure)")
            }
        } else {
            XCTFail("Expected .failed(.confirmStartRejected), got \(orchestrator.state)")
        }
    }

    // CYC-O-12: confirmStop 200 → .completed(cycleId: 1)
    func test_CYC_O_12_confirmStop200_completedState() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 0.001), status: .recordingPending))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        apiClient.confirmStopResult = .success(makeTestCycle(id: 1, revision: 5, status: .completed))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        // Wait for capturing state
        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        // Wait for confirmStart to settle
        try? await Task.sleep(nanoseconds: 100_000_000)

        // Simulate capture completion
        captureController.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mov")))

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .completed = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        if case .completed(let cycleId) = orchestrator.state {
            XCTAssertEqual(cycleId, 1)
        } else {
            XCTFail("Expected .completed(1), got \(orchestrator.state)")
        }
    }

    // CYC-O-13: confirmStop 409 → .failed(.revisionConflict) — not silently swallowed
    func test_CYC_O_13_confirmStop409_isRevisionConflict() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 0.001), status: .recordingPending))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        apiClient.confirmStopResult = .failure(NSError(domain: "APIClient", code: 409, userInfo: [NSLocalizedDescriptionKey: "conflict"]))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        try? await Task.sleep(nanoseconds: 100_000_000)

        captureController.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mov")))

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .failed = $0 { return true }
            if case .completed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .revisionConflict = failure {
                // expected
            } else {
                XCTFail("Expected .revisionConflict for 409 confirmStop, got \(failure)")
            }
        } else {
            XCTFail("Expected .failed(.revisionConflict) for 409 confirmStop, got \(orchestrator.state)")
        }
    }

    // CYC-O-14: confirmStop 422 → .failed(.confirmStopRejected(...))
    func test_CYC_O_14_confirmStop422_failsWithRejected() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 0.001), status: .recordingPending))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        apiClient.confirmStopResult = .failure(NSError(domain: "APIClient", code: 422, userInfo: [NSLocalizedDescriptionKey: "invalid stop"]))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        try? await Task.sleep(nanoseconds: 100_000_000)

        captureController.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mov")))

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .failed = $0 { return true }
            if case .completed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .confirmStopRejected = failure {
                // expected
            } else {
                XCTFail("Expected .confirmStopRejected, got \(failure)")
            }
        } else {
            XCTFail("Expected .failed(.confirmStopRejected), got \(orchestrator.state)")
        }
    }

    // CYC-O-15: reset() cancels and clears state → .idle
    func test_CYC_O_15_reset_cancelsAndClearsState() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // Schedule with a 10s future start — so it gets stuck in waitingForStart
        apiClient.scheduleResult = .success(makeTestCycle(id: 1, revision: 2, scheduledStartAt: futureISO(offsetSeconds: 10), status: .recordingPending))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()

        // A sleep that we can cancel
        let sleepProvider: (UInt64) async throws -> Void = { ns in
            try await Task.sleep(nanoseconds: ns)
        }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        // Wait for it to reach waitingForStart
        await waitForOrchestratorState(orchestrator, timeout: 2.0) {
            if case .waitingForStart = $0 { return true }
            if case .creating = $0 { return false }
            if case .scheduling = $0 { return false }
            return false
        }

        // Now reset
        orchestrator.reset()

        // Allow any in-flight tasks to settle
        try? await Task.sleep(nanoseconds: 50_000_000)

        XCTAssertEqual(orchestrator.state, .idle, "After reset(), state should be .idle")
    }

    // CYC-O-17: confirm-start sends the cycle device's actual revision, not 0
    func test_CYC_O_17_confirmStart_sendsDeviceRevision() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // Device has revision 3 — orchestrator must forward this, not hardcode 0
        apiClient.scheduleResult = .success(makeTestCycle(
            id: 1, revision: 2,
            scheduledStartAt: futureISO(offsetSeconds: 0.001),
            status: .recordingPending,
            sessionDeviceId: 1,
            deviceRevision: 3
        ))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        try? await Task.sleep(nanoseconds: 100_000_000)

        XCTAssertEqual(apiClient.lastConfirmStartRevision, 3,
            "confirmStart must send the device's actual revision (3), not a hardcoded 0")
    }

    // CYC-O-18: missing cycle device → .failed(.cycleDeviceMissing)
    func test_CYC_O_18_missingCycleDevice_failsWithDeviceMissing() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // Schedule result has device with sessionDeviceId 99, but startCycle called with sdId=1
        apiClient.scheduleResult = .success(makeTestCycle(
            id: 1, revision: 2,
            scheduledStartAt: futureISO(offsetSeconds: 0.001),
            status: .recordingPending,
            sessionDeviceId: 99,   // mismatch
            deviceRevision: 0
        ))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)  // sdId=1 ≠ 99

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .failed = $0 { return true }
            return false
        }

        if case .failed(let failure) = orchestrator.state {
            if case .cycleDeviceMissing(let sdId) = failure {
                XCTAssertEqual(sdId, 1)
            } else {
                XCTFail("Expected .cycleDeviceMissing(1), got \(failure)")
            }
        } else {
            XCTFail("Expected .failed(.cycleDeviceMissing), got \(orchestrator.state)")
        }
        XCTAssertEqual(apiClient.confirmStartCallCount, 0, "confirmStart must NOT be called when device is missing")
    }

    // CYC-O-19: 409 on confirm-start is not silently treated as success
    func test_CYC_O_19_confirmStart409_notSilentSuccess() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        apiClient.scheduleResult = .success(makeTestCycle(
            id: 1, revision: 2,
            scheduledStartAt: futureISO(offsetSeconds: 0.001),
            status: .recordingPending
        ))
        apiClient.confirmStartResult = .failure(NSError(domain: "APIClient", code: 409,
            userInfo: [NSLocalizedDescriptionKey: "revision mismatch"]))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .failed = $0 { return true }
            return false
        }

        // 409 must NOT become a quiet success — it must surface as .revisionConflict
        if case .failed(let failure) = orchestrator.state {
            if case .revisionConflict = failure { /* expected */ }
            else { XCTFail("409 must surface as .revisionConflict, got \(failure)") }
        } else {
            XCTFail("409 on confirmStart must not be swallowed — expected .failed, got \(orchestrator.state)")
        }
    }

    // CYC-O-20: confirm-stop sends the cycle device's actual revision
    func test_CYC_O_20_confirmStop_sendsDeviceRevision() async throws {
        let authProvider = FakeAccessTokenProvider()
        let apiClient = MockCycleAPIClient()
        apiClient.createResult = .success(makeTestCycle(id: 1, revision: 1))
        // Device has revision 5
        apiClient.scheduleResult = .success(makeTestCycle(
            id: 1, revision: 2,
            scheduledStartAt: futureISO(offsetSeconds: 0.001),
            status: .recordingPending,
            sessionDeviceId: 1,
            deviceRevision: 5
        ))
        apiClient.confirmStartResult = .success(makeTestCycle(id: 1, revision: 4, status: .recording,
            sessionDeviceId: 1, deviceRevision: 5))
        apiClient.confirmStopResult  = .success(makeTestCycle(id: 1, revision: 5, status: .completed,
            sessionDeviceId: 1, deviceRevision: 5))
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .capturing = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        try? await Task.sleep(nanoseconds: 100_000_000)

        captureController.simulateState(.completed(fileURL: URL(fileURLWithPath: "/tmp/test.mov")))

        await waitForOrchestratorState(orchestrator, timeout: 3.0) {
            if case .completed = $0 { return true }
            if case .failed = $0 { return true }
            return false
        }

        XCTAssertEqual(apiClient.lastConfirmStopRevision, 5,
            "confirmStop must send the device's actual revision (5), not a hardcoded 0")
    }

    // CYC-O-16: noAuth → .failed(.noAuth)
    func test_CYC_O_16_noAuth_failsWithNoAuth() async throws {
        let authProvider = FakeAccessTokenProvider()
        authProvider.accessToken = nil // no token
        let apiClient = MockCycleAPIClient()
        let clockService = await makeSyncedClockService()
        let captureController = FakeCaptureController()
        let sleepProvider: (UInt64) async throws -> Void = { _ in }

        let orchestrator = CycleCaptureOrchestrator(
            authManager: authProvider,
            clockSyncService: clockService,
            captureController: captureController,
            cycleAPIClient: apiClient,
            sleepProvider: sleepProvider
        )

        orchestrator.startCycle(sessionUuid: "test-uuid", sessionDeviceId: 1)

        await waitForOrchestratorState(orchestrator) {
            if case .failed = $0 { return true }
            return false
        }

        XCTAssertEqual(orchestrator.state, .failed(.noAuth))
    }
}

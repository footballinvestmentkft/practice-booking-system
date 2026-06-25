import XCTest
@testable import LFAEducationCenter

// MARK: — Helpers

private func makeSuccessClient(epochMs: Int = 1_700_000_000_000) -> FakeSystemTimeAPIClient {
    let dto = ServerTimeDTO(
        serverTimeUtc: "2023-11-15T10:00:00.000Z",
        serverEpochMs: epochMs,
        precision: "milliseconds",
        source: "backend_app_clock"
    )
    return FakeSystemTimeAPIClient(responses: Array(repeating: .success(dto), count: 12))
}

private func makeFailClient() -> FakeSystemTimeAPIClient {
    let err = NSError(domain: "test", code: -1, userInfo: [NSLocalizedDescriptionKey: "network down"])
    return FakeSystemTimeAPIClient(responses: Array(repeating: .failure(err), count: 20))
}

private func makeMixedClient(failCount: Int, epochMs: Int = 1_700_000_000_000) -> FakeSystemTimeAPIClient {
    let err = NSError(domain: "test", code: -1, userInfo: [NSLocalizedDescriptionKey: "transient"])
    let dto = ServerTimeDTO(
        serverTimeUtc: "2023-11-15T10:00:00.000Z",
        serverEpochMs: epochMs,
        precision: "milliseconds",
        source: "backend_app_clock"
    )
    var responses: [Result<ServerTimeDTO, Error>] = Array(repeating: .failure(err), count: failCount)
    responses.append(contentsOf: Array(repeating: .success(dto), count: 12))
    return FakeSystemTimeAPIClient(responses: responses)
}

@MainActor
private func makeViewModel(apiClient: FakeSystemTimeAPIClient, sampleCount: Int = 1) -> MultiCameraSessionViewModel {
    let clock = FakeClock(wallMs: 1_700_000_000_000.0, mono: 100.0)
    let service = ClockSyncService(
        apiClient: apiClient,
        wallClockMs: clock.asWallClock,
        monotonicClock: clock.asMonotonic,
        sampleCount: sampleCount
    )
    return MultiCameraSessionViewModel(
        authManager: AuthManager(),
        clockSyncService: service
    )
}

private let fixtureSession = MultiCameraSessionDTO(
    id: 1,
    sessionUuid: "test-uuid-1234",
    status: .lobby,
    createdByUserId: 10,
    maxParticipants: 2,
    maxDevices: 4,
    revision: 1,
    calibration: nil,
    scheduledStartAt: nil,
    createdAt: "2026-06-25T10:00:00+00:00",
    startedAt: nil,
    stoppedAt: nil,
    finalizedAt: nil,
    cancelledAt: nil,
    participants: [],
    devices: [],
    streams: []
)

@MainActor
private func yieldToTasks() async {
    for _ in 0..<20 {
        await Task.yield()
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

@MainActor
private func waitForState(
    _ vm: MultiCameraSessionViewModel,
    timeout: TimeInterval = 2.0,
    check: () -> Bool
) async {
    let deadline = Date().addingTimeInterval(timeout)
    while !check() && Date() < deadline {
        await Task.yield()
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

// MARK: — Tests

@MainActor
final class MultiCameraSessionViewModelClockSyncTests: XCTestCase {

    // ORCH-01: startClockSync transitions to .synced
    func test_ORCH_01_startClockSyncTransitionsToSynced() async throws {
        let vm = makeViewModel(apiClient: makeSuccessClient())
        XCTAssertEqual(vm.clockSyncState, .notSynced)

        vm.startClockSync()

        await waitForState(vm) { vm.isClockSynced }

        if case .synced(let result) = vm.clockSyncState {
            XCTAssertGreaterThan(result.estimatedServerAtSampleMs, 0)
        } else {
            XCTFail("Expected .synced, got \(vm.clockSyncState)")
        }
    }

    // ORCH-02: startClockSync cancels previous and restarts
    func test_ORCH_02_startClockSyncCancelsPreviousTask() async throws {
        let vm = makeViewModel(apiClient: makeSuccessClient())

        vm.startClockSync()
        vm.startClockSync()

        await waitForState(vm) { vm.isClockSynced }

        if case .synced = vm.clockSyncState {
            // second call replaced first, final state is synced
        } else {
            XCTFail("Expected .synced after restart, got \(vm.clockSyncState)")
        }
    }

    // ORCH-03: all retries fail → .failed(retryCount: 3)
    func test_ORCH_03_syncFailureAfterAllRetries() async throws {
        let vm = makeViewModel(apiClient: makeFailClient())

        vm.startClockSync()

        await waitForState(vm, timeout: 15.0) {
            if case .failed = vm.clockSyncState { return true }
            return false
        }

        if case .failed(let count, let msg) = vm.clockSyncState {
            XCTAssertEqual(count, 3)
            XCTAssertNotNil(msg)
        } else {
            XCTFail("Expected .failed, got \(vm.clockSyncState)")
        }
    }

    // ORCH-04: reset cancels clock sync and restores .notSynced
    func test_ORCH_04_resetCancelsClockSync() async throws {
        let vm = makeViewModel(apiClient: makeSuccessClient())

        vm.startClockSync()
        await Task.yield()
        vm.reset()

        XCTAssertEqual(vm.clockSyncState, .notSynced)
        XCTAssertEqual(vm.state, .idle)

        await yieldToTasks()
        XCTAssertEqual(vm.clockSyncState, .notSynced)
    }

    // ORCH-05: synced result accessible via isClockSynced
    func test_ORCH_05_syncedResultAccessible() async throws {
        let clock = FakeClock(wallMs: 1_700_000_000_000.0, mono: 100.0)
        let dto = ServerTimeDTO(
            serverTimeUtc: "2023-11-15T10:00:00.000Z",
            serverEpochMs: 1_700_000_050_000,
            precision: "milliseconds",
            source: "backend_app_clock"
        )
        let client = FakeSystemTimeAPIClient(responses: [.success(dto)])
        let service = ClockSyncService(
            apiClient: client,
            wallClockMs: clock.asWallClock,
            monotonicClock: clock.asMonotonic,
            sampleCount: 1
        )
        let vm = MultiCameraSessionViewModel(
            authManager: AuthManager(),
            clockSyncService: service
        )

        XCTAssertFalse(vm.isClockSynced)

        vm.startClockSync()
        await waitForState(vm) { vm.isClockSynced }

        XCTAssertTrue(vm.isClockSynced)
        if case .synced(let result) = vm.clockSyncState {
            XCTAssertGreaterThan(result.estimatedServerAtSampleMs, 0)
        }
    }

    // ORCH-06: canStartCapture false without sync
    func test_ORCH_06_canStartCaptureFalseWithoutSync() {
        let vm = makeViewModel(apiClient: makeSuccessClient())
        XCTAssertFalse(vm.canStartCapture)
    }

    // ORCH-07: canStartCapture false when synced but not inLobby
    func test_ORCH_07_canStartCaptureRequiresBothSyncAndLobby() async throws {
        let vm = makeViewModel(apiClient: makeSuccessClient())

        vm.startClockSync()
        await waitForState(vm) { vm.isClockSynced }

        XCTAssertTrue(vm.isClockSynced)
        XCTAssertEqual(vm.state, .idle)
        XCTAssertFalse(vm.canStartCapture)
    }

    // ORCH-08: retryClockSync restarts after failure
    func test_ORCH_08_retryAfterFailure() async throws {
        let err = NSError(domain: "test", code: -1, userInfo: [NSLocalizedDescriptionKey: "fail"])
        let dto = ServerTimeDTO(
            serverTimeUtc: "2023-11-15T10:00:00.000Z",
            serverEpochMs: 1_700_000_000_000,
            precision: "milliseconds",
            source: "backend_app_clock"
        )
        var responses: [Result<ServerTimeDTO, Error>] = Array(repeating: .failure(err), count: 4)
        responses.append(contentsOf: Array(repeating: .success(dto), count: 4))
        let client = FakeSystemTimeAPIClient(responses: responses)
        let clock = FakeClock()
        let service = ClockSyncService(
            apiClient: client,
            wallClockMs: clock.asWallClock,
            monotonicClock: clock.asMonotonic,
            sampleCount: 1
        )
        let vm = MultiCameraSessionViewModel(
            authManager: AuthManager(),
            clockSyncService: service
        )

        vm.startClockSync()
        await waitForState(vm, timeout: 15.0) {
            if case .failed = vm.clockSyncState { return true }
            return false
        }

        vm.retryClockSync()
        await waitForState(vm, timeout: 2.0) { vm.isClockSynced }

        XCTAssertTrue(vm.isClockSynced)
    }

    // ORCH-09: partial failure then success (2 sync() calls fail, 3rd succeeds)
    func test_ORCH_09_partialFailureRetrySucceeds() async throws {
        let client = makeMixedClient(failCount: 2, epochMs: 1_700_000_050_000)
        let clock = FakeClock()
        let service = ClockSyncService(
            apiClient: client,
            wallClockMs: clock.asWallClock,
            monotonicClock: clock.asMonotonic,
            sampleCount: 1
        )
        let vm = MultiCameraSessionViewModel(
            authManager: AuthManager(),
            clockSyncService: service
        )

        vm.startClockSync()

        await waitForState(vm, timeout: 10.0) { vm.isClockSynced }

        if case .synced(let result) = vm.clockSyncState {
            XCTAssertGreaterThan(result.estimatedServerAtSampleMs, 0)
        } else {
            XCTFail("Expected .synced after partial failure, got \(vm.clockSyncState)")
        }
    }

    // ORCH-10: canStartCapture requires lobby state
    func test_ORCH_10_canStartCaptureRequiresLobby() async throws {
        let vm = makeViewModel(apiClient: makeSuccessClient())

        vm.startClockSync()
        await waitForState(vm) { vm.isClockSynced }

        XCTAssertTrue(vm.isClockSynced)
        XCTAssertEqual(vm.state, .idle)
        XCTAssertFalse(vm.canStartCapture)
    }
}

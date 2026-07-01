import XCTest
import Combine
import QuartzCore
@testable import LFAEducationCenter

// MARK: — Regression: instructor device must never attach a PlayerCaptureOrchestrator
//
// (2026-07-01 flow audit finding #1) Before MultiCameraSessionViewModel.
// shouldAttachPlayerCaptureOrchestrator() existed, autoRegisterDevice() called
// orch.attach(...) unconditionally for EVERY device role — including the
// instructor. Since CycleCaptureOrchestrator (CCO, the instructor's own
// recording driver) and PlayerCaptureOrchestrator (PCO) share the SAME
// SessionCaptureManager instance in production (MultiCameraLobbyView.init),
// an attached PCO on the instructor device would independently react to the
// same cycle CCO was already driving and race it for confirmDeviceStart/Stop
// on the instructor's OWN device_id. Whichever call landed second got a
// stale-revision 409 — and CCO's error handler treats any confirm-start HTTP
// error as fatal, tearing down the instructor's own capture even though the
// backend already showed confirmed_start=true.
//
// These tests reproduce the exact production topology (CCO + PCO sharing one
// CaptureController) and prove that when PCO is never attached — which is
// what shouldAttachPlayerCaptureOrchestrator(.instructorPrimary) now
// guarantees — PCO stays .idle, never calls confirmDeviceStart/Stop, and
// CCO's own start/stop flow completes with exactly one confirm call each.

private final class FakeInstructorTokenProvider: AccessTokenProvider {
    var accessToken: String? = "test-token"
}

@MainActor
private final class MockCCOClientForGateTest: CycleAPIClient {
    private(set) var confirmStartCallCount = 0
    private(set) var confirmStopCallCount = 0

    func getSession(token: String, uuid: String) async throws -> MultiCameraSessionDTO {
        MultiCameraSessionDTO(
            id: 1, sessionUuid: uuid, status: .active,
            createdByUserId: 1, maxParticipants: 2, maxDevices: 4,
            revision: 5, calibration: nil, scheduledStartAt: nil,
            createdAt: "2026-07-01T00:00:00.000Z", startedAt: nil,
            stoppedAt: nil, finalizedAt: nil, cancelledAt: nil,
            participants: [], devices: [], streams: []
        )
    }
    func activateSession(token: String, uuid: String, revision: Int) async throws -> MultiCameraSessionDTO {
        try await getSession(token: token, uuid: uuid)
    }
    func createCycle(token: String, uuid: String, idempotencyKey: String) async throws -> CaptureCycleDTO {
        makeGateTestCycle(id: 1, revision: 1, status: .preparing)
    }
    func scheduleCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        makeGateTestCycle(id: cycleId, revision: revision + 1,
                          scheduledStartAt: pastGateISO(offsetSeconds: 0.5), status: .recordingPending)
    }
    func stopCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO {
        makeGateTestCycle(id: cycleId, revision: revision + 1, status: .stopping)
    }
    func confirmDeviceStart(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
                             startedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        confirmStartCallCount += 1
        return makeGateTestCycle(id: cycleId, revision: cycleDeviceRevision + 1, status: .recording)
    }
    func confirmDeviceStop(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
                            stoppedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        confirmStopCallCount += 1
        return makeGateTestCycle(id: cycleId, revision: cycleDeviceRevision + 1, status: .completed)
    }
}

@MainActor
private final class MockPCOClientForGateTest: CycleAPIClient {
    private(set) var confirmStartCallCount = 0
    private(set) var confirmStopCallCount = 0

    func getSession(token: String, uuid: String) async throws -> MultiCameraSessionDTO { fatalError("not used") }
    func activateSession(token: String, uuid: String, revision: Int) async throws -> MultiCameraSessionDTO { fatalError("not used") }
    func createCycle(token: String, uuid: String, idempotencyKey: String) async throws -> CaptureCycleDTO { fatalError("not used") }
    func scheduleCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO { fatalError("not used") }
    func stopCycle(token: String, uuid: String, cycleId: Int, revision: Int) async throws -> CaptureCycleDTO { fatalError("not used") }
    func confirmDeviceStart(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
                             startedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        confirmStartCallCount += 1
        return makeGateTestCycle(id: cycleId, revision: cycleDeviceRevision + 1, status: .recording)
    }
    func confirmDeviceStop(token: String, uuid: String, cycleId: Int, sessionDeviceId: Int,
                            stoppedAt: String, cycleDeviceRevision: Int) async throws -> CaptureCycleDTO {
        confirmStopCallCount += 1
        return makeGateTestCycle(id: cycleId, revision: cycleDeviceRevision + 1, status: .completed)
    }
}

/// No-op list client — PCL never needs to actually observe a real cycle in these tests,
/// since the whole point is PCO is never attach()-ed to it for the instructor role.
@MainActor
private final class GateTestStubCycleListClient: CycleListClient {
    func listCycles(token: String, uuid: String) async throws -> [CaptureCycleDTO] { [] }
}

private func pastGateISO(offsetSeconds: Double) -> String {
    let fmt = ISO8601DateFormatter()
    fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return fmt.string(from: Date().addingTimeInterval(-offsetSeconds))
}

private func makeGateTestCycle(
    id: Int, revision: Int, scheduledStartAt: String? = nil, status: CycleStatus
) -> CaptureCycleDTO {
    let device = CaptureCycleDeviceDTO(
        id: 1, captureCycleId: id, sessionDeviceId: 1,
        required: true, recordingStatus: .pending,
        startedAt: nil, stoppedAt: nil, failureReason: nil, revision: revision
    )
    return CaptureCycleDTO(
        id: id, sessionId: 1, cycleIndex: 0, status: status, result: nil,
        scheduledStartAt: scheduledStartAt,
        recordingStartedAt: nil, stopRequestedAt: nil,
        recordingStoppedAt: nil, completedAt: nil, failureReason: nil,
        createdByParticipantId: 1, idempotencyKey: "gate-test-\(id)",
        revision: revision,
        createdAt: "2026-07-01T00:00:00.000Z",
        updatedAt: "2026-07-01T00:00:00.000Z",
        cycleDevices: [device]
    )
}

private func makeGateSyncedClock() async -> ClockSyncService {
    let nowMs = Int(Date().timeIntervalSince1970 * 1000)
    let dto = ServerTimeDTO(
        serverTimeUtc: "2026-07-01T00:00:00.000Z", serverEpochMs: nowMs,
        precision: "milliseconds", source: "test"
    )
    let client = FakeSystemTimeAPIClient(responses: Array(repeating: .success(dto), count: 10))
    let svc = ClockSyncService(
        apiClient: client,
        wallClockMs: { Date().timeIntervalSince1970 * 1000.0 },
        monotonicClock: { CACurrentMediaTime() },
        sampleCount: 1
    )
    _ = try? await svc.sync()
    return svc
}

@MainActor
final class PCOInstructorRoleGateTests: XCTestCase {

    // GATE-01: instructor device — PCO constructed (as MultiCameraLobbyView always
    // constructs one) but, per shouldAttachPlayerCaptureOrchestrator(.instructorPrimary)
    // == false, NEVER attach()-ed. CCO drives the SAME shared capture controller through
    // a full start→stop cycle. PCO must stay .idle and never call confirm APIs; CCO's
    // own confirm calls must each fire exactly once.
    func test_GATE_01_instructor_pco_never_attached_no_interference_with_cco() async {
        // Sanity: this is the exact gate production code now checks before attaching.
        XCTAssertFalse(MultiCameraSessionViewModel.shouldAttachPlayerCaptureOrchestrator(deviceRole: .instructorPrimary))

        let token = FakeInstructorTokenProvider()
        let clock = await makeGateSyncedClock()
        let sharedCapture = FakeCaptureController()
        let ccoClient = MockCCOClientForGateTest()
        let pcoClient = MockPCOClientForGateTest()

        let cco = CycleCaptureOrchestrator(
            authManager: token, clockSyncService: clock,
            captureController: sharedCapture, cycleAPIClient: ccoClient
        )
        let pco = PlayerCaptureOrchestrator(
            authManager: token, clockSyncService: clock,
            captureController: sharedCapture, cycleAPIClient: pcoClient
        )
        // PCO is intentionally NEVER attach()-ed here — this is the production behavior
        // for an instructorPrimary device after the fix. `listener`/`pcoClient` exist only
        // to prove that even though PCO COULD react (it has a real listener available),
        // it never does, because it was never given one via attach().
        let listener = PlayerCycleListener(
            authManager: token, cycleListClient: GateTestStubCycleListClient(),
            pollingIntervalNs: 0, sleepProvider: { _ in }
        )
        _ = listener // never started, never attached — instructor device does not use it

        // Drive CCO through a full cycle, exactly like beginCycle()/endCycle() would.
        cco.startCycle(sessionUuid: "gate-test-session", sessionDeviceId: 1, sessionRevision: 5)
        for _ in 0..<30 { await Task.yield() }

        guard case .capturing = cco.state else {
            return XCTFail("CCO must reach .capturing; got \(cco.state)")
        }
        XCTAssertEqual(sharedCapture.startCallCount, 1, "startCapture() must be called exactly once")
        XCTAssertEqual(ccoClient.confirmStartCallCount, 1, "CCO confirmDeviceStart must be called exactly once")

        await cco.stopCycle()
        for _ in 0..<30 { await Task.yield() }

        guard case .completed = cco.state else {
            return XCTFail("CCO must reach .completed; got \(cco.state)")
        }
        XCTAssertEqual(sharedCapture.stopCallCount, 1, "stopCapture() must be called exactly once")
        XCTAssertEqual(ccoClient.confirmStopCallCount, 1, "CCO confirmDeviceStop must be called exactly once")

        // The core regression assertion: PCO — sharing the exact same capture controller
        // CCO just drove through a full recording cycle — never moved, never called anything.
        XCTAssertEqual(pco.state, .idle, "Un-attached PCO must remain .idle throughout the instructor's own cycle")
        XCTAssertEqual(pcoClient.confirmStartCallCount, 0, "PCO must NEVER call confirmDeviceStart on the instructor device")
        XCTAssertEqual(pcoClient.confirmStopCallCount, 0, "PCO must NEVER call confirmDeviceStop on the instructor device")
    }

    // GATE-02: control comparison — a player device (shouldAttachPlayerCaptureOrchestrator
    // == true) DOES attach, and reacts normally. This is not new coverage (see PCO-*/PSO-*
    // in PlayerCaptureOrchestratorTests.swift) — it exists here only to document the other
    // side of the same gate in one place.
    func test_GATE_02_player_pco_attaches_and_reacts_normally() async {
        XCTAssertTrue(MultiCameraSessionViewModel.shouldAttachPlayerCaptureOrchestrator(deviceRole: .playerPrimary))

        let token = FakeInstructorTokenProvider()
        let clock = await makeGateSyncedClock()
        let capture = FakeCaptureController()
        let pcoClient = MockPCOClientForGateTest()
        let pco = PlayerCaptureOrchestrator(
            authManager: token, clockSyncService: clock,
            captureController: capture, cycleAPIClient: pcoClient
        )
        let listener = PlayerCycleListener(
            authManager: token, cycleListClient: GateTestStubCycleListClient(),
            pollingIntervalNs: 0, sleepProvider: { _ in }
        )
        pco.attach(listener: listener, sessionUuid: "gate-test-session-player", playerSessionDeviceId: 1)

        let cycle = makeGateTestCycle(id: 1, revision: 1, scheduledStartAt: pastGateISO(offsetSeconds: 0.5), status: .recordingPending)
        pco.handleListenerState(.pendingCycleDetected(cycleId: 1), currentCycle: cycle)
        for _ in 0..<20 { await Task.yield() }

        XCTAssertEqual(pco.state, .confirmed(cycleId: 1))
        XCTAssertEqual(pcoClient.confirmStartCallCount, 1)
    }
}

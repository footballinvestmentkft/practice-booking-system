import XCTest
import Combine
@testable import LFAEducationCenter

// MARK: — Fakes

private final class FakeTokenProvider: AccessTokenProvider {
    var accessToken: String? = "test-token"
}

@MainActor
private final class FakeCycleListClient: CycleListClient {
    private var responses: [[CaptureCycleDTO]]
    private var callIndex = 0

    init(responses: [[CaptureCycleDTO]] = []) {
        self.responses = responses
    }

    func listCycles(token: String, uuid: String) async throws -> [CaptureCycleDTO] {
        guard callIndex < responses.count else { return [] }
        defer { callIndex += 1 }
        return responses[callIndex]
    }
}

// MARK: — Helpers

private func makeCycle(id: Int, status: CycleStatus, cycleIndex: Int = 0) -> CaptureCycleDTO {
    CaptureCycleDTO(
        id: id,
        sessionId: 1,
        cycleIndex: cycleIndex,
        status: status,
        result: nil,
        scheduledStartAt: nil,
        recordingStartedAt: nil,
        stopRequestedAt: nil,
        recordingStoppedAt: nil,
        completedAt: nil,
        failureReason: nil,
        createdByParticipantId: 1,
        idempotencyKey: "pcl-test-\(id)",
        revision: 1,
        createdAt: "2026-06-26T00:00:00Z",
        updatedAt: "2026-06-26T00:00:00Z",
        cycleDevices: []
    )
}

// MARK: — PlayerCycleListenerTests

@MainActor
final class PlayerCycleListenerTests: XCTestCase {

    private var fakeToken: FakeTokenProvider!
    private var listener: PlayerCycleListener!

    override func setUp() {
        super.setUp()
        fakeToken = FakeTokenProvider()
        listener = makeFreshListener()
    }

    override func tearDown() {
        listener.stop()
        listener = nil
        fakeToken = nil
        super.tearDown()
    }

    private func makeFreshListener(responses: [[CaptureCycleDTO]] = []) -> PlayerCycleListener {
        PlayerCycleListener(
            authManager: fakeToken,
            cycleListClient: FakeCycleListClient(responses: responses),
            pollingIntervalNs: 0,
            sleepProvider: { _ in }
        )
    }

    // PCL-01: Initial state is .idle before start() is called.
    func test_pcl_01_initial_state_is_idle() {
        XCTAssertEqual(listener.state, .idle)
    }

    // PCL-02: Empty cycles list → .waitingForCycle.
    func test_pcl_02_empty_cycles_returns_waiting() {
        let result = listener.classify([])
        XCTAssertEqual(result, .waitingForCycle)
    }

    // PCL-03: .preparing cycle → .waitingForCycle (not ready yet).
    func test_pcl_03_preparing_cycle_returns_waiting() {
        let result = listener.classify([makeCycle(id: 1, status: .preparing)])
        XCTAssertEqual(result, .waitingForCycle)
    }

    // PCL-04: .recordingPending → .pendingCycleDetected.
    func test_pcl_04_recording_pending_detected() {
        let result = listener.classify([makeCycle(id: 7, status: .recordingPending)])
        XCTAssertEqual(result, .pendingCycleDetected(cycleId: 7))
    }

    // PCL-05: .recording → .recordingDetected (late arrival, cycle already started).
    func test_pcl_05_recording_late_arrival_detected() {
        let result = listener.classify([makeCycle(id: 3, status: .recording)])
        XCTAssertEqual(result, .recordingDetected(cycleId: 3))
    }

    // PCL-06: .stopping → .stoppingDetected.
    func test_pcl_06_stopping_detected() {
        let result = listener.classify([makeCycle(id: 5, status: .stopping)])
        XCTAssertEqual(result, .stoppingDetected(cycleId: 5))
    }

    // PCL-07: .completed cycle → .waitingForCycle; cycle ID added to handledCycleIds.
    func test_pcl_07_completed_cycle_returns_waiting_and_marks_handled() {
        let result = listener.classify([makeCycle(id: 2, status: .completed)])
        XCTAssertEqual(result, .waitingForCycle)
        XCTAssertTrue(listener.handledCycleIds.contains(2), "Completed cycle must be in handledCycleIds")
    }

    // PCL-07b: .aborted cycle → same behaviour as .completed.
    func test_pcl_07b_aborted_cycle_returns_waiting_and_marks_handled() {
        let result = listener.classify([makeCycle(id: 8, status: .aborted)])
        XCTAssertEqual(result, .waitingForCycle)
        XCTAssertTrue(listener.handledCycleIds.contains(8))
    }

    // PCL-07c: CycleStatus.failed (backend failure) → same terminal handling as .completed.
    func test_pcl_07c_failed_status_cycle_returns_waiting_and_marks_handled() {
        let result = listener.classify([makeCycle(id: 11, status: .failed)])
        XCTAssertEqual(result, .waitingForCycle)
        XCTAssertTrue(listener.handledCycleIds.contains(11))
    }

    // PCL-08: Duplicate guard — cycle seen as .completed is blocked on next classify call.
    // Covers the scenario where the same cycle ID reappears as .recordingPending.
    func test_pcl_08_completed_cycle_id_blocked_on_next_classify() {
        _ = listener.classify([makeCycle(id: 9, status: .completed)])
        let result = listener.classify([makeCycle(id: 9, status: .recordingPending)])
        XCTAssertEqual(result, .waitingForCycle, "Handled cycle ID must not re-trigger")
    }

    // PCL-09: State persistence — two consecutive classify calls with same .recordingPending
    // cycle return equal states; the second call must NOT publish (Equatable match).
    func test_pcl_09_repeated_pending_classify_returns_equal_state() {
        let r1 = listener.classify([makeCycle(id: 4, status: .recordingPending)])
        let r2 = listener.classify([makeCycle(id: 4, status: .recordingPending)])
        XCTAssertEqual(r1, .pendingCycleDetected(cycleId: 4))
        XCTAssertEqual(r1, r2, "Repeated poll with same cycle must return equal state — no re-publish")
    }

    // PCL-10: Monotonic state progression — .recordingPending → .recording → .stopping
    // across three successive classify calls on the same cycle ID.
    func test_pcl_10_monotonic_state_progression() {
        let s1 = listener.classify([makeCycle(id: 6, status: .recordingPending)])
        let s2 = listener.classify([makeCycle(id: 6, status: .recording)])
        let s3 = listener.classify([makeCycle(id: 6, status: .stopping)])
        XCTAssertEqual(s1, .pendingCycleDetected(cycleId: 6))
        XCTAssertEqual(s2, .recordingDetected(cycleId: 6))
        XCTAssertEqual(s3, .stoppingDetected(cycleId: 6))
    }

    // PCL-11: start() → .waitingForCycle; stop() → .idle synchronously.
    // Tests the public state contract of start()/stop() without running the polling loop.
    func test_pcl_11_start_then_stop_returns_idle() {
        XCTAssertEqual(listener.state, .idle, "Precondition: idle before start()")
        listener.start(sessionUuid: "test-uuid")
        XCTAssertEqual(listener.state, .waitingForCycle, "start() must set .waitingForCycle synchronously")
        listener.stop()
        XCTAssertEqual(listener.state, .idle, "stop() must return state to .idle synchronously")
    }

    // PCL-12: reset() clears handledCycleIds — a previously handled cycle ID becomes
    // detectable again after reset.
    func test_pcl_12_reset_clears_handled_cycle_ids() {
        _ = listener.classify([makeCycle(id: 10, status: .completed)])
        XCTAssertEqual(
            listener.classify([makeCycle(id: 10, status: .recordingPending)]),
            .waitingForCycle,
            "Before reset: cycle 10 must be blocked"
        )
        listener.reset()
        XCTAssertEqual(listener.state, .idle, "reset() must return state to .idle")
        XCTAssertTrue(listener.handledCycleIds.isEmpty, "reset() must clear handledCycleIds")

        let result = listener.classify([makeCycle(id: 10, status: .recordingPending)])
        XCTAssertEqual(
            result, .pendingCycleDetected(cycleId: 10),
            "After reset: previously handled cycle must be detectable again"
        )
    }
}

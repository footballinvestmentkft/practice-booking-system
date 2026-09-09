import XCTest
@testable import LFAEducationCenter

/// MC2-PR3 status-dashboard state resolver tests (DPS-01..14).
///
/// The dual-player regression focus: with two players in one session the
/// resolver must produce independent, correctly-attributed states per
/// session_device_id — the old live-video dashboard cross-attributed the two
/// players because a single-peer MPC stream fed both panels.
final class DevicePanelStateTests: XCTestCase {

    private let now = ISO8601DateFormatter().date(from: "2026-07-12T18:00:00Z")!

    private func iso(_ secondsBeforeNow: TimeInterval) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: now.addingTimeInterval(-secondsBeforeNow))
    }

    private func device(
        id: Int,
        role: MCDeviceRole = .playerPrimary,
        status: MCDeviceStatus = .ready,
        heartbeatAgo: TimeInterval? = 2,
        registeredAgo: TimeInterval = 300,
        managedBy: Int? = nil
    ) -> SessionDeviceDTO {
        SessionDeviceDTO(
            id: id, sessionId: 1, deviceId: id * 10, participantId: role == .auxiliaryCamera ? nil : id,
            managedByDeviceId: managedBy, deviceRole: role, status: status, revision: 1,
            lastHeartbeat: heartbeatAgo.map { iso($0) }, registeredAt: iso(registeredAgo), removedAt: nil
        )
    }

    private func cycleDevice(
        sd: Int,
        status: CycleDeviceRecordingStatus,
        required: Bool = true,
        failureReason: String? = nil
    ) -> CaptureCycleDeviceDTO {
        CaptureCycleDeviceDTO(
            id: sd * 100, captureCycleId: 7, sessionDeviceId: sd, required: required,
            recordingStatus: status, startedAt: nil, stoppedAt: nil,
            failureReason: failureReason, revision: 1
        )
    }

    private func cycle(status: CycleStatus, devices: [CaptureCycleDeviceDTO]) -> CaptureCycleDTO {
        CaptureCycleDTO(
            id: 7, sessionId: 1, cycleIndex: 0, status: status, result: nil,
            scheduledStartAt: nil, recordingStartedAt: nil, stopRequestedAt: nil,
            recordingStoppedAt: nil, completedAt: nil, failureReason: nil,
            createdByParticipantId: 1, idempotencyKey: "k", revision: 1,
            createdAt: iso(60), updatedAt: iso(1), cycleDevices: devices
        )
    }

    // MARK: — Connectivity states

    func test_DPS_01_freshHeartbeat_isConnected() {
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1, heartbeatAgo: 3), cycleDevice: nil, cycle: nil, now: now)
        XCTAssertEqual(state, .connected)
    }

    func test_DPS_02_heartbeatPastStaleThreshold_isStale() {
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1, heartbeatAgo: 15), cycleDevice: nil, cycle: nil, now: now)
        guard case .stale(let age) = state else { return XCTFail("expected .stale, got \(state)") }
        XCTAssertEqual(age, 15, accuracy: 1)
    }

    func test_DPS_03_heartbeatPastDisconnectedThreshold_isDisconnected() {
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1, heartbeatAgo: 45), cycleDevice: nil, cycle: nil, now: now)
        XCTAssertEqual(state, .disconnected)
    }

    func test_DPS_04_backendDisconnectedStatus_isDisconnected_evenWithFreshHeartbeat() {
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1, status: .disconnected, heartbeatAgo: 1),
            cycleDevice: nil, cycle: nil, now: now)
        XCTAssertEqual(state, .disconnected)
    }

    func test_DPS_05_noHeartbeatYet_fallsBackToRegisteredAt() {
        // Registered 4 s ago, first heartbeat not sent yet → connected, not disconnected.
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1, heartbeatAgo: nil, registeredAgo: 4),
            cycleDevice: nil, cycle: nil, now: now)
        XCTAssertEqual(state, .connected)
    }

    // MARK: — Cycle-driven states

    func test_DPS_06_confirmedStart_inActiveCycle_isRecording() {
        let cd = cycleDevice(sd: 1, status: .confirmedStart)
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1), cycleDevice: cd,
            cycle: cycle(status: .recording, devices: [cd]), now: now)
        XCTAssertEqual(state, .recording)
    }

    func test_DPS_07_confirmedStop_inCompletedCycle_isCompleted() {
        let cd = cycleDevice(sd: 1, status: .confirmedStop)
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1), cycleDevice: cd,
            cycle: cycle(status: .completed, devices: [cd]), now: now)
        XCTAssertEqual(state, .completed)
    }

    func test_DPS_08_failedCycleDevice_isFailed_evenWithFreshHeartbeat() {
        let cd = cycleDevice(sd: 1, status: .failed, failureReason: "capture error")
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 1, heartbeatAgo: 1), cycleDevice: cd,
            cycle: cycle(status: .recording, devices: [cd]), now: now)
        XCTAssertEqual(state, .failed(reason: "capture error"))
    }

    func test_DPS_09_pendingNotRequired_staysConnectivityDriven() {
        // The non-recording instructor's cycle row is pending/not-required —
        // it must show plain connectivity, never a recording state.
        let cd = cycleDevice(sd: 3, status: .pending, required: false)
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 3, role: .instructorPrimary), cycleDevice: cd,
            cycle: cycle(status: .recording, devices: [cd]), now: now)
        XCTAssertEqual(state, .connected)
    }

    // MARK: — Auxiliary camera (GoPro — no app heartbeat)

    func test_DPS_10_auxReady_noHeartbeat_isConnected() {
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 4, role: .auxiliaryCamera, heartbeatAgo: nil, registeredAgo: 3600, managedBy: 1),
            cycleDevice: nil, cycle: nil, now: now)
        XCTAssertEqual(state, .connected, "aux cameras must not be judged by heartbeat age")
    }

    func test_DPS_11_auxConfirmedStart_inActiveCycle_isRecording() {
        let cd = cycleDevice(sd: 4, status: .confirmedStart, required: false)
        let state = DevicePanelStateResolver.resolve(
            device: device(id: 4, role: .auxiliaryCamera, heartbeatAgo: nil, registeredAgo: 3600, managedBy: 1),
            cycleDevice: cd, cycle: cycle(status: .recording, devices: [cd]), now: now)
        XCTAssertEqual(state, .recording)
    }

    // MARK: — Dual-player regression (DPS-12..14)

    func test_DPS_12_twoPlayers_independentStates_byDeviceId() {
        let cdA = cycleDevice(sd: 1, status: .confirmedStart)
        let cdB = cycleDevice(sd: 2, status: .pending)
        let c = cycle(status: .recording, devices: [cdA, cdB])
        let stateA = DevicePanelStateResolver.resolve(
            device: device(id: 1, role: .playerPrimary), cycleDevice: cdA, cycle: c, now: now)
        let stateB = DevicePanelStateResolver.resolve(
            device: device(id: 2, role: .playerSecondary), cycleDevice: cdB, cycle: c, now: now)
        XCTAssertEqual(stateA, .recording)
        XCTAssertEqual(stateB, .connected, "player B pending must not inherit player A's recording state")
    }

    func test_DPS_13_twoPlayers_oneDisconnected_otherUnaffected() {
        let stateA = DevicePanelStateResolver.resolve(
            device: device(id: 1, heartbeatAgo: 45), cycleDevice: nil, cycle: nil, now: now)
        let stateB = DevicePanelStateResolver.resolve(
            device: device(id: 2, role: .playerSecondary, heartbeatAgo: 2), cycleDevice: nil, cycle: nil, now: now)
        XCTAssertEqual(stateA, .disconnected)
        XCTAssertEqual(stateB, .connected)
    }

    func test_DPS_14_twoPlayers_oneFails_duringCycle_otherKeepsRecording() {
        let cdA = cycleDevice(sd: 1, status: .failed, failureReason: "storage full")
        let cdB = cycleDevice(sd: 2, status: .confirmedStart)
        let c = cycle(status: .recording, devices: [cdA, cdB])
        XCTAssertEqual(
            DevicePanelStateResolver.resolve(device: device(id: 1), cycleDevice: cdA, cycle: c, now: now),
            .failed(reason: "storage full"))
        XCTAssertEqual(
            DevicePanelStateResolver.resolve(device: device(id: 2, role: .playerSecondary), cycleDevice: cdB, cycle: c, now: now),
            .recording)
    }

    // MARK: — Reset-before-start (new-session lesson, 2026-07-12)

    @MainActor
    func test_DPS_15_shouldResetBeforeStart_matrix() {
        XCTAssertFalse(MultiCameraSessionViewModel.shouldResetBeforeStart(state: .idle))
        XCTAssertFalse(MultiCameraSessionViewModel.shouldResetBeforeStart(state: .creating))
        XCTAssertFalse(MultiCameraSessionViewModel.shouldResetBeforeStart(state: .joining))
        XCTAssertTrue(MultiCameraSessionViewModel.shouldResetBeforeStart(state: .error("x")))
        let session = MultiCameraSessionDTO(
            id: 1, sessionUuid: "u", status: .lobby,
            createdByUserId: 1, maxParticipants: 3, maxDevices: 4,
            revision: 1, calibration: nil, scheduledStartAt: nil,
            createdAt: "2026-07-12T17:00:00Z", startedAt: nil,
            stoppedAt: nil, finalizedAt: nil, cancelledAt: nil,
            participants: [], devices: [], streams: []
        )
        XCTAssertTrue(MultiCameraSessionViewModel.shouldResetBeforeStart(state: .inLobby(session: session)))
    }

    // MARK: — ISO8601 parsing (backend emits fractional seconds)

    func test_DPS_16_parsesFractionalAndPlainISO8601() {
        XCTAssertNotNil(DevicePanelStateResolver.parseISO8601("2026-07-12T17:25:17.496000Z"))
        XCTAssertNotNil(DevicePanelStateResolver.parseISO8601("2026-07-12T17:25:17Z"))
        XCTAssertNotNil(DevicePanelStateResolver.parseISO8601("2026-07-12T17:25:17.496000"))
    }
}

@testable import LFAEducationCenter
import XCTest

// MARK: — ORCH-4 Capture Authority: CTL-01..08
// Tests for myDeviceRole + isController logic via static helpers on MultiCameraSessionViewModel.
// Calls MultiCameraSessionViewModel.resolveDeviceRole / resolveIsController directly so no
// network or AVFoundation is touched.

@MainActor
final class CaptureAuthorityTests: XCTestCase {

    // MARK: — Helpers

    private func makeDevice(
        id: Int,
        role: MCDeviceRole,
        removedAt: String? = nil
    ) -> SessionDeviceDTO {
        SessionDeviceDTO(
            id: id, sessionId: 1, deviceId: id + 100,
            participantId: nil, managedByDeviceId: nil,
            deviceRole: role, status: .ready,
            revision: 1, lastHeartbeat: nil,
            registeredAt: "2026-06-27T00:00:00Z", removedAt: removedAt
        )
    }

    private func makeSession(devices: [SessionDeviceDTO]) -> MultiCameraSessionDTO {
        MultiCameraSessionDTO(
            id: 1, sessionUuid: "ctl-test-uuid", status: .lobby,
            createdByUserId: 1, maxParticipants: 4, maxDevices: 8,
            revision: 1, calibration: nil, scheduledStartAt: nil,
            createdAt: "2026-06-27T00:00:00Z", startedAt: nil,
            stoppedAt: nil, finalizedAt: nil, cancelledAt: nil,
            participants: [], devices: devices, streams: []
        )
    }

    private func inLobby(devices: [SessionDeviceDTO]) -> LobbyState {
        .inLobby(session: makeSession(devices: devices))
    }

    // MARK: — CTL-01: resolveDeviceRole returns nil when not inLobby

    func test_CTL_01_resolveDeviceRole_nil_whenIdle() {
        let result = MultiCameraSessionViewModel.resolveDeviceRole(state: .idle, sessionDeviceId: 1)
        XCTAssertNil(result, "CTL-01: must be nil in .idle state")
    }

    // MARK: — CTL-02: resolveDeviceRole returns nil when sessionDeviceId is nil

    func test_CTL_02_resolveDeviceRole_nil_whenNoDeviceId() {
        let state = inLobby(devices: [makeDevice(id: 1, role: .playerPrimary)])
        let result = MultiCameraSessionViewModel.resolveDeviceRole(state: state, sessionDeviceId: nil)
        XCTAssertNil(result, "CTL-02: must be nil when sessionDeviceId is nil")
    }

    // MARK: — CTL-03: resolveDeviceRole returns correct role for the matching device

    func test_CTL_03_resolveDeviceRole_returnsRole_forMatchingDevice() {
        let state = inLobby(devices: [
            makeDevice(id: 1, role: .playerPrimary),
            makeDevice(id: 2, role: .playerSecondary),
            makeDevice(id: 3, role: .instructorPrimary),
        ])
        XCTAssertEqual(
            MultiCameraSessionViewModel.resolveDeviceRole(state: state, sessionDeviceId: 1),
            .playerPrimary, "CTL-03a"
        )
        XCTAssertEqual(
            MultiCameraSessionViewModel.resolveDeviceRole(state: state, sessionDeviceId: 2),
            .playerSecondary, "CTL-03b"
        )
        XCTAssertEqual(
            MultiCameraSessionViewModel.resolveDeviceRole(state: state, sessionDeviceId: 3),
            .instructorPrimary, "CTL-03c"
        )
    }

    // MARK: — CTL-04: resolveDeviceRole returns nil for a removed device

    func test_CTL_04_resolveDeviceRole_nil_forRemovedDevice() {
        let state = inLobby(devices: [
            makeDevice(id: 1, role: .playerPrimary, removedAt: "2026-06-27T01:00:00Z"),
        ])
        let result = MultiCameraSessionViewModel.resolveDeviceRole(state: state, sessionDeviceId: 1)
        XCTAssertNil(result, "CTL-04: removed device must not be returned")
    }

    // MARK: — CTL-05: isController is true for instructorPrimary

    func test_CTL_05_isController_trueFor_instructorPrimary() {
        XCTAssertTrue(
            MultiCameraSessionViewModel.resolveIsController(role: .instructorPrimary),
            "CTL-05: instructorPrimary must be a controller"
        )
    }

    // MARK: — CTL-06: isController is false for playerPrimary (instructor-present sessions)

    func test_CTL_06_isController_falseFor_playerPrimary() {
        XCTAssertFalse(
            MultiCameraSessionViewModel.resolveIsController(role: .playerPrimary),
            "CTL-06: playerPrimary must NOT be a controller when instructor is present"
        )
    }

    // MARK: — CTL-07: isController is false for playerSecondary

    func test_CTL_07_isController_falseFor_playerSecondary() {
        XCTAssertFalse(
            MultiCameraSessionViewModel.resolveIsController(role: .playerSecondary),
            "CTL-07: playerSecondary must NOT be a controller"
        )
    }

    // MARK: — CTL-08: isController is false for auxiliaryCamera

    func test_CTL_08_isController_falseFor_auxiliaryCamera() {
        XCTAssertFalse(
            MultiCameraSessionViewModel.resolveIsController(role: .auxiliaryCamera),
            "CTL-08: auxiliaryCamera must NOT be a controller"
        )
    }

    // MARK: — AP-01: instructorPrimary must auto-prepare

    func test_AP_01_shouldAutoPrepare_instructorPrimary() {
        XCTAssertTrue(
            MultiCameraSessionViewModel.shouldAutoPrepare(deviceRole: .instructorPrimary),
            "AP-01: instructorPrimary must auto-prepare capture pipeline"
        )
    }

    // MARK: — AP-02: playerPrimary must auto-prepare

    func test_AP_02_shouldAutoPrepare_playerPrimary() {
        XCTAssertTrue(
            MultiCameraSessionViewModel.shouldAutoPrepare(deviceRole: .playerPrimary),
            "AP-02: playerPrimary must auto-prepare capture pipeline"
        )
    }

    // MARK: — AP-03: playerSecondary must auto-prepare

    func test_AP_03_shouldAutoPrepare_playerSecondary() {
        XCTAssertTrue(
            MultiCameraSessionViewModel.shouldAutoPrepare(deviceRole: .playerSecondary),
            "AP-03: playerSecondary must auto-prepare capture pipeline"
        )
    }

    // MARK: — AP-04: auxiliaryCamera must NOT auto-prepare (GoPro — separate scope)

    func test_AP_04_shouldAutoPrepare_auxiliaryCamera_false() {
        XCTAssertFalse(
            MultiCameraSessionViewModel.shouldAutoPrepare(deviceRole: .auxiliaryCamera),
            "AP-04: auxiliaryCamera must NOT auto-prepare (external device, separate scope)"
        )
    }

    // MARK: — PCA-01..04: shouldAttachPlayerCaptureOrchestrator (2026-07-01 flow audit fix)
    //
    // Regression coverage for the dual-orchestrator race: before this explicit positive
    // gate existed, autoRegisterDevice() attached a PlayerCaptureOrchestrator for EVERY
    // device role, including the instructor — whose own PCO then raced its
    // CycleCaptureOrchestrator for confirmDeviceStart/Stop on the instructor's own
    // device_id. See PCOInstructorRoleGateTests.swift for the end-to-end behavioral proof.

    func test_PCA_01_shouldAttachPCO_instructorPrimary_false() {
        XCTAssertFalse(
            MultiCameraSessionViewModel.shouldAttachPlayerCaptureOrchestrator(deviceRole: .instructorPrimary),
            "PCA-01: instructorPrimary must NEVER attach a PlayerCaptureOrchestrator"
        )
    }

    func test_PCA_02_shouldAttachPCO_playerPrimary_true() {
        XCTAssertTrue(
            MultiCameraSessionViewModel.shouldAttachPlayerCaptureOrchestrator(deviceRole: .playerPrimary),
            "PCA-02: playerPrimary must attach a PlayerCaptureOrchestrator"
        )
    }

    func test_PCA_03_shouldAttachPCO_playerSecondary_true() {
        XCTAssertTrue(
            MultiCameraSessionViewModel.shouldAttachPlayerCaptureOrchestrator(deviceRole: .playerSecondary),
            "PCA-03: playerSecondary must attach a PlayerCaptureOrchestrator"
        )
    }

    func test_PCA_04_shouldAttachPCO_auxiliaryCamera_false() {
        XCTAssertFalse(
            MultiCameraSessionViewModel.shouldAttachPlayerCaptureOrchestrator(deviceRole: .auxiliaryCamera),
            "PCA-04: auxiliaryCamera (GoPro) must NEVER attach a PlayerCaptureOrchestrator"
        )
    }
}

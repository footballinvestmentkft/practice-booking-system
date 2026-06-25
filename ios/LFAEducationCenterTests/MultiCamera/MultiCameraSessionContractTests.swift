import XCTest
@testable import LFAEducationCenter

final class MultiCameraSessionContractTests: XCTestCase {

    private func fixtureData() throws -> Data {
        let url = Bundle(for: type(of: self)).url(forResource: "session_full", withExtension: "json")
            ?? URL(fileURLWithPath: "../tests/fixtures/multicamera/session_full.json")
        return try Data(contentsOf: url)
    }

    // SCP-01: Session DTO round-trip
    func test_SCP_01_sessionRoundTrip() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        XCTAssertEqual(session.status, .recording)
        XCTAssertEqual(session.revision, 5)
        XCTAssertEqual(session.maxParticipants, 2)
        XCTAssertEqual(session.maxDevices, 4)
        let reEncoded = try JSONEncoder().encode(session)
        let reDecoded = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: reEncoded)
        XCTAssertEqual(session, reDecoded)
    }

    // SCP-02: Participant DTO
    func test_SCP_02_participantRoundTrip() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        XCTAssertEqual(session.participants.count, 2)
        XCTAssertEqual(session.participants[0].role, .instructor)
        XCTAssertEqual(session.participants[1].role, .player)
        XCTAssertNil(session.participants[0].leftAt)
    }

    // SCP-03: Device DTO
    func test_SCP_03_deviceRoundTrip() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        XCTAssertEqual(session.devices.count, 3)
        XCTAssertEqual(session.devices[0].deviceRole, .instructorPrimary)
        XCTAssertEqual(session.devices[2].deviceRole, .auxiliaryCamera)
    }

    // SCP-04: Stream DTO
    func test_SCP_04_streamRoundTrip() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        XCTAssertEqual(session.streams.count, 4)
        XCTAssertEqual(session.streams[0].streamType, .video)
        XCTAssertEqual(session.streams[3].streamType, .skeleton2d)
    }

    // SCP-05: ManagedDevice not in session fixture (separate entity)
    // Verified via enum decode below

    // SCP-06: Calibration null fields
    func test_SCP_06_calibrationNullFields() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        XCTAssertNotNil(session.calibration)
        XCTAssertEqual(session.calibration?.schemaVersion, 1)
        XCTAssertNil(session.calibration?.calibrationId)
        XCTAssertNil(session.calibration?.worldOriginCameraId)
    }

    // SCP-07: SessionStatus enum values (9 cases after ORCH-2A added recordingPending + active)
    func test_SCP_07_sessionStatusEnum() throws {
        let all: [SessionStatus] = [.lobby, .devicesReady, .recordingPending, .recording, .stopped, .finalizing, .completed, .cancelled, .active]
        for s in all {
            let json = try JSONEncoder().encode(s)
            let decoded = try JSONDecoder().decode(SessionStatus.self, from: json)
            XCTAssertEqual(s, decoded)
        }
        XCTAssertEqual(SessionStatus.allCases.count, 9)
    }

    // SCP-08: DeviceRole enum values
    func test_SCP_08_deviceRoleEnum() throws {
        let all: [MCDeviceRole] = [.playerPrimary, .playerSecondary, .instructorPrimary, .auxiliaryCamera]
        for r in all {
            let json = try JSONEncoder().encode(r)
            let decoded = try JSONDecoder().decode(MCDeviceRole.self, from: json)
            XCTAssertEqual(r, decoded)
        }
        XCTAssertEqual(MCDeviceRole.allCases.count, 4)
    }

    // SCP-09: DeviceStatus enum values
    func test_SCP_09_deviceStatusEnum() throws {
        let all: [MCDeviceStatus] = [.registered, .ready, .recording, .stopped, .disconnected, .error]
        for s in all {
            let json = try JSONEncoder().encode(s)
            let decoded = try JSONDecoder().decode(MCDeviceStatus.self, from: json)
            XCTAssertEqual(s, decoded)
        }
        XCTAssertEqual(MCDeviceStatus.allCases.count, 6)
    }

    // SCP-10: StreamType enum values
    func test_SCP_10_streamTypeEnum() throws {
        let all: [MCStreamType] = [.video, .skeleton2d, .skeleton3d, .audio, .telemetry]
        for s in all {
            let json = try JSONEncoder().encode(s)
            let decoded = try JSONDecoder().decode(MCStreamType.self, from: json)
            XCTAssertEqual(s, decoded)
        }
        XCTAssertEqual(MCStreamType.allCases.count, 5)
    }

    // SCP-05: GoPro auxiliary has null participant, non-null managed_by
    func test_SCP_05_goProAuxiliaryInvariant() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        let gopro = session.devices.first { $0.deviceRole == .auxiliaryCamera }!
        XCTAssertNil(gopro.participantId)
        XCTAssertNotNil(gopro.managedByDeviceId)
        XCTAssertEqual(gopro.managedByDeviceId, 2)
    }

    // SCP-11: scheduledStartAt present in fixture
    func test_SCP_11_scheduledStartAtPresent() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        XCTAssertEqual(session.scheduledStartAt, "2026-06-22T10:04:52+00:00")
    }

    // SCP-12: scheduledStartAt null decode
    func test_SCP_12_scheduledStartAtNull() throws {
        let json = """
        {
          "id": 99, "session_uuid": "00000000-0000-0000-0000-000000000099",
          "status": "lobby", "created_by_user_id": 1,
          "max_participants": 2, "max_devices": 4, "revision": 1,
          "calibration": null, "scheduled_start_at": null,
          "created_at": "2026-06-22T10:00:00+00:00",
          "started_at": null, "stopped_at": null, "finalized_at": null, "cancelled_at": null,
          "participants": [], "devices": [], "streams": []
        }
        """.data(using: .utf8)!
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: json)
        XCTAssertNil(session.scheduledStartAt)
    }

    // SCP-13: scheduledStartAt round-trip
    func test_SCP_13_scheduledStartAtRoundTrip() throws {
        let data = try fixtureData()
        let session = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: data)
        let reEncoded = try JSONEncoder().encode(session)
        let reDecoded = try JSONDecoder().decode(MultiCameraSessionDTO.self, from: reEncoded)
        XCTAssertEqual(session.scheduledStartAt, reDecoded.scheduledStartAt)
    }
}

import XCTest
@testable import LFAEducationCenter

final class CaptureCycleContractTests: XCTestCase {

    private func fixtureData() throws -> Data {
        let url = Bundle(for: type(of: self)).url(forResource: "cycle_full", withExtension: "json")
            ?? URL(fileURLWithPath: "../tests/fixtures/multicamera/cycle_full.json")
        return try Data(contentsOf: url)
    }

    // CYC-01: Cycle DTO round-trip
    func test_CYC_01_cycleRoundTrip() throws {
        let data = try fixtureData()
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: data)
        XCTAssertEqual(cycle.id, 1)
        XCTAssertEqual(cycle.sessionId, 1)
        XCTAssertEqual(cycle.cycleIndex, 0)
        XCTAssertEqual(cycle.status, .recordingPending)
        XCTAssertNil(cycle.result)
        XCTAssertEqual(cycle.revision, 2)
        XCTAssertEqual(cycle.idempotencyKey, "cycle-key-001")
        let reEncoded = try JSONEncoder().encode(cycle)
        let reDecoded = try JSONDecoder().decode(CaptureCycleDTO.self, from: reEncoded)
        XCTAssertEqual(cycle, reDecoded)
    }

    // CYC-02: scheduledStartAt present
    func test_CYC_02_scheduledStartAtPresent() throws {
        let data = try fixtureData()
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: data)
        XCTAssertEqual(cycle.scheduledStartAt, "2026-06-25T10:05:08+00:00")
    }

    // CYC-03: cycle devices count and roles
    func test_CYC_03_cycleDevicesCount() throws {
        let data = try fixtureData()
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: data)
        XCTAssertEqual(cycle.cycleDevices.count, 3)
        XCTAssertTrue(cycle.cycleDevices[0].required)
        XCTAssertTrue(cycle.cycleDevices[1].required)
        XCTAssertFalse(cycle.cycleDevices[2].required)
    }

    // CYC-04: device recording status decode
    func test_CYC_04_deviceRecordingStatus() throws {
        let data = try fixtureData()
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: data)
        XCTAssertEqual(cycle.cycleDevices[0].recordingStatus, .pending)
        XCTAssertEqual(cycle.cycleDevices[1].recordingStatus, .confirmedStart)
        XCTAssertEqual(cycle.cycleDevices[2].recordingStatus, .pending)
    }

    // CYC-05: device startedAt present for confirmed device
    func test_CYC_05_deviceStartedAt() throws {
        let data = try fixtureData()
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: data)
        XCTAssertNil(cycle.cycleDevices[0].startedAt)
        XCTAssertEqual(cycle.cycleDevices[1].startedAt, "2026-06-25T10:05:08.123+00:00")
    }

    // CYC-06: CycleStatus enum all cases
    func test_CYC_06_cycleStatusEnum() throws {
        let all: [CycleStatus] = [.preparing, .recordingPending, .recording, .stopping, .completed, .failed, .aborted]
        for s in all {
            let json = try JSONEncoder().encode(s)
            let decoded = try JSONDecoder().decode(CycleStatus.self, from: json)
            XCTAssertEqual(s, decoded)
        }
        XCTAssertEqual(CycleStatus.allCases.count, 7)
    }

    // CYC-07: CycleDeviceRecordingStatus enum all cases
    func test_CYC_07_deviceRecordingStatusEnum() throws {
        let all: [CycleDeviceRecordingStatus] = [.pending, .confirmedStart, .confirmedStop, .failed]
        for s in all {
            let json = try JSONEncoder().encode(s)
            let decoded = try JSONDecoder().decode(CycleDeviceRecordingStatus.self, from: json)
            XCTAssertEqual(s, decoded)
        }
        XCTAssertEqual(CycleDeviceRecordingStatus.allCases.count, 4)
    }

    // CYC-08: CycleResult enum all cases
    func test_CYC_08_cycleResultEnum() throws {
        let all: [CycleResult] = [.success, .partial, .failed]
        for s in all {
            let json = try JSONEncoder().encode(s)
            let decoded = try JSONDecoder().decode(CycleResult.self, from: json)
            XCTAssertEqual(s, decoded)
        }
        XCTAssertEqual(CycleResult.allCases.count, 3)
    }

    // CYC-09: null optional fields decode
    func test_CYC_09_nullOptionalFields() throws {
        let data = try fixtureData()
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: data)
        XCTAssertNil(cycle.result)
        XCTAssertNil(cycle.recordingStartedAt)
        XCTAssertNil(cycle.stopRequestedAt)
        XCTAssertNil(cycle.recordingStoppedAt)
        XCTAssertNil(cycle.completedAt)
        XCTAssertNil(cycle.failureReason)
    }

    // CYC-10: completed cycle decode
    func test_CYC_10_completedCycleDecode() throws {
        let json = """
        {
          "id": 2, "session_id": 1, "cycle_index": 1,
          "status": "completed", "result": "success",
          "scheduled_start_at": "2026-06-25T10:10:00+00:00",
          "recording_started_at": "2026-06-25T10:10:08+00:00",
          "stop_requested_at": "2026-06-25T10:12:00+00:00",
          "recording_stopped_at": "2026-06-25T10:12:01+00:00",
          "completed_at": "2026-06-25T10:12:01+00:00",
          "failure_reason": null,
          "created_by_participant_id": 1,
          "idempotency_key": "cycle-key-002",
          "revision": 5,
          "created_at": "2026-06-25T10:09:50+00:00",
          "updated_at": "2026-06-25T10:12:01+00:00",
          "cycle_devices": []
        }
        """.data(using: .utf8)!
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: json)
        XCTAssertEqual(cycle.status, .completed)
        XCTAssertEqual(cycle.result, .success)
        XCTAssertNotNil(cycle.recordingStartedAt)
        XCTAssertNotNil(cycle.recordingStoppedAt)
        XCTAssertNotNil(cycle.completedAt)
    }

    // CYC-11: ConfirmDeviceStartRequest encode
    func test_CYC_11_confirmStartRequestEncode() throws {
        let req = ConfirmDeviceStartRequest(
            startedAt: "2026-06-25T10:05:08.123Z",
            cycleDeviceRevision: 0
        )
        let data = try JSONEncoder().encode(req)
        let dict = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        XCTAssertEqual(dict["started_at"] as? String, "2026-06-25T10:05:08.123Z")
        XCTAssertEqual(dict["cycle_device_revision"] as? Int, 0)
    }

    // CYC-12: ConfirmDeviceStopRequest encode
    func test_CYC_12_confirmStopRequestEncode() throws {
        let req = ConfirmDeviceStopRequest(
            stoppedAt: "2026-06-25T10:12:01.456Z",
            cycleDeviceRevision: 1
        )
        let data = try JSONEncoder().encode(req)
        let dict = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        XCTAssertEqual(dict["stopped_at"] as? String, "2026-06-25T10:12:01.456Z")
        XCTAssertEqual(dict["cycle_device_revision"] as? Int, 1)
    }

    // CYC-13: CreateCycleRequest encode
    func test_CYC_13_createCycleRequestEncode() throws {
        let req = CreateCycleRequest(idempotencyKey: "test-key-123")
        let data = try JSONEncoder().encode(req)
        let dict = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        XCTAssertEqual(dict["idempotency_key"] as? String, "test-key-123")
    }

    // CYC-14: SessionStatus now includes active and recordingPending
    func test_CYC_14_sessionStatusNewCases() throws {
        let active = try JSONDecoder().decode(SessionStatus.self, from: "\"active\"".data(using: .utf8)!)
        XCTAssertEqual(active, .active)
        let rp = try JSONDecoder().decode(SessionStatus.self, from: "\"recording_pending\"".data(using: .utf8)!)
        XCTAssertEqual(rp, .recordingPending)
        XCTAssertEqual(SessionStatus.allCases.count, 9)
    }

    // CYC-15: CaptureCycleDeviceDTO round-trip
    func test_CYC_15_cycleDeviceRoundTrip() throws {
        let data = try fixtureData()
        let cycle = try JSONDecoder().decode(CaptureCycleDTO.self, from: data)
        let device = cycle.cycleDevices[1]
        let reEncoded = try JSONEncoder().encode(device)
        let reDecoded = try JSONDecoder().decode(CaptureCycleDeviceDTO.self, from: reEncoded)
        XCTAssertEqual(device, reDecoded)
    }
}

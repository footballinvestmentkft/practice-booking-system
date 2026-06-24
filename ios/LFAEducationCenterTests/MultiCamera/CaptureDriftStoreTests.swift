import XCTest
@testable import LFAEducationCenter

@MainActor
final class CaptureDriftStoreTests: XCTestCase {

    private var store: CaptureDriftStore!

    private static let uuid = "e2e-drift-test-session-0001"
    private let deviceA = 101   // iPhone (player)
    private let deviceB = 202   // iPad (instructor)

    override func setUp() async throws {
        try await super.setUp()
        // MockCaptureFileStore.capturesDirectory() returns tmp/mock_captures — create it so
        // file writes in exportJSON/exportCSV succeed (ensureDirectoryExists() is a no-op in mock).
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("mock_captures")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        store = CaptureDriftStore(fileStore: MockCaptureFileStore())
    }

    // MARK: — DRM-01: positive server offset (callback after scheduled time)

    func test_DRM_01_positive_server_offset() {
        let scheduled = Date(timeIntervalSince1970: 1_000_000.0)
        let ctx = makeContext(cycleIndex: 1, scheduled: scheduled, fire: scheduled)
        let record = CaptureDriftRecord.make(context: ctx,
                                              didStartRecordingAt: scheduled.addingTimeInterval(0.150),
                                              success: true)
        XCTAssertGreaterThan(record.serverOffsetMs, 0)
        XCTAssertEqual(record.serverOffsetMs, 150, accuracy: 0.5)
    }

    // MARK: — DRM-02: negative server offset (callback before scheduled time)

    func test_DRM_02_negative_server_offset() {
        let scheduled = Date(timeIntervalSince1970: 1_000_000.0)
        let ctx = makeContext(cycleIndex: 1, scheduled: scheduled, fire: scheduled)
        let record = CaptureDriftRecord.make(context: ctx,
                                              didStartRecordingAt: scheduled.addingTimeInterval(-0.080),
                                              success: true)
        XCTAssertLessThan(record.serverOffsetMs, 0)
        XCTAssertEqual(record.serverOffsetMs, -80, accuracy: 0.5)
    }

    // MARK: — DRM-03: callback after local fire → positive callbackDelayMs

    func test_DRM_03_callback_after_fire_positive_delay() {
        let fire = Date(timeIntervalSince1970: 1_000_000.0)
        let ctx = makeContext(cycleIndex: 1, scheduled: fire, fire: fire)
        let record = CaptureDriftRecord.make(context: ctx,
                                              didStartRecordingAt: fire.addingTimeInterval(0.045),
                                              success: true)
        XCTAssertGreaterThan(record.callbackDelayMs, 0)
        XCTAssertEqual(record.callbackDelayMs, 45, accuracy: 0.5)
    }

    // MARK: — DRM-04: callback before local fire → negative callbackDelayMs (early fire)

    func test_DRM_04_callback_before_fire_negative_delay() {
        let fire = Date(timeIntervalSince1970: 1_000_000.0)
        let ctx = makeContext(cycleIndex: 1, scheduled: fire, fire: fire)
        let record = CaptureDriftRecord.make(context: ctx,
                                              didStartRecordingAt: fire.addingTimeInterval(-0.010),
                                              success: true)
        XCTAssertLessThan(record.callbackDelayMs, 0)
    }

    // MARK: — DRM-05: exactly one record per cycle

    func test_DRM_05_one_record_per_cycle() {
        store.append(makeRecord(cycleIndex: 1))
        store.append(makeRecord(cycleIndex: 2))
        store.append(makeRecord(cycleIndex: 3))
        let recs = store.records(sessionUUID: Self.uuid, deviceId: deviceA)
        XCTAssertEqual(recs.count, 3)
        XCTAssertEqual(Set(recs.map { $0.cycleIndex }), [1, 2, 3])
    }

    // MARK: — DRM-06: duplicate callback → store faithfully appends (caller guard: hasMeasuredCurrentCycle)

    func test_DRM_06_duplicate_callback_guard_is_caller_responsibility() {
        // The store itself does not deduplicate — hasMeasuredCurrentCycle in SessionCaptureManager does.
        // This test documents the contract: two identical appends produce two records.
        store.append(makeRecord(cycleIndex: 1))
        store.append(makeRecord(cycleIndex: 1))
        XCTAssertEqual(store.records(sessionUUID: Self.uuid, deviceId: deviceA).count, 2)
    }

    // MARK: — DRM-07: failed measurement → summary reflects failure, stats nil

    func test_DRM_07_failed_measurement_reflected_in_summary() {
        let ctx = makeContext(cycleIndex: 1, scheduled: Date(), fire: Date())
        let record = CaptureDriftRecord.make(context: ctx, didStartRecordingAt: Date(),
                                              success: false, failureReason: "Timeout")
        store.append(record)
        let summary = store.summary(sessionUUID: Self.uuid, deviceId: deviceA)!
        XCTAssertEqual(summary.cycleCount, 1)
        XCTAssertEqual(summary.failureCount, 1)
        XCTAssertEqual(summary.successCount, 0)
        XCTAssertNil(summary.serverOffsetMs, "No stats for zero successful cycles")
    }

    // MARK: — DRM-08: two device records paired by session UUID + cycle index

    func test_DRM_08_pairwise_drift_pairing() {
        // Device A callback 50ms after scheduled
        store.append(makeRecord(cycleIndex: 1, deviceId: deviceA, offsetSec: 0.050))
        // Device B callback 120ms after scheduled
        store.append(makeRecord(cycleIndex: 1, deviceId: deviceB, offsetSec: 0.120))

        let drift = store.pairwiseDrift(sessionUUID: Self.uuid, deviceIdA: deviceA, deviceIdB: deviceB)
        XCTAssertNotNil(drift)
        // |50ms − 120ms| = 70ms
        XCTAssertEqual(drift!.avg, 70, accuracy: 1.0)
    }

    // MARK: — DRM-09: statistical aggregation over 10 cycles

    func test_DRM_09_statistical_aggregation_10_cycles() {
        let offsets = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        for (i, ms) in offsets.enumerated() {
            store.append(makeRecord(cycleIndex: i + 1, offsetSec: ms / 1000))
        }
        let summary = store.summary(sessionUUID: Self.uuid, deviceId: deviceA)!
        XCTAssertEqual(summary.cycleCount, 10)
        XCTAssertEqual(summary.successCount, 10)
        XCTAssertEqual(summary.serverOffsetMs!.avg, 55, accuracy: 0.1)
        XCTAssertEqual(summary.serverOffsetMs!.min, 10, accuracy: 0.1)
        XCTAssertEqual(summary.serverOffsetMs!.max, 100, accuracy: 0.1)
        XCTAssertEqual(summary.serverOffsetMs!.median, 55, accuracy: 0.1)
    }

    // MARK: — DRM-10: P95 correct calculation

    func test_DRM_10_p95_correct() {
        // 20 sorted values [1..20]; P95 index = ceil(0.95*20)-1 = 18 → value 19
        let values = (1...20).map { Double($0) }
        let block = StatBlock.compute(from: values)!
        XCTAssertEqual(block.p95, 19, accuracy: 0.01)
        XCTAssertEqual(block.avg, 10.5, accuracy: 0.01)
        XCTAssertEqual(block.median, 10.5, accuracy: 0.01)
        XCTAssertEqual(block.min, 1, accuracy: 0.01)
        XCTAssertEqual(block.max, 20, accuracy: 0.01)
    }

    // MARK: — DRM-11: JSON export schema

    func test_DRM_11_json_export_schema() throws {
        store.append(makeRecord(cycleIndex: 1))
        let url = try XCTUnwrap(store.exportJSON(sessionUUID: Self.uuid, deviceId: deviceA))
        let data = try Data(contentsOf: url)
        let decoded = try JSONDecoder().decode([CaptureDriftRecord].self, from: data)
        XCTAssertEqual(decoded.count, 1)
        XCTAssertEqual(decoded[0].sessionUUID, Self.uuid)
        XCTAssertEqual(decoded[0].deviceId, deviceA)
        XCTAssertTrue(decoded[0].didStartRecordingAtISO.contains("T"), "Must be ISO8601 with T separator")
    }

    // MARK: — DRM-12: CSV export schema

    func test_DRM_12_csv_export_schema() throws {
        store.append(makeRecord(cycleIndex: 1))
        let url = try XCTUnwrap(store.exportCSV(sessionUUID: Self.uuid, deviceId: deviceA))
        let csv = try String(contentsOf: url, encoding: .utf8)
        let lines = csv.components(separatedBy: "\n").filter { !$0.isEmpty }
        XCTAssertGreaterThanOrEqual(lines.count, 2)
        let header = lines[0]
        XCTAssertTrue(header.hasPrefix("session_uuid"), "First column must be session_uuid")
        XCTAssertTrue(header.contains("server_offset_ms"))
        XCTAssertTrue(header.contains("callback_delay_ms"))
        XCTAssertTrue(header.contains("capture_orientation"))
        XCTAssertTrue(lines[1].contains(Self.uuid))
    }

    // MARK: — Helpers

    private func makeContext(cycleIndex: Int,
                              scheduled: Date,
                              fire: Date,
                              deviceId: Int? = nil) -> DriftMeasurementContext {
        DriftMeasurementContext(
            sessionUUID: Self.uuid,
            cycleIndex: cycleIndex,
            deviceId: deviceId ?? self.deviceA,
            deviceType: "iphone",
            scheduledStartAt: scheduled,
            localFireAt: fire,
            serverOffsetEstimateSeconds: 0.001,
            clockQuality: "synchronized",
            captureOrientation: "portrait"
        )
    }

    private func makeRecord(cycleIndex: Int,
                             deviceId: Int? = nil,
                             offsetSec: TimeInterval = 0.050) -> CaptureDriftRecord {
        let scheduled = Date(timeIntervalSince1970: 1_000_000.0)
        let id = deviceId ?? self.deviceA
        let ctx = DriftMeasurementContext(
            sessionUUID: Self.uuid,
            cycleIndex: cycleIndex,
            deviceId: id,
            deviceType: id == self.deviceB ? "ipad" : "iphone",
            scheduledStartAt: scheduled,
            localFireAt: scheduled,
            serverOffsetEstimateSeconds: 0.001,
            clockQuality: "synchronized",
            captureOrientation: "portrait"
        )
        return CaptureDriftRecord.make(
            context: ctx,
            didStartRecordingAt: scheduled.addingTimeInterval(offsetSec),
            success: true
        )
    }
}

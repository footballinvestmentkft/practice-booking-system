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
                             offsetSec: TimeInterval = 0.050,
                             clockQuality: String = "synchronized") -> CaptureDriftRecord {
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
            clockQuality: clockQuality,
            captureOrientation: "portrait"
        )
        return CaptureDriftRecord.make(
            context: ctx,
            didStartRecordingAt: scheduled.addingTimeInterval(offsetSec),
            success: true
        )
    }
}

// MARK: — DST: Clock sync + pairing + error handling tests

@MainActor
final class ClockSyncAndPairingTests: XCTestCase {

    // MARK: — DST-PAIR-01: Two device records successfully paired by cycle index

    func test_DST_PAIR_01_twoDeviceRecordsPairedByCycleIndex() {
        let store = CaptureDriftStore(fileStore: MockCaptureFileStore())
        let scheduled = Date(timeIntervalSince1970: 1_000_000.0)
        let ctxA = DriftMeasurementContext(sessionUUID: "s1", cycleIndex: 1, deviceId: 10,
            deviceType: "iphone", scheduledStartAt: scheduled, localFireAt: scheduled,
            serverOffsetEstimateSeconds: 0.0, clockQuality: "synchronized", captureOrientation: "portrait")
        let ctxB = DriftMeasurementContext(sessionUUID: "s1", cycleIndex: 1, deviceId: 20,
            deviceType: "ipad", scheduledStartAt: scheduled, localFireAt: scheduled,
            serverOffsetEstimateSeconds: 0.0, clockQuality: "synchronized", captureOrientation: "landscapeRight")
        store.append(CaptureDriftRecord.make(context: ctxA, didStartRecordingAt: scheduled.addingTimeInterval(0.050), success: true))
        store.append(CaptureDriftRecord.make(context: ctxB, didStartRecordingAt: scheduled.addingTimeInterval(0.072), success: true))

        let drift = store.pairwiseDrift(sessionUUID: "s1", deviceIdA: 10, deviceIdB: 20)
        XCTAssertNotNil(drift, "must pair records with matching session+cycle")
        XCTAssertEqual(drift!.avg, 22, accuracy: 1.0, "|72ms - 50ms| = 22ms pairwise drift")
    }

    // MARK: — DST-PAIR-02: Pairwise drift calculation — known 22ms fixture

    func test_DST_PAIR_02_pairwiseDrift_known22ms() {
        let store = CaptureDriftStore(fileStore: MockCaptureFileStore())
        let base = Date(timeIntervalSince1970: 1_719_215_692.624)
        let ctxA = DriftMeasurementContext(sessionUUID: "fb1a", cycleIndex: 1, deviceId: 57,
            deviceType: "ipad", scheduledStartAt: base, localFireAt: base,
            serverOffsetEstimateSeconds: 0.0, clockQuality: "degradedMissingServerDate", captureOrientation: "landscapeRight")
        let ctxB = DriftMeasurementContext(sessionUUID: "fb1a", cycleIndex: 1, deviceId: 58,
            deviceType: "iphone", scheduledStartAt: base, localFireAt: base,
            serverOffsetEstimateSeconds: 0.0, clockQuality: "degradedMissingServerDate", captureOrientation: "portrait")
        store.append(CaptureDriftRecord.make(context: ctxA, didStartRecordingAt: base.addingTimeInterval(0.293), success: true))
        store.append(CaptureDriftRecord.make(context: ctxB, didStartRecordingAt: base.addingTimeInterval(0.315), success: true))

        let drift = store.pairwiseDrift(sessionUUID: "fb1a", deviceIdA: 57, deviceIdB: 58)!
        XCTAssertEqual(drift.avg, 22, accuracy: 1.0)
    }

    // MARK: — DST-MISS-03: Missing iPad record → StatBlock nil

    func test_DST_MISS_03_missingDeviceB_pairwiseNil() {
        let store = CaptureDriftStore(fileStore: MockCaptureFileStore())
        let ctx = DriftMeasurementContext(sessionUUID: "s1", cycleIndex: 1, deviceId: 10,
            deviceType: "iphone", scheduledStartAt: Date(), localFireAt: Date(),
            serverOffsetEstimateSeconds: 0.0, clockQuality: "synchronized", captureOrientation: "portrait")
        store.append(CaptureDriftRecord.make(context: ctx, didStartRecordingAt: Date(), success: true))

        let drift = store.pairwiseDrift(sessionUUID: "s1", deviceIdA: 10, deviceIdB: 20)
        XCTAssertNil(drift, "must return nil when one device has no records")
    }

    // MARK: — DST-DUP-04: Duplicate cycle record → Dictionary kezelés

    func test_DST_DUP_04_duplicateCycleHandling() {
        let store = CaptureDriftStore(fileStore: MockCaptureFileStore())
        let t = Date(timeIntervalSince1970: 1_000_000.0)
        let ctx1 = DriftMeasurementContext(sessionUUID: "s1", cycleIndex: 1, deviceId: 10,
            deviceType: "iphone", scheduledStartAt: t, localFireAt: t,
            serverOffsetEstimateSeconds: 0.0, clockQuality: "synchronized", captureOrientation: "portrait")
        store.append(CaptureDriftRecord.make(context: ctx1, didStartRecordingAt: t.addingTimeInterval(0.050), success: true))
        store.append(CaptureDriftRecord.make(context: ctx1, didStartRecordingAt: t.addingTimeInterval(0.060), success: true))

        let recs = store.records(sessionUUID: "s1", deviceId: 10)
        XCTAssertEqual(recs.count, 2, "store faithfully appends duplicates")
    }

    // MARK: — DST-MISMATCH-05: Session/cycle mismatch → empty common set

    func test_DST_MISMATCH_05_differentSession_noPairing() {
        let store = CaptureDriftStore(fileStore: MockCaptureFileStore())
        let t = Date()
        let ctxA = DriftMeasurementContext(sessionUUID: "session-A", cycleIndex: 1, deviceId: 10,
            deviceType: "iphone", scheduledStartAt: t, localFireAt: t,
            serverOffsetEstimateSeconds: 0.0, clockQuality: "synchronized", captureOrientation: "portrait")
        let ctxB = DriftMeasurementContext(sessionUUID: "session-B", cycleIndex: 1, deviceId: 20,
            deviceType: "ipad", scheduledStartAt: t, localFireAt: t,
            serverOffsetEstimateSeconds: 0.0, clockQuality: "synchronized", captureOrientation: "landscapeRight")
        store.append(CaptureDriftRecord.make(context: ctxA, didStartRecordingAt: t, success: true))
        store.append(CaptureDriftRecord.make(context: ctxB, didStartRecordingAt: t, success: true))

        let drift = store.pairwiseDrift(sessionUUID: "session-A", deviceIdA: 10, deviceIdB: 20)
        XCTAssertNil(drift, "different session UUIDs → no common records for deviceB")
    }

    // MARK: — DST-EXPORT-06: JSON + CSV export content

    func test_DST_EXPORT_06_jsonAndCsvExportContent() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("mock_captures")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let store = CaptureDriftStore(fileStore: MockCaptureFileStore())
        let t = Date(timeIntervalSince1970: 1_000_000.0)
        let ctx = DriftMeasurementContext(sessionUUID: "exp1", cycleIndex: 1, deviceId: 10,
            deviceType: "iphone", scheduledStartAt: t, localFireAt: t,
            serverOffsetEstimateSeconds: 0.005, clockQuality: "synchronized", captureOrientation: "portrait")
        store.append(CaptureDriftRecord.make(context: ctx, didStartRecordingAt: t.addingTimeInterval(0.050), success: true))

        let jsonURL = try XCTUnwrap(store.exportJSON(sessionUUID: "exp1", deviceId: 10))
        let jsonData = try Data(contentsOf: jsonURL)
        let decoded = try JSONDecoder().decode([CaptureDriftRecord].self, from: jsonData)
        XCTAssertEqual(decoded.count, 1)
        XCTAssertEqual(decoded[0].sessionUUID, "exp1")
        XCTAssertEqual(decoded[0].serverOffsetMs, 50, accuracy: 1.0)

        let csvURL = try XCTUnwrap(store.exportCSV(sessionUUID: "exp1", deviceId: 10))
        let csv = try String(contentsOf: csvURL, encoding: .utf8)
        XCTAssertTrue(csv.contains("exp1"))
        XCTAssertTrue(csv.contains("iphone"))
    }

    // MARK: — DST-CLOCK-07: Clock offset positive (+500ms) and negative (-300ms)

    func test_DST_CLOCK_07_positiveAndNegativeClockOffset() {
        let clock = ScheduledCaptureClockManager()
        let now = Date()
        // Positive offset: server is 500ms ahead of local
        for _ in 0..<3 {
            clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: now.addingTimeInterval(0.5))
        }
        XCTAssertGreaterThan(clock.currentOffset.offsetSeconds, 0.4)
        XCTAssertEqual(clock.currentOffset.quality, .synchronized)

        clock.reset()

        // Negative offset: server is 300ms behind local
        for _ in 0..<3 {
            clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: now.addingTimeInterval(-0.35))
        }
        XCTAssertLessThan(clock.currentOffset.offsetSeconds, -0.2)
        XCTAssertEqual(clock.currentOffset.quality, .synchronized)
    }

    // MARK: — DST-DEGRAD-08: Degraded clock quality

    func test_DST_DEGRAD_08_degradedClockQuality() {
        let clock = ScheduledCaptureClockManager()
        // No samples → degradedMissingServerDate
        XCTAssertEqual(clock.currentOffset.quality, .degradedMissingServerDate)

        // 1 sample → still degraded (need 3)
        clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: Date())
        XCTAssertEqual(clock.currentOffset.quality, .degradedMissingServerDate)
        XCTAssertEqual(clock.sampleCount, 1)

        // 2 samples → still degraded
        clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: Date())
        XCTAssertEqual(clock.currentOffset.quality, .degradedMissingServerDate)

        // 3 samples → synchronized
        clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: Date())
        XCTAssertEqual(clock.currentOffset.quality, .synchronized)
        XCTAssertEqual(clock.sampleCount, 3)
    }

    // MARK: — DST-UNIQUE-09: Exactly 1 record per device/cycle combination

    func test_DST_UNIQUE_09_oneRecordPerDeviceCycle() {
        let store = CaptureDriftStore(fileStore: MockCaptureFileStore())
        let t = Date(timeIntervalSince1970: 1_000_000.0)
        for cycle in 1...5 {
            for devId in [10, 20] {
                let ctx = DriftMeasurementContext(sessionUUID: "s1", cycleIndex: cycle, deviceId: devId,
                    deviceType: devId == 10 ? "iphone" : "ipad", scheduledStartAt: t, localFireAt: t,
                    serverOffsetEstimateSeconds: 0.0, clockQuality: "synchronized", captureOrientation: "portrait")
                store.append(CaptureDriftRecord.make(context: ctx, didStartRecordingAt: t.addingTimeInterval(0.050), success: true))
            }
        }
        let iPhoneRecs = store.records(sessionUUID: "s1", deviceId: 10)
        let iPadRecs = store.records(sessionUUID: "s1", deviceId: 20)
        XCTAssertEqual(iPhoneRecs.count, 5)
        XCTAssertEqual(iPadRecs.count, 5)
        XCTAssertEqual(Set(iPhoneRecs.map { $0.cycleIndex }), Set(1...5))
    }

    // MARK: — DST-CLOCK-10: High RTT rejected, doesn't corrupt offset

    func test_DST_CLOCK_10_highRTTRejected() {
        let clock = ScheduledCaptureClockManager()
        let now = Date()
        for _ in 0..<3 {
            clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: now)
        }
        XCTAssertEqual(clock.currentOffset.quality, .synchronized)
        let goodOffset = clock.currentOffset.offsetSeconds

        // High RTT sample should be rejected
        clock.updateFromPolling(requestDuration: 5.0, serverDateHeader: now.addingTimeInterval(10))
        XCTAssertEqual(clock.sampleCount, 3, "high RTT sample must not be added")
        XCTAssertEqual(clock.currentOffset.offsetSeconds, goodOffset, accuracy: 0.001)
    }

    // MARK: — DST-CLOCK-11: Missing Date header → no-op

    func test_DST_CLOCK_11_missingDateHeader_noOp() {
        let clock = ScheduledCaptureClockManager()
        for _ in 0..<3 {
            clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: Date())
        }
        XCTAssertEqual(clock.currentOffset.quality, .synchronized)
        let offset = clock.currentOffset.offsetSeconds

        // nil Date header → no change
        clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: nil)
        XCTAssertEqual(clock.sampleCount, 3, "nil Date must not affect samples")
        XCTAssertEqual(clock.currentOffset.offsetSeconds, offset, accuracy: 0.001)
    }

    // MARK: — DST-CLOCK-12: Multiple samples stabilize (median robust to outlier)

    func test_DST_CLOCK_12_medianRobustToOutlier() {
        let clock = ScheduledCaptureClockManager()
        let now = Date()
        // 4 good samples with server ~100ms ahead
        for _ in 0..<4 {
            clock.updateFromPolling(requestDuration: 0.05, serverDateHeader: now.addingTimeInterval(0.125))
        }
        let stableOffset = clock.currentOffset.offsetSeconds

        // 1 bad sample with server apparently 5 seconds ahead (within RTT limit but outlier)
        clock.updateFromPolling(requestDuration: 0.1, serverDateHeader: now.addingTimeInterval(5.15))

        // Median of [0.1, 0.1, 0.1, 0.1, 5.1] = 0.1 — outlier doesn't move median
        XCTAssertEqual(clock.currentOffset.offsetSeconds, stableOffset, accuracy: 0.05,
                       "median must be robust to single outlier")
    }

    // MARK: — DST-HTTP-13: HTTP Date header RFC parse

    func test_DST_HTTP_13_parseHTTPDate() {
        let valid = MultiCameraAPIClient.parseHTTPDate("Wed, 24 Jun 2026 08:03:16 GMT")
        XCTAssertNotNil(valid, "standard RFC 7231 Date header must parse")

        let missing = MultiCameraAPIClient.parseHTTPDate(nil)
        XCTAssertNil(missing, "nil → nil")

        let invalid = MultiCameraAPIClient.parseHTTPDate("not-a-date")
        XCTAssertNil(invalid, "invalid string → nil")

        let empty = MultiCameraAPIClient.parseHTTPDate("")
        XCTAssertNil(empty, "empty string → nil")
    }
}


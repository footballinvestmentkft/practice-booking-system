import Foundation
import AVFoundation

#if DEBUG

// MARK: — Context assembled at fire time, passed to startCapture

struct DriftMeasurementContext {
    let sessionUUID: String
    let cycleIndex: Int
    let deviceId: Int
    let deviceType: String          // "iphone" / "ipad"
    let scheduledStartAt: Date      // server UTC scheduled_start_at
    let localFireAt: Date           // clock-corrected local fire date
    let serverOffsetEstimateSeconds: Double  // ClockOffset.offsetSeconds at fire time
    let clockQuality: String        // ClockSyncQuality.rawValue
    let captureOrientation: String  // AVCaptureVideoOrientation.name
}

// MARK: — Single-cycle drift record (one per device per capture cycle)

struct CaptureDriftRecord: Codable, Equatable {
    // Identity
    let sessionUUID: String
    let cycleIndex: Int
    let deviceId: Int
    let deviceType: String

    // Timestamps (ISO8601 with fractional seconds for cross-device string comparison)
    let scheduledStartAtISO: String       // server UTC scheduled_start_at
    let localFireAtISO: String            // clock-corrected local fire date
    let didStartRecordingAtISO: String    // AVCaptureFileOutput.didStartRecordingTo callback time

    // Derived metrics (ms)
    let serverOffsetEstimateMs: Double    // ClockOffset.offsetSeconds * 1000
    let serverOffsetMs: Double            // (didStartRecordingAt − scheduledStartAt) * 1000
    let callbackDelayMs: Double           // (didStartRecordingAt − localFireAt) * 1000

    // Context
    let captureOrientation: String
    let clockQuality: String
    let success: Bool
    let failureReason: String?

    enum CodingKeys: String, CodingKey {
        case sessionUUID = "session_uuid"
        case cycleIndex = "cycle_index"
        case deviceId = "device_id"
        case deviceType = "device_type"
        case scheduledStartAtISO = "scheduled_start_at"
        case localFireAtISO = "local_fire_at"
        case didStartRecordingAtISO = "did_start_recording_at"
        case serverOffsetEstimateMs = "server_offset_estimate_ms"
        case serverOffsetMs = "server_offset_ms"
        case callbackDelayMs = "callback_delay_ms"
        case captureOrientation = "capture_orientation"
        case clockQuality = "clock_quality"
        case success
        case failureReason = "failure_reason"
    }

    // Factory — called on @MainActor after didStartRecordingTo delivers callbackTime
    static func make(context: DriftMeasurementContext,
                     didStartRecordingAt callbackTime: Date,
                     success: Bool,
                     failureReason: String? = nil) -> CaptureDriftRecord {
        let fmt = isoFormatter
        return CaptureDriftRecord(
            sessionUUID: context.sessionUUID,
            cycleIndex: context.cycleIndex,
            deviceId: context.deviceId,
            deviceType: context.deviceType,
            scheduledStartAtISO: fmt.string(from: context.scheduledStartAt),
            localFireAtISO: fmt.string(from: context.localFireAt),
            didStartRecordingAtISO: fmt.string(from: callbackTime),
            serverOffsetEstimateMs: context.serverOffsetEstimateSeconds * 1000,
            serverOffsetMs: callbackTime.timeIntervalSince(context.scheduledStartAt) * 1000,
            callbackDelayMs: callbackTime.timeIntervalSince(context.localFireAt) * 1000,
            captureOrientation: context.captureOrientation,
            clockQuality: context.clockQuality,
            success: success,
            failureReason: failureReason
        )
    }

    // MARK: — CSV

    static let csvHeader = [
        "session_uuid", "cycle_index", "device_id", "device_type",
        "scheduled_start_at", "local_fire_at", "did_start_recording_at",
        "server_offset_estimate_ms", "server_offset_ms", "callback_delay_ms",
        "capture_orientation", "clock_quality", "success", "failure_reason"
    ].joined(separator: ",")

    var csvRow: String {
        [sessionUUID, "\(cycleIndex)", "\(deviceId)", deviceType,
         scheduledStartAtISO, localFireAtISO, didStartRecordingAtISO,
         String(format: "%.3f", serverOffsetEstimateMs),
         String(format: "%.3f", serverOffsetMs),
         String(format: "%.3f", callbackDelayMs),
         captureOrientation, clockQuality,
         success ? "true" : "false",
         failureReason.map { "\"\($0)\"" } ?? ""
        ].joined(separator: ",")
    }
}

// MARK: — Statistical block (used in summary)

struct StatBlock: Codable, Equatable {
    let avg: Double
    let median: Double
    let min: Double
    let max: Double
    let stddev: Double
    let p95: Double

    static func compute(from values: [Double]) -> StatBlock? {
        guard !values.isEmpty else { return nil }
        let n = values.count
        let sorted = values.sorted()
        let avg = values.reduce(0, +) / Double(n)
        let median: Double = n % 2 == 0
            ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
            : sorted[n / 2]
        let variance = values.map { ($0 - avg) * ($0 - avg) }.reduce(0, +) / Double(n)
        let p95Idx = Swift.max(0, Int(ceil(0.95 * Double(n))) - 1)
        return StatBlock(
            avg: avg, median: median,
            min: sorted.first!, max: sorted.last!,
            stddev: variance.squareRoot(),
            p95: sorted[Swift.min(p95Idx, n - 1)]
        )
    }
}

// MARK: — 10-cycle aggregated summary

struct CaptureDriftSummary: Codable {
    let sessionUUID: String
    let deviceId: Int
    let deviceType: String
    let cycleCount: Int
    let successCount: Int
    let failureCount: Int
    let serverOffsetMs: StatBlock?
    let callbackDelayMs: StatBlock?
    var pairwiseDriftMs: StatBlock?   // populated externally after cross-device pairing

    enum CodingKeys: String, CodingKey {
        case sessionUUID = "session_uuid"
        case deviceId = "device_id"
        case deviceType = "device_type"
        case cycleCount = "cycle_count"
        case successCount = "success_count"
        case failureCount = "failure_count"
        case serverOffsetMs = "server_offset_ms"
        case callbackDelayMs = "callback_delay_ms"
        case pairwiseDriftMs = "pairwise_drift_ms"
    }
}

// MARK: — Shared ISO8601 formatter

let isoFormatter: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()

#endif

import Foundation

/// Explicit per-panel status for the instructor status dashboard (MC2-PR3).
///
/// The dashboard is backend-polling only: every panel state is a pure function
/// of the polled session/cycle DTOs and the current time. No live frames, no
/// MPC, no on-device inference — the resolver must stay side-effect free so
/// two simultaneous players can never be cross-attributed (each panel is keyed
/// by session_device_id and reads only that device's rows).
enum DevicePanelState: Equatable {
    case connected
    case stale(heartbeatAge: TimeInterval)
    case disconnected
    case recording
    case completed
    case failed(reason: String?)

    var label: String {
        switch self {
        case .connected:            return "connected"
        case .stale(let age):       return "stale \(Int(age))s"
        case .disconnected:         return "disconnected"
        case .recording:            return "recording"
        case .completed:            return "completed"
        case .failed(let reason):   return "failed\(reason.map { ": \($0.prefix(24))" } ?? "")"
        }
    }
}

enum DevicePanelStateResolver {

    /// Heartbeat older than this (but younger than `disconnectedAfter`) → `.stale`.
    /// 12 s = 2+ missed 5 s heartbeats; transient network hiccups don't flap the panel.
    static let staleAfter: TimeInterval = 12
    /// Heartbeat older than this → `.disconnected`.
    static let disconnectedAfter: TimeInterval = 30

    /// Resolve one panel's state from the polled backend rows.
    ///
    /// Precedence: cycle failure > backend error/disconnect > active recording >
    /// completed cycle > heartbeat freshness. A device that failed its cycle stays
    /// `.failed` even while its heartbeat is healthy — the operator must see it.
    ///
    /// - Parameters:
    ///   - device: the session_device row (identity + status + heartbeat)
    ///   - cycleDevice: this device's row in `cycle` (matched by session_device_id
    ///     by the caller; nil when there is no cycle yet or the device isn't in it)
    ///   - cycle: the latest capture cycle, if any
    ///   - now: injected for testability
    static func resolve(
        device: SessionDeviceDTO,
        cycleDevice: CaptureCycleDeviceDTO?,
        cycle: CaptureCycleDTO?,
        now: Date
    ) -> DevicePanelState {
        if let cd = cycleDevice, cd.recordingStatus == .failed {
            return .failed(reason: cd.failureReason)
        }
        if device.status == .error {
            return .failed(reason: nil)
        }
        if device.status == .disconnected {
            return .disconnected
        }
        if let cd = cycleDevice, let cycle = cycle {
            let cycleActive: Bool = {
                switch cycle.status {
                case .preparing, .recordingPending, .recording, .stopping: return true
                case .completed, .failed, .aborted: return false
                }
            }()
            if cycleActive && cd.recordingStatus == .confirmedStart {
                return .recording
            }
            if !cycleActive && cd.recordingStatus == .confirmedStop {
                return .completed
            }
        }
        // Auxiliary cameras (GoPro) have no app heartbeat — their manager device
        // confirms cycles and signals status; heartbeat staleness does not apply.
        if device.deviceRole == .auxiliaryCamera {
            switch device.status {
            case .ready, .recording, .stopped: return .connected
            case .registered:                  return .stale(heartbeatAge: 0)
            case .disconnected:                return .disconnected
            case .error:                       return .failed(reason: nil)
            }
        }
        guard let age = heartbeatAge(device: device, now: now) else {
            // No heartbeat AND no registration timestamp parse — can't judge freshness.
            return .stale(heartbeatAge: 0)
        }
        if age > disconnectedAfter { return .disconnected }
        if age > staleAfter { return .stale(heartbeatAge: age) }
        return .connected
    }

    /// Age of the freshest liveness signal. Falls back to `registered_at` so a
    /// device that registered moments ago (first heartbeat not yet sent) shows
    /// `.connected` instead of flashing `.disconnected`.
    static func heartbeatAge(device: SessionDeviceDTO, now: Date) -> TimeInterval? {
        let reference = device.lastHeartbeat.flatMap(parseISO8601) ?? parseISO8601(device.registeredAt)
        guard let ref = reference else { return nil }
        return max(0, now.timeIntervalSince(ref))
    }

    /// Backend emits fractional-second ISO8601 (`2026-07-12T17:25:17.496000Z`);
    /// plain ISO8601DateFormatter rejects fractions, so try both variants.
    static func parseISO8601(_ s: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = fractional.date(from: s) { return d }
        let plain = ISO8601DateFormatter()
        plain.formatOptions = [.withInternetDateTime]
        if let d = plain.date(from: s) { return d }
        // Backend may omit the timezone suffix entirely (naive UTC) — normalize.
        return fractional.date(from: s + "Z") ?? plain.date(from: s + "Z")
    }
}

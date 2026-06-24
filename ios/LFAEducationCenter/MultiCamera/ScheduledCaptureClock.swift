import Foundation

// MARK: — Clock offset

enum ClockSyncQuality: String {
    case synchronized
    case degradedMissingServerDate
    case degradedHighRTT
}

struct ClockOffset {
    let offsetSeconds: Double
    let uncertaintySeconds: Double
    let quality: ClockSyncQuality

    func localFireDate(for serverTimestamp: Date) -> Date {
        serverTimestamp.addingTimeInterval(-offsetSeconds)
    }

    static let zero = ClockOffset(offsetSeconds: 0, uncertaintySeconds: 0, quality: .degradedMissingServerDate)
}

// MARK: — Timer protocol

protocol Cancellable { func cancel() }
extension DispatchWorkItem: Cancellable {}

protocol OrchestrationTimerProvider {
    func scheduleTimer(fireAt: Date, handler: @escaping () -> Void) -> Cancellable
}

final class SystemOrchestrationTimer: OrchestrationTimerProvider {
    private let timerQueue = DispatchQueue(label: "com.lfa.multicamera.timer", qos: .userInteractive)

    func scheduleTimer(fireAt: Date, handler: @escaping () -> Void) -> Cancellable {
        let delay = max(0, fireAt.timeIntervalSinceNow)
        let item = DispatchWorkItem { handler() }
        timerQueue.asyncAfter(deadline: .now() + delay, execute: item)
        return item
    }
}

// MARK: — Clock manager

@MainActor
final class ScheduledCaptureClockManager: ObservableObject {
    @Published private(set) var currentOffset: ClockOffset = .zero

    private var samples: [Double] = []
    private(set) var rejectedSampleCount: Int = 0
    private static let maxSamples = 8
    private static let minSamplesForSync = 3
    private static let maxRTTSeconds: TimeInterval = 2.0
    // HTTP Date header has 1-second precision. CDN edge timing + cold starts can
    // cause offsets of several seconds even when both clocks are NTP-accurate.
    // Reject any sample where |offset| > 1s — NTP-synchronized devices should be
    // within ~100ms, so a 1s offset is certainly a measurement artifact.
    private static let maxAbsoluteOffsetSeconds: Double = 1.0

    var sampleCount: Int { samples.count }

    func updateFromPolling(requestDuration: TimeInterval, serverDateHeader: Date?) {
        guard let serverDate = serverDateHeader else { return }
        let rtt = requestDuration
        guard rtt <= Self.maxRTTSeconds else {
            if samples.isEmpty {
                currentOffset = ClockOffset(
                    offsetSeconds: 0, uncertaintySeconds: rtt / 2, quality: .degradedHighRTT
                )
            }
            return
        }
        let estimatedServerNow = serverDate.timeIntervalSince1970 + rtt / 2
        let localNow = Date().timeIntervalSince1970
        let rawOffset = estimatedServerNow - localNow

        guard abs(rawOffset) <= Self.maxAbsoluteOffsetSeconds else {
            rejectedSampleCount += 1
            return
        }

        samples.append(rawOffset)
        if samples.count > Self.maxSamples { samples.removeFirst() }

        let sorted = samples.sorted()
        let n = sorted.count
        let median = n % 2 == 0
            ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
            : sorted[n / 2]

        let quality: ClockSyncQuality = n >= Self.minSamplesForSync
            ? .synchronized : .degradedMissingServerDate

        currentOffset = ClockOffset(
            offsetSeconds: median,
            uncertaintySeconds: rtt / 2,
            quality: quality
        )
    }

    func localFireDate(for serverTimestamp: Date) -> Date {
        currentOffset.localFireDate(for: serverTimestamp)
    }

    func reset() {
        samples = []
        currentOffset = .zero
    }
}

import Foundation

#if DEBUG

@MainActor
final class CaptureDriftStore: ObservableObject {

    static let shared = CaptureDriftStore()

    @Published private(set) var records: [CaptureDriftRecord] = []

    private let fileStore: CaptureFileStore

    init(fileStore: CaptureFileStore = SystemCaptureFileStore()) {
        self.fileStore = fileStore
    }

    // MARK: — Append (idempotency enforced by caller via hasMeasuredCurrentCycle)

    func append(_ record: CaptureDriftRecord) {
        records.append(record)
        persistJSON(for: record)
    }

    // MARK: — Query

    func records(sessionUUID: String, deviceId: Int) -> [CaptureDriftRecord] {
        records.filter { $0.sessionUUID == sessionUUID && $0.deviceId == deviceId }
    }

    // MARK: — Aggregation

    func summary(sessionUUID: String, deviceId: Int) -> CaptureDriftSummary? {
        let matching = records(sessionUUID: sessionUUID, deviceId: deviceId)
        guard !matching.isEmpty else { return nil }
        let successful = matching.filter { $0.success }
        return CaptureDriftSummary(
            sessionUUID: sessionUUID,
            deviceId: deviceId,
            deviceType: matching.first?.deviceType ?? "unknown",
            cycleCount: matching.count,
            successCount: successful.count,
            failureCount: matching.count - successful.count,
            serverOffsetMs: StatBlock.compute(from: successful.map { $0.serverOffsetMs }),
            callbackDelayMs: StatBlock.compute(from: successful.map { $0.callbackDelayMs }),
            pairwiseDriftMs: nil
        )
    }

    // pairwise_drift_ms = |didStartRecordingAt_A − didStartRecordingAt_B| per matching cycle
    func pairwiseDrift(sessionUUID: String, deviceIdA: Int, deviceIdB: Int) -> StatBlock? {
        let a = records(sessionUUID: sessionUUID, deviceId: deviceIdA).filter { $0.success }
        let b = records(sessionUUID: sessionUUID, deviceId: deviceIdB).filter { $0.success }
        let aMap = Dictionary(uniqueKeysWithValues: a.map { ($0.cycleIndex, $0) })
        let bMap = Dictionary(uniqueKeysWithValues: b.map { ($0.cycleIndex, $0) })
        let common = Set(aMap.keys).intersection(Set(bMap.keys))
        guard !common.isEmpty else { return nil }
        let drifts: [Double] = common.compactMap { idx in
            guard let ra = aMap[idx], let rb = bMap[idx],
                  let ta = isoFormatter.date(from: ra.didStartRecordingAtISO),
                  let tb = isoFormatter.date(from: rb.didStartRecordingAtISO) else { return nil }
            return abs(ta.timeIntervalSince(tb)) * 1000
        }
        return StatBlock.compute(from: drifts)
    }

    // MARK: — Export

    @discardableResult
    func exportJSON(sessionUUID: String, deviceId: Int) -> URL? {
        let matching = records(sessionUUID: sessionUUID, deviceId: deviceId)
        guard !matching.isEmpty else { return nil }
        let url = exportURL(sessionUUID: sessionUUID, deviceId: deviceId, suffix: ".json")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(matching) else { return nil }
        try? data.write(to: url, options: .atomic)
        return url
    }

    @discardableResult
    func exportCSV(sessionUUID: String, deviceId: Int) -> URL? {
        let matching = records(sessionUUID: sessionUUID, deviceId: deviceId)
        guard !matching.isEmpty else { return nil }
        let url = exportURL(sessionUUID: sessionUUID, deviceId: deviceId, suffix: ".csv")
        let lines = ([CaptureDriftRecord.csvHeader] + matching.map { $0.csvRow }).joined(separator: "\n")
        try? lines.data(using: .utf8)?.write(to: url, options: .atomic)
        return url
    }

    @discardableResult
    func exportSummaryJSON(sessionUUID: String, deviceId: Int) -> URL? {
        guard var s = summary(sessionUUID: sessionUUID, deviceId: deviceId) else { return nil }
        let url = exportURL(sessionUUID: sessionUUID, deviceId: deviceId, suffix: "_summary.json")
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(s) else { return nil }
        try? data.write(to: url, options: .atomic)
        return url
    }

    // MARK: — Reset (between sessions / tests)

    func reset() { records = [] }

    // MARK: — Private

    private func persistJSON(for record: CaptureDriftRecord) {
        try? fileStore.ensureDirectoryExists()
        exportJSON(sessionUUID: record.sessionUUID, deviceId: record.deviceId)
    }

    private func exportURL(sessionUUID: String, deviceId: Int, suffix: String) -> URL {
        fileStore.capturesDirectory()
            .appendingPathComponent("drift_session_\(sessionUUID)_device_\(deviceId)\(suffix)")
    }
}

#endif

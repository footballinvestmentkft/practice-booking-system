import Foundation

enum CycleIdempotencyKey {
    // Format: "<devUUID.prefix(8)>:<sessionUuid.prefix(8)>:c<cycleIndex>"
    // Max ~22 chars, well under 64 char backend limit
    // Stable: same (device, session, cycleIndex) → same key → idempotent retry
    static func make(sessionUuid: String, cycleIndex: Int) -> String {
        let deviceUUID = DeviceIdentity.stableDeviceUUID()
        let devPrefix  = String(deviceUUID.replacingOccurrences(of: "-", with: "").prefix(8))
        let sessPrefix = String(sessionUuid.replacingOccurrences(of: "-", with: "").prefix(8))
        return "\(devPrefix):\(sessPrefix):c\(cycleIndex)"
    }
}

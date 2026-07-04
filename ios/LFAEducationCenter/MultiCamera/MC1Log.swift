import Foundation
import os

// MARK: — MC1Log
//
// Physical-evidence logging for the MC1 multicamera stack.
//
// The 2026-07-04 tricamera physical run proved that `print()` output is
// INVISIBLE to idevicesyslog on physical devices: the 78k-line iPhone console
// capture contained ZERO app-tagged lines ([PCO]/[CCO]/[MC1-AUTO]/[GOPRO-AUTO])
// even though the code printed them — print() goes to stdout, which the syslog
// relay never sees. Every console-grounded evidence extraction in the
// regression harness (ConsoleOffsetTracker.extract_tagged_lines, debug
// snapshots, GOPRO-MEDIA markers) was silently blind.
//
// os_log entries at .default level DO appear in idevicesyslog (the same
// capture was full of CFNetwork/UIKitCore os_log lines). The `%{public}@`
// format is load-bearing: without it, iOS redacts the interpolated message to
// `<private>` on physical devices, which is exactly as useless as no line.
enum MC1Log {
    private static let osLog = OSLog(subsystem: "com.lfa.educationcenter.mc1", category: "MC1")

    /// Drop-in replacement for `print()` in the MC1 evidence path. Message text
    /// (including the leading `[TAG]`) must stay byte-identical to what the
    /// harness greps for.
    static func notice(_ message: String) {
        os_log("%{public}@", log: osLog, type: .default, message)
    }
}

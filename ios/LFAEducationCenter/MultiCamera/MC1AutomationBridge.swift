import Foundation
import Combine

// MARK: — MC1 physical-test automation bridge (DEBUG-only, MC1-AUTO-1)
//
// Lets an external script drive the Session Lab via the lfa-mc1:// URL scheme
// instead of manual taps, so a 3-cycle physical validation can run unattended.
// Scheme: lfa-mc1://automate?action=<action>[&session_uuid=<uuid>][&role=<role>]
//
// Actions:
//   join          — join (or resume) a session created via the backend API;
//                   works for both instructor and player (join_session is
//                   idempotent server-side for a participant who already exists)
//   mark-ready    — instructor: LOBBY → DEVICES_READY
//   begin-cycle   — instructor: vm.beginCycle()
//   end-cycle     — instructor: vm.endCycle()
//   dump-snapshot — either device: prints the same text as "Copy Debug Snapshot"
//                   to the console, wrapped in [MC1-SNAPSHOT-BEGIN]/[MC1-SNAPSHOT-END]
//                   markers so the regression runner can extract it from
//                   `xcrun devicectl device console` output.
//
// Only the instructor device needs mark-ready/begin-cycle/end-cycle; the
// player device only ever needs `join` — auto-prepare/auto-start/auto-stop
// (ORCH-3/ORCH-4F) handle the rest without further automation hooks.

#if DEBUG
enum MC1AutomationAction: Equatable {
    case joinSession(uuid: String, role: ParticipantRole)
    case markDevicesReady
    case beginCycle
    case endCycle
    case dumpSnapshot
}

final class MC1AutomationBridge: ObservableObject {
    static let shared = MC1AutomationBridge()
    private init() {}

    static let urlScheme = "lfa-mc1"

    @Published var presentSessionLab = false
    @Published private(set) var lastAction: MC1AutomationAction?

    /// Returns true if the URL matched this bridge's scheme and was handled.
    @discardableResult
    func handle(url: URL) -> Bool {
        guard url.scheme == Self.urlScheme, url.host == "automate" else { return false }
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func value(_ name: String) -> String? { items.first { $0.name == name }?.value }

        guard let action = value("action") else { return false }
        switch action {
        case "join":
            guard let uuid = value("session_uuid"), !uuid.isEmpty else { return false }
            let role: ParticipantRole = value("role") == "instructor" ? .instructor : .player
            presentSessionLab = true
            lastAction = .joinSession(uuid: uuid, role: role)
            return true
        case "mark-ready":
            lastAction = .markDevicesReady
            return true
        case "begin-cycle":
            lastAction = .beginCycle
            return true
        case "end-cycle":
            lastAction = .endCycle
            return true
        case "dump-snapshot":
            lastAction = .dumpSnapshot
            return true
        default:
            return false
        }
    }
}
#endif

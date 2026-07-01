import Foundation
import Combine

// MARK: — MC1 physical-test automation bridge (DEBUG-only, MC1-AUTO-1)
//
// Lets an external script drive the Session Lab via the lfa-mc1:// URL scheme
// instead of manual taps, so a 3-cycle physical validation can run unattended.
// Scheme: lfa-mc1://automate?action=<action>[&session_uuid=<uuid>][&role=<role>]
//
// Actions:
//   join           — join (or resume) a session created via the backend API;
//                    works for both instructor and player (join_session is
//                    idempotent server-side for a participant who already exists)
//   mark-ready     — instructor: LOBBY → DEVICES_READY
//   begin-cycle    — instructor: vm.beginCycle()
//   end-cycle      — instructor: vm.endCycle()
//   dump-snapshot  — either device: prints the same text as "Copy Debug Snapshot"
//                    to the console, wrapped in [MC1-SNAPSHOT-BEGIN]/[MC1-SNAPSHOT-END]
//                    markers so the regression runner can extract it from device logs.
//   reset-session  — either device: vm.reset() → state = .idle, cancels polling/
//                    heartbeat. Used by the regression runner between scenarios so
//                    the next `join` deep link finds the ViewModel in .idle state.
//                    Does NOT dismiss the Session Lab view.
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
    case resetSession
    case goProConnect(goProDeviceId: Int?)
    case goProStartRecording(goProDeviceId: Int)
    case goProStopRecording(goProDeviceId: Int)
    case goProStatus
    case goProMediaList
    case goProHttpDiag
    case goProDownloadLatest
    case skeletonProcess
    case captureInfo
    // MC1 Block-1: network routing diagnostics (GoPro WiFi + cellular coexistence)
    case networkRoutingDiag(label: String)
    // GoPro live preview POC (docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md) — debug-only
    case goProPreviewPOC(durationSeconds: TimeInterval)
    // GoPro Block 3: preview + recording combined cycle proof — debug-only
    case goProCombinedCycleProof(durationSeconds: TimeInterval)
    // Capture Quality block: read (not write) the GoPro's current camera/state — debug-only
    case goProCameraStateProbe
    // GoPro Preview Aspect Probe: starts live preview, measures actual decoded
    // width/height/aspect — distinct from goProCameraStateProbe (no preview there)
    case goProPreviewAspectProbe(durationSeconds: TimeInterval)
    // GoPro 8:7 Recording Preset Read/Write Validation — the first GoPro POC
    // that actually WRITES a setting, with mandatory rollback on any failure.
    case goProPresetWriteValidation
    // Start GoProStreamProbe while the InstructorDashboard is live so the GoPro
    // panel receives frames and skeleton overlay can be validated in-scenario.
    case goProStreamStart
    // Export per-panel (instructor/player/gopro) LivePoseOverlayProcessor frame
    // diagnostics to Documents/pose_overlay_diag.json — handled directly by
    // InstructorDashboardView (owns the 3 processor instances), not MultiCameraLobbyView.
    case poseOverlayDiag
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
            print("[MC1-AUTO] received action=join uuid=\(uuid) role=\(role)")
            presentSessionLab = true
            lastAction = .joinSession(uuid: uuid, role: role)
            return true
        case "mark-ready":
            print("[MC1-AUTO] received action=mark-ready")
            lastAction = .markDevicesReady
            return true
        case "begin-cycle":
            print("[MC1-AUTO] received action=begin-cycle")
            lastAction = .beginCycle
            return true
        case "end-cycle":
            print("[MC1-AUTO] received action=end-cycle")
            lastAction = .endCycle
            return true
        case "dump-snapshot":
            print("[MC1-AUTO] received action=dump-snapshot")
            lastAction = .dumpSnapshot
            return true
        case "reset-session":
            print("[MC1-AUTO] received action=reset-session")
            lastAction = .resetSession
            return true
        case "gopro-connect":
            let did = value("gopro_device_id").flatMap(Int.init)
            print("[MC1-AUTO] received action=gopro-connect gopro_device_id=\(did ?? -1)")
            lastAction = .goProConnect(goProDeviceId: did)
            return true
        case "gopro-start":
            guard let did = value("gopro_device_id").flatMap(Int.init) else { return false }
            print("[MC1-AUTO] received action=gopro-start gopro_device_id=\(did)")
            lastAction = .goProStartRecording(goProDeviceId: did)
            return true
        case "gopro-stop":
            guard let did = value("gopro_device_id").flatMap(Int.init) else { return false }
            print("[MC1-AUTO] received action=gopro-stop gopro_device_id=\(did)")
            lastAction = .goProStopRecording(goProDeviceId: did)
            return true
        case "gopro-status":
            print("[MC1-AUTO] received action=gopro-status")
            lastAction = .goProStatus
            return true
        case "gopro-media-list":
            print("[MC1-AUTO] received action=gopro-media-list")
            lastAction = .goProMediaList
            return true
        case "gopro-http-diag":
            print("[MC1-AUTO] received action=gopro-http-diag")
            lastAction = .goProHttpDiag
            return true
        case "gopro-download-latest":
            print("[MC1-AUTO] received action=gopro-download-latest")
            lastAction = .goProDownloadLatest
            return true
        case "skeleton-process":
            print("[MC1-AUTO] received action=skeleton-process")
            lastAction = .skeletonProcess
            return true
        case "capture-info":
            print("[MC1-AUTO] received action=capture-info")
            lastAction = .captureInfo
            return true
        case "network-routing-diag":
            let label = value("label") ?? "unlabeled"
            print("[MC1-AUTO] received action=network-routing-diag label=\(label)")
            lastAction = .networkRoutingDiag(label: label)
            return true
        case "gopro-preview-poc":
            let duration = value("duration_s").flatMap(Double.init) ?? 25
            print("[MC1-AUTO] received action=gopro-preview-poc duration_s=\(duration)")
            lastAction = .goProPreviewPOC(durationSeconds: duration)
            return true
        case "gopro-combined-cycle-proof":
            let duration = value("duration_s").flatMap(Double.init) ?? 15
            print("[MC1-AUTO] received action=gopro-combined-cycle-proof duration_s=\(duration)")
            lastAction = .goProCombinedCycleProof(durationSeconds: duration)
            return true
        case "gopro-camera-state-probe":
            print("[MC1-AUTO] received action=gopro-camera-state-probe")
            lastAction = .goProCameraStateProbe
            return true
        case "gopro-preview-aspect-probe":
            let duration = value("duration_s").flatMap(Double.init) ?? 20
            print("[MC1-AUTO] received action=gopro-preview-aspect-probe duration_s=\(duration)")
            lastAction = .goProPreviewAspectProbe(durationSeconds: duration)
            return true
        case "gopro-preset-write-validation":
            print("[MC1-AUTO] received action=gopro-preset-write-validation")
            lastAction = .goProPresetWriteValidation
            return true
        case "gopro-stream-start":
            print("[MC1-AUTO] received action=gopro-stream-start")
            lastAction = .goProStreamStart
            return true
        case "pose-overlay-diag":
            print("[MC1-AUTO] received action=pose-overlay-diag")
            lastAction = .poseOverlayDiag
            return true
        default:
            print("[MC1-AUTO] received unknown action=\(action)")
            return false
        }
    }
}
#endif

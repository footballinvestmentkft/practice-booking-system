import Foundation

// MARK: — AN-3B2A P0 — Runtime diagnostics (DEBUG only)
//
// Purpose: make the persistence root-cause (events disappearing on reopen)
// provable from a single manual test, without modifying session state.
//
// This file is intentionally side-effect free: it only records what
// happened (timestamps, results, paths) for display in
// AnnotationDebugOverlay. It must never be reachable from a RELEASE build.

#if DEBUG

// Manually-maintained build marker — bump this string whenever a commit
// changes annotation persistence/lifecycle behaviour, so the overlay can
// confirm which source revision produced the running binary.
enum AnnotationBuildInfo {
    static let tag = "AN3B2D-3-ball-trajectory-overlay"
}

// Lightweight diagnostic logger. Prints to the Xcode console with a
// consistent prefix and timestamp so log lines can be grep'd easily.
enum AnnotationDiagnosticsLog {
    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss.SSS"
        return f
    }()

    static func log(_ message: @autoclosure () -> String) {
        let ts = formatter.string(from: Date())
        print("[AN3B2A-DIAG \(ts)] \(message())")
    }
}

// MARK: — AnnotationDiagnosticsSnapshot

// Read-only snapshot of the most recent load/save outcomes for one
// (userId, videoId) session. Populated by JugglingAnnotationViewModel at the
// required log points; rendered by AnnotationDebugOverlay.
struct AnnotationDiagnosticsSnapshot {
    enum LoadResult: CustomStringConvertible {
        case none
        case notFound
        case loaded(draftCount: Int)
        case quarantined(path: URL?, hasLocalOnlyEvents: Bool)

        var description: String {
            switch self {
            case .none:
                return "—"
            case .notFound:
                return "notFound"
            case .loaded(let count):
                return "loaded (\(count) draft\(count == 1 ? "" : "s"))"
            case .quarantined(let path, let hasLocalOnly):
                return "quarantined (hasLocalOnlyEvents=\(hasLocalOnly), path=\(path?.path ?? "—"))"
            }
        }
    }

    enum SaveResult: CustomStringConvertible {
        case none
        case success
        case failed(String)

        var description: String {
            switch self {
            case .none:        return "—"
            case .success:     return "success"
            case .failed(let m): return "failed: \(m)"
            }
        }
    }

    var loadResult: LoadResult = .none
    var lastLoadAt: Date?

    var lastSaveResult: SaveResult = .none
    var lastSaveAt: Date?

    var quarantinePath: URL?
}

#endif

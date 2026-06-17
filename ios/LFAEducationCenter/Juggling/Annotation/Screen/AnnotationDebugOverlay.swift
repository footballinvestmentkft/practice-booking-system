#if DEBUG
import SwiftUI

// MARK: — AnnotationDebugOverlay (AN-3B2A P0)
//
// DEBUG-only diagnostic sheet for JugglingAnnotationScreen. Read-only: never
// mutates vm.session, never touches the local store beyond the existing
// read-only diagnostics accessors (sessionFileURL / sessionFileExists /
// quarantineDirectory — none of which create or modify files).
//
// Presentation: a toolbar bug icon, visible only in DEBUG builds, opens this
// as a sheet. There is no equivalent UI in RELEASE builds.

struct AnnotationDebugOverlay: View {
    @ObservedObject var vm: JugglingAnnotationViewModel
    let authManager: AuthManager
    let videoId: String

    @Environment(\.presentationMode) private var presentationMode
    @State private var didCopy = false

    var body: some View {
        NavigationView {
            List {
                Section(header: Text("Build")) {
                    row("Build tag", AnnotationBuildInfo.tag)
                }

                Section(header: Text("Identity")) {
                    row("authManager.currentUserId", authManager.currentUserId.map(String.init) ?? "nil")
                    row("vm.userId", String(vm.userId))
                    row("videoId", videoId)
                }

                Section(header: Text("Local session file")) {
                    row("Path", vm.diagSessionFilePath.path)
                    row("File exists", String(vm.diagSessionFileExists))
                    row("Quarantine dir", vm.diagQuarantineDirectory.path)
                }

                Section(header: Text("Last load")) {
                    row("Result", vm.diagnostics.loadResult.description)
                    row("At", formatted(vm.diagnostics.lastLoadAt))
                    if let qPath = vm.diagnostics.quarantinePath {
                        row("Quarantine path", qPath.path)
                    }
                }

                Section(header: Text("Session counts")) {
                    row("Active events", String(vm.activeEvents.count))
                    row("Unlabeled", String(vm.unlabeledCount))
                    row("Label pending", String(vm.labelPendingCount))
                }

                Section(header: Text("Last save")) {
                    row("Result", vm.diagnostics.lastSaveResult.description)
                    row("At", formatted(vm.diagnostics.lastSaveAt))
                    row("isSaving", String(vm.isSaving))
                    row("saveStatus", String(describing: vm.saveStatus))
                }

                Section(header: Text("Errors / warnings")) {
                    row("saveError", vm.saveError ?? "—")
                    row("loadWarning", vm.loadWarning ?? "—")
                }
            }
            .navigationTitle("AN-3B2A Diagnostics")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button(didCopy ? "Másolva ✓" : "Másolás") {
                        UIPasteboard.general.string = copyText
                        didCopy = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                            didCopy = false
                        }
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Bezárás") {
                        presentationMode.wrappedValue.dismiss()
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
    }

    private func row(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption)
                .foregroundColor(.secondary)
            Text(value)
                .font(.system(.footnote, design: .monospaced))
        }
    }

    private func formatted(_ date: Date?) -> String {
        guard let date = date else { return "—" }
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss.SSS"
        return f.string(from: date)
    }

    // MARK: — Copy-all

    // Plain-text dump of every value shown above, in the same order, so it
    // can be pasted directly into a bug report.
    private var copyText: String {
        var lines: [String] = []
        lines.append("=== AN-3B2A Diagnostics ===")
        lines.append("Build tag: \(AnnotationBuildInfo.tag)")
        lines.append("")
        lines.append("-- Identity --")
        lines.append("authManager.currentUserId: \(authManager.currentUserId.map(String.init) ?? "nil")")
        lines.append("vm.userId: \(vm.userId)")
        lines.append("videoId: \(videoId)")
        lines.append("")
        lines.append("-- Local session file --")
        lines.append("Path: \(vm.diagSessionFilePath.path)")
        lines.append("File exists: \(vm.diagSessionFileExists)")
        lines.append("Quarantine dir: \(vm.diagQuarantineDirectory.path)")
        lines.append("")
        lines.append("-- Last load --")
        lines.append("Result: \(vm.diagnostics.loadResult.description)")
        lines.append("At: \(formatted(vm.diagnostics.lastLoadAt))")
        if let qPath = vm.diagnostics.quarantinePath {
            lines.append("Quarantine path: \(qPath.path)")
        }
        lines.append("")
        lines.append("-- Session counts --")
        lines.append("Active events: \(vm.activeEvents.count)")
        lines.append("Unlabeled: \(vm.unlabeledCount)")
        lines.append("Label pending: \(vm.labelPendingCount)")
        lines.append("")
        lines.append("-- Last save --")
        lines.append("Result: \(vm.diagnostics.lastSaveResult.description)")
        lines.append("At: \(formatted(vm.diagnostics.lastSaveAt))")
        lines.append("isSaving: \(vm.isSaving)")
        lines.append("saveStatus: \(vm.saveStatus)")
        lines.append("")
        lines.append("-- Errors / warnings --")
        lines.append("saveError: \(vm.saveError ?? "—")")
        lines.append("loadWarning: \(vm.loadWarning ?? "—")")
        return lines.joined(separator: "\n")
    }
}
#endif

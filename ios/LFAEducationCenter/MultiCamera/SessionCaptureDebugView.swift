#if DEBUG
import SwiftUI

struct SessionCaptureDebugView: View {
    @StateObject private var manager = SessionCaptureManager()
    @Environment(\.presentationMode) private var presentationMode

    var body: some View {
        NavigationView {
            List {
                Section("State") {
                    Text(String(describing: manager.state)).font(.caption.weight(.semibold))
                }
                Section("Actions") {
                    Button("1. Request Permissions") { Task { await manager.requestPermissions() } }
                    Button("2. Prepare") { manager.prepare(sessionUUID: "debug-test", deviceId: 0) }
                    Button("3. Start Capture") { manager.startCapture() }
                        .disabled(manager.state != .ready)
                    Button("4. Stop Capture") { manager.stopCapture() }
                        .foregroundColor(.red)
                        .disabled(manager.state != .capturing && manager.state != .interrupted)
                    Button("5. Teardown") { manager.teardown() }
                }
                if let url = manager.outputFileURL {
                    Section("Output") {
                        Text(url.lastPathComponent).font(.caption)
                    }
                }
                if let v = manager.lastValidation {
                    Section("Validation") {
                        switch v {
                        case .valid(let dur, let res, let orient, let audio, let transform):
                            Text("Duration: \(dur, specifier: "%.1f")s")
                            Text("Resolution: \(Int(res.width))×\(Int(res.height))")
                            Text("Orientation: \(orient)")
                            Text("Audio: \(audio ? "Yes" : "No")")
                            Text("Transform: \(transform)").font(.system(size: 9, design: .monospaced))
                        case .invalid(let reason):
                            Text("Invalid: \(reason)").foregroundColor(.red)
                        }
                    }
                }
            }
            .navigationTitle("Capture Test")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { presentationMode.wrappedValue.dismiss() } label: {
                        Image(systemName: "xmark").font(.system(size: 14, weight: .semibold))
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
        .onDisappear { manager.teardown() }
    }
}
#endif

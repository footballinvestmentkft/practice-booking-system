import SwiftUI

struct GoProConnectionDebugView: View {

    @StateObject private var manager: GoProConnectionManager
    @ObservedObject private var streamProbe = GoProStreamProbe.shared
    @Environment(\.presentationMode) private var presentationMode

    init(manager: GoProConnectionManager) {
        _manager = StateObject(wrappedValue: manager)
    }

    var body: some View {
        NavigationView {
            List {
                statusSection
                livePreviewSection
                actionsSection
                cameraSection
                diagnosticSection
            }
            .navigationTitle("GoPro Connection")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { presentationMode.wrappedValue.dismiss() } label: {
                        Image(systemName: "xmark").font(.system(size: 14, weight: .semibold))
                    }
                }
            }
            .onReceive(NotificationCenter.default.publisher(for: UIApplication.willEnterForegroundNotification)) { _ in
                manager.onForeground()
            }
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Status

    private var statusSection: some View {
        Section("Connection Status") {
            HStack {
                Circle()
                    .fill(statusColor)
                    .frame(width: 12, height: 12)
                Text(manager.state.userFacingStatus)
                    .font(.body.weight(.medium))
            }
            if case .ready(let fw) = manager.state {
                Label("Firmware: \(fw)", systemImage: "checkmark.seal")
                    .font(.caption)
                    .foregroundColor(.green)
            }
        }
    }

    // MARK: — Live preview POC (docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md)

    @ViewBuilder
    private var livePreviewSection: some View {
        if streamProbe.lastFrame != nil || streamProbe.isRunning {
            Section("Live Preview POC") {
                if let frame = streamProbe.lastFrame {
                    Image(uiImage: frame)
                        .resizable()
                        .aspectRatio(contentMode: .fit)
                        .frame(maxHeight: 200)
                } else {
                    Text("Waiting for first decoded frame…")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
    }

    private var statusColor: Color {
        switch manager.state {
        case .ready: return .green
        case .failed: return .red
        case .idle: return .gray
        default: return .orange
        }
    }

    // MARK: — Actions

    private var actionsSection: some View {
        Section("Actions") {
            switch manager.state {
            case .idle, .failed, .bluetoothUnavailable:
                Button("Connect GoPro") {
                    manager.startConnection()
                }
                .font(.body.weight(.semibold))
            case .awaitingManualWiFiJoin(let ssid):
                VStack(alignment: .leading, spacing: 8) {
                    Text("Beállítások → Wi-Fi → \(ssid)")
                        .font(.caption.weight(.semibold))
                    Text("Jelszó a GoPro kijelzőjén")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                Button("Csatlakoztam a Wi-Fi-hez") {
                    manager.confirmManualWiFiJoined()
                }
                .font(.body.weight(.semibold))
                .foregroundColor(.green)
                Button("Cancel") { manager.cancel() }
                    .foregroundColor(.orange)
            case .ready:
                Button("Disconnect") {
                    manager.disconnect()
                }
                .foregroundColor(.red)
                Button(streamProbe.isRunning ? "Live Preview POC running…" : "Start Live Preview POC (25s)") {
                    Task {
                        let diag = await streamProbe.run(durationSeconds: 25)
                        GoProStreamDiagWriter.write(diag)
                    }
                }
                .disabled(streamProbe.isRunning)
                .foregroundColor(.blue)
            default:
                Button("Cancel") {
                    manager.cancel()
                }
                .foregroundColor(.orange)
            }
            if case .failed(let err) = manager.state, err.isRecoverable {
                Button("Retry") {
                    manager.retry()
                }
            }
        }
    }

    // MARK: — Camera info

    @ViewBuilder
    private var cameraSection: some View {
        if let status = manager.cameraStatus {
            Section("Camera Info") {
                if let battery = status.batteryLevel {
                    Label("Battery: \(battery)%", systemImage: "battery.100")
                }
                if let recording = status.isRecording {
                    Label("Recording: \(recording ? "Yes" : "No")", systemImage: "record.circle")
                }
                if let space = status.sdCardSpaceRemaining {
                    Label("SD Free: \(space) MB", systemImage: "sdcard")
                }
            }
        }
    }

    // MARK: — Diagnostics

    private var diagnosticSection: some View {
        Section("Diagnostic Log (last 10)") {
            ForEach(Array(manager.diagnosticLog.suffix(10).enumerated()), id: \.offset) { _, event in
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(event.trigger)")
                        .font(.caption.weight(.semibold))
                    Text("\(event.fromState) → \(event.toState)")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
        }
    }
}

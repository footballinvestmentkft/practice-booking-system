import SwiftUI

struct GoProConnectionDebugView: View {

    @StateObject private var manager: GoProConnectionManager

    init(manager: GoProConnectionManager) {
        _manager = StateObject(wrappedValue: manager)
    }

    var body: some View {
        NavigationView {
            List {
                statusSection
                actionsSection
                cameraSection
                diagnosticSection
            }
            .navigationTitle("GoPro Connection")
            .navigationBarTitleDisplayMode(.inline)
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

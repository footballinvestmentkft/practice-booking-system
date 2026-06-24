import SwiftUI
import UIKit

#if DEBUG
struct MultiCameraLobbyView: View {

    @StateObject private var vm: MultiCameraSessionViewModel
    @State private var joinUuid = ""
    @State private var showQRScanner = false
    @State private var qrDecodeError: String?
    @State private var orchestrationTick = 0
    @State private var interfaceOrientation: UIInterfaceOrientation = .portrait
    @Environment(\.presentationMode) private var presentationMode

    init(authManager: AuthManager) {
        _vm = StateObject(wrappedValue: MultiCameraSessionViewModel(authManager: authManager))
    }

    var body: some View {
        NavigationView {
            List {
                switch vm.state {
                case .idle:
                    idleSection
                case .creating:
                    Section { Label("Session létrehozása…", systemImage: "hourglass") }
                case .joining:
                    Section { Label("Csatlakozás…", systemImage: "hourglass") }
                case .inLobby(let session):
                    lobbySection(session)
                case .error(let msg):
                    errorSection(msg)
                }
            }
            .navigationTitle("Session Lab")
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
        .onAppear {
            interfaceOrientation = CaptureOrientationHelper.currentInterfaceOrientation()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIDevice.orientationDidChangeNotification)) { _ in
            let current = CaptureOrientationHelper.currentInterfaceOrientation()
            // Ignore faceUp / faceDown — scene.interfaceOrientation stays at last valid rotation
            if current != .unknown { interfaceOrientation = current }
        }
        .onDisappear { vm.reset() }
        .onReceive(vm.orchestrator.objectWillChange) { _ in
            // Propagate orchestrator state changes to SwiftUI render cycle
            orchestrationTick += 1
        }
        .sheet(isPresented: $showQRScanner) {
            QRScannerView(
                onScanned: { raw in
                    showQRScanner = false
                    switch SessionQRPayload.decode(from: raw) {
                    case .success(let payload):
                        vm.joinSession(uuid: payload.sessionUuid)
                    case .failure(let err):
                        qrDecodeError = err.localizedDescription
                    }
                },
                onDismiss: { showQRScanner = false }
            )
            .ignoresSafeArea()
        }
    }

    // MARK: — Idle

    private var idleSection: some View {
        Group {
            Section("Új session") {
                Button("Create Session") { vm.createSession() }
                    .font(.body.weight(.semibold))
            }
            Section("Csatlakozás meglévőhöz") {
                Button("QR-kóddal csatlakozás") { showQRScanner = true }
                    .font(.body.weight(.semibold))
                TextField("Session UUID (manuális)", text: $joinUuid)
                    .font(.system(.body, design: .monospaced))
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                Button("Join Session") {
                    vm.joinSession(uuid: joinUuid.trimmingCharacters(in: .whitespaces))
                }
                .disabled(joinUuid.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let err = qrDecodeError {
                Section {
                    HStack(spacing: 8) {
                        Image(systemName: "qrcode.viewfinder").foregroundColor(.red)
                        Text(err).font(.caption).foregroundColor(.red)
                    }
                    Button("Rendben") { qrDecodeError = nil }
                        .font(.caption)
                }
            }
        }
    }

    // MARK: — Lobby

    private func lobbySection(_ session: MultiCameraSessionDTO) -> some View {
        Group {
            qrSection(session)
            Section("Session") {
                LabeledRow("UUID", session.sessionUuid)
                LabeledRow("Status", session.status.rawValue)
                LabeledRow("Revision", "\(session.revision)")
            }
            Section("Participants (\(session.participants.count)/\(session.maxParticipants))") {
                ForEach(session.participants, id: \.id) { p in
                    HStack {
                        Text(p.role == .instructor ? "👨‍🏫" : "🏃")
                        Text("\(p.role.rawValue) (user \(p.userId))")
                            .font(.caption)
                        Spacer()
                        if p.leftAt != nil {
                            Text("left").font(.caption2).foregroundColor(.red)
                        }
                    }
                }
            }
            Section("Devices (\(session.devices.count)/\(session.maxDevices))") {
                ForEach(session.devices, id: \.id) { d in
                    HStack {
                        Text(deviceIcon(d.deviceRole))
                        VStack(alignment: .leading, spacing: 2) {
                            Text(d.deviceRole.rawValue).font(.caption.weight(.semibold))
                            Text("status: \(d.status.rawValue)").font(.caption2).foregroundColor(.secondary)
                        }
                        Spacer()
                        if d.managedByDeviceId != nil {
                            Text("managed").font(.caption2).foregroundColor(.orange)
                        }
                    }
                }
            }
            previewSection
            Section("Capture") {
                HStack {
                    Text("Local").font(.caption).foregroundColor(.secondary)
                    Spacer()
                    Text(String(describing: vm.orchestrator.orchestrationState))
                        .font(.caption.weight(.semibold))
                }
                HStack {
                    Text("Clock").font(.caption).foregroundColor(.secondary)
                    Spacer()
                    Text(vm.orchestrator.clockQuality.rawValue)
                        .font(.caption2)
                        .foregroundColor(vm.orchestrator.clockQuality == .synchronized ? .green : .orange)
                }
                if let sid = vm.orchestrator.streamId {
                    HStack {
                        Text("Stream").font(.caption).foregroundColor(.secondary)
                        Spacer()
                        Text("id=\(sid)").font(.caption2)
                    }
                }
            }
            Section("Actions") {
                if let instrErr = vm.instructorIdentityError(for: session) {
                    HStack(spacing: 6) {
                        Image(systemName: "person.crop.circle.badge.exclamationmark").foregroundColor(.orange)
                        Text(instrErr).font(.caption).foregroundColor(.orange)
                    }
                } else if session.status == .lobby {
                    Button("Mark Devices Ready") { vm.transitionToDevicesReady() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.green)
                } else if session.status == .devicesReady {
                    Button("Start Capture") { Task { await vm.startCapture() } }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.green)
                        .disabled(!vm.allAppleDevicesReadyPublic(session))
                    if let notReadyMsg = vm.deviceNotReadyMessage {
                        HStack(spacing: 6) {
                            Image(systemName: "exclamationmark.circle").foregroundColor(.red)
                            Text(notReadyMsg).font(.caption).foregroundColor(.red)
                        }
                    } else if !vm.allAppleDevicesReadyPublic(session) {
                        let pending = session.devices
                            .filter { $0.deviceRole != .auxiliaryCamera && $0.removedAt == nil && $0.status != .ready }
                        Text("Várakozás: \(pending.map { "\($0.deviceRole.rawValue) (\($0.status.rawValue))" }.joined(separator: ", "))")
                            .font(.caption).foregroundColor(.orange)
                    }
                } else if session.status == .recording || session.status == .recordingPending {
                    Button("Stop Capture") { vm.stopCapture() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.red)
                } else if !vm.isInstructor {
                    Text("Várakozás az instructor-ra…")
                        .font(.caption).foregroundColor(.secondary)
                }
                Button("Cancel Session") { vm.cancelSession() }
                    .foregroundColor(.red)
            }
            if let sdId = vm.sessionDeviceId {
                Section("Debug") {
                    LabeledRow("Session Device ID", "\(sdId)")
                    LabeledRow("Heartbeat", "Active")
                }
            }
        }
    }

    // MARK: — QR display (instructor only)

    @ViewBuilder
    private func qrSection(_ session: MultiCameraSessionDTO) -> some View {
        if vm.isInstructor,
           let qrString = SessionQRPayload.encode(sessionUuid: session.sessionUuid),
           let qrImage = QRCodeGenerator.image(from: qrString, scale: 6) {
            Section("Join QR-kód") {
                HStack {
                    Spacer()
                    Image(uiImage: qrImage)
                        .interpolation(.none)
                        .resizable()
                        .scaledToFit()
                        .frame(width: 180, height: 180)
                    Spacer()
                }
                .listRowBackground(Color.white)
            }
        }
    }

    // MARK: — Camera preview (armed and above)

    @ViewBuilder
    private var previewSection: some View {
        if let previewSession = vm.orchestrator.captureSessionForPreview {
            Section("Camera") {
                ZStack(alignment: .topLeading) {
                    CapturePreviewView(captureSession: previewSession,
                                       interfaceOrientation: interfaceOrientation)
                        .frame(height: 240)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    CaptureOverlayView(orchestrationState: vm.orchestrator.orchestrationState)
                }
                .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
            }
        }
    }

    // MARK: — Error

    private func errorSection(_ msg: String) -> some View {
        Section {
            HStack {
                Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.red)
                Text(msg).font(.body)
            }
            Button("Vissza") { vm.reset() }
        }
    }

    // MARK: — Helpers

    private func deviceIcon(_ role: MCDeviceRole) -> String {
        switch role {
        case .instructorPrimary: return "📱"
        case .playerPrimary: return "📱"
        case .playerSecondary: return "📱"
        case .auxiliaryCamera: return "📷"
        }
    }
}

private struct LabeledRow: View {
    let label: String
    let value: String
    init(_ label: String, _ value: String) { self.label = label; self.value = value }
    var body: some View {
        HStack {
            Text(label).font(.caption).foregroundColor(.secondary)
            Spacer()
            Text(value).font(.caption.weight(.semibold))
        }
    }
}
#endif

import SwiftUI

#if DEBUG
struct MultiCameraLobbyView: View {

    @StateObject private var vm: MultiCameraSessionViewModel
    @StateObject private var orchestrator: CycleCaptureOrchestrator
    @StateObject private var captureManager: SessionCaptureManager
    @State private var joinUuid = ""
    @State private var showQRScanner = false
    @State private var qrDecodeError: String?
    @Environment(\.presentationMode) private var presentationMode

    init(authManager: AuthManager) {
        let clockSync = ClockSyncService()
        let captureMgr = SessionCaptureManager()
        let orch = CycleCaptureOrchestrator(
            authManager: authManager,
            clockSyncService: clockSync,
            captureController: captureMgr
        )
        _captureManager = StateObject(wrappedValue: captureMgr)
        _orchestrator = StateObject(wrappedValue: orch)
        _vm = StateObject(wrappedValue: MultiCameraSessionViewModel(
            authManager: authManager,
            clockSyncService: clockSync,
            cycleOrchestrator: orch
        ))
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
        .onDisappear { vm.reset() }
        .sheet(isPresented: $showQRScanner) {
            QRScannerView(
                onScanned: { raw in
                    showQRScanner = false
                    switch SessionQRPayload.decode(from: raw) {
                    case .success(let payload):
                        qrDecodeError = nil
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
                TextField("UUID (manuális fallback)", text: $joinUuid)
                    .font(.system(.body, design: .monospaced))
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                Button("Join Session") { vm.joinSession(uuid: joinUuid.trimmingCharacters(in: .whitespaces)) }
                    .disabled(joinUuid.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let err = qrDecodeError {
                Section {
                    HStack(spacing: 8) {
                        Image(systemName: "qrcode.viewfinder").foregroundColor(.red)
                        Text(err).font(.caption).foregroundColor(.red)
                    }
                }
            }
        }
    }

    // MARK: — Lobby

    private func lobbySection(_ session: MultiCameraSessionDTO) -> some View {
        Group {
            if vm.isInstructor,
               let qrString = SessionQRPayload.encode(sessionUuid: session.sessionUuid),
               let qrImage  = QRCodeGenerator.image(from: qrString, scale: 6) {
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
                    .padding(.vertical, 8)
                }
            }
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
            Section("Actions") {
                if vm.isInstructor && session.status == .lobby {
                    Button("Mark Devices Ready") { vm.transitionToDevicesReady() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.green)
                } else if !vm.isInstructor {
                    Text("Várakozás az instructor-ra…")
                        .font(.caption).foregroundColor(.secondary)
                }
                if captureManager.state == .idle || captureManager.state == .requestingPermissions {
                    Button("Prepare Capture") {
                        Task {
                            await captureManager.requestPermissions()
                            if let sdId = vm.sessionDeviceId {
                                captureManager.prepare(sessionUUID: session.sessionUuid, deviceId: sdId)
                            }
                        }
                    }
                    .disabled(captureManager.state == .requestingPermissions)
                }
                if vm.canStartCapture && captureManager.state == .ready {
                    Button("Begin Cycle") { vm.beginCycle() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.blue)
                }
                if case .capturing = orchestrator.state {
                    Button("End Cycle") { vm.endCycle() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.orange)
                }
                Button("Cancel Session") { vm.cancelSession() }
                    .foregroundColor(.red)
            }
            if let sdId = vm.sessionDeviceId {
                Section("Debug") {
                    LabeledRow("Session Device ID", "\(sdId)")
                    LabeledRow("Heartbeat", "Active")
                    LabeledRow("Clock", clockSyncDescription)
                    LabeledRow("Capture", captureStateDescription)
                    LabeledRow("Orchestrator", orchestratorStateDescription)
                }
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

    private var clockSyncDescription: String {
        switch vm.clockSyncState {
        case .notSynced:       return "notSynced"
        case .syncing:         return "syncing…"
        case .synced:          return "synced ✓"
        case .failed(let n, _): return "failed(\(n))"
        }
    }

    private var captureStateDescription: String {
        switch captureManager.state {
        case .idle:                  return "idle"
        case .requestingPermissions: return "requestingPermissions"
        case .configuring:           return "configuring"
        case .ready:                 return "ready ✓"
        case .capturing:             return "capturing ●"
        case .stopping:              return "stopping"
        case .interrupted:           return "interrupted"
        case .completed:             return "completed ✓"
        case .failed(let msg):       return "failed: \(msg)"
        case .tornDown:              return "tornDown"
        }
    }

    private var orchestratorStateDescription: String {
        switch orchestrator.state {
        case .idle:                    return "idle"
        case .creating:                return "creating…"
        case .scheduling:              return "scheduling…"
        case .waitingForStart:         return "waitingForStart…"
        case .capturing(let id):       return "capturing(#\(id)) ●"
        case .stopping(let id):        return "stopping(#\(id))"
        case .completed(let id):       return "completed(#\(id)) ✓"
        case .failed(let f):           return "failed: \(f)"
        }
    }

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

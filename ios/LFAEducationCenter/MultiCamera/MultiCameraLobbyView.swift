import SwiftUI

#if DEBUG
struct MultiCameraLobbyView: View {

    @StateObject private var vm: MultiCameraSessionViewModel
    @StateObject private var orchestrator: CycleCaptureOrchestrator
    @StateObject private var captureManager: SessionCaptureManager
    @StateObject private var playerListener: PlayerCycleListener
    @State private var joinUuid = ""
    @State private var showQRScanner = false
    @State private var qrDecodeError: String?
    @State private var snapshotCopied = false
    @Environment(\.presentationMode) private var presentationMode

    init(authManager: AuthManager) {
        let clockSync = ClockSyncService()
        let captureMgr = SessionCaptureManager()
        let listener = PlayerCycleListener(authManager: authManager)
        let orch = CycleCaptureOrchestrator(
            authManager: authManager,
            clockSyncService: clockSync,
            captureController: captureMgr
        )
        _captureManager = StateObject(wrappedValue: captureMgr)
        _orchestrator = StateObject(wrappedValue: orch)
        _playerListener = StateObject(wrappedValue: listener)
        _vm = StateObject(wrappedValue: MultiCameraSessionViewModel(
            authManager: authManager,
            clockSyncService: clockSync,
            cycleOrchestrator: orch,
            playerCycleListener: listener
        ))
    }

    private static let buildFingerprint = "mc1-debug-v5-2026-06-26"

    var body: some View {
        NavigationView {
            List {
                Section {
                    Text(Self.buildFingerprint)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundColor(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .listRowBackground(Color.clear)
                }
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
                alwaysVisibleDebugSection
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
            if let err = vm.deviceRegisterError {
                Section("Device Registration Error") {
                    Text(err)
                        .font(.caption2).foregroundColor(.red)
                        .lineLimit(nil)
                    Button("Retry Register Device") { vm.retryDeviceRegistration() }
                        .font(.body.weight(.semibold))
                }
            }
        }
    }

    // MARK: — Debug Snapshot (always visible)

    private var alwaysVisibleDebugSection: some View {
        Group {
            if case .inLobby(let session) = vm.state {
                debugSnapshotSection(session)
            } else {
                Section("Debug Snapshot") {
                    LabeledRow("Build", Self.buildFingerprint)
                    LabeledRow("API", APIConfig.baseURL)
                    LabeledRow("User ID", "\(Self.cachedUserId ?? 0)")
                    LabeledRow("State", "\(vm.state)")
                    LabeledRow("Orchestrator", orchestratorStateDescription)
                    if case .error(let msg) = vm.state {
                        LabeledRow("Error", msg)
                    }
                    Button {
                        let text = [
                            "=== MC1 Session Lab Debug Snapshot ===",
                            "build: \(Self.buildFingerprint)",
                            "timestamp: \(ISO8601DateFormatter().string(from: Date()))",
                            "api_base_url: \(APIConfig.baseURL)",
                            "user_id: \(Self.cachedUserId ?? 0)",
                            "state: \(vm.state)",
                            "orchestrator: \(orchestratorStateDescription)",
                            "player_listener: \(playerListenerDescription)",
                            "======================================",
                        ].joined(separator: "\n")
                        UIPasteboard.general.string = text
                        snapshotCopied = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { snapshotCopied = false }
                    } label: {
                        HStack {
                            Image(systemName: snapshotCopied ? "checkmark" : "doc.on.doc")
                            Text(snapshotCopied ? "Copied" : "Copy Debug Snapshot")
                        }
                        .font(.body.weight(.semibold))
                        .foregroundColor(snapshotCopied ? .green : .accentColor)
                    }
                }
            }
        }
    }

    private func debugSnapshotSection(_ session: MultiCameraSessionDTO) -> some View {
        Section("Debug Snapshot") {
            LabeledRow("Build", Self.buildFingerprint)
            LabeledRow("API", APIConfig.baseURL)
            LabeledRow("User ID", "\(Self.cachedUserId ?? 0)")
            LabeledRow("Role", vm.isInstructor ? "instructor" : "player")
            LabeledRow("Session UUID", session.sessionUuid)
            LabeledRow("Status", session.status.rawValue)
            LabeledRow("Revision", "\(session.revision)")
            LabeledRow("Participants", "\(session.participants.count)/\(session.maxParticipants)")
            LabeledRow("Devices", "\(session.devices.count)/\(session.maxDevices)")
            LabeledRow("Device ID", vm.sessionDeviceId.map { "\($0)" } ?? "—")
            LabeledRow("Heartbeat", vm.sessionDeviceId != nil ? "Active" : "—")
            LabeledRow("Clock", clockSyncDescription)
            LabeledRow("Capture", captureStateDescription)
            LabeledRow("Orchestrator", orchestratorStateDescription)
            LabeledRow("Listener", playerListenerDescription)
            LabeledRow("DevReg Error", vm.deviceRegisterError ?? "—")
            if case .failed = vm.clockSyncState {
                Button("Retry Clock Sync") { vm.retryClockSync() }
                    .font(.body.weight(.semibold))
            }
            Button {
                UIPasteboard.general.string = buildSnapshotText(session)
                snapshotCopied = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { snapshotCopied = false }
            } label: {
                HStack {
                    Image(systemName: snapshotCopied ? "checkmark" : "doc.on.doc")
                    Text(snapshotCopied ? "Copied" : "Copy Debug Snapshot")
                }
                .font(.body.weight(.semibold))
                .foregroundColor(snapshotCopied ? .green : .accentColor)
            }
        }
    }

    private func buildSnapshotText(_ session: MultiCameraSessionDTO) -> String {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let ts = iso.string(from: Date())

        let lastError: String
        if case .error(let msg) = vm.state { lastError = msg }
        else { lastError = "—" }

        let orchError: String
        if case .failed(let f) = orchestrator.state { orchError = "\(f)" }
        else { orchError = "—" }

        return [
            "=== MC1 Session Lab Debug Snapshot ===",
            "build: \(Self.buildFingerprint)",
            "timestamp: \(ts)",
            "api_base_url: \(APIConfig.baseURL)",
            "user_id: \(Self.cachedUserId ?? 0)",
            "role: \(vm.isInstructor ? "instructor" : "player")",
            "session_uuid: \(session.sessionUuid)",
            "session_status: \(session.status.rawValue)",
            "session_revision: \(session.revision)",
            "participants: \(session.participants.count)/\(session.maxParticipants)",
            "devices: \(session.devices.count)/\(session.maxDevices)",
            "session_device_id: \(vm.sessionDeviceId.map { "\($0)" } ?? "—")",
            "heartbeat: \(vm.sessionDeviceId != nil ? "active" : "—")",
            "clock: \(clockSyncDescription)",
            "capture: \(captureStateDescription)",
            "orchestrator: \(orchestratorStateDescription)",
            "player_listener: \(playerListenerDescription)",
            "device_reg_error: \(vm.deviceRegisterError ?? "—")",
            "last_error: \(lastError)",
            "last_orch_failure: \(orchError)",
            "======================================",
        ].joined(separator: "\n")
    }

    private static var cachedUserId: Int? {
        let v = UserDefaults.standard.integer(forKey: "lfa_current_user_id")
        return v > 0 ? v : nil
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
        case .failed(let n, let msg): return "failed(\(n)) \(msg ?? "?")"
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

    private var playerListenerDescription: String {
        switch playerListener.state {
        case .idle:                         return "idle"
        case .waitingForCycle:              return "waitingForCycle"
        case .pendingCycleDetected(let id): return "pendingCycle(#\(id))"
        case .recordingDetected(let id):    return "recording(#\(id))"
        case .stoppingDetected(let id):     return "stopping(#\(id))"
        case .failed(let msg):              return "error: \(msg)"
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

import SwiftUI
// ORCH-4: capture authority — device_role auto-assignment, isController gate, non-controller auto-prepare

#if DEBUG
struct MultiCameraLobbyView: View {

    @StateObject private var vm: MultiCameraSessionViewModel
    @StateObject private var orchestrator: CycleCaptureOrchestrator
    @StateObject private var captureManager: SessionCaptureManager
    @StateObject private var playerListener: PlayerCycleListener
    @StateObject private var playerOrchestrator: PlayerCaptureOrchestrator
    @StateObject private var streamService: CameraStreamService
    @StateObject private var playerStreamService: CameraStreamService
    @StateObject private var framePublisher: CameraFramePublisher
    @State private var joinUuid = ""
    @State private var showQRScanner = false
    @State private var qrDecodeError: String?
    @State private var snapshotCopied = false
    @State private var showCaptureView = false
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
        let playerOrch = PlayerCaptureOrchestrator(
            authManager: authManager,
            clockSyncService: clockSync,
            captureController: captureMgr
        )
        _captureManager = StateObject(wrappedValue: captureMgr)
        _orchestrator = StateObject(wrappedValue: orch)
        _playerListener = StateObject(wrappedValue: listener)
        _playerOrchestrator = StateObject(wrappedValue: playerOrch)
        _streamService = StateObject(wrappedValue: CameraStreamService(role: .instructor, sessionUuid: "pending"))
        _playerStreamService = StateObject(wrappedValue: CameraStreamService(role: .player, sessionUuid: "pending", deviceName: UIDevice.current.name))
        _framePublisher = StateObject(wrappedValue: CameraFramePublisher())
        _vm = StateObject(wrappedValue: MultiCameraSessionViewModel(
            authManager: authManager,
            clockSyncService: clockSync,
            cycleOrchestrator: orch,
            playerCycleListener: listener,
            playerCaptureOrchestrator: playerOrch,
            capturePreparable: captureMgr
        ))
    }

    private static let buildFingerprint = "mc1-debug-v11-2026-06-28"

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
        .onChange(of: vm.sessionDeviceId) { newId in
            if newId != nil && !showCaptureView {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    showCaptureView = true
                }
            }
        }
        // MC1-AUTO-1: dispatches automation commands from lfa-mc1:// deep links
        // onto the same vm methods the manual buttons call.
        .onReceive(MC1AutomationBridge.shared.$lastAction.compactMap { $0 }) { action in
            switch action {
            case .joinSession(let uuid, let role):
                print("[MC1-AUTO] dispatching action=join uuid=\(uuid) role=\(role) state=\(vm.state)")
                vm.joinSession(uuid: uuid, role: role)
            case .markDevicesReady:
                print("[MC1-AUTO] dispatching action=mark-ready state=\(vm.state)")
                vm.transitionToDevicesReady()
            case .beginCycle:
                print("[MC1-AUTO] dispatching action=begin-cycle state=\(vm.state) canStartCapture=\(vm.canStartCapture) isClockSynced=\(vm.isClockSynced)")
                vm.beginCycle()
            case .endCycle:
                print("[MC1-AUTO] dispatching action=end-cycle state=\(vm.state)")
                vm.endCycle()
            case .dumpSnapshot:
                print("[MC1-AUTO] dispatching action=dump-snapshot")
                dumpSnapshotToConsole()
            case .resetSession:
                print("[MC1-AUTO] dispatching action=reset-session state=\(vm.state) capture=\(captureManager.state)")
                vm.reset()
                captureManager.resetForReuse()
            case .goProConnect(let goProDeviceId):
                let gp = GoProConnectionManager.shared
                print("[GOPRO-AUTO] dispatching gopro-connect connection=\(gp.state) goProDeviceId=\(goProDeviceId ?? -1)")
                if case .ready = gp.state {
                    print("[GOPRO-AUTO] already connected+ready")
                    Task { await self.signalGoProReady(goProDeviceId: goProDeviceId) }
                } else if case .awaitingManualWiFiJoin = gp.state {
                    print("[GOPRO-AUTO] attempting confirmManualWiFiJoined...")
                    gp.confirmManualWiFiJoined()
                    Task { await self.waitAndSignalGoProReady(goProDeviceId: goProDeviceId) }
                } else if case .failed(let err) = gp.state, err.isRecoverable {
                    print("[GOPRO-AUTO] retrying from failed state...")
                    gp.retry()
                    Task { await self.waitAndSignalGoProReady(goProDeviceId: goProDeviceId) }
                } else if gp.state == .idle {
                    print("[GOPRO-AUTO] starting fresh connection...")
                    gp.startConnection()
                    Task { await self.waitAndSignalGoProReady(goProDeviceId: goProDeviceId) }
                } else {
                    print("[GOPRO-AUTO] cannot connect from state=\(gp.state)")
                }
            case .goProStartRecording(let goProDeviceId):
                let gp = GoProConnectionManager.shared
                print("[GOPRO-AUTO] dispatching gopro-start connection=\(gp.state) recording=\(gp.recordingState) goProDeviceId=\(goProDeviceId)")
                Task {
                    do {
                        try await gp.startRecording()
                        print("[GOPRO-AUTO] shutter start OK, confirming to backend...")
                        guard let token = vm.authManager.accessToken,
                              let sessionUuid = vm.sessionUuid else {
                            print("[GOPRO-AUTO] confirm skipped: no auth/session")
                            return
                        }
                        let cycles = try await MultiCameraAPIClient.listCycles(token: token, uuid: sessionUuid)
                        guard let cycle = cycles.max(by: { $0.cycleIndex < $1.cycleIndex }) else {
                            print("[GOPRO-AUTO] confirm skipped: no cycle found")
                            return
                        }
                        guard let cd = cycle.cycleDevices.first(where: { $0.sessionDeviceId == goProDeviceId }) else {
                            print("[GOPRO-AUTO] confirm skipped: no cycle_device for GoPro")
                            return
                        }
                        let ts = Self.isoNow()
                        _ = try await MultiCameraAPIClient.confirmDeviceStart(
                            token: token, uuid: sessionUuid, cycleId: cycle.id,
                            sessionDeviceId: goProDeviceId, startedAt: ts,
                            cycleDeviceRevision: cd.revision
                        )
                        print("[GOPRO-AUTO] confirmDeviceStart OK cycleId=\(cycle.id)")
                    } catch {
                        print("[GOPRO-AUTO] start FAILED: \(error)")
                    }
                }
            case .goProStopRecording(let goProDeviceId):
                let gp = GoProConnectionManager.shared
                print("[GOPRO-AUTO] dispatching gopro-stop connection=\(gp.state) recording=\(gp.recordingState) goProDeviceId=\(goProDeviceId)")
                Task {
                    do {
                        try await gp.stopRecording()
                        print("[GOPRO-AUTO] shutter stop OK, confirming to backend...")
                        guard let token = vm.authManager.accessToken,
                              let sessionUuid = vm.sessionUuid else {
                            print("[GOPRO-AUTO] confirm skipped: no auth/session")
                            return
                        }
                        let cycles = try await MultiCameraAPIClient.listCycles(token: token, uuid: sessionUuid)
                        guard let cycle = cycles.max(by: { $0.cycleIndex < $1.cycleIndex }) else {
                            print("[GOPRO-AUTO] confirm skipped: no cycle found")
                            return
                        }
                        guard let cd = cycle.cycleDevices.first(where: { $0.sessionDeviceId == goProDeviceId }) else {
                            print("[GOPRO-AUTO] confirm skipped: no cycle_device for GoPro")
                            return
                        }
                        let ts = Self.isoNow()
                        _ = try await MultiCameraAPIClient.confirmDeviceStop(
                            token: token, uuid: sessionUuid, cycleId: cycle.id,
                            sessionDeviceId: goProDeviceId, stoppedAt: ts,
                            cycleDeviceRevision: cd.revision
                        )
                        print("[GOPRO-AUTO] confirmDeviceStop OK cycleId=\(cycle.id)")
                    } catch {
                        print("[GOPRO-AUTO] stop FAILED: \(error)")
                    }
                }
            case .goProHttpDiag:
                print("[GOPRO-DIAG] === GoPro HERO13 HTTP Diagnostics ===")
                let gp = GoProConnectionManager.shared
                print("[GOPRO-DIAG] connection_state=\(gp.state)")
                print("[GOPRO-DIAG] recording_state=\(gp.recordingState)")
                print("[GOPRO-DIAG] camera_status=\(String(describing: gp.cameraStatus))")
                Task {
                    let transport = GoProHTTPClientTransport()

                    // 1. HTTP reachability
                    let reachable = await transport.isReachable(timeout: 5)
                    print("[GOPRO-DIAG] http_reachable=\(reachable)")

                    // 2. Camera state (firmware version, battery, recording)
                    do {
                        let data = try await transport.get(path: GoProSpec.cameraStatePath, timeout: 5)
                        let text = String(data: data, encoding: .utf8) ?? "(binary \(data.count)B)"
                        print("[GOPRO-DIAG] camera_state_response=\(text.prefix(800))")
                    } catch {
                        print("[GOPRO-DIAG] camera_state_error=\(error)")
                    }

                    // 3. Preview stream start endpoint (HERO13 validation)
                    print("[GOPRO-DIAG] testing preview stream: GET \(GoProSpec.streamStartPath)...")
                    do {
                        let data = try await transport.get(path: GoProSpec.streamStartPath, timeout: 5)
                        let text = String(data: data, encoding: .utf8) ?? "(binary \(data.count)B)"
                        print("[GOPRO-DIAG] stream_start_response=\(text.prefix(500))")
                        print("[GOPRO-DIAG] stream_start=SUCCESS — preview should be available on UDP:\(GoProSpec.previewStreamPort)")
                    } catch {
                        print("[GOPRO-DIAG] stream_start_error=\(error)")
                    }

                    // 4. Preview stream stop (cleanup)
                    do {
                        _ = try await transport.get(path: GoProSpec.streamStopPath, timeout: 5)
                        print("[GOPRO-DIAG] stream_stop=OK")
                    } catch {
                        print("[GOPRO-DIAG] stream_stop_error=\(error)")
                    }

                    // 5. Backend reachable (cellular alongside GoPro WiFi)
                    if let token = vm.authManager.accessToken {
                        do {
                            let session = try await MultiCameraAPIClient.getSession(token: token, uuid: vm.sessionUuid ?? "none")
                            print("[GOPRO-DIAG] backend_reachable=true session_status=\(session.status.rawValue)")
                        } catch {
                            print("[GOPRO-DIAG] backend_reachable=false error=\(error)")
                        }
                    } else {
                        print("[GOPRO-DIAG] backend_reachable=unknown (no token)")
                    }
                    print("[GOPRO-DIAG] === end ===")
                }
            case .goProStatus:
                let gp = GoProConnectionManager.shared
                print("[GOPRO-AUTO] status connection=\(gp.state) recording=\(gp.recordingState) battery=\(gp.cameraStatus?.batteryLevel ?? -1)")
            case .goProMediaList:
                let gp = GoProConnectionManager.shared
                print("[GOPRO-AUTO] fetching media list...")
                Task {
                    if let data = await gp.fetchMediaList(),
                       let text = String(data: data, encoding: .utf8) {
                        print("[GOPRO-MEDIA-BEGIN]\n\(text)\n[GOPRO-MEDIA-END]")
                    } else {
                        print("[GOPRO-AUTO] media list: unavailable")
                    }
                }
            case .goProDownloadLatest:
                print("[GOPRO-AUTO] downloading latest GoPro media...")
                Task {
                    let gp = GoProConnectionManager.shared
                    guard let listData = await gp.fetchMediaList(),
                          let json = try? JSONSerialization.jsonObject(with: listData) as? [String: Any],
                          let media = json["media"] as? [[String: Any]],
                          let lastDir = media.last,
                          let files = lastDir["fs"] as? [[String: Any]],
                          let lastFile = files.last,
                          let filename = lastFile["n"] as? String,
                          let dirName = lastDir["d"] as? String else {
                        print("[GOPRO-AUTO] download: no media found")
                        return
                    }
                    let path = "\(GoProSpec.mediaDownloadBase)/\(dirName)/\(filename)"
                    print("[GOPRO-AUTO] downloading: \(path)...")
                    let transport = GoProHTTPClientTransport()
                    do {
                        let data = try await transport.get(path: path, timeout: 60)
                        let outputDir = FileManager.default.temporaryDirectory.appendingPathComponent("gopro_downloads", isDirectory: true)
                        try? FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
                        let outputFile = outputDir.appendingPathComponent(filename)
                        try data.write(to: outputFile)
                        print("[GOPRO-DOWNLOAD] saved: \(outputFile.path) size=\(data.count)")
                    } catch {
                        print("[GOPRO-AUTO] download failed: \(error)")
                    }
                }
            case .skeletonProcess:
                print("[SKELETON] starting skeleton processing on local video...")
                Task {
                    let processor = SkeletonProcessor()
                    guard let videoURL = captureManager.outputFileURL else {
                        print("[SKELETON] no local video file to process")
                        return
                    }
                    let sessionUuid = vm.sessionUuid ?? "unknown"
                    let deviceId = vm.sessionDeviceId.map { "\($0)" } ?? "unknown"
                    await processor.process(videoURL: videoURL, sessionUuid: sessionUuid, deviceId: deviceId)
                    switch processor.state {
                    case .completed(let frames, let joints):
                        if let url = processor.outputURL {
                            print("[SKELETON-RESULT] file=\(url.path) frames=\(frames) joints=\(joints)")
                        }
                    case .failed(let msg):
                        print("[SKELETON-RESULT] FAILED: \(msg)")
                    default:
                        print("[SKELETON-RESULT] unexpected state: \(processor.state)")
                    }
                }
            case .captureInfo:
                let fileURL = captureManager.outputFileURL
                let fileSize: Int = {
                    guard let url = fileURL else { return 0 }
                    return (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
                }()
                print("[CAPTURE-INFO] state=\(captureManager.state) outputFile=\(fileURL?.path ?? "nil") size=\(fileSize)")
            case .networkRoutingDiag(let label):
                Task { await BackendNetworkDiagnostics.probe(label: label) }
            case .goProPreviewPOC(let durationSeconds):
                Task {
                    let diag = await GoProStreamProbe.shared.run(durationSeconds: durationSeconds)
                    GoProStreamDiagWriter.write(diag)
                }
            }
        }
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
        .fullScreenCover(isPresented: $showCaptureView) {
            if vm.isController {
                InstructorDashboardView(
                    captureManager: captureManager,
                    streamService: streamService,
                    orchestrator: orchestrator,
                    vm: vm
                )
                .onAppear { streamService.start() }
                .onDisappear { streamService.stop() }
            } else {
                PlayerCaptureView(
                    captureManager: captureManager,
                    playerOrchestrator: playerOrchestrator
                )
                .onAppear {
                    framePublisher.configure()
                    playerStreamService.start()
                    framePublisher.startCapture(streamService: playerStreamService)
                }
                .onDisappear {
                    framePublisher.stopCapture()
                    playerStreamService.stop()
                }
            }
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
                if vm.isController && vm.canStartCapture && captureManager.state == .ready {
                    Button("Begin Cycle") { vm.beginCycle() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.blue)
                }
                if vm.isController, case .capturing = orchestrator.state {
                    Button("End Cycle") { vm.endCycle() }
                        .font(.body.weight(.semibold))
                        .foregroundColor(.orange)
                }
                Button("Cancel Session") { vm.cancelSession() }
                    .foregroundColor(.red)
                Button {
                    showCaptureView = true
                } label: {
                    Label("Open Camera", systemImage: "camera.viewfinder")
                        .font(.body.weight(.semibold))
                }
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
                    LabeledRow("PlayerOrch", playerOrchestratorDescription)
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
                            "player_orch: \(playerOrchestratorDescription)",
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
            LabeledRow("DeviceRole", vm.myDeviceRole?.rawValue ?? "—")
            LabeledRow("IsController", vm.isController ? "yes" : "no")
            LabeledRow("Heartbeat", vm.sessionDeviceId != nil ? "Active" : "—")
            LabeledRow("Clock", clockSyncDescription)
            LabeledRow("Capture", captureStateDescription)
            LabeledRow("Orchestrator", orchestratorStateDescription)
            LabeledRow("RC Retry", orchestrator.revisionConflictRetried ? "yes" : "no")
            LabeledRow("Act Skip", orchestrator.sessionAlreadyActiveSkipped ? "yes" : "no")
            LabeledRow("Listener", playerListenerDescription)
            LabeledRow("PlayerOrch", playerOrchestratorDescription)
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
            "device_role: \(vm.myDeviceRole?.rawValue ?? "—")",
            "is_controller: \(vm.isController ? "yes" : "no")",
            "heartbeat: \(vm.sessionDeviceId != nil ? "active" : "—")",
            "clock: \(clockSyncDescription)",
            "capture: \(captureStateDescription)",
            "orchestrator: \(orchestratorStateDescription)",
            "revision_conflict_retried: \(orchestrator.revisionConflictRetried ? "yes" : "no")",
            "session_already_active_skipped: \(orchestrator.sessionAlreadyActiveSkipped ? "yes" : "no")",
            "player_listener: \(playerListenerDescription)",
            "player_orch: \(playerOrchestratorDescription)",
            "device_reg_error: \(vm.deviceRegisterError ?? "—")",
            "last_error: \(lastError)",
            "last_orch_failure: \(orchError)",
            "gopro_connection: \(GoProConnectionManager.shared.state.userFacingStatus)",
            "gopro_recording: \(GoProConnectionManager.shared.recordingState)",
            "gopro_battery: \(GoProConnectionManager.shared.cameraStatus?.batteryLevel.map { "\($0)%" } ?? "—")",
            "======================================",
        ].joined(separator: "\n")
    }

    private func signalGoProReady(goProDeviceId: Int?) async {
        guard let did = goProDeviceId,
              let token = vm.authManager.accessToken,
              let sessionUuid = vm.sessionUuid else {
            print("[GOPRO-AUTO] signalReady skipped: no deviceId/auth/session")
            GoProDiagRecorder.write(
                goProDeviceId: goProDeviceId, localState: "\(GoProConnectionManager.shared.state)",
                outcome: "skipped_no_context", httpStatus: nil, detail: nil
            )
            return
        }
        // Log gopro connection state at call time — if we're on GoPro WiFi here,
        // the APIClient.backendSession (waitsForConnectivity=true) handles routing.
        print("[GOPRO-AUTO] signalReady: deviceId=\(did) gopro_state=\(GoProConnectionManager.shared.state)")
        // session_device.revision server_default is 1, not 0 (see
        // app/models/multicamera_session.py) — a freshly-registered device is
        // already at revision=1. Fetch the current revision instead of
        // assuming 0, same 409-retry pattern as CycleCaptureOrchestrator.
        var revision = 0
        if let session = try? await MultiCameraAPIClient.getSession(token: token, uuid: sessionUuid),
           let device = session.devices.first(where: { $0.id == did }) {
            revision = device.revision
        }
        do {
            let sd = try await updateGoProStatus(token: token, sessionUuid: sessionUuid, did: did, revision: revision)
            print("[GOPRO-AUTO] signalReady OK: GoPro device \(did) → ready (rev=\(sd.revision))")
            GoProDiagRecorder.write(
                goProDeviceId: did, localState: "\(GoProConnectionManager.shared.state)",
                outcome: "signalReady_ok", httpStatus: nil, detail: "revision=\(sd.revision)"
            )
        } catch {
            let httpErr = (error as? APIError).flatMap {
                if case .httpError(let code, let detail) = $0 { return (code, detail) } else { return nil }
            }
            // 409 on first attempt: another update raced us to bump the
            // revision between getSession and the PATCH — refetch once more
            // and retry, exactly like CycleCaptureOrchestrator's begin/end-cycle retry.
            if httpErr?.0 == 409,
               let session = try? await MultiCameraAPIClient.getSession(token: token, uuid: sessionUuid),
               let device = session.devices.first(where: { $0.id == did }) {
                do {
                    let sd = try await updateGoProStatus(token: token, sessionUuid: sessionUuid, did: did, revision: device.revision)
                    print("[GOPRO-AUTO] signalReady OK after 409 retry: GoPro device \(did) → ready (rev=\(sd.revision))")
                    GoProDiagRecorder.write(
                        goProDeviceId: did, localState: "\(GoProConnectionManager.shared.state)",
                        outcome: "signalReady_ok_after_409_retry", httpStatus: nil, detail: "revision=\(sd.revision)"
                    )
                    return
                } catch {
                    let retryHttpErr = (error as? APIError).flatMap {
                        if case .httpError(let code, let detail) = $0 { return (code, detail) } else { return nil }
                    }
                    print("[GOPRO-AUTO] signalReady FAILED after 409 retry: \(error)")
                    GoProDiagRecorder.write(
                        goProDeviceId: did, localState: "\(GoProConnectionManager.shared.state)",
                        outcome: "signalReady_failed_after_409_retry",
                        httpStatus: retryHttpErr?.0, detail: retryHttpErr?.1 ?? "\(error)"
                    )
                    return
                }
            }
            let urlErr = (error as? APIError).flatMap {
                if case .networkError(let e) = $0 { return e as? URLError } else { return nil }
            }
            let errDetail = urlErr.map { "URLError(\($0.code.rawValue))" } ?? "\(error)"
            print("[GOPRO-AUTO] signalReady FAILED: \(errDetail)")
            GoProDiagRecorder.write(
                goProDeviceId: did, localState: "\(GoProConnectionManager.shared.state)",
                outcome: "signalReady_failed", httpStatus: httpErr?.0, detail: httpErr?.1 ?? errDetail
            )
        }
    }

    private func updateGoProStatus(token: String, sessionUuid: String, did: Int, revision: Int) async throws -> SessionDeviceDTO {
        try await MultiCameraAPIClient.updateDeviceStatus(
            token: token, uuid: sessionUuid,
            sessionDeviceId: did, targetStatus: .ready, deviceRevision: revision
        )
    }

    private func waitAndSignalGoProReady(goProDeviceId: Int?) async {
        let gp = GoProConnectionManager.shared
        let deadline = Date().addingTimeInterval(45)
        while Date() < deadline {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if case .ready = gp.state {
                print("[GOPRO-AUTO] GoPro reached .ready state")
                await signalGoProReady(goProDeviceId: goProDeviceId)
                return
            }
            if case .failed(let err) = gp.state {
                print("[GOPRO-AUTO] GoPro connect failed: \(err)")
                GoProDiagRecorder.write(
                    goProDeviceId: goProDeviceId, localState: "\(gp.state)",
                    outcome: "connect_failed", httpStatus: nil, detail: "\(err)"
                )
                return
            }
        }
        print("[GOPRO-AUTO] GoPro connect timeout (45s), state=\(gp.state)")
        GoProDiagRecorder.write(
            goProDeviceId: goProDeviceId, localState: "\(gp.state)",
            outcome: "wait_timeout_45s", httpStatus: nil, detail: nil
        )
    }

    private static func isoNow() -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: Date())
    }

    private static var cachedUserId: Int? {
        let v = UserDefaults.standard.integer(forKey: "lfa_current_user_id")
        return v > 0 ? v : nil
    }

    // MARK: — MC1-AUTO-2: console-based snapshot dump for the regression runner

    private func dumpSnapshotToConsole() {
        let text: String
        if case .inLobby(let session) = vm.state {
            text = buildSnapshotText(session)
        } else {
            text = "=== MC1 Session Lab Debug Snapshot ===\nstate: \(vm.state)\n======================================"
        }
        print("[MC1-SNAPSHOT-BEGIN]\n\(text)\n[MC1-SNAPSHOT-END]")
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

    private var playerOrchestratorDescription: String {
        switch playerOrchestrator.state {
        case .idle:                         return "idle"
        case .waitingForStart(let id):      return "waitingForStart(#\(id))"
        case .capturing(let id):            return "capturing(#\(id)) ●"
        case .confirmed(let id):            return "confirmed(#\(id)) ✓"
        case .stoppingCapture(let id):      return "stoppingCapture(#\(id)) ◼"
        case .confirmedStop(let id):        return "confirmedStop(#\(id)) ✓✓"
        case .skippedCycle(let id):         return "skippedCycle(#\(id)) ⏭"
        case .failed(let msg):              return "failed: \(msg)"
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

// MARK: — GoPro ready-signal diagnostics (Block 1)
//
// idevicesyslog does not reliably capture Swift print() output on physical
// devices (privacy redaction varies by attach timing/lock state), so the
// outcome of every signalGoProReady attempt is also persisted to a fixed
// path in the app's Documents directory. The regression script pulls this
// file directly via `devicectl device copy from --domain-type
// appDataContainer`, which needs no console log parsing at all.
private struct GoProDiagRecord: Codable {
    let timestamp: String
    let goProDeviceId: Int?
    let localState: String
    let outcome: String
    let httpStatus: Int?
    let detail: String?
}

enum GoProDiagRecorder {
    static let fileName = "gopro_diag.json"

    static func write(goProDeviceId: Int?, localState: String, outcome: String,
                       httpStatus: Int?, detail: String?) {
        let record = GoProDiagRecord(
            timestamp: ISO8601DateFormatter().string(from: Date()),
            goProDeviceId: goProDeviceId, localState: localState,
            outcome: outcome, httpStatus: httpStatus, detail: detail
        )
        guard let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first,
              let data = try? JSONEncoder().encode(record) else { return }
        let url = docs.appendingPathComponent(fileName)
        try? data.write(to: url, options: .atomic)
    }
}
#endif

import SwiftUI

#if DEBUG
struct InstructorDashboardView: View {

    @ObservedObject var captureManager: SessionCaptureManager
    @ObservedObject var streamService: CameraStreamService
    @ObservedObject var orchestrator: CycleCaptureOrchestrator
    @ObservedObject var vm: MultiCameraSessionViewModel
    @Environment(\.presentationMode) private var presentationMode

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 0) {
                topBar
                cameraGrid
                statusBar
                controlBar
            }
        }
        .statusBarHidden(true)
    }

    // MARK: - Session device list (source of truth for panels)

    private var sessionDevices: [SessionDeviceDTO] {
        guard case .inLobby(let session) = vm.state else { return [] }
        return session.devices.filter { $0.removedAt == nil }
    }

    // Ordering: instructor first, then players (by id), then auxiliary cameras (GoPro)
    private var orderedPanels: [SessionDeviceDTO] {
        let rank: (MCDeviceRole) -> Int = {
            switch $0 {
            case .instructorPrimary: return 0
            case .playerPrimary:     return 1
            case .playerSecondary:   return 2
            case .auxiliaryCamera:   return 3
            }
        }
        return sessionDevices.sorted {
            let ra = rank($0.deviceRole), rb = rank($1.deviceRole)
            return ra != rb ? ra < rb : $0.id < $1.id
        }
    }

    // MARK: - Top bar

    private var topBar: some View {
        HStack {
            Button { presentationMode.wrappedValue.dismiss() } label: {
                Image(systemName: "chevron.left")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(.white)
                    .padding(8)
                    .background(.black.opacity(0.5))
                    .clipShape(Circle())
            }
            Spacer()
            RecordingOverlay(isRecording: isRecording)
            Spacer()
            sessionBadge
        }
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .padding(.bottom, 4)
        .background(.black)
    }

    private var sessionBadge: some View {
        HStack(spacing: 4) {
            Circle().fill(vm.isClockSynced ? .green : .orange).frame(width: 8, height: 8)
            Text(vm.sessionUuid?.prefix(8) ?? "—")
                .font(.system(size: 11, design: .monospaced))
                .foregroundColor(.white.opacity(0.7))
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(.black.opacity(0.5))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    // MARK: - Camera grid (dynamic — driven by session device list)

    private var cameraGrid: some View {
        HStack(spacing: 2) {
            if orderedPanels.isEmpty {
                // Session not yet joined or devices not registered
                Color(white: 0.08)
                    .overlay(
                        Text("Waiting for devices…")
                            .font(.system(size: 12, design: .monospaced))
                            .foregroundColor(.white.opacity(0.4))
                    )
                    .aspectRatio(3.0/4.0, contentMode: .fit)
            } else {
                ForEach(orderedPanels, id: \.id) { device in
                    VStack(spacing: 0) {
                        deviceLabel(device)
                        devicePreview(device)
                            .aspectRatio(3.0/4.0, contentMode: .fit)
                            .clipped()
                    }
                    .background(Color(white: 0.08))
                }
            }
        }
    }

    private func deviceLabel(_ device: SessionDeviceDTO) -> some View {
        let label = panelLabel(for: device)
        let color = panelStatusColor(for: device)
        return HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label)
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundColor(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 4)
        .background(Color(white: 0.1))
    }

    @ViewBuilder
    private func devicePreview(_ device: SessionDeviceDTO) -> some View {
        if device.id == vm.sessionDeviceId {
            // This device — local camera
            CapturePreviewLayer(session: captureManager.previewSession)
        } else if device.deviceRole == .auxiliaryCamera {
            // GoPro (or other managed auxiliary camera)
            goProPreviewPanel
        } else {
            // Remote participant device — WebRTC/peer stream
            RemoteCameraView(streamService: streamService)
        }
    }

    // GoPro preview uses GoProConnectionManager real-time state
    private var goProPreviewPanel: some View {
        ZStack {
            Color.black
            VStack(spacing: 6) {
                Image(systemName: goProIcon)
                    .font(.system(size: 24))
                    .foregroundColor(goProRealtimeColor)
                Text(goProRealtimeLabel)
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundColor(goProRealtimeColor)
                    .multilineTextAlignment(.center)
                if let bat = GoProConnectionManager.shared.cameraStatus?.batteryLevel {
                    Text("\(bat)%")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.5))
                }
            }
        }
    }

    // MARK: - Status bar (dynamic)

    private var statusBar: some View {
        HStack(spacing: 1) {
            if orderedPanels.isEmpty {
                statusCell("—", "waiting", .gray)
            } else {
                ForEach(orderedPanels, id: \.id) { device in
                    statusCell(
                        panelLabel(for: device),
                        panelStatusText(for: device),
                        panelStatusColor(for: device)
                    )
                }
            }
        }
        .background(Color(white: 0.1))
    }

    private func statusCell(_ label: String, _ status: String, _ color: Color) -> some View {
        VStack(spacing: 2) {
            Text(label).font(.system(size: 10, weight: .semibold)).foregroundColor(.white)
            Text(status).font(.system(size: 9, design: .monospaced)).foregroundColor(color)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .background(Color(white: 0.12))
    }

    // MARK: - Panel label helpers

    private func panelLabel(for device: SessionDeviceDTO) -> String {
        switch device.deviceRole {
        case .instructorPrimary:
            return "Instructor"
        case .playerPrimary, .playerSecondary:
            let players = orderedPanels.filter {
                $0.deviceRole == .playerPrimary || $0.deviceRole == .playerSecondary
            }
            if players.count == 1 { return "Player" }
            let idx = players.firstIndex(where: { $0.id == device.id }) ?? 0
            return "Player \(idx + 1)"
        case .auxiliaryCamera:
            let aux = orderedPanels.filter { $0.deviceRole == .auxiliaryCamera }
            if aux.count == 1 { return "GoPro" }
            let idx = aux.firstIndex(where: { $0.id == device.id }) ?? 0
            return "GoPro \(idx + 1)"
        }
    }

    private func panelStatusColor(for device: SessionDeviceDTO) -> Color {
        if device.deviceRole == .auxiliaryCamera {
            return goProRealtimeColor
        }
        if device.id == vm.sessionDeviceId {
            return localCaptureColor
        }
        return backendStatusColor(device.status)
    }

    private func panelStatusText(for device: SessionDeviceDTO) -> String {
        if device.deviceRole == .auxiliaryCamera {
            return goProRealtimeLabel
        }
        if device.id == vm.sessionDeviceId {
            return localCaptureStatus
        }
        return device.status.rawValue
    }

    // MARK: - Local device status (this iPhone)

    private var localCaptureColor: Color {
        switch captureManager.state {
        case .ready:     return .green
        case .capturing: return .red
        case .failed:    return .orange
        default:         return .gray
        }
    }

    private var localCaptureStatus: String {
        switch orchestrator.state {
        case .idle:             return captureManager.state == .ready ? "ready" : "preparing"
        case .creating:         return "creating cycle"
        case .scheduling:       return "scheduling"
        case .waitingForStart:  return "starting"
        case .stopping:         return "stopping"
        case .completed:        return "done"
        case .failed:           return "error"
        case .capturing:        return "recording"
        }
    }

    // MARK: - Backend status color (remote devices polled from API)

    private func backendStatusColor(_ status: MCDeviceStatus) -> Color {
        switch status {
        case .ready:        return .green
        case .recording:    return .red
        case .registered:   return .gray
        case .stopped:      return .green
        case .disconnected: return .red
        case .error:        return .orange
        }
    }

    // MARK: - GoPro real-time state (GoProConnectionManager singleton)

    private var goProRealtimeColor: Color {
        let gp = GoProConnectionManager.shared
        if gp.recordingState == .recording     { return .red }
        if case .ready = gp.state             { return .green }
        if case .failed = gp.recordingState   { return .orange }
        return .gray
    }

    private var goProRealtimeLabel: String {
        let gp = GoProConnectionManager.shared
        switch gp.recordingState {
        case .recording:        return "REC"
        case .starting:         return "starting…"
        case .stopping:         return "stopping…"
        case .stopped:          return "stopped"
        case .failed(let m):    return "err:\(m.prefix(15))"
        case .idle:
            if case .ready = gp.state { return "ready" }
            return "offline"
        }
    }

    private var goProIcon: String {
        let gp = GoProConnectionManager.shared
        if gp.recordingState == .recording { return "record.circle.fill" }
        if case .ready = gp.state          { return "camera.fill" }
        return "camera"
    }

    // MARK: - Control bar

    private var controlBar: some View {
        VStack(spacing: 8) {
            if canBeginCycle {
                Button { vm.beginCycle() } label: {
                    Label("BEGIN CYCLE", systemImage: "record.circle")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(.red)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
            if canEndCycle {
                Button { vm.endCycle() } label: {
                    Label("END CYCLE", systemImage: "stop.circle.fill")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 12)
                        .background(.gray)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
            if !canBeginCycle && !canEndCycle {
                Text(orchestratorLabel)
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(.white.opacity(0.5))
                    .padding(.vertical, 6)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.black)
    }

    // MARK: - Computed helpers

    private var isRecording: Bool {
        if case .capturing = orchestrator.state { return true }
        return false
    }
    private var canBeginCycle: Bool { vm.isController && vm.canStartCapture && !isRecording }
    private var canEndCycle: Bool { if case .capturing = orchestrator.state { return true }; return false }

    private var orchestratorLabel: String {
        switch orchestrator.state {
        case .idle:             return captureManager.state == .ready ? "Ready" : "Preparing…"
        case .creating:         return "Creating cycle…"
        case .scheduling:       return "Scheduling…"
        case .waitingForStart:  return "Starting…"
        case .stopping:         return "Stopping…"
        case .completed:        return "Cycle complete"
        case .failed(let f):    return "Error: \(f)"
        case .capturing:        return "Recording"
        }
    }
}
#endif

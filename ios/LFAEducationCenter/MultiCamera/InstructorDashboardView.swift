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

    // MARK: - Top bar

    private var topBar: some View {
        HStack {
            Button {
                presentationMode.wrappedValue.dismiss()
            } label: {
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

    // MARK: - Camera grid (3 panels: local + iPad remote + GoPro)

    private var cameraGrid: some View {
        HStack(spacing: 2) {
            VStack(spacing: 0) {
                cameraLabel("iPhone", color: localCameraColor)
                CapturePreviewLayer(session: captureManager.previewSession)
                    .aspectRatio(3.0/4.0, contentMode: .fit)
                    .clipped()
            }
            .background(Color(white: 0.08))

            VStack(spacing: 0) {
                cameraLabel("iPad", color: remoteCameraColor)
                RemoteCameraView(streamService: streamService)
                    .aspectRatio(3.0/4.0, contentMode: .fit)
                    .clipped()
            }
            .background(Color(white: 0.08))

            VStack(spacing: 0) {
                cameraLabel("GoPro", color: goProPanelColor)
                goProPreviewPanel
                    .aspectRatio(3.0/4.0, contentMode: .fit)
                    .clipped()
            }
            .background(Color(white: 0.08))
        }
    }

    private var goProPreviewPanel: some View {
        ZStack {
            Color.black
            VStack(spacing: 6) {
                Image(systemName: goProIcon)
                    .font(.system(size: 24))
                    .foregroundColor(goProPanelColor)
                Text(goProLabel)
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundColor(goProPanelColor)
                    .multilineTextAlignment(.center)
                if let bat = GoProConnectionManager.shared.cameraStatus?.batteryLevel {
                    Text("\(bat)%")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.5))
                }
            }
        }
    }

    private var goProIcon: String {
        let gp = GoProConnectionManager.shared
        switch gp.recordingState {
        case .recording: return "record.circle.fill"
        case .idle:
            if case .ready = gp.state { return "camera.fill" }
            return "camera"
        default: return "camera"
        }
    }

    private var goProLabel: String {
        let gp = GoProConnectionManager.shared
        switch gp.recordingState {
        case .recording: return "REC"
        case .starting: return "starting..."
        case .stopping: return "stopping..."
        case .stopped: return "stopped"
        case .failed(let m): return "err:\(m.prefix(15))"
        case .idle:
            if case .ready = gp.state { return "ready" }
            return "offline"
        }
    }

    private var goProPanelColor: Color {
        let gp = GoProConnectionManager.shared
        if gp.recordingState == .recording { return .red }
        if case .ready = gp.state { return .green }
        if case .failed = gp.recordingState { return .orange }
        return .gray
    }

    private func cameraLabel(_ text: String, color: Color) -> some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(text)
                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                .foregroundColor(.white)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 4)
        .background(Color(white: 0.1))
    }

    private var localCameraColor: Color {
        switch captureManager.state {
        case .ready: return .green
        case .capturing: return .red
        case .failed: return .orange
        default: return .gray
        }
    }

    private var remoteCameraColor: Color {
        switch streamService.peerState {
        case .connected where streamService.lastReceivedFrame != nil && streamService.lastFrameAge < 2:
            return .green
        case .connected:
            return .yellow
        case .connecting:
            return .orange
        case .disconnected:
            return .red
        }
    }

    // MARK: - Status bar

    private var statusBar: some View {
        HStack(spacing: 1) {
            statusCell("iPhone", ipadStatus, ipadColor)
            statusCell("iPad", remoteStatus, remoteCameraColor)
            goProStatusCell
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

    private var goProStatusCell: some View {
        let gp = GoProConnectionManager.shared
        let isConn: Bool = { if case .ready = gp.state { return true }; return false }()
        let color: Color = gp.recordingState == .recording ? .red : isConn ? .green : .gray
        let status: String = {
            switch gp.recordingState {
            case .recording: return "recording"
            case .idle: return isConn ? "ready" : "offline"
            default: return "\(gp.recordingState)"
            }
        }()
        return VStack(spacing: 2) {
            Text("GoPro").font(.system(size: 10, weight: .semibold)).foregroundColor(.white)
            Text(status).font(.system(size: 9, design: .monospaced)).foregroundColor(color)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .background(Color(white: 0.12))
    }

    private var ipadStatus: String {
        switch orchestrator.state {
        case .idle: return captureManager.state == .ready ? "ready" : "preparing"
        case .capturing: return "recording"
        case .completed: return "done"
        case .failed: return "error"
        default: return "..."
        }
    }

    private var ipadColor: Color {
        switch orchestrator.state {
        case .capturing: return .red
        case .completed: return .green
        case .failed: return .orange
        case .idle: return captureManager.state == .ready ? .green : .gray
        default: return .yellow
        }
    }

    private var remoteStatus: String {
        switch streamService.peerState {
        case .disconnected: return "offline"
        case .connecting: return "connecting"
        case .connected where streamService.lastReceivedFrame != nil: return "streaming"
        case .connected: return "connected"
        }
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

    // MARK: - Helpers

    private var isRecording: Bool { if case .capturing = orchestrator.state { return true }; return false }
    private var canBeginCycle: Bool { vm.isController && vm.canStartCapture && !isRecording }
    private var canEndCycle: Bool { if case .capturing = orchestrator.state { return true }; return false }

    private var orchestratorLabel: String {
        switch orchestrator.state {
        case .idle: return captureManager.state == .ready ? "Ready" : "Preparing..."
        case .creating: return "Creating cycle..."
        case .scheduling: return "Scheduling..."
        case .waitingForStart: return "Starting..."
        case .stopping: return "Stopping..."
        case .completed: return "Cycle complete"
        case .failed(let f): return "Error: \(f)"
        case .capturing: return "Recording"
        }
    }
}
#endif

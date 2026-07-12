import SwiftUI

#if DEBUG
struct InstructorCaptureView: View {

    @ObservedObject var vm: MultiCameraSessionViewModel
    @ObservedObject var captureManager: SessionCaptureManager
    @ObservedObject var orchestrator: CycleCaptureOrchestrator
    @ObservedObject var playerListener: PlayerCycleListener
    @Environment(\.presentationMode) private var presentationMode

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                cameraPreview
                deviceStatusRow
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

    // MARK: - Camera preview (iPad local camera)

    private var cameraPreview: some View {
        ZStack {
            CapturePreviewLayer(session: captureManager.previewSession)

            if captureManager.state == .idle || captureManager.state == .configuring {
                VStack(spacing: 8) {
                    ProgressView().tint(.white)
                    Text("Preparing camera...")
                        .font(.caption)
                        .foregroundColor(.white.opacity(0.7))
                }
            }
        }
        .frame(maxWidth: .infinity)
        .aspectRatio(4.0/3.0, contentMode: .fit)
        .clipped()
    }

    // MARK: - Device status row

    private var deviceStatusRow: some View {
        HStack(spacing: 1) {
            devicePanel(
                icon: "ipad",
                label: "iPad",
                status: ipadStatus,
                color: ipadColor
            )
            devicePanel(
                icon: "iphone",
                label: "iPhone",
                status: iphoneStatus,
                color: iphoneColor
            )
            goProPanel
        }
        .frame(maxWidth: .infinity)
        .background(Color(white: 0.1))
    }

    private func devicePanel(icon: String, label: String, status: String, color: Color, extra: String? = nil) -> some View {
        VStack(spacing: 4) {
            HStack(spacing: 4) {
                Image(systemName: icon)
                    .font(.system(size: 12))
                    .foregroundColor(color)
                Text(label)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.white)
            }
            Text(status)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundColor(color)
            if let extra {
                Text(extra)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.white.opacity(0.5))
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Color(white: 0.12))
    }

    // MARK: - Control bar

    private var controlBar: some View {
        VStack(spacing: 12) {
            if canBeginCycle {
                Button {
                    vm.beginCycle()
                } label: {
                    Label("BEGIN CYCLE", systemImage: "record.circle")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(.red)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }

            if canEndCycle {
                Button {
                    vm.endCycle()
                } label: {
                    Label("END CYCLE", systemImage: "stop.circle.fill")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(.gray)
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                }
            }

            if !canBeginCycle && !canEndCycle {
                Text(orchestratorStatusText)
                    .font(.system(size: 13, weight: .medium, design: .monospaced))
                    .foregroundColor(.white.opacity(0.6))
                    .padding(.vertical, 8)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.black)
    }

    // MARK: - State helpers

    private var isRecording: Bool {
        if case .capturing = orchestrator.state { return true }
        return false
    }

    private var canBeginCycle: Bool {
        vm.isController && vm.canStartCapture && !isRecording
    }

    private var canEndCycle: Bool {
        if case .capturing = orchestrator.state { return true }
        return false
    }

    // iPad status
    private var ipadStatus: String {
        switch orchestrator.state {
        case .idle: return "ready"
        case .creating, .scheduling: return "preparing..."
        case .waitingForStart: return "waiting..."
        case .capturing: return "recording"
        case .stopping: return "stopping..."
        case .completed: return "done"
        case .failed(let f): return "error: \(f)"
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

    // iPhone status
    private var iphoneStatus: String {
        switch playerListener.state {
        case .idle: return "idle"
        case .waitingForCycle: return "waiting"
        case .pendingCycleDetected: return "pending..."
        case .recordingDetected: return "recording"
        case .stoppingDetected: return "stopping"
        case .failed(let msg): return "error: \(msg)"
        }
    }

    private var iphoneColor: Color {
        switch playerListener.state {
        case .recordingDetected: return .red
        case .waitingForCycle: return .green
        case .failed: return .orange
        default: return .gray
        }
    }

    // GoPro detailed panel
    private var goProPanel: some View {
        let gp = GoProConnectionManager.shared
        let isConnected: Bool = { if case .ready = gp.state { return true }; return false }()
        let isRec: Bool = gp.recordingState == .recording

        let borderColor: Color = {
            if isRec { return .red }
            if case .failed = gp.recordingState { return .orange }
            if isConnected { return .green }
            if case .failed = gp.state { return .orange }
            return .gray
        }()

        return VStack(spacing: 3) {
            HStack(spacing: 4) {
                Image(systemName: "camera.fill")
                    .font(.system(size: 12))
                    .foregroundColor(borderColor)
                Text("GoPro")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.white)
            }

            HStack(spacing: 4) {
                Circle().fill(isConnected ? .green : .orange).frame(width: 6, height: 6)
                Text(isConnected ? "connected" : "not connected")
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(isConnected ? .green : .orange)
            }

            Text(goProRecordingLabel(gp))
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundColor(isRec ? .red : .white.opacity(0.7))

            if let bat = gp.cameraStatus?.batteryLevel {
                HStack(spacing: 2) {
                    Image(systemName: bat > 20 ? "battery.75" : "battery.25")
                        .font(.system(size: 8))
                        .foregroundColor(bat > 20 ? .green : .red)
                    Text("\(bat)%")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(.white.opacity(0.6))
                }
            }

            if let sd = gp.cameraStatus?.sdCardSpaceRemaining {
                Text("SD: \(sd) MB")
                    .font(.system(size: 8, design: .monospaced))
                    .foregroundColor(.white.opacity(0.4))
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(Color(white: 0.12))
        .overlay(
            RoundedRectangle(cornerRadius: 0)
                .stroke(borderColor.opacity(0.3), lineWidth: 1)
        )
    }

    private func goProRecordingLabel(_ gp: GoProConnectionManager) -> String {
        switch gp.recordingState {
        case .idle:
            if case .ready = gp.state { return "ready" }
            return gp.state.userFacingStatus
        case .starting: return "starting..."
        case .recording: return "recording"
        case .stopping: return "stopping..."
        case .stopped: return "stopped"
        case .failed(let msg): return "err: \(msg.prefix(20))"
        }
    }

    private var orchestratorStatusText: String {
        switch orchestrator.state {
        case .idle: return captureManager.state == .ready ? "Ready to begin" : "Preparing..."
        case .creating: return "Creating cycle..."
        case .scheduling: return "Scheduling..."
        case .waitingForStart: return "Starting..."
        case .stopping: return "Stopping..."
        case .completed: return "Cycle complete"
        case .failed(let f): return "Failed: \(f)"
        case .capturing: return "Recording..."
        }
    }
}
#endif

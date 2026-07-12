import SwiftUI

#if DEBUG
/// MC2-PR3 status dashboard — backend-polling only.
///
/// The previous live-video dashboard drove three on-device pose processors
/// plus a single-peer MPC stream on the main actor; with two players sending
/// simultaneously the main thread saturated and the iPad UI froze (2026-07-12
/// dual-player run RCA). This view renders NOTHING but polled backend state:
/// no capture session, no MPC, no frame decoding, no inference. Every panel is
/// keyed by session_device_id, so two players can never be cross-attributed.
struct InstructorDashboardView: View {

    @ObservedObject var orchestrator: CycleCaptureOrchestrator
    @ObservedObject var vm: MultiCameraSessionViewModel

    @Environment(\.presentationMode) private var presentationMode

    /// Re-render tick so heartbeat ages advance between 3 s polls.
    @State private var now = Date()
    private let clock = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 0) {
                topBar
                statusGrid
                Spacer(minLength: 0)
                cycleSummaryBar
                controlBar
            }
        }
        .statusBarHidden(true)
        .onReceive(clock) { now = $0 }
    }

    // MARK: - Session device list (source of truth for panels)

    private var sessionDevices: [SessionDeviceDTO] {
        guard case .inLobby(let session) = vm.state else { return [] }
        return session.devices.filter { $0.removedAt == nil }
    }

    /// Deterministic panel order: instructor, players (by id), auxiliary cameras.
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

    private var latestCycle: CaptureCycleDTO? { vm.latestCycle }

    private func cycleDevice(for device: SessionDeviceDTO) -> CaptureCycleDeviceDTO? {
        latestCycle?.cycleDevices.first { $0.sessionDeviceId == device.id }
    }

    private func panelState(for device: SessionDeviceDTO) -> DevicePanelState {
        DevicePanelStateResolver.resolve(
            device: device,
            cycleDevice: cycleDevice(for: device),
            cycle: latestCycle,
            now: now
        )
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

    // MARK: - Status grid

    private var statusGrid: some View {
        VStack(spacing: 8) {
            if orderedPanels.isEmpty {
                Text("Waiting for devices…")
                    .font(.system(size: 12, design: .monospaced))
                    .foregroundColor(.white.opacity(0.4))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 40)
            } else {
                ForEach(orderedPanels, id: \.id) { device in
                    statusTile(device)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.top, 10)
    }

    private func statusTile(_ device: SessionDeviceDTO) -> some View {
        let state = panelState(for: device)
        let cd = cycleDevice(for: device)
        return HStack(spacing: 10) {
            Circle().fill(stateColor(state)).frame(width: 12, height: 12)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(panelLabel(for: device))
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(.white)
                    Text("sd \(device.id)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.white.opacity(0.4))
                }
                Text(detailLine(device: device, cycleDevice: cd))
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(.white.opacity(0.6))
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            }
            Spacer()
            Text(state.label)
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
                .foregroundColor(stateColor(state))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(stateColor(state).opacity(0.15))
                .clipShape(Capsule())
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(Color(white: 0.1))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func detailLine(device: SessionDeviceDTO, cycleDevice cd: CaptureCycleDeviceDTO?) -> String {
        var parts: [String] = []
        if device.deviceRole == .instructorPrimary {
            parts.append("coordinator — not recording")
        } else if device.deviceRole == .auxiliaryCamera, let mgr = device.managedByDeviceId {
            parts.append("managed by sd \(mgr)")
        }
        if device.deviceRole != .auxiliaryCamera,
           let age = DevicePanelStateResolver.heartbeatAge(device: device, now: now) {
            parts.append("hb \(Int(age))s ago")
        }
        if let cd = cd {
            parts.append("cycle: \(cd.recordingStatus.rawValue)\(cd.required ? "" : " (not required)")")
        }
        return parts.isEmpty ? "status: \(device.status.rawValue)" : parts.joined(separator: " · ")
    }

    private func stateColor(_ state: DevicePanelState) -> Color {
        switch state {
        case .connected:    return .green
        case .stale:        return .yellow
        case .disconnected: return .red
        case .recording:    return .red
        case .completed:    return .green
        case .failed:       return .orange
        }
    }

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

    // MARK: - Cycle summary bar

    private var cycleSummaryBar: some View {
        HStack(spacing: 8) {
            if let cycle = latestCycle {
                Text("cycle #\(cycle.cycleIndex)")
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .foregroundColor(.white)
                Text(cycle.status.rawValue)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(cycleStatusColor(cycle.status))
                if let result = cycle.result {
                    Text(result.rawValue)
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(result == .success ? .green : .orange)
                }
            } else {
                Text("no cycle yet")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(.white.opacity(0.4))
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(Color(white: 0.08))
    }

    private func cycleStatusColor(_ status: CycleStatus) -> Color {
        switch status {
        case .recording:                        return .red
        case .completed:                        return .green
        case .failed, .aborted:                 return .orange
        case .preparing, .recordingPending, .stopping: return .yellow
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

    // MARK: - Computed helpers

    private var isRecording: Bool {
        if case .capturing = orchestrator.state { return true }
        return false
    }
    private var canBeginCycle: Bool { vm.isController && vm.canStartCapture && !isRecording }
    private var canEndCycle: Bool { if case .capturing = orchestrator.state { return true }; return false }

    private var orchestratorLabel: String {
        switch orchestrator.state {
        case .idle:             return "Ready"
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

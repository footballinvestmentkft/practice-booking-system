import SwiftUI

#if DEBUG
struct PlayerCaptureView: View {

    @ObservedObject var captureManager: SessionCaptureManager
    @ObservedObject var playerOrchestrator: PlayerCaptureOrchestrator
    @Environment(\.presentationMode) private var presentationMode

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            CapturePreviewLayer(session: captureManager.previewSession)
                .ignoresSafeArea()

            VStack {
                topBar
                Spacer()
                bottomStatus
            }
            .padding()
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
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(.white)
                    .padding(10)
                    .background(.black.opacity(0.5))
                    .clipShape(Circle())
            }
            Spacer()
            RecordingOverlay(isRecording: isRecording)
            Spacer()
            captureStateBadge
        }
    }

    // MARK: - Bottom status

    private var bottomStatus: some View {
        Group {
            if !isRecording {
                Text(statusText)
                    .font(.system(size: 14, weight: .medium, design: .monospaced))
                    .foregroundColor(.white.opacity(0.7))
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .background(.black.opacity(0.5))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    // MARK: - Helpers

    private var isRecording: Bool {
        switch playerOrchestrator.state {
        case .capturing, .confirmed: return true
        default: return false
        }
    }

    private var statusText: String {
        switch captureManager.state {
        case .idle: return "Initializing..."
        case .requestingPermissions: return "Requesting permissions..."
        case .configuring: return "Configuring camera..."
        case .ready: return "Camera ready — waiting for cycle"
        case .capturing: return "Recording"
        case .completed: return "Cycle complete"
        case .failed(let msg): return "Error: \(msg)"
        default: return "\(captureManager.state)"
        }
    }

    private var captureStateBadge: some View {
        Circle()
            .fill(captureStateColor)
            .frame(width: 12, height: 12)
            .padding(10)
            .background(.black.opacity(0.5))
            .clipShape(Circle())
    }

    private var captureStateColor: Color {
        switch captureManager.state {
        case .ready: return .green
        case .capturing: return .red
        case .completed: return .blue
        case .failed: return .orange
        default: return .gray
        }
    }
}
#endif

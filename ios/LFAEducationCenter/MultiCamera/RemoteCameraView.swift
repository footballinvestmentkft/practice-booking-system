import SwiftUI

struct RemoteCameraView: View {

    @ObservedObject var streamService: CameraStreamService

    var body: some View {
        ZStack {
            Color.black

            if let data = streamService.lastReceivedFrame,
               let uiImage = UIImage(data: data) {
                Image(uiImage: uiImage)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "video.slash")
                        .font(.system(size: 32))
                        .foregroundColor(.gray)
                    Text(statusText)
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundColor(.gray)
                }
            }

            VStack {
                Spacer()
                HStack(spacing: 12) {
                    streamBadge
                    fpsBadge
                    latencyBadge
                }
                .padding(6)
            }
        }
    }

    private var statusText: String {
        switch streamService.peerState {
        case .disconnected: return "Waiting for player..."
        case .connecting: return "Connecting..."
        case .connected(let name): return "Connected: \(name)\nWaiting for frames..."
        }
    }

    private var streamBadge: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(streamColor)
                .frame(width: 8, height: 8)
            Text(streamLabel)
                .font(.system(size: 10, weight: .medium, design: .monospaced))
                .foregroundColor(.white)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(.black.opacity(0.6))
        .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private var streamColor: Color {
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

    private var streamLabel: String {
        switch streamService.peerState {
        case .disconnected: return "offline"
        case .connecting: return "connecting"
        case .connected where streamService.lastReceivedFrame != nil: return "live"
        case .connected: return "connected"
        }
    }

    private var fpsBadge: some View {
        Text(String(format: "%.0f fps", streamService.receivedFPS))
            .font(.system(size: 10, design: .monospaced))
            .foregroundColor(.white.opacity(0.7))
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(.black.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }

    private var latencyBadge: some View {
        let age = streamService.lastFrameAge
        let color: Color = age < 0.2 ? .green : age < 1.0 ? .yellow : .red
        return Text(String(format: "%.0fms", age * 1000))
            .font(.system(size: 10, design: .monospaced))
            .foregroundColor(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(.black.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: 4))
    }
}

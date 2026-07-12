import SwiftUI

struct RecordingOverlay: View {

    let isRecording: Bool
    @State private var elapsed: TimeInterval = 0
    @State private var startDate: Date?
    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        if isRecording {
            HStack(spacing: 8) {
                Circle()
                    .fill(.red)
                    .frame(width: 12, height: 12)
                Text("REC")
                    .font(.system(size: 14, weight: .bold, design: .monospaced))
                    .foregroundColor(.red)
                Text(formattedElapsed)
                    .font(.system(size: 14, weight: .medium, design: .monospaced))
                    .foregroundColor(.white)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(.black.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .onReceive(timer) { _ in
                if let start = startDate {
                    elapsed = Date().timeIntervalSince(start)
                }
            }
            .onAppear {
                startDate = Date()
                elapsed = 0
            }
            .onDisappear {
                startDate = nil
            }
        }
    }

    private var formattedElapsed: String {
        let mins = Int(elapsed) / 60
        let secs = Int(elapsed) % 60
        return String(format: "%02d:%02d", mins, secs)
    }
}

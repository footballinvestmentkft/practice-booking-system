import SwiftUI
import AVKit

// MARK: — Player view

// Fetches the processed video via Bearer-authenticated GET, writes it to a
// temporary file, and plays it with AVPlayer (seeking supported natively).
//
// Temp file is removed on dismiss to avoid leaving stale MP4s.
// Files > 200 MB show a confirmation alert before download starts.
struct JugglingPlayerView: View {
    let video: JugglingVideoItem
    @EnvironmentObject var authManager: AuthManager
    @Environment(\.presentationMode) var presentationMode

    enum PlayerState {
        case loading
        case ready(AVPlayer)
        case error(String)
    }

    @State private var playerState: PlayerState = .loading
    @State private var tempURL: URL? = nil
    @State private var showLargeFileAlert = false
    @State private var userConfirmedLargeFile = false

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Color.black.ignoresSafeArea()

            switch playerState {
            case .loading:
                VStack(spacing: Theme.Spacing.md) {
                    ProgressView()
                        .progressViewStyle(CircularProgressViewStyle(tint: .white))
                        .scaleEffect(1.4)
                    Text("Loading video…")
                        .foregroundColor(.white.opacity(0.8))
                        .font(.subheadline)
                }

            case .ready(let player):
                VideoPlayer(player: player)
                    .ignoresSafeArea()

            case .error(let msg):
                VStack(spacing: Theme.Spacing.md) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.system(size: 48))
                        .foregroundColor(.white.opacity(0.6))
                    Text(msg)
                        .foregroundColor(.white.opacity(0.8))
                        .font(.subheadline)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, Theme.Spacing.xl)
                    Button("Close") { dismiss() }
                        .foregroundColor(Theme.Color.primary)
                        .font(.body.weight(.semibold))
                }
            }

            // Close button — always visible
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 28))
                    .foregroundColor(.white.opacity(0.85))
                    .padding(Theme.Spacing.md)
            }
            .accessibilityLabel("Close player")
        }
        .onAppear { startLoad() }
        .onDisappear { cleanup() }
        // iOS 14-compatible alert (Alert struct, no role: parameter)
        .alert(isPresented: $showLargeFileAlert) {
            let mb = video.fileSizeDisplay ?? "large file"
            return Alert(
                title: Text("Large File"),
                message: Text("This video is \(mb). Downloading may take a while on slow connections."),
                primaryButton: .default(Text("Download Anyway")) { userConfirmedLargeFile = true },
                secondaryButton: .cancel(Text("Cancel")) { dismiss() }
            )
        }
        .onChange(of: userConfirmedLargeFile) { confirmed in
            if confirmed { Task { await fetchAndPlay() } }
        }
    }

    // MARK: — Load

    private func startLoad() {
        if video.isLargeFile {
            showLargeFileAlert = true
        } else {
            Task { await fetchAndPlay() }
        }
    }

    private func fetchAndPlay() async {
        playerState = .loading
        let path = "/api/v1/users/me/juggling/videos/\(video.videoId)/media"
        do {
            let data = try await authManager.authenticatedFetchData(path: path)
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString + ".mp4")
            try data.write(to: url)
            tempURL = url
            let player = AVPlayer(url: url)
            player.play()
            playerState = .ready(player)
        } catch APIError.httpError(409, _) {
            playerState = .error("Video is still processing. Try again later.")
        } catch APIError.httpError(404, _) {
            playerState = .error("Video file not found.")
        } catch APIError.httpError(410, _) {
            playerState = .error("This video has been permanently deleted.")
        } catch APIError.unauthorized {
            playerState = .error("Session expired. Please log in again.")
        } catch APIError.networkError {
            playerState = .error("No connection. Try again.")
        } catch {
            playerState = .error("Could not load video. Please try again.")
        }
    }

    // MARK: — Cleanup

    private func dismiss() {
        cleanup()
        presentationMode.wrappedValue.dismiss()
    }

    private func cleanup() {
        // Pause player before dismissing
        if case .ready(let player) = playerState { player.pause() }
        // Remove temp file
        if let url = tempURL {
            try? FileManager.default.removeItem(at: url)
            tempURL = nil
        }
    }
}

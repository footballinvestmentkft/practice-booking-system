import SwiftUI
import AVFoundation
import AVKit

// MARK: — JugglingVideoUploadView
//
// Sheet content for B3 (video upload list integration). Presented from
// JugglingVideoListView via .sheet(isPresented:onDismiss:).
//
// Dismiss lifecycle: the "Mégse" toolbar button and swipe-to-dismiss both
// route through the parent's .sheet(onDismiss:), which calls
// viewModel.cancel() if an upload is active — no orphan upload tasks.

struct JugglingVideoUploadView: View {
    @ObservedObject var viewModel: JugglingVideoUploadViewModel
    @Binding var isPresented: Bool
    @State private var showingCamera = false
    @State private var trainingConsent = false
    @State private var adminReviewConsent = true

    var body: some View {
        NavigationView {
            VStack(spacing: Theme.Spacing.lg) {
                Spacer()
                content
                Spacer()
            }
            .padding(Theme.Spacing.xl)
            .navigationTitle("Videó feltöltése")
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("Mégse") {
                        isPresented = false
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
        .fullScreenCover(isPresented: $showingCamera) {
            JugglingDirectCameraView(
                onFinish: { url, metadata in
                    showingCamera = false
                    viewModel.captureDidFinish(tempURL: url, metadata: metadata)
                },
                onPermissionDenied: {
                    showingCamera = false
                    viewModel.cameraPermissionDenied()
                },
                onInterrupted: { url in
                    showingCamera = false
                    viewModel.captureInterrupted(tempURL: url)
                },
                onCancel: {
                    showingCamera = false
                    viewModel.cancelPreparedCapture()
                }
            )
        }
    }

    @ViewBuilder
    private var content: some View {
        switch viewModel.state {
        case .idle:
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "video.badge.plus")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.primary)
                Text("Record a juggling attempt with the front camera.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.lg)
                Button("Record with front camera") {
                    Task {
                        if await viewModel.prepareDirectCapture() {
                            showingCamera = true
                        }
                    }
                }
                    .buttonStyle(.borderedProminent)
                    .disabled(viewModel.isPreparingCapture)
            }

        case .capturePreview:
            VStack(spacing: Theme.Spacing.md) {
                if let url = viewModel.capturedPreviewURL {
                    VideoPlayer(player: AVPlayer(url: url)).frame(minHeight: 260)
                }
                Toggle("Allow human review", isOn: $adminReviewConsent)
                Toggle("Allow privacy-safe dataset use", isOn: $trainingConsent)
                Text("Uploading confirms consent to process this video. Guardian consent is required for players under 18.")
                    .font(.caption).foregroundColor(Theme.Color.muted)
                Button("Upload recording") {
                    viewModel.uploadCapturedVideo(
                        trainingConsent: trainingConsent,
                        adminReviewConsent: adminReviewConsent
                    )
                }
                .buttonStyle(.borderedProminent)
                .disabled(!adminReviewConsent)
                Button("Delete and retry") { viewModel.discardCapturedVideo() }
                    .buttonStyle(.bordered)
            }

        case .selecting:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                Text("Videó kiválasztása…")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
            }

        case .preparing, .exporting, .uploading:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                Text("Feltöltés…")
                    .font(.body.weight(.medium))
                    .foregroundColor(Theme.Color.onSurface)
            }

        case .completing:
            VStack(spacing: Theme.Spacing.md) {
                ProgressView()
                Text("Feldolgozás indítása…")
                    .font(.body.weight(.medium))
                    .foregroundColor(Theme.Color.onSurface)
            }

        case .success:
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.primary)
                Text("Feltöltés sikeres")
                    .font(.body.weight(.semibold))
                    .foregroundColor(Theme.Color.onSurface)
            }

        case .failure(let err):
            VStack(spacing: Theme.Spacing.md) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.system(size: 48))
                    .foregroundColor(Theme.Color.error)
                Text(err.errorDescription ?? "Hiba történt a feltöltés során.")
                    .font(.subheadline)
                    .foregroundColor(Theme.Color.muted)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.lg)
                Button {
                    viewModel.retry()
                } label: {
                    Text("Újrapróbálás")
                        .font(.body.weight(.semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, Theme.Spacing.lg)
                        .padding(.vertical, Theme.Spacing.sm)
                        .background(Theme.Color.primary)
                        .cornerRadius(Theme.Radius.md)
                }
            }
        }
    }
}

private struct JugglingDirectCameraView: View {
    @StateObject private var recorder: JugglingCameraRecorder
    let onCancel: () -> Void
    @State private var countdown: Int?
    @State private var countdownTask: Task<Void, Never>?

    init(
        onFinish: @escaping (URL, [String: JugglingMetadataValue]) -> Void,
        onPermissionDenied: @escaping () -> Void,
        onInterrupted: @escaping (URL?) -> Void,
        onCancel: @escaping () -> Void
    ) {
        _recorder = StateObject(wrappedValue: JugglingCameraRecorder(
            onFinish: onFinish, onPermissionDenied: onPermissionDenied,
            onInterrupted: onInterrupted
        ))
        self.onCancel = onCancel
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            JugglingCameraPreview(session: recorder.session).ignoresSafeArea()
            HStack(spacing: 36) {
                Button("Cancel") { recorder.cancel(); onCancel() }
                Button(recorder.isRecording ? "Stop" : (countdown.map(String.init) ?? "Record")) {
                    if recorder.isRecording {
                        recorder.stop()
                    } else if countdown == nil {
                        startCountdown()
                    }
                }.buttonStyle(.borderedProminent)
                    .disabled(countdown != nil)
            }.padding(28).background(.ultraThinMaterial)
        }
        .task { recorder.prepare() }
        .onDisappear { countdownTask?.cancel(); recorder.cancel() }
    }

    private func startCountdown() {
        countdownTask?.cancel()
        countdownTask = Task {
            for value in [3, 2, 1] {
                guard !Task.isCancelled else { return }
                await MainActor.run { countdown = value }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
            guard !Task.isCancelled else { return }
            await MainActor.run { countdown = nil; recorder.start() }
        }
    }
}

private final class JugglingCameraRecorder: NSObject, ObservableObject, AVCaptureFileOutputRecordingDelegate {
    let session = AVCaptureSession()
    @Published private(set) var isRecording = false
    private let output = AVCaptureMovieFileOutput()
    private var outputURL: URL?
    private let onFinish: (URL, [String: JugglingMetadataValue]) -> Void
    private let onPermissionDenied: () -> Void
    private let onInterrupted: (URL?) -> Void

    init(onFinish: @escaping (URL, [String: JugglingMetadataValue]) -> Void,
         onPermissionDenied: @escaping () -> Void,
         onInterrupted: @escaping (URL?) -> Void) {
        self.onFinish = onFinish
        self.onPermissionDenied = onPermissionDenied
        self.onInterrupted = onInterrupted
    }

    @MainActor func prepare() {
        Task {
            let allowed: Bool
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .authorized: allowed = true
            case .notDetermined: allowed = await AVCaptureDevice.requestAccess(for: .video)
            default: allowed = false
            }
            guard allowed else { onPermissionDenied(); return }
            configureAndStart()
        }
    }

    private func configureAndStart() {
        guard session.inputs.isEmpty else { session.startRunning(); return }
        session.beginConfiguration()
        session.sessionPreset = .high
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front),
              let input = try? AVCaptureDeviceInput(device: camera), session.canAddInput(input),
              session.canAddOutput(output) else {
            session.commitConfiguration(); onInterrupted(nil); return
        }
        session.addInput(input); session.addOutput(output); session.commitConfiguration()
        DispatchQueue.global(qos: .userInitiated).async { self.session.startRunning() }
    }

    func start() {
        guard !output.isRecording else { return }
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("juggling-capture-\(UUID().uuidString).mov")
        outputURL = url
        output.startRecording(to: url, recordingDelegate: self)
        isRecording = true
    }

    func stop() { if output.isRecording { output.stopRecording() } }

    func cancel() {
        if output.isRecording { output.stopRecording() }
        if let url = outputURL { try? FileManager.default.removeItem(at: url) }
        outputURL = nil
        session.stopRunning()
    }

    func fileOutput(_ output: AVCaptureFileOutput, didFinishRecordingTo outputFileURL: URL,
                    from connections: [AVCaptureConnection], error: Error?) {
        DispatchQueue.main.async {
            self.isRecording = false
            self.session.stopRunning()
            guard error == nil, FileManager.default.fileExists(atPath: outputFileURL.path) else {
                try? FileManager.default.removeItem(at: outputFileURL)
                self.onInterrupted(outputFileURL)
                return
            }
            let asset = AVAsset(url: outputFileURL)
            let duration = CMTimeGetSeconds(asset.duration)
            let track = asset.tracks(withMediaType: .video).first
            let metadata: [String: JugglingMetadataValue] = [
                "duration_seconds": .number(duration.isFinite ? duration : 0),
                "fps": .number(Double(track?.nominalFrameRate ?? 0)),
                "resolution": .string("\(Int(track?.naturalSize.width ?? 0))x\(Int(track?.naturalSize.height ?? 0))"),
                "camera_position": .string("front"),
                "orientation_degrees": .integer(0),
                "device_model": .string(UIDevice.current.model),
                "os_version": .string(UIDevice.current.systemVersion),
                "capture_version": .string("PC6B_V1")
            ]
            self.outputURL = nil
            self.onFinish(outputFileURL, metadata)
        }
    }
}

private struct JugglingCameraPreview: UIViewRepresentable {
    let session: AVCaptureSession
    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView(); view.layerView.session = session; return view
    }
    func updateUIView(_ uiView: PreviewView, context: Context) {}
    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var layerView: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
        override init(frame: CGRect) { super.init(frame: frame); layerView.videoGravity = .resizeAspectFill }
        required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    }
}

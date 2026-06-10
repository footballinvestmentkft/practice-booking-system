import SwiftUI
import AVFoundation

// Liveness challenge UI.
//
// DEV/TEST MVP — manual step advancement, no automatic head-position detection.
// The user manually taps through CENTER→LEFT→CENTER→RIGHT→CENTER steps,
// then CameraImagePicker captures a still photo for the CAPTURE step.
//
// Anti-spoofing note: manual step advancement provides NO liveness guarantee.
// Automatic Vision/MediaPipe-based head-turn detection is a separate future PR.
// This view is suitable only for dev/test environments.
//
// CameraManager reuse: CameraPreview (AVCaptureSession) is reused from Skeleton module.
// detector is set to nil — no BodyPoseDetector is attached in liveness flow.
struct BiometricLivenessView: View {

    @EnvironmentObject private var authManager: AuthManager
    @StateObject private var cameraManager = CameraManager()
    @StateObject private var vm: BiometricLivenessViewModel

    @State private var showCapturePicker = false
    @State private var navigateToVerify  = false
    @State private var photoFilenameForVerify: String?

    private let onDismiss: () -> Void

    init(service: BiometricService, onDismiss: @escaping () -> Void) {
        _vm = StateObject(wrappedValue: BiometricLivenessViewModel(service: service))
        self.onDismiss = onDismiss
    }

    var body: some View {
        NavigationView {
            ZStack(alignment: .bottom) {
                cameraLayer
                overlayLayer
                if vm.isLoading { loadingOverlay }
            }
            .ignoresSafeArea(edges: .bottom)
            .navigationTitle("Liveness Teszt")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { closeButton }
            .alert(item: $vm.error) { err in
                Alert(
                    title: Text("Hiba"),
                    message: Text(err.userFacingMessage),
                    primaryButton: .default(Text("Újra")) { vm.retry() },
                    secondaryButton: .cancel(Text("Mégse"), action: onDismiss)
                )
            }
            .sheet(isPresented: $showCapturePicker) {
                CameraImagePicker { image in
                    showCapturePicker = false
                    if let image = image { vm.onPhotoCaptured(image) }
                }
            }
            .background(
                NavigationLink(
                    destination: BiometricVerifyView(
                        service: BiometricService(auth: authManager),
                        photoFilename: photoFilenameForVerify,
                        onDismiss: onDismiss
                    )
                    .environmentObject(authManager),
                    isActive: $navigateToVerify
                ) { EmptyView() }
                .hidden()
            )
        }
        .onAppear {
            cameraManager.requestPermissionAndStart()
            vm.start()
            vm.onLivenessComplete = { filename in
                photoFilenameForVerify = filename
                navigateToVerify = true
            }
        }
        .onDisappear { cameraManager.stop() }
    }

    // MARK: — Camera

    @ViewBuilder
    private var cameraLayer: some View {
        if cameraManager.authDenied {
            cameraPermissionDeniedView
        } else {
            CameraPreview(session: cameraManager.session)
                .ignoresSafeArea()
        }
    }

    private var cameraPermissionDeniedView: some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "camera.slash.fill")
                .font(.system(size: 48))
                .foregroundColor(Theme.Color.warning)
            Text("Kamera hozzáférés szükséges.")
                .font(.system(size: Theme.FontSize.body, weight: .semibold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)
            Text("Engedélyezd a kamera hozzáférést a Beállításokban.")
                .font(.system(size: Theme.FontSize.body))
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
            Button("Beállítások megnyitása") {
                if let url = URL(string: UIApplication.openSettingsURLString) {
                    UIApplication.shared.open(url)
                }
            }
            .foregroundColor(Theme.Color.primary)
        }
        .padding(Theme.Spacing.md)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.Color.background)
    }

    // MARK: — Overlay

    private var overlayLayer: some View {
        VStack(spacing: Theme.Spacing.md) {
            progressBar
            instructionCard
            actionButton
        }
        .padding(Theme.Spacing.md)
        .padding(.bottom, Theme.Spacing.xl)
    }

    private var progressBar: some View {
        VStack(spacing: Theme.Spacing.xs) {
            Text("\(vm.currentStepIndex + 1) / \(vm.totalSteps)")
                .font(.system(size: Theme.FontSize.caption, weight: .semibold))
                .foregroundColor(.white)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.white.opacity(0.3))
                        .frame(height: 6)
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.white)
                        .frame(
                            width: geo.size.width * CGFloat(vm.currentStepIndex + 1) / CGFloat(vm.totalSteps),
                            height: 6
                        )
                        .animation(.easeInOut(duration: 0.25), value: vm.currentStepIndex)
                }
            }
            .frame(height: 6)
        }
    }

    private var instructionCard: some View {
        Text(vm.currentStep.instruction)
            .font(.system(size: Theme.FontSize.title3, weight: .bold))
            .foregroundColor(.white)
            .multilineTextAlignment(.center)
            .padding(Theme.Spacing.md)
            .frame(maxWidth: .infinity)
            .background(Color.black.opacity(0.55))
            .cornerRadius(Theme.Radius.md)
    }

    @ViewBuilder
    private var actionButton: some View {
        if vm.currentStep.isCapture {
            Button {
                showCapturePicker = true
            } label: {
                Label("Kép rögzítése", systemImage: "camera.fill")
                    .font(.system(size: Theme.FontSize.body, weight: .semibold))
                    .foregroundColor(.black)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, Theme.Spacing.sm)
                    .background(Color.white)
                    .cornerRadius(Theme.Radius.sm)
            }
            .disabled(vm.isLoading)
        } else {
            Button {
                vm.advanceStep()
            } label: {
                Text("Tovább →")
                    .font(.system(size: Theme.FontSize.body, weight: .semibold))
                    .foregroundColor(.black)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, Theme.Spacing.sm)
                    .background(Color.white)
                    .cornerRadius(Theme.Radius.sm)
            }
            .disabled(vm.isLoading)
        }
    }

    private var loadingOverlay: some View {
        Color.black.opacity(0.4)
            .ignoresSafeArea()
            .overlay(ProgressView().accentColor(.white))
    }

    private var closeButton: some ToolbarContent {
        ToolbarItem(placement: .navigationBarLeading) {
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(Theme.Color.onSurface)
            }
        }
    }
}

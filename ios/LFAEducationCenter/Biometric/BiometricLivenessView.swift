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
// Navigation contract (white screen fix):
//   navigateToVerify tracks the active NavigationLink push.
//   onPopToLiveness resets navigateToVerify = false BEFORE any dismiss so
//   the navigation stack never enters an inconsistent state. This closure is
//   passed into BiometricVerifyView as a dedicated callback — separate from
//   the full-flow onDismiss.
//
// CameraManager reuse: CameraPreview (AVCaptureSession) is reused from Skeleton module.
// detector is set to nil — no BodyPoseDetector is attached in liveness flow.
struct BiometricLivenessView: View {

    @EnvironmentObject private var authManager: AuthManager
    @StateObject private var cameraManager = CameraManager()
    @StateObject private var vm: BiometricLivenessViewModel

    @State private var showCapturePicker  = false
    @State private var navigateToVerify   = false
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
            .navigationTitle("Liveness Test")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { closeButton }
            .alert(item: $vm.error) { err in
                Alert(
                    title: Text("Error"),
                    message: Text(err.userFacingMessage),
                    primaryButton: .default(Text("Retry")) { vm.retry() },
                    secondaryButton: .cancel(Text("Cancel"), action: onDismiss)
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
                        service:         BiometricService(auth: authManager),
                        photoFilename:   photoFilenameForVerify,
                        // onPopToLiveness: reset the NavigationLink FIRST so the
                        // navigation stack never enters an inconsistent state.
                        // Does NOT close the biometric fullScreenCover.
                        onPopToLiveness: { navigateToVerify = false },
                        // onDismiss: close the entire biometric fullScreenCover.
                        // BiometricVerifyView always calls onPopToLiveness before
                        // calling this, so navigateToVerify is false by the time
                        // the fullScreenCover dismiss animation runs.
                        onDismiss:       onDismiss
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
            Text("Camera access required.")
                .font(.system(size: Theme.FontSize.body, weight: .semibold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)
            Text("Please enable camera access in Settings.")
                .font(.system(size: Theme.FontSize.body))
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
            Button("Open Settings") {
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
                Label("Capture Photo", systemImage: "camera.fill")
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
                Text("Continue →")
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

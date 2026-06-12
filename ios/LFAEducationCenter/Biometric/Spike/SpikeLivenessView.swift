import ARKit
import SwiftUI

// Spike liveness view — ARKit face tracking, fully automatic gesture detection.
//
// This view is ONLY presented when kBiometricAutoCaptureSpikeEnabled == true.
// It is completely isolated from the production BiometricLivenessView.
//
// What it does:
//   - Runs ARFaceTrackingView (TrueDepth camera feed + ARKit tracking)
//   - Shows the current target gesture instruction + icon
//   - Animates a stabilizer progress bar as the gesture is held
//   - Flashes green + haptic when confirmed; auto-advances to next gesture
//   - Shows timeout retry after 15 seconds of failed detection
//   - Shows non-TrueDepth fallback message on unsupported hardware
//
// What it does NOT do (spike scope):
//   - No image capture, no disk writes, no backend upload
//   - No JPEG bytes, no filename, no biometric submission
//   - The flow ends at .complete with a local summary screen only
struct SpikeLivenessView: View {

    @EnvironmentObject private var authManager: AuthManager
    @StateObject private var vm = SpikeLivenessViewModel()

    private let onDismiss: () -> Void

    init(onDismiss: @escaping () -> Void) {
        self.onDismiss = onDismiss
    }

    var body: some View {
        NavigationView {
            ZStack(alignment: .bottom) {
                if ARFaceTrackingView.isDeviceSupported {
                    ARFaceTrackingView(viewModel: vm)
                        .ignoresSafeArea()
                } else {
                    unsupportedDeviceView
                }
                if ARFaceTrackingView.isDeviceSupported {
                    overlayLayer
                }
            }
            .navigationTitle("Liveness Test — Spike")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { closeToolbarItem }
        }
        .onAppear {
            vm.start()
#if DEBUG
            let build   = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
            let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
            print("[SPIKE] SpikeLivenessView appeared — v\(version) (\(build)) — flag: \(kBiometricAutoCaptureSpikeEnabled) — TrueDepth: \(ARFaceTrackingView.isDeviceSupported)")
#endif
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Main overlay

    private var overlayLayer: some View {
        VStack(spacing: 12) {
            progressStepBar
            stateCard
#if DEBUG
            debugOverlay
#endif
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 40)
    }

    // MARK: — Step progress bar

    private var progressStepBar: some View {
        VStack(spacing: 4) {
            Text("\(vm.currentStepIndex + 1) / \(vm.totalSteps)")
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(.white)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.3)).frame(height: 5)
                    Capsule()
                        .fill(Color.white)
                        .frame(
                            width: geo.size.width *
                                   CGFloat(vm.currentStepIndex) / CGFloat(vm.totalSteps),
                            height: 5
                        )
                        .animation(.easeInOut(duration: 0.25), value: vm.currentStepIndex)
                }
            }
            .frame(height: 5)
        }
    }

    // MARK: — State card

    @ViewBuilder
    private var stateCard: some View {
        switch vm.stepState {
        case .noFace:
            instructionCard(
                icon: vm.currentGesture?.systemIcon ?? "questionmark",
                instruction: vm.currentGesture?.instruction ?? "",
                hint: "Position your face in the frame",
                borderColor: .white.opacity(0.6)
            )

        case .detecting:
            instructionCard(
                icon: vm.currentGesture?.systemIcon ?? "questionmark",
                instruction: vm.currentGesture?.instruction ?? "",
                hint: nil,
                borderColor: .white.opacity(0.6)
            )

        case .stabilizing(let progress):
            VStack(spacing: 8) {
                instructionCard(
                    icon: vm.currentGesture?.systemIcon ?? "questionmark",
                    instruction: vm.currentGesture?.instruction ?? "",
                    hint: "Hold...",
                    borderColor: .yellow
                )
                stabilizerBar(progress: progress)
            }

        case .confirmed:
            confirmedCard

        case .timedOut:
            timedOutCard

        case .complete:
            completeCard
        }
    }

    // MARK: — Card builders

    private func instructionCard(
        icon: String,
        instruction: String,
        hint: String?,
        borderColor: Color
    ) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 24))
                .foregroundColor(.white)
                .frame(width: 36)
            VStack(alignment: .leading, spacing: 2) {
                Text(instruction)
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(.white)
                if let hint {
                    Text(hint)
                        .font(.system(size: 12))
                        .foregroundColor(.white.opacity(0.7))
                }
            }
            Spacer()
        }
        .padding(14)
        .background(Color.black.opacity(0.55))
        .cornerRadius(12)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(borderColor, lineWidth: 2))
        .animation(.easeInOut(duration: 0.2), value: borderColor.description)
    }

    private func stabilizerBar(progress: Double) -> some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.yellow.opacity(0.3)).frame(height: 8)
                Capsule()
                    .fill(Color.yellow)
                    .frame(width: geo.size.width * CGFloat(progress), height: 8)
                    .animation(.linear(duration: 0.05), value: progress)
            }
        }
        .frame(height: 8)
    }

    private var confirmedCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 24))
                .foregroundColor(Theme.Color.primary)
                .frame(width: 36)
            Text("Confirmed — next gesture")
                .font(.system(size: 16, weight: .bold))
                .foregroundColor(.white)
            Spacer()
        }
        .padding(14)
        .background(Color.black.opacity(0.55))
        .cornerRadius(12)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.Color.primary, lineWidth: 2))
        .onAppear { UIImpactFeedbackGenerator(style: .medium).impactOccurred() }
    }

    private var timedOutCard: some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 20))
                    .foregroundColor(Theme.Color.warning)
                    .frame(width: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Gesture not detected")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(.white)
                    Text("Make sure your face is well lit and visible.")
                        .font(.system(size: 11))
                        .foregroundColor(.white.opacity(0.7))
                }
                Spacer()
            }
            Button {
                vm.retryCurrentStep()
            } label: {
                Text("Try again (\(vm.retryCount + 1))")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(.black)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background(Color.white)
                    .cornerRadius(8)
            }
        }
        .padding(14)
        .background(Color.black.opacity(0.65))
        .cornerRadius(12)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.Color.warning, lineWidth: 2))
    }

    private var completeCard: some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 48))
                .foregroundColor(Theme.Color.primary)
            Text("Spike flow complete")
                .font(.system(size: 18, weight: .bold))
                .foregroundColor(.white)
            Text("All 7 gestures detected.\nNo data was stored or transmitted.")
                .font(.system(size: 13))
                .foregroundColor(.white.opacity(0.8))
                .multilineTextAlignment(.center)
            Button {
                onDismiss()
            } label: {
                Text("Close")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(.black)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(Color.white)
                    .cornerRadius(8)
            }
        }
        .padding(20)
        .background(Color.black.opacity(0.7))
        .cornerRadius(14)
        .onAppear { UINotificationFeedbackGenerator().notificationOccurred(.success) }
    }

    // MARK: — Non-TrueDepth fallback

    private var unsupportedDeviceView: some View {
        VStack(spacing: Theme.Spacing.lg) {
            Image(systemName: "faceid")
                .font(.system(size: 52))
                .foregroundColor(Theme.Color.warning)
            Text("TrueDepth camera required")
                .font(.system(size: 17, weight: .semibold))
                .foregroundColor(Theme.Color.onSurface)
                .multilineTextAlignment(.center)
            Text("ARKit face tracking is only available on iPhone X and later with a TrueDepth front camera.\n\nThe standard biometric flow will be used on this device.")
                .font(.system(size: 14))
                .foregroundColor(Theme.Color.muted)
                .multilineTextAlignment(.center)
            Button(action: onDismiss) {
                Text("Close")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(Theme.Color.primary)
                    .cornerRadius(8)
            }
        }
        .padding(Theme.Spacing.md)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(UIColor.systemBackground))
    }

    // MARK: — Toolbar

    private var closeToolbarItem: some ToolbarContent {
        ToolbarItem(placement: .navigationBarLeading) {
            Button(action: onDismiss) {
                Image(systemName: "xmark")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundColor(Theme.Color.onSurface)
            }
        }
    }

    // MARK: — Debug overlay (DEBUG builds only)

#if DEBUG
    private var debugOverlay: some View {
        let v       = vm.debugValues
        let build   = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let truedepth = ARFaceTrackingView.isDeviceSupported

        let holdPct: Int = {
            if case .stabilizing(let p) = vm.stepState { return Int(p * 100) }
            if case .confirmed = vm.stepState { return 100 }
            return 0
        }()

        let lines: [String] = [
            "build: \(version) (\(build))  TrueDepth: \(truedepth ? "YES" : "NO")",
            "step \(vm.currentStepIndex + 1)/\(vm.totalSteps): \(vm.currentGesture?.debugLabel ?? "—")  detected: \(v.detected ? "YES✓" : "no")",
            String(format: "yaw: %+.3f  pitch: %+.3f  hold: \(holdPct)%%", v.yaw, v.pitch),
            String(format: "blinkL: %.2f  blinkR: %.2f", v.blinkLeft, v.blinkRight),
            String(format: "smileL: %.2f  smileR: %.2f  sqntAvg: %.2f",
                   v.smileLeft, v.smileRight, (v.squintLeft + v.squintRight) / 2),
        ]
        return VStack(alignment: .leading, spacing: 2) {
            Text("⚙ SPIKE DEBUG")
                .font(.system(size: 9, weight: .bold, design: .monospaced))
                .foregroundColor(.orange)
            ForEach(lines, id: \.self) { line in
                Text(line)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundColor(.orange.opacity(0.9))
            }
        }
        .padding(6)
        .background(Color.black.opacity(0.08))
        .cornerRadius(6)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
#endif
}

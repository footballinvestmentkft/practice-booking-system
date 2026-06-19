import SwiftUI
import UIKit

// MARK: — BallTrainingFrameView
//
// Displays one frame (JPEG) from the global ball-training queue,
// overlays the model's predicted ball position, and exposes four actions:
// Helyes (confirm) / Nincs labda (no_ball) / Javítom (corrected) / Kihagyom (skip).
//
// In correction mode a full-image tap detector captures the user's tap
// and normalises it to [0, 1] relative to the displayed image.
// The server back-calculates the true full-frame coordinate from the
// crop metadata stored on the assignment — the client never needs to know
// whether the image is a context_crop or full_frame.

struct BallTrainingFrameView: View {

    @ObservedObject var vm: BallTrainingHubViewModel
    let item: GlobalTrainingQueueItem
    let frameData: Data

    var body: some View {
        VStack(spacing: 0) {
            progressHeader
            frameArea
            actionBar
        }
    }

    // MARK: — Progress header

    private var progressHeader: some View {
        HStack {
            Text("Feladat")
                .font(.subheadline.weight(.semibold))
                .foregroundColor(Theme.Color.muted)
            Spacer()
            Text(vm.progressText)
                .font(.subheadline.weight(.bold))
                .foregroundColor(Theme.Color.primary)
        }
        .padding(.horizontal, Theme.Spacing.lg)
        .padding(.vertical, Theme.Spacing.sm)
    }

    // MARK: — Frame image + overlays

    private var frameArea: some View {
        GeometryReader { geo in
            ZStack {
                Color.black
                if let uiImage = UIImage(data: frameData) {
                    let dims = imageDims(uiImage: uiImage, in: geo.size)
                    Image(uiImage: uiImage)
                        .resizable()
                        .scaledToFit()
                        .frame(width: dims.width, height: dims.height)
                        .position(x: dims.offsetX + dims.width / 2,
                                  y: dims.offsetY + dims.height / 2)

                    ballOverlay(uiImage: uiImage, dims: dims)

                    if vm.isInCorrectionMode {
                        tapOverlay(dims: dims)
                    }
                }
            }
        }
        .overlay(correctionModeLabel, alignment: .top)
    }

    // MARK: — Ball prediction overlay

    @ViewBuilder
    private func ballOverlay(uiImage: UIImage, dims: ImageDims) -> some View {
        if !vm.isInCorrectionMode,
           let bx = item.modelPredictedX,
           let by = item.modelPredictedY {
            let px = dims.offsetX + CGFloat(bx) * dims.width
            let py = dims.offsetY + CGFloat(by) * dims.height
            ZStack {
                Circle()
                    .strokeBorder(ballOverlayColor, lineWidth: 2.5)
                    .background(Circle().fill(ballOverlayColor.opacity(0.18)))
                    .frame(width: 28, height: 28)
                    .position(x: px, y: py)
                if let conf = item.modelConfidence {
                    Text("\(Int(conf * 100))%")
                        .font(.system(size: 8, weight: .semibold).monospacedDigit())
                        .foregroundColor(.white)
                        .padding(.horizontal, 3)
                        .background(Color.black.opacity(0.55))
                        .cornerRadius(3)
                        .position(x: px + 22, y: py - 16)
                }
            }
            .allowsHitTesting(false)
        }
    }

    private var ballOverlayColor: Color {
        guard let conf = item.modelConfidence else { return .orange }
        if conf >= 0.80 { return .green }
        if conf >= 0.50 { return .yellow }
        return .orange
    }

    // MARK: — Correction mode tap layer

    @ViewBuilder
    private func tapOverlay(dims: ImageDims) -> some View {
        Rectangle()
            .fill(Color.blue.opacity(0.08))
            .frame(width: dims.width, height: dims.height)
            .position(x: dims.offsetX + dims.width / 2,
                      y: dims.offsetY + dims.height / 2)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onEnded { value in
                        let raw = value.location
                        let tx = (raw.x - dims.offsetX) / dims.width
                        let ty = (raw.y - dims.offsetY) / dims.height
                        let clampedX = max(0, min(1, Double(tx)))
                        let clampedY = max(0, min(1, Double(ty)))
                        Task { await vm.corrected(tapX: clampedX, tapY: clampedY) }
                    }
            )
    }

    private var correctionModeLabel: some View {
        Group {
            if vm.isInCorrectionMode {
                Text("Érintsd meg a labda valódi helyzetét")
                    .font(.caption.weight(.semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, Theme.Spacing.md)
                    .padding(.vertical, Theme.Spacing.xs)
                    .background(Color.blue.opacity(0.85))
                    .cornerRadius(Theme.Radius.sm)
                    .padding(.top, Theme.Spacing.sm)
            }
        }
    }

    // MARK: — Action bar

    private var actionBar: some View {
        VStack(spacing: Theme.Spacing.sm) {
            if let msg = vm.lastErrorMessage {
                Text(msg)
                    .font(.caption)
                    .foregroundColor(Theme.Color.error)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, Theme.Spacing.lg)
            }

            if vm.isInCorrectionMode {
                Button("Mégse") { vm.cancelCorrectionMode() }
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(Theme.Color.muted)
            } else {
                HStack(spacing: Theme.Spacing.sm) {
                    actionButton(
                        label: "Helyes",
                        icon: "checkmark.circle.fill",
                        color: .green,
                        disabled: vm.isSubmitting
                    ) { Task { await vm.confirm() } }

                    actionButton(
                        label: "Nincs labda",
                        icon: "xmark.circle.fill",
                        color: .red,
                        disabled: vm.isSubmitting
                    ) { Task { await vm.noBall() } }
                }
                HStack(spacing: Theme.Spacing.sm) {
                    actionButton(
                        label: "Javítom",
                        icon: "pencil.circle.fill",
                        color: .blue,
                        disabled: vm.isSubmitting
                    ) { vm.enterCorrectionMode() }

                    actionButton(
                        label: "Kihagyom",
                        icon: "forward.circle.fill",
                        color: Theme.Color.muted,
                        disabled: vm.isSubmitting
                    ) { vm.skip() }
                }
            }

            if vm.isSubmitting {
                ProgressView()
                    .progressViewStyle(.circular)
                    .padding(.vertical, Theme.Spacing.xs)
            }
        }
        .padding(.horizontal, Theme.Spacing.lg)
        .padding(.vertical, Theme.Spacing.md)
        .background(Theme.Color.surface)
    }

    private func actionButton(
        label: String,
        icon: String,
        color: Color,
        disabled: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: Theme.Spacing.xs) {
                Image(systemName: icon)
                    .font(.system(size: 15))
                Text(label)
                    .font(.subheadline.weight(.semibold))
            }
            .frame(maxWidth: .infinity)
            .frame(height: 44)
            .background(color.opacity(disabled ? 0.08 : 0.14))
            .foregroundColor(disabled ? Theme.Color.muted : color)
            .cornerRadius(Theme.Radius.sm)
        }
        .disabled(disabled)
    }

    // MARK: — Image geometry helpers

    private struct ImageDims {
        let width:   CGFloat
        let height:  CGFloat
        let offsetX: CGFloat
        let offsetY: CGFloat
    }

    private func imageDims(uiImage: UIImage, in containerSize: CGSize) -> ImageDims {
        let iw = uiImage.size.width
        let ih = uiImage.size.height
        guard iw > 0, ih > 0 else {
            return ImageDims(width: containerSize.width, height: containerSize.height, offsetX: 0, offsetY: 0)
        }
        let imageAR     = iw / ih
        let containerAR = containerSize.width / containerSize.height
        let displayW: CGFloat
        let displayH: CGFloat
        if imageAR > containerAR {
            displayW = containerSize.width
            displayH = containerSize.width / imageAR
        } else {
            displayH = containerSize.height
            displayW = containerSize.height * imageAR
        }
        let offsetX = (containerSize.width  - displayW) / 2
        let offsetY = (containerSize.height - displayH) / 2
        return ImageDims(width: displayW, height: displayH, offsetX: offsetX, offsetY: offsetY)
    }
}

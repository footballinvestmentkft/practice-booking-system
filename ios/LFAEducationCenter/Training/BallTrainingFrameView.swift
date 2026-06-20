import SwiftUI
import UIKit

// MARK: — BallTrainingFrameView
//
// Displays one frame (JPEG) from the global ball-training queue,
// overlays the model's predicted ball position, and exposes four actions:
// Helyes (confirm) / Nincs labda (no_ball) / Javítom (corrected) / Kihagyom (skip).
//
// Correction flow (3-step, POST only on explicit confirmation):
//   1. User taps "Javítom" → correction mode entered, model overlay dims to 40 %.
//   2. User taps or drags on the image → cyan crosshair marker appears; loupe
//      magnifies the touched area during drag; out-of-bounds taps are ignored.
//   3. User presses "Megerősítés" → corrected POST sent with the marker's
//      normalised [0, 1] coordinate.
//   Alternatively: "Új kijelölés" clears the marker (stays in correction mode);
//   "Mégse" exits correction mode without any POST.
//
// Coordinate invariant: imageDims() is the single source of truth for the
// displayed image rect (letterbox/pillarbox aware).  Every overlay, the loupe
// patch crop, and the submitted tap_x/tap_y all derive from the same dims.

struct BallTrainingFrameView: View {

    @ObservedObject var vm: BallTrainingHubViewModel
    let item: GlobalTrainingQueueItem
    let frameData: Data

    @State private var loupeDragNorm: CGPoint? = nil

    private let loupeSize:          CGFloat = 120
    private let loupePatchFraction: CGFloat = 0.12   // fraction of image width shown in loupe

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

                    // Model overlay — always visible; dimmed during correction mode
                    ballOverlay(uiImage: uiImage, dims: dims)

                    if vm.isInCorrectionMode {
                        tapOverlay(dims: dims)
                        correctionMarker(dims: dims)
                        if let drag = loupeDragNorm {
                            loupeView(normPoint: drag, uiImage: uiImage, dims: dims)
                        }
                    }
                }
            }
        }
        .overlay(alignment: .top) {
            // Instruction label only when in correction mode with no pending point yet
            if vm.isInCorrectionMode && !vm.hasPendingTap {
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

    // MARK: — Ball prediction overlay

    @ViewBuilder
    private func ballOverlay(uiImage: UIImage, dims: ImageDims) -> some View {
        if let bx = item.modelPredictedX,
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
            // Dimmed to 40 % in correction mode — visible as a reference point
            .opacity(vm.isInCorrectionMode ? 0.40 : 1.0)
            .allowsHitTesting(false)
        }
    }

    private var ballOverlayColor: Color {
        guard let conf = item.modelConfidence else { return .orange }
        if conf >= 0.80 { return .green }
        if conf >= 0.50 { return .yellow }
        return .orange
    }

    // MARK: — Tap overlay (correction mode)
    //
    // onChanged: update loupe position during drag (in-bounds only).
    // onEnded:   set pending tap (in-bounds only); out-of-bounds → silently ignored.

    @ViewBuilder
    private func tapOverlay(dims: ImageDims) -> some View {
        Rectangle()
            .fill(Color.blue.opacity(0.06))
            .frame(width: dims.width, height: dims.height)
            .position(x: dims.offsetX + dims.width / 2,
                      y: dims.offsetY + dims.height / 2)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        let tx = (value.location.x - dims.offsetX) / dims.width
                        let ty = (value.location.y - dims.offsetY) / dims.height
                        guard tx >= 0, tx <= 1, ty >= 0, ty <= 1 else {
                            loupeDragNorm = nil
                            return
                        }
                        loupeDragNorm = CGPoint(x: tx, y: ty)
                    }
                    .onEnded { value in
                        loupeDragNorm = nil
                        let tx = (value.location.x - dims.offsetX) / dims.width
                        let ty = (value.location.y - dims.offsetY) / dims.height
                        // Out-of-bounds tap: ignore — do NOT clamp to image edge
                        guard tx >= 0, tx <= 1, ty >= 0, ty <= 1 else { return }
                        vm.setPendingTap(x: Double(tx), y: Double(ty))
                    }
            )
    }

    // MARK: — Correction marker (pending tap point)

    @ViewBuilder
    private func correctionMarker(dims: ImageDims) -> some View {
        if let px = vm.pendingTapX, let py = vm.pendingTapY {
            let sx = dims.offsetX + CGFloat(px) * dims.width
            let sy = dims.offsetY + CGFloat(py) * dims.height
            ZStack {
                Circle()
                    .strokeBorder(Color.cyan, lineWidth: 2.5)
                    .background(Circle().fill(Color.cyan.opacity(0.20)))
                    .frame(width: 30, height: 30)
                Rectangle()
                    .fill(Color.cyan)
                    .frame(width: 20, height: 1.5)
                Rectangle()
                    .fill(Color.cyan)
                    .frame(width: 1.5, height: 20)
            }
            .position(x: sx, y: sy)
            .allowsHitTesting(false)
        }
    }

    // MARK: — Loupe (magnifier shown during drag)
    //
    // Crops a patchFraction-wide square from the UIImage at the drag position
    // and displays it in a 120 pt circle above the user's finger.
    // The crosshair in the centre shows exactly which pixel will be recorded.

    @ViewBuilder
    private func loupeView(normPoint: CGPoint, uiImage: UIImage, dims: ImageDims) -> some View {
        if let cgImage = uiImage.cgImage,
           let cgCrop  = cgImage.cropping(to: loupeCropRect(cgImage: cgImage, norm: normPoint)) {
            let screenX = dims.offsetX + normPoint.x * dims.width
            let screenY = dims.offsetY + normPoint.y * dims.height
            ZStack {
                Image(uiImage: UIImage(cgImage: cgCrop))
                    .resizable()
                    .scaledToFill()
                    .frame(width: loupeSize, height: loupeSize)
                    .clipped()
                // Crosshair
                ZStack {
                    Rectangle()
                        .fill(Color.white.opacity(0.85))
                        .frame(width: 36, height: 1.5)
                    Rectangle()
                        .fill(Color.white.opacity(0.85))
                        .frame(width: 1.5, height: 36)
                    Circle()
                        .strokeBorder(Color.white.opacity(0.85), lineWidth: 1)
                        .frame(width: 14, height: 14)
                }
            }
            .clipShape(Circle())
            .overlay(Circle().strokeBorder(Color.white, lineWidth: 2.5))
            .shadow(color: .black.opacity(0.5), radius: 8, y: 3)
            .frame(width: loupeSize, height: loupeSize)
            .position(
                x: screenX,
                y: max(loupeSize / 2 + 8, screenY - loupeSize / 2 - 24)
            )
            .allowsHitTesting(false)
        }
    }

    private func loupeCropRect(cgImage: CGImage, norm: CGPoint) -> CGRect {
        let imgW    = CGFloat(cgImage.width)
        let imgH    = CGFloat(cgImage.height)
        let halfW   = loupePatchFraction * imgW / 2
        let halfH   = loupePatchFraction * imgH / 2
        let originX = max(0, min(imgW - halfW * 2, norm.x * imgW - halfW))
        let originY = max(0, min(imgH - halfH * 2, norm.y * imgH - halfH))
        return CGRect(x: originX, y: originY, width: halfW * 2, height: halfH * 2)
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
                correctionActionBar
            } else {
                normalActionBar
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

    // Correction mode: pending point exists → Confirm / Reselect / Cancel
    //                  no pending point     → Cancel only

    @ViewBuilder
    private var correctionActionBar: some View {
        if vm.hasPendingTap {
            VStack(spacing: Theme.Spacing.sm) {
                Button {
                    Task { await vm.confirmCorrection() }
                } label: {
                    Label("Megerősítés", systemImage: "checkmark.circle.fill")
                        .font(.body.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .frame(height: 44)
                        .background(Color.cyan.opacity(vm.isSubmitting ? 0.06 : 0.18))
                        .foregroundColor(vm.isSubmitting ? Theme.Color.muted : .cyan)
                        .cornerRadius(Theme.Radius.sm)
                }
                .disabled(vm.isSubmitting)

                HStack(spacing: Theme.Spacing.sm) {
                    Button("Új kijelölés") {
                        vm.clearPendingTap()
                    }
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 44)
                    .background(Theme.Color.primary.opacity(0.10))
                    .foregroundColor(Theme.Color.primary)
                    .cornerRadius(Theme.Radius.sm)

                    Button("Mégse") {
                        vm.cancelCorrectionMode()
                    }
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .frame(height: 44)
                    .background(Color.clear)
                    .foregroundColor(Theme.Color.muted)
                    .cornerRadius(Theme.Radius.sm)
                }
            }
        } else {
            Button("Mégse") {
                vm.cancelCorrectionMode()
            }
            .font(.subheadline.weight(.semibold))
            .foregroundColor(Theme.Color.muted)
        }
    }

    private var normalActionBar: some View {
        VStack(spacing: Theme.Spacing.sm) {
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

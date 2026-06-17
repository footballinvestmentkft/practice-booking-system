import SwiftUI

// MARK: — BallOverlayView (AN-3B2C-1)
//
// Ball position overlay drawn over the video render area.
// Follows the same normalized-coordinate contract as PoseSnapshotOverlayView:
//   ballX/ballY ∈ [0,1], origin top-left.
//
// Sized to the video's rendered frame by the caller; GeometryReader maps
// normalized coords to actual pixel positions.
//
// Colour coding (circle border + fill tint):
//   manual              → blue   (detection_source == "manual")
//   confidence >= 0.80  → green
//   confidence >= 0.50  → yellow
//   confidence <  0.50 or nil → orange
//
// When isDragEnabled is true the user can drag the circle anywhere inside the
// frame; onPositionCommitted is called with clamped [0,1]×[0,1] coords on
// release. When false, the circle is non-interactive (allowsHitTesting(false)).

struct BallOverlayView: View {

    let detection: BallDetectionOut
    let isDragEnabled: Bool
    /// Called with normalized x,y ∈ [0,1] when the user releases a drag.
    var onPositionCommitted: (Double, Double) -> Void = { _, _ in }

    @GestureState private var dragDelta: CGSize = .zero

    var body: some View {
        GeometryReader { geo in
            if detection.noBallDetected {
                noBallLabel(geo: geo)
            } else if let bx = detection.ballX, let by = detection.ballY {
                ballDot(
                    geo: geo,
                    baseX: CGFloat(bx) * geo.size.width,
                    baseY: CGFloat(by) * geo.size.height
                )
            }
        }
        .allowsHitTesting(isDragEnabled)
    }

    // MARK: — Sub-views

    @ViewBuilder
    private func noBallLabel(geo: GeometryProxy) -> some View {
        Text("Nincs labda")
            .font(.caption2)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .background(Color.black.opacity(0.55))
            .foregroundColor(.white)
            .cornerRadius(4)
            .position(x: geo.size.width / 2, y: geo.size.height / 2)
    }

    @ViewBuilder
    private func ballDot(geo: GeometryProxy, baseX: CGFloat, baseY: CGFloat) -> some View {
        let currentX = clamp(baseX + dragDelta.width,  lo: 0, hi: geo.size.width)
        let currentY = clamp(baseY + dragDelta.height, lo: 0, hi: geo.size.height)
        Circle()
            .strokeBorder(ballColor, lineWidth: 2)
            .background(Circle().fill(ballColor.opacity(0.25)))
            .frame(width: 24, height: 24)
            .position(x: currentX, y: currentY)
            .gesture(
                DragGesture(minimumDistance: 0)
                    .updating($dragDelta) { value, state, _ in
                        state = CGSize(
                            width:  clamp(baseX + value.translation.width,  lo: 0, hi: geo.size.width)  - baseX,
                            height: clamp(baseY + value.translation.height, lo: 0, hi: geo.size.height) - baseY
                        )
                    }
                    .onEnded { value in
                        let nx = Double(clamp(baseX + value.translation.width,  lo: 0, hi: geo.size.width)  / geo.size.width)
                        let ny = Double(clamp(baseY + value.translation.height, lo: 0, hi: geo.size.height) / geo.size.height)
                        onPositionCommitted(nx, ny)
                    }
            )
    }

    // MARK: — Helpers

    private var ballColor: Color {
        if detection.detectionSource == "manual" { return .blue }
        guard let c = detection.confidence else { return .orange }
        if c >= 0.80 { return .green }
        if c >= 0.50 { return .yellow }
        return .orange
    }

    private func clamp(_ v: CGFloat, lo: CGFloat, hi: CGFloat) -> CGFloat {
        min(max(v, lo), hi)
    }
}

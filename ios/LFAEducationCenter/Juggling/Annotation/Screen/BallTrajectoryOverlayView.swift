import SwiftUI

// MARK: — BallTrajectoryOverlayView (AN-3B2D-3)
//
// Dense ball trajectory overlay: playhead-synced marker + trail.
// Drawn over the video render area alongside the skeleton overlay.
//
// Colour encoding:
//   manual_seed         → blue
//   detected ≥ 0.80     → green
//   detected ≥ 0.50     → yellow
//   detected < 0.50     → orange
//   predicted           → orange, dashed border
//   lost                → not rendered
//
// Trail: last 10 visible points, decreasing size + opacity.

struct BallTrajectoryOverlayView: View {

    let currentPoint: BallTrajectoryPointDTO?
    let trail: [BallTrajectoryPointDTO]
    let trackingLost: Bool

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height

            ZStack {
                trailLayer(w: w, h: h)

                if let pt = currentPoint, let bx = pt.ballX, let by = pt.ballY {
                    markerView(pt: pt, x: bx * w, y: by * h)
                }

                if trackingLost && currentPoint == nil {
                    trackingLostBanner
                }
            }
        }
        .allowsHitTesting(false)
    }

    // MARK: — Trail (decreasing size + opacity)

    @ViewBuilder
    private func trailLayer(w: CGFloat, h: CGFloat) -> some View {
        ForEach(trail.indices, id: \.self) { i in
            if let bx = trail[i].ballX, let by = trail[i].ballY {
                Circle()
                    .fill(Self.trailColor(for: trail[i]).opacity(Self.trailOpacity(index: i)))
                    .frame(
                        width: max(6.0 - CGFloat(i) * 0.4, 2.0),
                        height: max(6.0 - CGFloat(i) * 0.4, 2.0)
                    )
                    .position(x: bx * w, y: by * h)
            }
        }
    }

    // MARK: — Current marker

    @ViewBuilder
    private func markerView(pt: BallTrajectoryPointDTO, x: CGFloat, y: CGFloat) -> some View {
        ZStack {
            if pt.trackingState == "predicted" {
                Circle()
                    .strokeBorder(Self.markerColor(for: pt), style: StrokeStyle(lineWidth: 2.5, dash: [4, 4]))
                    .background(Circle().fill(Self.markerColor(for: pt).opacity(0.15)))
                    .frame(width: 24, height: 24)
            } else {
                Circle()
                    .strokeBorder(Self.markerColor(for: pt), lineWidth: 2.5)
                    .background(Circle().fill(Self.markerColor(for: pt).opacity(0.20)))
                    .frame(width: 28, height: 28)
            }

            if let conf = pt.confidence {
                Text("\(Int(conf * 100))%")
                    .font(.system(size: 8, weight: .semibold).monospacedDigit())
                    .foregroundColor(.white)
                    .padding(.horizontal, 3)
                    .padding(.vertical, 1)
                    .background(Color.black.opacity(0.55))
                    .cornerRadius(3)
                    .offset(x: 20, y: -16)
            }
        }
        .position(x: x, y: y)
    }

    // MARK: — Tracking lost banner

    private var trackingLostBanner: some View {
        VStack {
            Spacer()
            Text("Labda elveszett — koppints a labdára")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(.white.opacity(0.85))
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(Color.black.opacity(0.60))
                .cornerRadius(6)
                .padding(.bottom, 60)
        }
    }

    // MARK: — Colour helpers (static for testing)

    static func markerColor(for point: BallTrajectoryPointDTO) -> Color {
        switch point.trackingState {
        case "manual_seed":
            return .blue
        case "predicted":
            return .orange
        default:
            guard let c = point.confidence else { return .orange }
            if c >= 0.80 { return .green }
            if c >= 0.50 { return .yellow }
            return .orange
        }
    }

    static func trailColor(for point: BallTrajectoryPointDTO) -> Color {
        if point.isManual { return .blue }
        return .orange
    }

    static func trailOpacity(index: Int) -> Double {
        max(1.0 - Double(index) * 0.09, 0.10)
    }
}

import SwiftUI

// MARK: — BallVideoOverlayView (AN-3B2C-1 / main screen)
//
// Read-only ball position indicator for the main JugglingAnnotationScreen.
// Shows a coloured circle at the detected ball position — no drag support.
// (Drag correction is in EventLabelDetailView via BallOverlayView.)
//
// Only rendered when ball_x/ball_y are non-nil (noBallDetected=false).
// noBallDetected events are communicated by the xmark badge on the timeline pin.
//
// Colour encoding mirrors BallOverlayViewColorHelper:
//   manual              → blue
//   confidence >= 0.80  → green
//   confidence >= 0.50  → yellow
//   confidence <  0.50 or nil → orange
//
// Sized to the video render area by the caller via .frame(width:height:).

struct BallVideoOverlayView: View {

    let detection: BallDetectionOut

    var body: some View {
        GeometryReader { geo in
            if !detection.noBallDetected,
               let bx = detection.ballX,
               let by = detection.ballY {
                let x = CGFloat(bx) * geo.size.width
                let y = CGFloat(by) * geo.size.height
                ZStack {
                    Circle()
                        .strokeBorder(ballColor, lineWidth: 2.5)
                        .background(Circle().fill(ballColor.opacity(0.20)))
                        .frame(width: 28, height: 28)
                        .position(x: x, y: y)

                    // Confidence label (small, top-right of circle)
                    if let conf = detection.confidence {
                        Text("\(Int(conf * 100))%")
                            .font(.system(size: 8, weight: .semibold).monospacedDigit())
                            .foregroundColor(.white)
                            .padding(.horizontal, 3)
                            .padding(.vertical, 1)
                            .background(Color.black.opacity(0.55))
                            .cornerRadius(3)
                            .position(x: x + 20, y: y - 16)
                    }
                }
            }
        }
        .allowsHitTesting(false)
    }

    private var ballColor: Color {
        BallVideoOverlayColorHelper.ballColor(for: detection)
    }
}

// MARK: — BallVideoOverlayColorHelper (internal for unit tests)

enum BallVideoOverlayColorHelper {
    static func ballColor(for detection: BallDetectionOut) -> Color {
        if detection.detectionSource == "manual" { return .blue }
        guard let c = detection.confidence else { return .orange }
        if c >= 0.80 { return .green }
        if c >= 0.50 { return .yellow }
        return .orange
    }
}

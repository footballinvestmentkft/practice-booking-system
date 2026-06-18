import SwiftUI

// MARK: — ContinuousSkeletonOverlayView (AN-3B2D-2)
//
// Full-skeleton overlay drawn over the video render area, driven by
// DenseSkeletonViewModel's continuous frame data (not event-snapshot).
//
// Visual encoding matches PoseSnapshotOverlayView for real joints/bones:
//   Bones: 5pt dark halo + 2.5pt cyan inner (solid)
//   Joints: 14pt dark ring + 10pt confidence-coloured fill
//
// Synthetic feet (VISUALLY DISTINCT from detected joints):
//   Ankle→foot lines: dashed (dash [4,4]), reduced opacity
//   Foot points: smaller (12pt ring + 8pt fill), degraded confidence colour

struct ContinuousSkeletonOverlayView: View {

    let frame: DensePoseFrame?
    var showSyntheticFeet: Bool = true

    private static let bones: [(String, String)] = [
        ("neck", "root"),
        ("neck", "left_shoulder"), ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
        ("neck", "right_shoulder"), ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
        ("root", "left_hip"), ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
        ("root", "right_hip"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
        ("nose", "left_eye"), ("nose", "right_eye"),
        ("left_eye", "left_ear"), ("right_eye", "right_ear"),
    ]

    var body: some View {
        GeometryReader { geo in
            if let frame = frame, !frame.keypoints.body.isEmpty {
                let w = geo.size.width
                let h = geo.size.height
                let byName = Dictionary(uniqueKeysWithValues: frame.keypoints.body.map { ($0.name, $0) })

                ZStack {
                    realBoneLayer(byName: byName, w: w, h: h)

                    if showSyntheticFeet, let feet = frame.syntheticFeet {
                        syntheticFootLineLayer(feet: feet, w: w, h: h)
                    }

                    realJointLayer(keypoints: frame.keypoints, w: w, h: h)

                    if showSyntheticFeet, let feet = frame.syntheticFeet {
                        syntheticFootPointLayer(feet: feet, w: w, h: h)
                    }
                }
            }
        }
        .allowsHitTesting(false)
    }

    // MARK: — Real bones (solid double-stroke)

    private func realBoneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
        let segs = Self.realBoneSegments(byName: byName, w: w, h: h)
        return ZStack {
            Path { path in
                for (s, e) in segs { path.move(to: s); path.addLine(to: e) }
            }
            .stroke(Color.black.opacity(0.55), lineWidth: 5)

            Path { path in
                for (s, e) in segs { path.move(to: s); path.addLine(to: e) }
            }
            .stroke(Color.cyan.opacity(0.92), lineWidth: 2.5)
        }
    }

    static func realBoneSegments(
        byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat
    ) -> [(CGPoint, CGPoint)] {
        bones.compactMap { (a, b) in
            guard let pa = byName[a], let pb = byName[b] else { return nil }
            return (
                CGPoint(x: CGFloat(pa.x) * w, y: CGFloat(pa.y) * h),
                CGPoint(x: CGFloat(pb.x) * w, y: CGFloat(pb.y) * h)
            )
        }
    }

    // MARK: — Real joints

    @ViewBuilder
    private func realJointLayer(keypoints: PoseKeypointsDTO, w: CGFloat, h: CGFloat) -> some View {
        ForEach(keypoints.body, id: \.name) { lm in
            ZStack {
                Circle()
                    .fill(Color.black.opacity(0.55))
                    .frame(width: 14, height: 14)
                Circle()
                    .fill(Self.jointColor(confidence: lm.confidence))
                    .frame(width: 10, height: 10)
            }
            .position(x: lm.x * w, y: lm.y * h)
        }
    }

    // MARK: — Synthetic foot lines (dashed, reduced opacity)

    private func syntheticFootLineLayer(feet: SyntheticFeetDTO, w: CGFloat, h: CGFloat) -> some View {
        let segs = Self.syntheticFootSegments(feet: feet, w: w, h: h)
        return ZStack {
            Path { path in
                for (s, e) in segs { path.move(to: s); path.addLine(to: e) }
            }
            .stroke(
                Color.black.opacity(0.45),
                style: StrokeStyle(lineWidth: 4, dash: [4, 4])
            )

            Path { path in
                for (s, e) in segs { path.move(to: s); path.addLine(to: e) }
            }
            .stroke(
                Color.cyan.opacity(0.6),
                style: StrokeStyle(lineWidth: 2, dash: [4, 4])
            )
        }
    }

    // MARK: — Synthetic foot points (smaller, distinguishable)

    @ViewBuilder
    private func syntheticFootPointLayer(feet: SyntheticFeetDTO, w: CGFloat, h: CGFloat) -> some View {
        if let lf = feet.leftFoot {
            ZStack {
                Circle().fill(Color.black.opacity(0.45)).frame(width: 12, height: 12)
                Circle().fill(Self.jointColor(confidence: lf.confidence)).frame(width: 8, height: 8)
            }
            .position(x: lf.x * w, y: lf.y * h)
        }
        if let rf = feet.rightFoot {
            ZStack {
                Circle().fill(Color.black.opacity(0.45)).frame(width: 12, height: 12)
                Circle().fill(Self.jointColor(confidence: rf.confidence)).frame(width: 8, height: 8)
            }
            .position(x: rf.x * w, y: rf.y * h)
        }
    }

    // MARK: — Exposed for unit tests

    static func syntheticFootSegments(
        feet: SyntheticFeetDTO, w: CGFloat, h: CGFloat
    ) -> [(CGPoint, CGPoint)] {
        var segments: [(CGPoint, CGPoint)] = []
        if let lf = feet.leftFoot {
            segments.append((
                CGPoint(x: lf.ankleX * w, y: lf.ankleY * h),
                CGPoint(x: lf.x * w, y: lf.y * h)
            ))
        }
        if let rf = feet.rightFoot {
            segments.append((
                CGPoint(x: rf.ankleX * w, y: rf.ankleY * h),
                CGPoint(x: rf.x * w, y: rf.y * h)
            ))
        }
        return segments
    }

    static func jointColor(confidence: Double) -> Color {
        if confidence >= 0.70 { return Color.yellow.opacity(0.95) }
        if confidence >= 0.50 { return Color.orange.opacity(0.90) }
        return Color.red.opacity(0.85)
    }
}

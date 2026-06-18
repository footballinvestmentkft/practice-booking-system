import SwiftUI

// MARK: — PoseSnapshotOverlayView (Phase 2A)
//
// Full-skeleton overlay drawn over the video render area.
// Normalized coordinates come from PoseSnapshotService (y already flipped):
//   x: [0,1] left → right
//   y: [0,1] top  → bottom
//
// Sized to the video's rendered frame via .frame(width:height:) at the call site;
// GeometryReader inside scales all joint positions to the actual pixel area.
//
// Landmark coverage: Apple Vision 2D body pose (VNDetectHumanBodyPoseRequest)
// provides 19 body joints — face, shoulders, elbows, wrists, hips, knees, ankles.
// Foot/toe landmarks are NOT available in the Vision 2D body pose API.
// The lowest detectable landmarks are left_ankle and right_ankle.
//
// Visual encoding:
//   Bones: double-stroke — 5 pt dark halo + 2.5 pt cyan inner line.
//          Visible on green pitch, white backgrounds, and dark surfaces.
//   Joints: 14 pt dark outline ring + 10 pt confidence-coloured fill.
//     ≥ 0.70 → yellow  (high confidence)
//     ≥ 0.50 → orange  (medium confidence)
//     <  0.50 → red    (low confidence)

struct PoseSnapshotOverlayView: View {

    let keypoints: PoseKeypointsDTO

    // MARK: — Skeleton connectivity (source name → target name)
    // Joint names match the snake_case strings stored in PoseSnapshotService.jointNameMap.

    private static let bones: [(String, String)] = [
        // Spine
        ("neck", "root"),
        // Arms — left
        ("neck",          "left_shoulder"),
        ("left_shoulder", "left_elbow"),
        ("left_elbow",    "left_wrist"),
        // Arms — right
        ("neck",           "right_shoulder"),
        ("right_shoulder", "right_elbow"),
        ("right_elbow",    "right_wrist"),
        // Legs — left
        ("root",      "left_hip"),
        ("left_hip",  "left_knee"),
        ("left_knee", "left_ankle"),
        // Legs — right
        ("root",       "right_hip"),
        ("right_hip",  "right_knee"),
        ("right_knee", "right_ankle"),
        // Face
        ("nose", "left_eye"),  ("nose", "right_eye"),
        ("left_eye", "left_ear"), ("right_eye", "right_ear"),
    ]

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            let byName = Dictionary(uniqueKeysWithValues: keypoints.body.map { ($0.name, $0) })

            ZStack {
                boneLayer(byName: byName, w: w, h: h)
                jointLayer(w: w, h: h)
            }
        }
    }

    // MARK: — Bone segments (extracted for unit tests)
    //
    // Returns the start/end CGPoint pairs for every bone whose both endpoints
    // exist in byName. Used by boneLayer and directly tested in SkeletonOverlayTests.

    static func boneSegments(
        byName: [String: BodyLandmarkDTO],
        w: CGFloat,
        h: CGFloat
    ) -> [(CGPoint, CGPoint)] {
        Self.bones.compactMap { (a, b) in
            guard let pa = byName[a], let pb = byName[b] else { return nil }
            return (
                CGPoint(x: CGFloat(pa.x) * w, y: CGFloat(pa.y) * h),
                CGPoint(x: CGFloat(pb.x) * w, y: CGFloat(pb.y) * h)
            )
        }
    }

    // MARK: — Bone lines (double-stroke — dark halo + cyan inner)
    //
    // Outer 5 pt dark stroke creates a halo that makes bones readable on any
    // background (green pitch, white wall, dark gymnasium).
    // Inner 2.5 pt cyan stroke provides the visible skeleton colour.
    // Segments computed once via boneSegments() and reused for both passes.

    private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
        let segs = Self.boneSegments(byName: byName, w: w, h: h)
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

    // MARK: — Joint dots (dark-outlined ring + confidence-coloured fill)
    //
    // Outer dark ring (14 pt) provides contrast on any background.
    // Inner coloured fill (10 pt) carries the confidence colour coding.

    @ViewBuilder
    private func jointLayer(w: CGFloat, h: CGFloat) -> some View {
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

    // MARK: — Joint colour helper (internal for unit tests)

    static func jointColor(confidence: Double) -> Color {
        if confidence >= 0.70 { return Color.yellow.opacity(0.95) }
        if confidence >= 0.50 { return Color.orange.opacity(0.90) }
        return Color.red.opacity(0.85)
    }
}

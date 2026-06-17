import SwiftUI

// MARK: — PoseSnapshotOverlayView (Phase 2A)
//
// Skeleton overlay drawn over the video render area.
// Normalized coordinates come from PoseSnapshotService (y already flipped):
//   x: [0,1] left → right
//   y: [0,1] top  → bottom
//
// Sized to the video's rendered frame via .frame(width:height:) at the call site;
// GeometryReader inside scales all joint positions to the actual pixel area.

struct PoseSnapshotOverlayView: View {

    let keypoints: PoseKeypointsDTO

    // MARK: — Skeleton connectivity (source name, target name)

    private static let bones: [(String, String)] = [
        // Spine
        ("neck", "root"),
        // Arms
        ("neck",           "left_shoulder"),  ("neck",           "right_shoulder"),
        ("left_shoulder",  "left_elbow"),     ("right_shoulder", "right_elbow"),
        ("left_elbow",     "left_wrist"),     ("right_elbow",    "right_wrist"),
        // Legs
        ("root", "left_hip"),  ("root", "right_hip"),
        ("left_hip",  "left_knee"),  ("right_hip",  "right_knee"),
        ("left_knee", "left_ankle"), ("right_knee", "right_ankle"),
        // Face
        ("nose", "left_eye"),  ("nose", "right_eye"),
        ("left_eye", "left_ear"), ("right_eye", "right_ear"),
    ]

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            let byName = Dictionary(
                uniqueKeysWithValues: keypoints.body.map { ($0.name, $0) }
            )

            ZStack {
                boneLayer(byName: byName, w: w, h: h)
                jointLayer(w: w, h: h)
            }
        }
    }

    // MARK: — Bone lines

    @ViewBuilder
    private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
        ForEach(Self.bones.indices, id: \.self) { i in
            let (aName, bName) = Self.bones[i]
            if let pa = byName[aName], let pb = byName[bName] {
                Path { path in
                    path.move(to: CGPoint(x: pa.x * w, y: pa.y * h))
                    path.addLine(to: CGPoint(x: pb.x * w, y: pb.y * h))
                }
                .stroke(Color.green.opacity(0.80), lineWidth: 2)
            }
        }
    }

    // MARK: — Joint dots

    @ViewBuilder
    private func jointLayer(w: CGFloat, h: CGFloat) -> some View {
        ForEach(keypoints.body, id: \.name) { lm in
            Circle()
                .fill(Color.yellow.opacity(0.90))
                .frame(width: 6, height: 6)
                .position(x: lm.x * w, y: lm.y * h)
        }
    }
}

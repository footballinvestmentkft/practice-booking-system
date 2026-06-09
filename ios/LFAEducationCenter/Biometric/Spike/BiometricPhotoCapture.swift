import ARKit
import SceneKit
import UIKit

// Captures a JPEG snapshot from an ARSCNView at liveness-challenge completion.
//
// Privacy rules:
//   - Snapshot taken only on explicit caller request (after 7/7 gesture confirmation
//     and a short neutral-hold recapture window).
//   - Image data lives in memory only; written to disk only during upload.
//   - No landmark, blendshape, yaw, pitch, or face_match_score is extracted here.
//   - No image is stored persistently on the iOS device.
//
// Compression:
//   - Primary quality: 0.82 — typical frontal face JPEG ≤ 300 KB.
//   - Fallback quality: 0.55 — used when primary output exceeds 5 MB.
//   - If fallback still exceeds limit (extremely unusual), capture fails.
enum BiometricPhotoCapture {

    private static let maxBytes:       Int   = 5 * 1024 * 1024  // 5 MB
    private static let primaryQuality: CGFloat = 0.82
    private static let fallbackQuality: CGFloat = 0.55

    // Capture a JPEG snapshot from the live ARSCNView.
    //
    // Returns nil if:
    //   - sceneView.snapshot() returns an image with zero size
    //   - JPEG compression at fallback quality still exceeds maxBytes
    //
    // The caller is responsible for uploading the returned Data and then
    // discarding the reference — Data is not cached internally.
    static func captureJPEG(from sceneView: ARSCNView) -> Data? {
        let image = sceneView.snapshot()
        guard image.size.width > 0, image.size.height > 0 else { return nil }
        return compress(image: image)
    }

    // Compress a UIImage to JPEG, with quality fallback.
    // Internal + visible for unit testing.
    static func compress(image: UIImage) -> Data? {
        if let data = image.jpegData(compressionQuality: primaryQuality),
           data.count <= maxBytes {
            return data
        }
        if let data = image.jpegData(compressionQuality: fallbackQuality),
           data.count <= maxBytes {
            return data
        }
        return nil  // image too large even at minimum quality
    }
}

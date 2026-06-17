import XCTest
import CoreGraphics
@testable import LFAEducationCenter

// MARK: — Phase 2A: iOS Pose Snapshot tests — PSI-01..PSI-05

@MainActor
final class PoseSnapshotServiceTests: XCTestCase {

    // MARK: — PSI-01: PoseKeypointsDTO.empty() returns zero-joint keypoints

    func test_PSI_01_empty_keypoints_has_no_joints() {
        let kp = PoseKeypointsDTO.empty()
        XCTAssertEqual(kp.schemaVersion, "1")
        XCTAssertTrue(kp.body.isEmpty)
        XCTAssertTrue(kp.leftHand.isEmpty)
        XCTAssertTrue(kp.rightHand.isEmpty)
    }

    // MARK: — PSI-02: BodyLandmarkDTO JSON round-trip uses snake_case keys

    func test_PSI_02_body_landmark_encodes_snake_case() throws {
        let landmark = BodyLandmarkDTO(name: "left_ankle", x: 0.41, y: 0.83, confidence: 0.97)
        let data = try JSONEncoder().encode(landmark)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        // Fields are flat (no CodingKeys remapping on BodyLandmarkDTO)
        XCTAssertEqual(json["name"] as? String, "left_ankle")
        XCTAssertEqual((json["x"] as? Double) ?? -1, 0.41, accuracy: 1e-6)
        XCTAssertEqual((json["y"] as? Double) ?? -1, 0.83, accuracy: 1e-6)
        XCTAssertEqual((json["confidence"] as? Double) ?? -1, 0.97, accuracy: 1e-6)
    }

    // MARK: — PSI-03: PoseSnapshotService.confidenceThreshold is 0.3

    func test_PSI_03_confidence_threshold_is_0point3() {
        XCTAssertEqual(PoseSnapshotService.confidenceThreshold, 0.3, accuracy: 1e-6)
    }

    // MARK: — PSI-04: runPoseDetection on a blank 1×1 image does not crash and
    //           returns an empty body list (no person detected).

    func test_PSI_04_run_pose_detection_on_blank_image_returns_empty_body() {
        let cgImage = makeSolidCGImage(width: 1, height: 1, gray: 1.0)
        let (keypoints, _) = PoseSnapshotService.runPoseDetection(on: cgImage)
        XCTAssertTrue(keypoints.body.isEmpty,
                      "Blank 1×1 image should produce no detected body joints")
    }

    // MARK: — PSI-05: PoseSnapshotOverlayView holds the keypoints it was given

    func test_PSI_05_overlay_view_preserves_keypoints() {
        let joints = [
            BodyLandmarkDTO(name: "root",        x: 0.5,  y: 0.5,  confidence: 0.99),
            BodyLandmarkDTO(name: "left_ankle",  x: 0.41, y: 0.83, confidence: 0.97),
            BodyLandmarkDTO(name: "right_ankle", x: 0.59, y: 0.83, confidence: 0.96),
        ]
        let kp = PoseKeypointsDTO(schemaVersion: "1", body: joints, leftHand: [], rightHand: [])
        let view = PoseSnapshotOverlayView(keypoints: kp)
        XCTAssertEqual(view.keypoints.body.count, 3)
        XCTAssertEqual(view.keypoints.body.first?.name, "root")
    }

    // MARK: — PSI-06: PoseSnapshotUploadRequest with captureSource "ios_retroactive" encodes correctly

    func test_PSI_06_upload_request_retroactive_source_encodes_snake_case() throws {
        let req = PoseSnapshotUploadRequest(
            keypoints:           PoseKeypointsDTO.empty(),
            modelVersion:        "apple_vision_v1",
            captureSource:       "ios_retroactive",
            capturedAtMs:        3500,
            imageWidthPx:        nil,
            imageHeightPx:       nil,
            inferenceConfidence: nil
        )
        let data = try JSONEncoder().encode(req)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        XCTAssertEqual(json["capture_source"] as? String, "ios_retroactive")
        XCTAssertEqual(json["captured_at_ms"] as? Int, 3500)
    }

    // MARK: — Helpers

    private func makeSolidCGImage(width: Int, height: Int, gray: CGFloat) -> CGImage {
        let colorSpace = CGColorSpaceCreateDeviceGray()
        let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue)
        let ctx = CGContext(
            data: nil,
            width: width, height: height,
            bitsPerComponent: 8, bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: bitmapInfo.rawValue
        )!
        ctx.setFillColor(gray: gray, alpha: 1.0)
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        return ctx.makeImage()!
    }
}

import XCTest
@testable import LFAEducationCenter

final class LegacySkeletonAdapterTests: XCTestCase {

    private let fixSessionId = "00000000-0000-4000-8000-000000000001"
    private let fixCaptureId = "00000000-0000-4000-8000-000000000002"
    private let fixFrameId   = "00000000-0000-4000-8000-000000000003"

    private func sampleV1() -> PoseKeypointsDTO {
        let body: [BodyLandmarkDTO] = [
            BodyLandmarkDTO(name: "nose", x: 0.50, y: 0.15, confidence: 0.98),
            BodyLandmarkDTO(name: "left_shoulder", x: 0.41, y: 0.32, confidence: 0.95),
            BodyLandmarkDTO(name: "right_shoulder", x: 0.59, y: 0.33, confidence: 0.93),
        ]
        return PoseKeypointsDTO(schemaVersion: "1", body: body, leftHand: [], rightHand: [])
    }

    // LA-S-01: v1 → v2 produces Skeleton3DFrame
    func test_LA_S_01_v1_to_v2() {
        let v1 = sampleV1()
        let frame = LegacySkeletonAdapter.adaptV1ToV2(
            keypoints: v1,
            sessionId: fixSessionId,
            captureId: fixCaptureId,
            frameId: fixFrameId
        )
        XCTAssertEqual(frame.schemaVersion, "2")
        XCTAssertEqual(frame.joints.count, 3)
    }

    // LA-S-02: All world fields nil
    func test_LA_S_02_world_nil() {
        let v1 = sampleV1()
        let frame = LegacySkeletonAdapter.adaptV1ToV2(
            keypoints: v1, sessionId: fixSessionId,
            captureId: fixCaptureId, frameId: fixFrameId
        )
        for j in frame.joints {
            XCTAssertNil(j.worldX)
            XCTAssertNil(j.worldY)
            XCTAssertNil(j.worldZ)
        }
    }

    // LA-S-03: camera_id default
    func test_LA_S_03_camera_id_default() {
        let v1 = sampleV1()
        let frame = LegacySkeletonAdapter.adaptV1ToV2(
            keypoints: v1, sessionId: fixSessionId,
            captureId: fixCaptureId, frameId: fixFrameId
        )
        XCTAssertEqual(frame.cameraId, "iphone_primary")
    }

    // LA-S-04: Deterministic — same input same output
    func test_LA_S_04_deterministic_output() {
        let v1 = sampleV1()
        let frame1 = LegacySkeletonAdapter.adaptV1ToV2(
            keypoints: v1, sessionId: fixSessionId,
            captureId: fixCaptureId, frameId: fixFrameId
        )
        let frame2 = LegacySkeletonAdapter.adaptV1ToV2(
            keypoints: v1, sessionId: fixSessionId,
            captureId: fixCaptureId, frameId: fixFrameId
        )
        XCTAssertEqual(frame1, frame2)
    }

    // LA-S-05: Fixture decode — frame_v1_adapted.json matches adapter output semantics
    func test_LA_S_05_fixture_parity() throws {
        let bundle = Bundle(for: type(of: self))
        guard let url = bundle.url(forResource: "frame_v1_adapted", withExtension: "json") else {
            throw XCTSkip("frame_v1_adapted.json not found in test bundle")
        }
        let data = try Data(contentsOf: url)
        let expected = try JSONDecoder().decode(Skeleton3DFrame.self, from: data)
        XCTAssertEqual(expected.schemaVersion, "2")
        XCTAssertEqual(expected.sessionId, fixSessionId)
        XCTAssertEqual(expected.cameraId, "iphone_primary")
        XCTAssertEqual(expected.joints.count, 19)
        for j in expected.joints {
            XCTAssertNil(j.worldX)
            XCTAssertEqual(j.triangulationStatus, "single_view_only")
        }
    }
}

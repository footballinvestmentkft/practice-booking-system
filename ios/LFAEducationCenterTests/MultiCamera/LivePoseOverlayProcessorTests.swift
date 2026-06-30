import XCTest
@testable import LFAEducationCenter

final class LivePoseOverlayProcessorTests: XCTestCase {

    // MARK: — Joint list

    func test_jointList_has19Joints() {
        XCTAssertEqual(LivePoseOverlayProcessor.jointList.count, 19)
    }

    func test_jointList_coversAllOverlayBoneEndpoints() {
        // ContinuousSkeletonOverlayView.bones uses these 19 names.
        // Any missing name means a bone will be silently dropped in the overlay.
        let required: Set<String> = [
            "nose", "neck", "root",
            "left_eye", "right_eye", "left_ear", "right_ear",
            "left_shoulder", "right_shoulder",
            "left_elbow", "right_elbow",
            "left_wrist", "right_wrist",
            "left_hip", "right_hip",
            "left_knee", "right_knee",
            "left_ankle", "right_ankle",
        ]
        let provided = Set(LivePoseOverlayProcessor.jointList.map { $0.1 })
        XCTAssertEqual(provided, required)
    }

    func test_jointList_hasNoDuplicateNames() {
        let names = LivePoseOverlayProcessor.jointList.map { $0.1 }
        XCTAssertEqual(names.count, Set(names).count)
    }

    // MARK: — Initial state

    @MainActor
    func test_initialFrame_isNil() {
        let processor = LivePoseOverlayProcessor()
        XCTAssertNil(processor.frame)
    }

    // MARK: — Feed blank image

    @MainActor
    func test_feedingBlankImage_doesNotCrash() {
        let processor = LivePoseOverlayProcessor()
        let blank = UIImage(systemName: "square") ?? UIImage()
        // Should not crash; Vision will find no body in a system icon.
        processor.feed(blank)
        // frame stays nil — no person in a blank image; async Vision result may
        // arrive later but that's fine, we just test no crash here.
        XCTAssertNil(processor.frame)
    }

    // MARK: — Throttle constant

    func test_minInterval_is200ms() {
        // 5fps → 0.2s interval
        XCTAssertEqual(LivePoseOverlayProcessor.minInterval, 0.2, accuracy: 0.001)
    }
}

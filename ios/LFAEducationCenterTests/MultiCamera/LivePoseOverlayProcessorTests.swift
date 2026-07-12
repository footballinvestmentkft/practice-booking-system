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

    // MARK: — Per-panel diagnostics (2026-07-01 flow audit)

    @MainActor
    func test_diagnostics_allZeroNil_initially() {
        let processor = LivePoseOverlayProcessor()
        XCTAssertEqual(processor.framesReceived, 0)
        XCTAssertEqual(processor.framesProcessed, 0)
        XCTAssertEqual(processor.visionDetectionSuccesses, 0)
        XCTAssertEqual(processor.framesWithSkeletonPoints, 0)
        XCTAssertNil(processor.lastFrameReceivedAt)
    }

    @MainActor
    func test_feed_incrementsFramesReceivedAndLastFrameReceivedAt_synchronously() {
        let processor = LivePoseOverlayProcessor()
        let blank = UIImage(systemName: "square") ?? UIImage()
        processor.feed(blank)
        // framesReceived/lastFrameReceivedAt are set synchronously inside feed() (MainActor),
        // unlike framesProcessed/visionDetectionSuccesses which hop through poseQueue.
        XCTAssertEqual(processor.framesReceived, 1)
        XCTAssertNotNil(processor.lastFrameReceivedAt)
    }

    @MainActor
    func test_feed_multipleFrames_incrementsFramesReceivedEachTime() {
        let processor = LivePoseOverlayProcessor()
        let blank = UIImage(systemName: "square") ?? UIImage()
        processor.feed(blank)
        processor.feed(blank)
        processor.feed(blank)
        XCTAssertEqual(processor.framesReceived, 3, "framesReceived must count every feed() call, pre-throttle")
    }

    @MainActor
    func test_feed_blankImage_eventuallyProcessesButFindsNoSkeleton() async {
        let processor = LivePoseOverlayProcessor()
        let blank = UIImage(systemName: "square") ?? UIImage()
        processor.feed(blank)
        for _ in 0..<30 { await Task.yield() }

        XCTAssertEqual(processor.framesProcessed, 1, "first feed() is never throttled — must reach Vision")
        XCTAssertEqual(processor.framesWithSkeletonPoints, 0, "a blank system icon has no detectable body")
        XCTAssertNil(processor.frame)
    }

    @MainActor
    func test_diagnosticSnapshot_containsAllFiveMetrics() {
        let processor = LivePoseOverlayProcessor()
        let snapshot = processor.diagnosticSnapshot
        for key in ["framesReceived", "framesProcessed", "visionDetectionSuccesses",
                    "framesWithSkeletonPoints", "lastFrameReceivedAt"] {
            XCTAssertNotNil(snapshot[key], "diagnosticSnapshot must include \(key)")
        }
    }
}

import XCTest
import ARKit
@testable import LFAEducationCenter

// MARK: — Mock face anchor input (no real ARSession needed)

// Injects synthetic euler angles + blendshapes to test FaceGestureDetector in isolation.
// Tests run on any Mac/simulator — no TrueDepth hardware required.
struct MockFaceAnchorInput: FaceAnchorInput {
    var faceEulerAngles: SIMD3<Float>
    var faceBlendShapes: [ARFaceAnchor.BlendShapeLocation: NSNumber]

    // MARK: — Canonical factory methods

    static func neutral() -> MockFaceAnchorInput {
        MockFaceAnchorInput(
            faceEulerAngles: SIMD3(0, 0, 0),
            faceBlendShapes: [:]   // all zeros = all below neutralMaxBlend
        )
    }

    static func headLeft(yaw: Float = 0.35) -> MockFaceAnchorInput {
        MockFaceAnchorInput(faceEulerAngles: SIMD3(yaw, 0, 0), faceBlendShapes: [:])
    }

    static func headRight(yaw: Float = -0.35) -> MockFaceAnchorInput {
        MockFaceAnchorInput(faceEulerAngles: SIMD3(yaw, 0, 0), faceBlendShapes: [:])
    }

    static func chinUp(pitch: Float = 0.25) -> MockFaceAnchorInput {
        MockFaceAnchorInput(faceEulerAngles: SIMD3(0, pitch, 0), faceBlendShapes: [:])
    }

    static func blinkRight(blinkR: Float = 0.85, blinkL: Float = 0.10) -> MockFaceAnchorInput {
        MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .eyeBlinkRight: NSNumber(value: blinkR),
            .eyeBlinkLeft:  NSNumber(value: blinkL),
        ])
    }

    static func blinkLeft(blinkL: Float = 0.85, blinkR: Float = 0.10) -> MockFaceAnchorInput {
        MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .eyeBlinkLeft:  NSNumber(value: blinkL),
            .eyeBlinkRight: NSNumber(value: blinkR),
        ])
    }

    static func smile(smileL: Float = 0.55, smileR: Float = 0.55,
                      squintL: Float = 0.20, squintR: Float = 0.20) -> MockFaceAnchorInput {
        MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .mouthSmileLeft:   NSNumber(value: smileL),
            .mouthSmileRight:  NSNumber(value: smileR),
            .cheekSquintLeft:  NSNumber(value: squintL),
            .cheekSquintRight: NSNumber(value: squintR),
        ])
    }
}

// MARK: — FGD-01…FGD-18: FaceGestureDetector unit tests

final class FaceGestureDetectorTests: XCTestCase {

    private let detector = FaceGestureDetector(thresholds: .production)
    private let t        = FacePoseThresholds.production   // shorthand

    // MARK: — FGD-01: neutral at zero → true

    func test_FGD01_neutral_at_zero_angles_and_zero_blends() {
        XCTAssertTrue(detector.detect(gesture: .neutral, from: .neutral()))
    }

    // FGD-02: neutral rejects non-zero yaw

    func test_FGD02_neutral_rejects_large_yaw() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: SIMD3(0.20, 0, 0), faceBlendShapes: [:])
        XCTAssertFalse(detector.detect(gesture: .neutral, from: anchor))
    }

    // FGD-03: neutral rejects high blendshape (smile)

    func test_FGD03_neutral_rejects_high_blendshape() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: .zero,
                                        faceBlendShapes: [.mouthSmileLeft: 0.80])
        XCTAssertFalse(detector.detect(gesture: .neutral, from: anchor))
    }

    // MARK: — Head left/right

    // FGD-04: headLeft above threshold → true

    func test_FGD04_headLeft_above_threshold() {
        XCTAssertTrue(detector.detect(gesture: .headLeft, from: .headLeft(yaw: t.yawLeft + 0.05)))
    }

    // FGD-05: headLeft exactly at threshold → false (must exceed, not equal)

    func test_FGD05_headLeft_at_threshold_not_accepted() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: SIMD3(t.yawLeft, 0, 0), faceBlendShapes: [:])
        // yaw == threshold is borderline; the detector uses > not >=
        // Expectation: false (strict greater-than)
        XCTAssertFalse(detector.detect(gesture: .headLeft, from: anchor))
    }

    // FGD-06: headRight below −threshold → true

    func test_FGD06_headRight_below_threshold() {
        XCTAssertTrue(detector.detect(gesture: .headRight, from: .headRight(yaw: -(t.yawRight + 0.05))))
    }

    // FGD-07: headRight with positive yaw → false

    func test_FGD07_headRight_with_positive_yaw_is_false() {
        XCTAssertFalse(detector.detect(gesture: .headRight, from: .headLeft()))
    }

    // MARK: — Chin up

    // FGD-08: chinUp above threshold → true

    func test_FGD08_chinUp_above_threshold() {
        XCTAssertTrue(detector.detect(gesture: .chinUp, from: .chinUp(pitch: t.pitchUp + 0.05)))
    }

    // FGD-09: chinUp below threshold → false

    func test_FGD09_chinUp_below_threshold_false() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: SIMD3(0, 0.10, 0), faceBlendShapes: [:])
        XCTAssertFalse(detector.detect(gesture: .chinUp, from: anchor))
    }

    // MARK: — Blink right

    // FGD-10: right blink with left eye open → true

    func test_FGD10_blinkRight_leftOpen_accepted() {
        XCTAssertTrue(detector.detect(gesture: .blinkRight, from: .blinkRight()))
    }

    // FGD-11: right blink but left eye also closed → false (both eyes squint prevention)

    func test_FGD11_blinkRight_leftAlsoClosed_rejected() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .eyeBlinkRight: 0.85,
            .eyeBlinkLeft:  0.80,   // too high
        ])
        XCTAssertFalse(detector.detect(gesture: .blinkRight, from: anchor))
    }

    // FGD-12: blinkRight with right < threshold → false

    func test_FGD12_blinkRight_belowThreshold_false() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .eyeBlinkRight: NSNumber(value: t.blinkMin - 0.05),
        ])
        XCTAssertFalse(detector.detect(gesture: .blinkRight, from: anchor))
    }

    // MARK: — Blink left (symmetrical)

    // FGD-13: left blink with right eye open → true

    func test_FGD13_blinkLeft_rightOpen_accepted() {
        XCTAssertTrue(detector.detect(gesture: .blinkLeft, from: .blinkLeft()))
    }

    // FGD-14: left blink but both eyes closed → false

    func test_FGD14_blinkLeft_bothClosed_rejected() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .eyeBlinkLeft:  0.85,
            .eyeBlinkRight: 0.80,
        ])
        XCTAssertFalse(detector.detect(gesture: .blinkLeft, from: anchor))
    }

    // MARK: — Smile

    // FGD-15: smile with sufficient avg + squint → true

    func test_FGD15_smile_with_squint_accepted() {
        XCTAssertTrue(detector.detect(gesture: .smile, from: .smile()))
    }

    // FGD-16: smile avg above threshold but no squint → false (anti false-positive)

    func test_FGD16_smile_without_squint_rejected() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .mouthSmileLeft:  0.60,
            .mouthSmileRight: 0.60,
            .cheekSquintLeft:  0.05,  // below smileSquintMin
            .cheekSquintRight: 0.05,
        ])
        XCTAssertFalse(detector.detect(gesture: .smile, from: anchor))
    }

    // FGD-17: smile avg below threshold → false even with squint

    func test_FGD17_smile_avg_below_threshold_false() {
        let anchor = MockFaceAnchorInput(faceEulerAngles: .zero, faceBlendShapes: [
            .mouthSmileLeft:  NSNumber(value: t.smileAvg - 0.10),
            .mouthSmileRight: NSNumber(value: t.smileAvg - 0.10),
            .cheekSquintLeft:  0.30,
            .cheekSquintRight: 0.30,
        ])
        XCTAssertFalse(detector.detect(gesture: .smile, from: anchor))
    }

    // MARK: — Cross-gesture independence

    // FGD-18: headLeft input does not trigger headRight or any other gesture

    func test_FGD18_headLeft_does_not_trigger_other_gestures() {
        let anchor = MockFaceAnchorInput.headLeft()
        XCTAssertFalse(detector.detect(gesture: .headRight,  from: anchor))
        XCTAssertFalse(detector.detect(gesture: .neutral,    from: anchor))
        XCTAssertFalse(detector.detect(gesture: .chinUp,     from: anchor))
        XCTAssertFalse(detector.detect(gesture: .blinkRight, from: anchor))
        XCTAssertFalse(detector.detect(gesture: .smile,      from: anchor))
    }
}

// MARK: — GS-01…GS-06: GestureStabilizer unit tests

@MainActor
final class GestureStabilizerTests: XCTestCase {

    // GS-01: not confirmed before holdDuration has elapsed

    func test_GS01_not_confirmed_before_hold_duration() async {
        let s = GestureStabilizer(holdDurationMs: 400)
        // Feed 10 "detected" ticks at t=0 (< 400ms elapsed)
        for _ in 0..<10 { s.update(detected: true) }
        XCTAssertFalse(s.isConfirmed)
    }

    // GS-02: timer resets when detection drops

    func test_GS02_reset_on_missed_frame() {
        let s = GestureStabilizer(holdDurationMs: 400)
        s.update(detected: true)
        XCTAssertTrue(s.isDetecting)
        s.update(detected: false)   // drop
        XCTAssertFalse(s.isDetecting)
        XCTAssertEqual(s.holdProgress, 0.0, accuracy: 0.001)
    }

    // GS-03: holdProgress is 0 when not detecting

    func test_GS03_holdProgress_zero_when_idle() {
        let s = GestureStabilizer(holdDurationMs: 400)
        XCTAssertEqual(s.holdProgress, 0.0, accuracy: 0.001)
    }

    // GS-04: reset() clears confirmed state

    func test_GS04_reset_clears_state() {
        let s = GestureStabilizer(holdDurationMs: 1)   // 1 ms — confirms almost immediately
        var fired = false
        s.onConfirmed = { fired = true }
        s.update(detected: true)
        // Sleep just past 1ms
        Thread.sleep(forTimeInterval: 0.005)
        s.update(detected: true)   // should confirm now
        XCTAssertTrue(fired)
        s.reset()
        XCTAssertFalse(s.isConfirmed)
        XCTAssertFalse(s.isDetecting)
        XCTAssertEqual(s.holdProgress, 0.0, accuracy: 0.001)
    }

    // GS-05: onConfirmed fires exactly once per gesture cycle

    func test_GS05_onConfirmed_fires_once() {
        let s = GestureStabilizer(holdDurationMs: 1)
        var count = 0
        s.onConfirmed = { count += 1 }
        s.update(detected: true)
        Thread.sleep(forTimeInterval: 0.005)
        s.update(detected: true)   // confirm
        s.update(detected: true)   // subsequent ticks after confirm
        s.update(detected: true)
        XCTAssertEqual(count, 1, "onConfirmed must fire exactly once")
    }

    // GS-06: holdProgress increases while detecting, clamps at 1.0

    func test_GS06_holdProgress_clamps_at_1() {
        let s = GestureStabilizer(holdDurationMs: 1)
        s.update(detected: true)
        Thread.sleep(forTimeInterval: 0.010)   // well past holdDuration
        XCTAssertLessThanOrEqual(s.holdProgress, 1.0)
    }
}

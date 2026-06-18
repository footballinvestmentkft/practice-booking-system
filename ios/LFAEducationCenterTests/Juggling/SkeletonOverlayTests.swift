import XCTest
import SwiftUI
@testable import LFAEducationCenter

// MARK: — SkeletonOverlayTests (AN-3B2C-1 visual improvements)
//
// SK-OV-01..06: Verify PoseSnapshotOverlayView joint colour logic and
// BallVideoOverlayView colour logic — no ViewInspector required.

final class SkeletonOverlayTests: XCTestCase {

    // MARK: — PoseSnapshotOverlayView.jointColor

    // SK-OV-01: high confidence (≥ 0.70) → yellow.
    func test_SK_OV_01_highConfidenceIsYellow() {
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.70), Color.yellow.opacity(0.95))
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.90), Color.yellow.opacity(0.95))
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 1.00), Color.yellow.opacity(0.95))
    }

    // SK-OV-02: medium confidence [0.50, 0.70) → orange.
    func test_SK_OV_02_mediumConfidenceIsOrange() {
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.50), Color.orange.opacity(0.90))
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.65), Color.orange.opacity(0.90))
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.699), Color.orange.opacity(0.90))
    }

    // SK-OV-03: low confidence (< 0.50) → red.
    func test_SK_OV_03_lowConfidenceIsRed() {
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.00), Color.red.opacity(0.85))
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.30), Color.red.opacity(0.85))
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.499), Color.red.opacity(0.85))
    }

    // SK-OV-04: boundary at exactly 0.50 → orange (not red).
    func test_SK_OV_04_boundaryAt050IsOrange() {
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.50), Color.orange.opacity(0.90),
                       "Confidence exactly 0.50 must be orange (≥ 0.50 threshold)")
    }

    // SK-OV-05: boundary at exactly 0.70 → yellow (not orange).
    func test_SK_OV_05_boundaryAt070IsYellow() {
        XCTAssertEqual(PoseSnapshotOverlayView.jointColor(confidence: 0.70), Color.yellow.opacity(0.95),
                       "Confidence exactly 0.70 must be yellow (≥ 0.70 threshold)")
    }

    // MARK: — BallVideoOverlayColorHelper

    // SK-OV-06: manual source → blue, regardless of confidence.
    func test_SK_OV_06_manualSourceIsBlue() {
        let d = makeDetection(source: "manual", confidence: 0.95)
        XCTAssertEqual(BallVideoOverlayColorHelper.ballColor(for: d), Color.blue)
    }

    // SK-OV-07: auto ≥ 0.80 → green.
    func test_SK_OV_07_autoHighConfidenceIsGreen() {
        let d = makeDetection(source: "mobilenet_ssd_v1", confidence: 0.85)
        XCTAssertEqual(BallVideoOverlayColorHelper.ballColor(for: d), Color.green)
    }

    // SK-OV-08: auto [0.50, 0.80) → yellow.
    func test_SK_OV_08_autoMediumConfidenceIsYellow() {
        let d = makeDetection(source: "mobilenet_ssd_v1", confidence: 0.60)
        XCTAssertEqual(BallVideoOverlayColorHelper.ballColor(for: d), Color.yellow)
    }

    // SK-OV-09: auto < 0.50 → orange.
    func test_SK_OV_09_autoLowConfidenceIsOrange() {
        let d = makeDetection(source: "mobilenet_ssd_v1", confidence: 0.30)
        XCTAssertEqual(BallVideoOverlayColorHelper.ballColor(for: d), Color.orange)
    }

    // SK-OV-10: nil confidence → orange.
    func test_SK_OV_10_nilConfidenceIsOrange() {
        let d = makeDetection(source: "mobilenet_ssd_v1", confidence: nil)
        XCTAssertEqual(BallVideoOverlayColorHelper.ballColor(for: d), Color.orange)
    }

    // MARK: — Fixture

    private func makeDetection(source: String, confidence: Double?) -> BallDetectionOut {
        BallDetectionOut(
            id: UUID(), contactEventId: UUID(), videoId: UUID(),
            detectionSource:      source,
            ballX:                0.5,
            ballY:                0.5,
            confidence:           confidence,
            worldXM: nil, worldYM: nil, modelVersion: nil,
            noBallDetected:       false,
            excludedFromTraining: false,
            autoBallX: nil, autoBallY: nil, autoBallConfidence: nil,
            createdAt: Date(), updatedAt: Date()
        )
    }
}

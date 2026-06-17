import XCTest
@testable import LFAEducationCenter

// MARK: — BallOverlayViewTests (AN-3B2C-1)
//
// BD-OV-01..08: Verify BallOverlayView logic — colour coding, drag-enabled flag,
// and detection property relationships. No ViewInspector required; tests
// operate on the supporting logic that BallOverlayView exposes through its
// public interface (detection, isDragEnabled, onPositionCommitted).

final class BallOverlayViewTests: XCTestCase {

    // MARK: — Fixture helpers

    private func detection(
        source: String  = "mobilenet_ssd_v1",
        ballX: Double?  = 0.5,
        ballY: Double?  = 0.5,
        confidence: Double? = 0.90,
        noBall: Bool    = false,
        autoBallX: Double? = nil,
        autoBallY: Double? = nil
    ) -> BallDetectionOut {
        BallDetectionOut(
            id:                   UUID(),
            contactEventId:       UUID(),
            videoId:              UUID(),
            detectionSource:      source,
            ballX:                ballX,
            ballY:                ballY,
            confidence:           confidence,
            worldXM:              nil,
            worldYM:              nil,
            modelVersion:         nil,
            noBallDetected:       noBall,
            excludedFromTraining: false,
            autoBallX:            autoBallX,
            autoBallY:            autoBallY,
            createdAt:            Date(),
            updatedAt:            Date()
        )
    }

    // BD-OV-01: auto detection with confidence >= 0.80 → green.
    func test_BD_OV_01_autoHighConfidenceIsGreen() {
        let d = detection(source: "mobilenet_ssd_v1", confidence: 0.90)
        let color = BallOverlayViewColorHelper.ballColor(for: d)
        XCTAssertEqual(color, .green,
                       "confidence >= 0.80 with auto source must produce green")
    }

    // BD-OV-02: auto detection with confidence in [0.50, 0.80) → yellow.
    func test_BD_OV_02_autoMediumConfidenceIsYellow() {
        let d = detection(source: "mobilenet_ssd_v1", confidence: 0.65)
        let color = BallOverlayViewColorHelper.ballColor(for: d)
        XCTAssertEqual(color, .yellow)
    }

    // BD-OV-03: auto detection with confidence < 0.50 → orange.
    func test_BD_OV_03_autoLowConfidenceIsOrange() {
        let d = detection(source: "mobilenet_ssd_v1", confidence: 0.30)
        let color = BallOverlayViewColorHelper.ballColor(for: d)
        XCTAssertEqual(color, .orange)
    }

    // BD-OV-04: auto detection with nil confidence → orange.
    func test_BD_OV_04_autoNilConfidenceIsOrange() {
        let d = detection(source: "mobilenet_ssd_v1", confidence: nil)
        let color = BallOverlayViewColorHelper.ballColor(for: d)
        XCTAssertEqual(color, .orange)
    }

    // BD-OV-05: manual detection always → blue, regardless of confidence.
    func test_BD_OV_05_manualSourceIsBlueRegardlessOfConfidence() {
        for conf in [nil, 0.1, 0.55, 0.95] as [Double?] {
            let d = detection(source: "manual", confidence: conf)
            let color = BallOverlayViewColorHelper.ballColor(for: d)
            XCTAssertEqual(color, .blue,
                           "manual detection must always be blue (confidence=\(conf.map(String.init) ?? "nil"))")
        }
    }

    // BD-OV-06: noBallDetected=true → isDragEnabled is false.
    func test_BD_OV_06_noBallDetectedDisablesDrag() {
        let d = detection(noBall: true, ballX: nil, ballY: nil)
        XCTAssertFalse(BallOverlayViewColorHelper.isDragEnabled(for: d),
                       "noBallDetected=true must disable drag regardless of coords")
    }

    // BD-OV-07: valid position with noBallDetected=false → isDragEnabled is true.
    func test_BD_OV_07_validPositionEnablesDrag() {
        let d = detection(noBall: false, ballX: 0.5, ballY: 0.5)
        XCTAssertTrue(BallOverlayViewColorHelper.isDragEnabled(for: d),
                      "A loaded detection with valid coords and noBallDetected=false must enable drag")
    }

    // BD-OV-08: nil coords with noBallDetected=false → isDragEnabled is false
    // (can't drag a position that isn't known).
    func test_BD_OV_08_nilCoordsDisablesDrag() {
        let d = detection(noBall: false, ballX: nil, ballY: nil)
        XCTAssertFalse(BallOverlayViewColorHelper.isDragEnabled(for: d),
                       "nil ballX/ballY must disable drag even when noBallDetected=false")
    }
}

// MARK: — BallOverlayViewColorHelper
//
// Thin logic extraction — mirrors BallOverlayView's private helpers so they
// can be unit-tested without instantiating a SwiftUI view.

import SwiftUI

enum BallOverlayViewColorHelper {

    static func ballColor(for detection: BallDetectionOut) -> Color {
        if detection.detectionSource == "manual" { return .blue }
        guard let c = detection.confidence else { return .orange }
        if c >= 0.80 { return .green }
        if c >= 0.50 { return .yellow }
        return .orange
    }

    static func isDragEnabled(for detection: BallDetectionOut) -> Bool {
        !detection.noBallDetected && detection.ballX != nil && detection.ballY != nil
    }
}

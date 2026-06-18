import XCTest
import SwiftUI
@testable import LFAEducationCenter

// MARK: — BallTrajectoryOverlayTests (AN-3B2D-3)
//
// Pure-logic tests for BallTrajectoryViewModel + BallTrajectoryOverlayView.
// BTR-01..12: point lookup, trail, colour logic, opacity.

final class BallTrajectoryOverlayTests: XCTestCase {

    // MARK: — Helpers

    private func makePoint(
        ms: Int, x: Double? = 0.5, y: Double? = 0.5,
        conf: Double? = 0.8, manual: Bool = false, state: String = "detected"
    ) -> BallTrajectoryPointDTO {
        BallTrajectoryPointDTO(
            frameMs: ms, ballX: x, ballY: y,
            confidence: conf, isManual: manual, trackingState: state
        )
    }

    private func makeVM(points: [BallTrajectoryPointDTO]) -> BallTrajectoryViewModel {
        let vm = BallTrajectoryViewModel(videoId: "test")
        vm.points = points
        return vm
    }

    // MARK: — BTR-01..04: Point lookup

    func test_BTR_01_pointExactMatch() {
        let vm = makeVM(points: (0..<10).map { makePoint(ms: $0 * 100) })
        let pt = vm.point(atMs: 300)
        XCTAssertNotNil(pt)
        XCTAssertEqual(pt!.frameMs, 300)
    }

    func test_BTR_02_point30msOff() {
        let vm = makeVM(points: (0..<10).map { makePoint(ms: $0 * 100) })
        let pt = vm.point(atMs: 270)
        XCTAssertNotNil(pt)
        XCTAssertEqual(pt!.frameMs, 300)
    }

    func test_BTR_03_point150msOff_nil() {
        let vm = makeVM(points: [makePoint(ms: 0)])
        let pt = vm.point(atMs: 150)
        XCTAssertNil(pt)
    }

    func test_BTR_04_pointEmpty_nil() {
        let vm = makeVM(points: [])
        let pt = vm.point(atMs: 100)
        XCTAssertNil(pt)
    }

    // MARK: — BTR-05..06: Trail

    func test_BTR_05_trailReturnsLast10() {
        let points = (0..<20).map { makePoint(ms: $0 * 100, x: 0.5, y: 0.5) }
        let vm = makeVM(points: points)
        let trail = vm.trail(beforeMs: 1500, count: 10)
        XCTAssertEqual(trail.count, 10)
        XCTAssertTrue(trail.allSatisfy { $0.frameMs < 1500 })
    }

    func test_BTR_06_trailEmpty() {
        let vm = makeVM(points: [])
        let trail = vm.trail(beforeMs: 1000)
        XCTAssertTrue(trail.isEmpty)
    }

    // MARK: — BTR-07..10: Marker colour

    func test_BTR_07_manualSeedIsBlue() {
        let pt = makePoint(ms: 0, manual: true, state: "manual_seed")
        XCTAssertEqual(BallTrajectoryOverlayView.markerColor(for: pt), Color.blue)
    }

    func test_BTR_08_detectedHighConfIsGreen() {
        let pt = makePoint(ms: 0, conf: 0.85, state: "detected")
        XCTAssertEqual(BallTrajectoryOverlayView.markerColor(for: pt), Color.green)
    }

    func test_BTR_09_detectedLowConfIsOrange() {
        let pt = makePoint(ms: 0, conf: 0.30, state: "detected")
        XCTAssertEqual(BallTrajectoryOverlayView.markerColor(for: pt), Color.orange)
    }

    func test_BTR_10_predictedIsOrange() {
        let pt = makePoint(ms: 0, conf: nil, state: "predicted")
        XCTAssertEqual(BallTrajectoryOverlayView.markerColor(for: pt), Color.orange)
    }

    // MARK: — BTR-11..12: Trail opacity

    func test_BTR_11_trailOpacityIndex0() {
        let opacity = BallTrajectoryOverlayView.trailOpacity(index: 0)
        XCTAssertEqual(opacity, 1.0, accuracy: 0.01)
    }

    func test_BTR_12_trailOpacityIndex9() {
        let opacity = BallTrajectoryOverlayView.trailOpacity(index: 9)
        XCTAssertEqual(opacity, 0.19, accuracy: 0.01)
    }
}

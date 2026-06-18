import XCTest
import SwiftUI
@testable import LFAEducationCenter

// MARK: — DensePoseExtractionTests (AN-3B2D-2)
//
// Pure-logic unit tests — no AVFoundation, no video files needed.
// DPSE-01..18: synthetic feet, interpolation, binary search, overlay segments.

final class DensePoseExtractionTests: XCTestCase {

    // MARK: — DPSE-01..06: Synthetic foot estimation

    // DPSE-01: both knee + ankle → foot extended along shin vector
    func test_DPSE_01_bothKneeAnkle_footExtended() {
        let knee  = BodyLandmarkDTO(name: "left_knee",  x: 0.5, y: 0.6, confidence: 0.9)
        let ankle = BodyLandmarkDTO(name: "left_ankle", x: 0.5, y: 0.8, confidence: 0.85)
        let foot = DensePoseExtractor.estimateOneFoot(knee: knee, ankle: ankle, ext: 0.25)

        XCTAssertNotNil(foot)
        // shin vector: (0, 0.2), foot = ankle + 0.25*(0, 0.2) = (0.5, 0.85)
        XCTAssertEqual(foot!.x, 0.5, accuracy: 0.001)
        XCTAssertEqual(foot!.y, 0.85, accuracy: 0.001)
        XCTAssertEqual(foot!.ankleX, 0.5, accuracy: 0.001)
        XCTAssertEqual(foot!.ankleY, 0.8, accuracy: 0.001)
    }

    // DPSE-02: ankle only (no knee) → foot = ankle + 0.03 downward
    func test_DPSE_02_ankleOnly_footDownward() {
        let ankle = BodyLandmarkDTO(name: "left_ankle", x: 0.4, y: 0.9, confidence: 0.8)
        let foot = DensePoseExtractor.estimateOneFoot(knee: nil, ankle: ankle, ext: 0.25)

        XCTAssertNotNil(foot)
        XCTAssertEqual(foot!.x, 0.4, accuracy: 0.001)
        XCTAssertEqual(foot!.y, 0.93, accuracy: 0.001)
    }

    // DPSE-03: no ankle → nil
    func test_DPSE_03_noAnkle_nil() {
        let foot = DensePoseExtractor.estimateOneFoot(knee: nil, ankle: nil, ext: 0.25)
        XCTAssertNil(foot)
    }

    // DPSE-04: foot point clamped to y ≤ 0.98
    func test_DPSE_04_footClampedAtBottom() {
        let knee  = BodyLandmarkDTO(name: "left_knee",  x: 0.5, y: 0.7, confidence: 0.9)
        let ankle = BodyLandmarkDTO(name: "left_ankle", x: 0.5, y: 0.97, confidence: 0.85)
        let foot = DensePoseExtractor.estimateOneFoot(knee: knee, ankle: ankle, ext: 0.25)

        XCTAssertNotNil(foot)
        XCTAssertLessThanOrEqual(foot!.y, 0.98)
    }

    // DPSE-05: confidence degraded = ankle × 0.7 (with knee)
    func test_DPSE_05_confidenceDegraded_withKnee() {
        let knee  = BodyLandmarkDTO(name: "left_knee",  x: 0.5, y: 0.6, confidence: 0.9)
        let ankle = BodyLandmarkDTO(name: "left_ankle", x: 0.5, y: 0.8, confidence: 0.8)
        let foot = DensePoseExtractor.estimateOneFoot(knee: knee, ankle: ankle, ext: 0.25)

        XCTAssertNotNil(foot)
        XCTAssertEqual(foot!.confidence, 0.8 * 0.7, accuracy: 0.001)
    }

    // DPSE-06: confidence degraded = ankle × 0.5 (without knee)
    func test_DPSE_06_confidenceDegraded_withoutKnee() {
        let ankle = BodyLandmarkDTO(name: "left_ankle", x: 0.5, y: 0.8, confidence: 0.8)
        let foot = DensePoseExtractor.estimateOneFoot(knee: nil, ankle: ankle, ext: 0.25)

        XCTAssertNotNil(foot)
        XCTAssertEqual(foot!.confidence, 0.8 * 0.5, accuracy: 0.001)
    }

    // MARK: — DPSE-07..11: Interpolation

    private func makeFrame(ms: Int, x: Double, y: Double, conf: Double) -> DensePoseFrame {
        let body = [BodyLandmarkDTO(name: "nose", x: x, y: y, confidence: conf)]
        let kp = PoseKeypointsDTO(schemaVersion: "1", body: body, leftHand: [], rightHand: [])
        return DensePoseFrame(timestampMs: ms, keypoints: kp, confidence: Float(conf), syntheticFeet: nil)
    }

    // DPSE-07: t=0.0 → returns prev frame
    func test_DPSE_07_interpolateT0() {
        let prev = makeFrame(ms: 0, x: 0.1, y: 0.2, conf: 0.8)
        let next = makeFrame(ms: 100, x: 0.3, y: 0.4, conf: 0.9)
        let result = DenseSkeletonViewModel.interpolate(prev: prev, next: next, t: 0.0)

        XCTAssertEqual(result.timestampMs, 0)
        XCTAssertEqual(result.keypoints.body.first!.x, 0.1, accuracy: 0.001)
        XCTAssertEqual(result.keypoints.body.first!.y, 0.2, accuracy: 0.001)
    }

    // DPSE-08: t=1.0 → returns next frame
    func test_DPSE_08_interpolateT1() {
        let prev = makeFrame(ms: 0, x: 0.1, y: 0.2, conf: 0.8)
        let next = makeFrame(ms: 100, x: 0.3, y: 0.4, conf: 0.9)
        let result = DenseSkeletonViewModel.interpolate(prev: prev, next: next, t: 1.0)

        XCTAssertEqual(result.timestampMs, 100)
        XCTAssertEqual(result.keypoints.body.first!.x, 0.3, accuracy: 0.001)
        XCTAssertEqual(result.keypoints.body.first!.y, 0.4, accuracy: 0.001)
    }

    // DPSE-09: t=0.5 → midpoint
    func test_DPSE_09_interpolateMidpoint() {
        let prev = makeFrame(ms: 0, x: 0.0, y: 0.0, conf: 0.8)
        let next = makeFrame(ms: 100, x: 1.0, y: 1.0, conf: 1.0)
        let result = DenseSkeletonViewModel.interpolate(prev: prev, next: next, t: 0.5)

        XCTAssertEqual(result.timestampMs, 50)
        XCTAssertEqual(result.keypoints.body.first!.x, 0.5, accuracy: 0.001)
        XCTAssertEqual(result.keypoints.body.first!.y, 0.5, accuracy: 0.001)
        XCTAssertEqual(result.keypoints.body.first!.confidence, 0.9, accuracy: 0.001)
    }

    // DPSE-10: joint only in prev → returned as-is
    func test_DPSE_10_jointOnlyInPrev() {
        let prevBody = [BodyLandmarkDTO(name: "nose", x: 0.5, y: 0.5, confidence: 0.8),
                        BodyLandmarkDTO(name: "left_eye", x: 0.3, y: 0.3, confidence: 0.7)]
        let nextBody = [BodyLandmarkDTO(name: "nose", x: 0.6, y: 0.6, confidence: 0.9)]
        let prev = DensePoseFrame(timestampMs: 0,
                                  keypoints: PoseKeypointsDTO(schemaVersion: "1", body: prevBody, leftHand: [], rightHand: []),
                                  confidence: nil, syntheticFeet: nil)
        let next = DensePoseFrame(timestampMs: 100,
                                  keypoints: PoseKeypointsDTO(schemaVersion: "1", body: nextBody, leftHand: [], rightHand: []),
                                  confidence: nil, syntheticFeet: nil)
        let result = DenseSkeletonViewModel.interpolate(prev: prev, next: next, t: 0.5)

        let resultByName = Dictionary(uniqueKeysWithValues: result.keypoints.body.map { ($0.name, $0) })
        XCTAssertNotNil(resultByName["left_eye"])
        XCTAssertEqual(resultByName["left_eye"]!.x, 0.3, accuracy: 0.001)
    }

    // DPSE-11: joint only in next → returned as-is
    func test_DPSE_11_jointOnlyInNext() {
        let prevBody = [BodyLandmarkDTO(name: "nose", x: 0.5, y: 0.5, confidence: 0.8)]
        let nextBody = [BodyLandmarkDTO(name: "nose", x: 0.6, y: 0.6, confidence: 0.9),
                        BodyLandmarkDTO(name: "right_eye", x: 0.7, y: 0.4, confidence: 0.85)]
        let prev = DensePoseFrame(timestampMs: 0,
                                  keypoints: PoseKeypointsDTO(schemaVersion: "1", body: prevBody, leftHand: [], rightHand: []),
                                  confidence: nil, syntheticFeet: nil)
        let next = DensePoseFrame(timestampMs: 100,
                                  keypoints: PoseKeypointsDTO(schemaVersion: "1", body: nextBody, leftHand: [], rightHand: []),
                                  confidence: nil, syntheticFeet: nil)
        let result = DenseSkeletonViewModel.interpolate(prev: prev, next: next, t: 0.5)

        let resultByName = Dictionary(uniqueKeysWithValues: result.keypoints.body.map { ($0.name, $0) })
        XCTAssertNotNil(resultByName["right_eye"])
        XCTAssertEqual(resultByName["right_eye"]!.x, 0.7, accuracy: 0.001)
    }

    // MARK: — DPSE-12..15: Binary search / frame lookup

    // DPSE-12: exact match
    func test_DPSE_12_frameExactMatch() {
        let cache = DensePoseCache()
        let vm = DenseSkeletonViewModel(videoId: "test", cache: cache)
        let frames = (0..<10).map { i in makeFrame(ms: i * 100, x: 0.5, y: 0.5, conf: 0.8) }
        cache.set("test", frames: frames)

        let result = vm.frame(atMs: 300)
        XCTAssertNotNil(result)
        XCTAssertEqual(result!.timestampMs, 300)
    }

    // DPSE-13: 30ms off → returns nearest (unambiguous: 270 is closer to 300 than 200)
    func test_DPSE_13_frame30msOff() {
        let cache = DensePoseCache()
        let vm = DenseSkeletonViewModel(videoId: "test", cache: cache)
        let frames = (0..<10).map { i in makeFrame(ms: i * 100, x: 0.5, y: 0.5, conf: 0.8) }
        cache.set("test", frames: frames)

        let result = vm.frame(atMs: 270)
        XCTAssertNotNil(result)
        XCTAssertEqual(result!.timestampMs, 300)
    }

    // DPSE-14: 150ms off → nil (beyond 100ms threshold)
    func test_DPSE_14_frame150msOff_nil() {
        let cache = DensePoseCache()
        let vm = DenseSkeletonViewModel(videoId: "test", cache: cache)
        cache.set("test", frames: [makeFrame(ms: 0, x: 0.5, y: 0.5, conf: 0.8)])

        let result = vm.frame(atMs: 150)
        XCTAssertNil(result)
    }

    // DPSE-15: empty cache → nil
    func test_DPSE_15_emptyCache_nil() {
        let cache = DensePoseCache()
        let vm = DenseSkeletonViewModel(videoId: "test", cache: cache)

        let result = vm.frame(atMs: 100)
        XCTAssertNil(result)
    }

    // MARK: — DPSE-16..18: Overlay foot segments

    // DPSE-16: both feet → 2 segments
    func test_DPSE_16_bothFeet_twoSegments() {
        let feet = SyntheticFeetDTO(
            leftFoot:  SyntheticFootPoint(x: 0.3, y: 0.9, confidence: 0.5, ankleX: 0.3, ankleY: 0.8),
            rightFoot: SyntheticFootPoint(x: 0.7, y: 0.9, confidence: 0.5, ankleX: 0.7, ankleY: 0.8)
        )
        let segs = ContinuousSkeletonOverlayView.syntheticFootSegments(feet: feet, w: 100, h: 100)
        XCTAssertEqual(segs.count, 2)
    }

    // DPSE-17: left only → 1 segment
    func test_DPSE_17_leftOnly_oneSegment() {
        let feet = SyntheticFeetDTO(
            leftFoot:  SyntheticFootPoint(x: 0.3, y: 0.9, confidence: 0.5, ankleX: 0.3, ankleY: 0.8),
            rightFoot: nil
        )
        let segs = ContinuousSkeletonOverlayView.syntheticFootSegments(feet: feet, w: 100, h: 100)
        XCTAssertEqual(segs.count, 1)
    }

    // DPSE-18: no feet → 0 segments
    func test_DPSE_18_noFeet_zeroSegments() {
        let feet = SyntheticFeetDTO(leftFoot: nil, rightFoot: nil)
        let segs = ContinuousSkeletonOverlayView.syntheticFootSegments(feet: feet, w: 100, h: 100)
        XCTAssertEqual(segs.count, 0)
    }
}

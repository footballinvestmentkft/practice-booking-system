import XCTest
@testable import LFAEducationCenter

// MARK: — EventPreviewSession unit tests (AN-3B2A P2C-1)
//
// All tests are synchronous and never touch an actual AVAsset / network.
// They verify:
//   • initial published state
//   • stop() from idle (no crash)
//   • clampRange boundary cases (pure math, no AVFoundation)
//   • start() triggers isLoading immediately

@MainActor
final class EventPreviewSessionTests: XCTestCase {

    // P2C_PREV_01 — initial published state is idle/clean
    func test_P2C_PREV_01_initialState() {
        let sut = EventPreviewSession()
        XCTAssertFalse(sut.isLoading,   "isLoading should start false")
        XCTAssertFalse(sut.isPlaying,   "isPlaying should start false")
        XCTAssertFalse(sut.hasError,    "hasError should start false")
        XCTAssertEqual(sut.loopStart, 0, "loopStart should start 0")
        XCTAssertEqual(sut.loopEnd,   0, "loopEnd should start 0")
    }

    // P2C_PREV_02 — stop() from idle does not crash and leaves state clean
    func test_P2C_PREV_02_stopFromIdle_noCrash() {
        let sut = EventPreviewSession()
        sut.stop()
        XCTAssertFalse(sut.isLoading)
        XCTAssertFalse(sut.isPlaying)
        XCTAssertFalse(sut.hasError)
    }

    // P2C_PREV_03 — clampRange: center well inside clip
    func test_P2C_PREV_03_clampRange_center() {
        let (start, end) = EventPreviewSession.clampRange(timestampMs: 5_000, durationSec: 10.0)
        XCTAssertEqual(start, 4.5, accuracy: 0.001)
        XCTAssertEqual(end,   5.5, accuracy: 0.001)
    }

    // P2C_PREV_04 — clampRange: near start — start clamps to 0
    func test_P2C_PREV_04_clampRange_nearStart() {
        let (start, end) = EventPreviewSession.clampRange(timestampMs: 200, durationSec: 10.0)
        XCTAssertEqual(start, 0.0,  accuracy: 0.001)
        XCTAssertEqual(end,   0.7,  accuracy: 0.001)
    }

    // P2C_PREV_05 — clampRange: near end — end clamps to durationSec
    func test_P2C_PREV_05_clampRange_nearEnd() {
        let (start, end) = EventPreviewSession.clampRange(timestampMs: 9_800, durationSec: 10.0)
        XCTAssertEqual(start, 9.3,  accuracy: 0.001)
        XCTAssertEqual(end,  10.0,  accuracy: 0.001)
    }

    // P2C_PREV_06 — clampRange: exactly at start (t=0)
    func test_P2C_PREV_06_clampRange_atStart() {
        let (start, end) = EventPreviewSession.clampRange(timestampMs: 0, durationSec: 5.0)
        XCTAssertEqual(start, 0.0, accuracy: 0.001)
        XCTAssertEqual(end,   0.5, accuracy: 0.001)
    }

    // P2C_PREV_07 — clampRange: exactly at end (t == durationSec)
    func test_P2C_PREV_07_clampRange_atEnd() {
        let (start, end) = EventPreviewSession.clampRange(timestampMs: 5_000, durationSec: 5.0)
        XCTAssertEqual(start, 4.5, accuracy: 0.001)
        XCTAssertEqual(end,   5.0, accuracy: 0.001)
    }

    // P2C_PREV_08 — start() sets isLoading=true synchronously
    // (the async asset load happens later; we only check the immediate state)
    func test_P2C_PREV_08_startSetsIsLoadingImmediately() {
        let sut = EventPreviewSession()
        let url = URL(string: "https://example.invalid/test.mp4")!
        sut.start(url: url, timestampMs: 1_000)
        XCTAssertTrue(sut.isLoading, "isLoading must be true immediately after start()")
        XCTAssertFalse(sut.isPlaying)
        XCTAssertFalse(sut.hasError)
        // Clean up the in-flight Task so it doesn't outlive the test.
        sut.stop()
    }
}

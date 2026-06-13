import XCTest
import AVFoundation
@testable import LFAEducationCenter

// MARK: — AN-3B1: PlaybackControlBar unit tests (AN3B-B01..B08)
//
// PlaybackControlBar is a SwiftUI view — its rendering is not unit-testable without
// ViewInspector.  What IS testable:
//   - PlaybackControlBar.formatTimestamp(ms:)  (static pure function)
//   - Interaction: tapping actions delegate to PlaybackController (via MockPlayer)
//   - The bar can be instantiated without crashing

@MainActor
final class PlaybackBarTests: XCTestCase {

    // MARK: — AN3B-B01: timestamp format — zero

    func test_AN3B_B01_timestampFormat_zero() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 0), "0:00.000")
    }

    // MARK: — AN3B-B02: timestamp format — 1 second

    func test_AN3B_B02_timestampFormat_oneSecond() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 1000), "0:01.000")
    }

    // MARK: — AN3B-B03: timestamp format — 1 minute

    func test_AN3B_B03_timestampFormat_oneMinute() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 60_000), "1:00.000")
    }

    // MARK: — AN3B-B04: timestamp format — complex (1:15.500)

    func test_AN3B_B04_timestampFormat_complex() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 75_500), "1:15.500")
    }

    // MARK: — AN3B-B05: timestamp format — sub-second

    func test_AN3B_B05_timestampFormat_subSecond() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 42), "0:00.042")
    }

    // MARK: — AN3B-B06: timestamp format — two minutes

    func test_AN3B_B06_timestampFormat_twoMinutes() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 120_001), "2:00.001")
    }

    // MARK: — AN3B-B07: control bar initialises without crashing

    func test_AN3B_B07_controlBarInitDoesNotCrash() {
        let player     = MockPlayer()
        let controller = PlaybackController(player: player)
        // SwiftUI views are value types — instantiation must not crash.
        let bar = PlaybackControlBar(controller: controller)
        _ = bar   // suppress "never used" warning
    }

    // MARK: — AN3B-B08: formatTimestamp handles large values

    func test_AN3B_B08_timestampFormat_largeValue() {
        // 10 minutes, 5 seconds, 123 ms = 605_123 ms
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 605_123), "10:05.123")
    }

    // MARK: — Validation supplement

    // AN3B-B09: isEnabled=false is accepted without crash (disabled-when-loader-not-ready)
    func test_AN3B_B09_controlBarAcceptsIsEnabledFalse() {
        let controller = PlaybackController(player: MockPlayer())
        let bar = PlaybackControlBar(controller: controller, isEnabled: false)
        _ = bar   // SwiftUI views are value types — init must not crash
    }

    // AN3B-B10: isEnabled defaults to true (enabled when no explicit value provided)
    func test_AN3B_B10_controlBarDefaultsToEnabled() {
        let controller = PlaybackController(player: MockPlayer())
        let bar = PlaybackControlBar(controller: controller)
        XCTAssertTrue(bar.isEnabled)
    }

    // AN3B-B11: formatTimestamp — 59.999 s boundary
    func test_AN3B_B11_timestampFormat_59999ms() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 59_999), "0:59.999")
    }

    // AN3B-B12: formatTimestamp — exactly 60.000 s (minute rollover)
    func test_AN3B_B12_timestampFormat_60000ms() {
        XCTAssertEqual(PlaybackControlBar.formatTimestamp(ms: 60_000), "1:00.000")
    }

    // AN3B-B13: bar has no reference to AnnotationVideoLoader (no coupling)
    // PlaybackControlBar only depends on PlaybackController — verified structurally.
    func test_AN3B_B13_barHasNoLoaderDependency() {
        // This test compiles only if PlaybackControlBar does not require
        // AnnotationVideoLoader as a parameter.  If it did, this would fail to compile.
        let ctrl = PlaybackController(player: MockPlayer())
        let bar  = PlaybackControlBar(controller: ctrl)
        _ = bar
    }

    // AN3B-B14: step backward delegates to controller.stepBackward
    func test_AN3B_B14_stepBackwardDelegatesToController() {
        let player = MockPlayer()
        player._currentTime = CMTime(value: 1000, timescale: 1000)  // 1.0 s
        let ctrl = PlaybackController(player: player)
        ctrl.frameDuration = CMTime(value: 1, timescale: 30)

        ctrl.stepBackward()

        XCTAssertTrue(player.seekCalled, "stepBackward must call seek on the underlying player")
        XCTAssertTrue(player.pauseCalled, "stepBackward must pause before seeking")
    }

    // AN3B-B15: step forward delegates to controller.stepForward
    func test_AN3B_B15_stepForwardDelegatesToController() {
        let player = MockPlayer()
        player._currentTime = CMTime(value: 500, timescale: 1000)  // 0.5 s
        let ctrl = PlaybackController(player: player)
        ctrl.frameDuration = CMTime(value: 1, timescale: 30)

        ctrl.stepForward()

        XCTAssertTrue(player.seekCalled)
        XCTAssertTrue(player.pauseCalled)
    }

    // AN3B-B16: rate 0.25× stored and applied
    func test_AN3B_B16_rateQuarterStoredAndApplied() {
        let player = MockPlayer()
        let ctrl   = PlaybackController(player: player)
        ctrl.play()   // must be playing for rate to apply immediately

        ctrl.setRate(.quarter)

        XCTAssertEqual(ctrl.selectedRate, .quarter)
        XCTAssertEqual(player.rate, 0.25, accuracy: 0.001)
    }

    // AN3B-B17: rate 0.5× stored and applied
    func test_AN3B_B17_rateHalfStoredAndApplied() {
        let player = MockPlayer()
        let ctrl   = PlaybackController(player: player)
        ctrl.play()

        ctrl.setRate(.half)

        XCTAssertEqual(ctrl.selectedRate, .half)
        XCTAssertEqual(player.rate, 0.5, accuracy: 0.001)
    }

    // AN3B-B18: rate 1× (normal) is the default
    func test_AN3B_B18_rateNormalIsDefault() {
        let ctrl = PlaybackController(player: MockPlayer())
        XCTAssertEqual(ctrl.selectedRate, .normal)
    }
}

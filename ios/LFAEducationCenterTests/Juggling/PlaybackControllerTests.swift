import XCTest
import AVFoundation
@testable import LFAEducationCenter

// MARK: — AN-3A: PlaybackController unit tests (AN3-T17..T28)
//
// Uses MockPlayer (PlayerSeekable) to avoid real AVPlayer.
// PlaybackController.frameDuration(nominalFPS:minFrameDuration:) is a static
// pure function — tested directly without any AVAsset or network involvement.

// MARK: — MockPlayer

final class MockPlayer: PlayerSeekable {
    var _currentTime: CMTime = .zero  // backing store; set directly in tests
    func currentTime() -> CMTime { _currentTime }
    var rate:        Float  = 0
    var seekCalled        = false
    var lastSeekTarget:  CMTime?
    var lastToleranceBefore: CMTime?
    var lastToleranceAfter:  CMTime?
    var playCalled  = false
    var pauseCalled = false

    func seek(to time: CMTime, toleranceBefore: CMTime, toleranceAfter: CMTime) {
        seekCalled            = true
        lastSeekTarget        = time
        lastToleranceBefore   = toleranceBefore
        lastToleranceAfter    = toleranceAfter
        _currentTime          = time  // simulate instantaneous seek for tests
    }

    func play()  { playCalled  = true; rate = 1.0 }
    func pause() { pauseCalled = true; rate = 0 }
}

// MARK: — PlaybackControllerTests

@MainActor
final class PlaybackControllerTests: XCTestCase {

    private func makeController(currentTime: CMTime = .zero, duration: CMTime = .zero) -> (PlaybackController, MockPlayer) {
        let player = MockPlayer()
        player._currentTime = currentTime
        let controller = PlaybackController(player: player)
        if duration > .zero {
            controller.duration = duration
        }
        return (controller, player)
    }

    // MARK: — frameDuration pure-function tests (AN3-T17..T23)

    // AN3-T17: 24fps
    func test_AN3_T17_frameDuration24fps() {
        let d = PlaybackController.frameDuration(nominalFPS: 24, minFrameDuration: nil)
        XCTAssertEqual(d.seconds, 1.0 / 24.0, accuracy: 0.0001)
    }

    // AN3-T18: 25fps
    func test_AN3_T18_frameDuration25fps() {
        let d = PlaybackController.frameDuration(nominalFPS: 25, minFrameDuration: nil)
        XCTAssertEqual(d.seconds, 1.0 / 25.0, accuracy: 0.0001)
    }

    // AN3-T19: 29.97fps
    func test_AN3_T19_frameDuration2997fps() {
        let d = PlaybackController.frameDuration(nominalFPS: 29.97, minFrameDuration: nil)
        XCTAssertEqual(d.seconds, 1.0 / 29.97, accuracy: 0.0001)
    }

    // AN3-T20: 30fps
    func test_AN3_T20_frameDuration30fps() {
        let d = PlaybackController.frameDuration(nominalFPS: 30, minFrameDuration: nil)
        XCTAssertEqual(d.seconds, 1.0 / 30.0, accuracy: 0.0001)
    }

    // AN3-T21: 60fps
    func test_AN3_T21_frameDuration60fps() {
        let d = PlaybackController.frameDuration(nominalFPS: 60, minFrameDuration: nil)
        XCTAssertEqual(d.seconds, 1.0 / 60.0, accuracy: 0.0001)
    }

    // AN3-T22: missing metadata (nominalFPS=0, no minFrameDuration) → 30fps fallback
    func test_AN3_T22_frameDurationMissingMetadataFallsBackTo30fps() {
        let d = PlaybackController.frameDuration(nominalFPS: 0, minFrameDuration: nil)
        XCTAssertEqual(d.seconds, 1.0 / 30.0, accuracy: 0.0001)
    }

    // AN3-T23: VFR — nominalFPS=0 but minFrameDuration valid → uses minFrameDuration
    func test_AN3_T23_frameDurationVFRUsesMinFrameDuration() {
        let minDur = CMTime(value: 1, timescale: 60)  // 1/60s
        let d = PlaybackController.frameDuration(nominalFPS: 0, minFrameDuration: minDur)
        XCTAssertEqual(d.seconds, minDur.seconds, accuracy: 0.0001)
    }

    // MARK: — Clamp tests (AN3-T24..T25)

    // AN3-T24: stepForward clamps at duration
    func test_AN3_T24_stepForwardClampsAtDuration() {
        let duration   = CMTime(value: 5000, timescale: 1000)  // 5.000 s
        let nearEnd    = CMTime(value: 4995, timescale: 1000)  // 4.995 s
        let (ctrl, player) = makeController(currentTime: nearEnd, duration: duration)
        ctrl.frameDuration = CMTime(value: 1, timescale: 30)   // ~33ms

        ctrl.stepForward()

        // 4.995 + 0.033 > 5.000 → clamped to 5.000
        XCTAssertLessThanOrEqual(player.lastSeekTarget!.seconds, duration.seconds)
        XCTAssertEqual(player.lastToleranceBefore, .zero)
        XCTAssertEqual(player.lastToleranceAfter,  .zero)
    }

    // AN3-T25: stepBackward clamps at zero
    func test_AN3_T25_stepBackwardClampsAtZero() {
        let nearStart  = CMTime(value: 10, timescale: 1000)   // 0.010 s
        let (ctrl, player) = makeController(currentTime: nearStart)
        ctrl.frameDuration = CMTime(value: 1, timescale: 30)  // ~33ms

        ctrl.stepBackward()

        // 0.010 - 0.033 < 0 → clamped to .zero
        XCTAssertEqual(player.lastSeekTarget?.seconds ?? -1, 0.0, accuracy: 0.0001)
    }

    // MARK: — Step-while-paused guard (AN3-T26)

    // AN3-T26: stepForward pauses player before seeking
    func test_AN3_T26_stepForwardAlwaysPausesFirst() {
        let (ctrl, player) = makeController(currentTime: CMTime(value: 1000, timescale: 1000))
        ctrl.frameDuration = CMTime(value: 1, timescale: 30)
        player.rate = 1.0  // simulate playing

        ctrl.stepForward()

        XCTAssertTrue(player.pauseCalled, "step must pause before seeking")
        XCTAssertTrue(player.seekCalled)
    }

    // MARK: — Rate (AN3-T27)

    // AN3-T27: setRate updates selectedRate; applies to player only while playing
    func test_AN3_T27_setRateUpdatesRateWhilePlaying() {
        let (ctrl, player) = makeController()
        ctrl.play()                    // starts at .normal (1×)
        player.rate = 1.0

        ctrl.setRate(.quarter)

        XCTAssertEqual(ctrl.selectedRate, .quarter)
        XCTAssertEqual(player.rate, 0.25, accuracy: 0.001)
    }

    func test_AN3_T27b_setRateDoesNotChangePlayerRateWhilePaused() {
        let (ctrl, player) = makeController()
        // ctrl is not playing

        ctrl.setRate(.half)

        XCTAssertEqual(ctrl.selectedRate, .half)
        XCTAssertEqual(player.rate, 0.0)  // player not affected while paused
    }

    // MARK: — Timestamp conversion (AN3-T28)

    // AN3-T28: seek(toTimestampMs:) converts ms → CMTime correctly
    func test_AN3_T28_seekToTimestampMsConvertsCorrectly() {
        let (ctrl, player) = makeController()

        ctrl.seek(toTimestampMs: 1500)

        XCTAssertEqual(player.lastSeekTarget?.seconds ?? 0, 1.5, accuracy: 0.001)
        XCTAssertEqual(ctrl.currentTimestampMs, 1500)
        // timeline seek uses loose tolerance
        XCTAssertEqual(player.lastToleranceBefore, .positiveInfinity)
        XCTAssertEqual(player.lastToleranceAfter,  .positiveInfinity)
    }

    // MARK: — CMTime.asMilliseconds helper

    func test_cmTimeAsMilliseconds_valid() {
        XCTAssertEqual(CMTime(value: 2500, timescale: 1000).asMilliseconds, 2500)
        XCTAssertEqual(CMTime(value: 1,    timescale: 2).asMilliseconds,    500)
    }

    func test_cmTimeAsMilliseconds_invalid() {
        XCTAssertEqual(CMTime.invalid.asMilliseconds, 0)
        XCTAssertEqual(CMTime.indefinite.asMilliseconds, 0)
    }
}

import XCTest
import SwiftUI
@testable import LFAEducationCenter

// MARK: — AN-3B2: EventTimelineView static helpers (AN3B-T01..T08)
//
// EventTimelineView exposes two static helpers specifically for unit testing
// without a SwiftUI render loop:
//   - xPosition(ms:durationMs:trackWidth:)
//   - pinColor(for:)

final class EventTimelineTests: XCTestCase {

    // MARK: — xPosition

    // AN3B-T01: timestamp at 0 → x = 0
    func test_AN3B_T01_xPosition_atStart_isZero() {
        let x = EventTimelineView.xPosition(ms: 0, durationMs: 10_000, trackWidth: 300)
        XCTAssertEqual(x, 0, accuracy: 0.5, "Start of timeline must be at x=0")
    }

    // AN3B-T02: timestamp equal to duration → x = trackWidth
    func test_AN3B_T02_xPosition_atEnd_isTrackWidth() {
        let trackWidth: CGFloat = 300
        let x = EventTimelineView.xPosition(ms: 10_000, durationMs: 10_000, trackWidth: trackWidth)
        XCTAssertEqual(x, trackWidth, accuracy: 0.5, "End of timeline must be at x=trackWidth")
    }

    // AN3B-T03: timestamp at midpoint → x = trackWidth / 2
    func test_AN3B_T03_xPosition_atMidpoint_isHalfWidth() {
        let trackWidth: CGFloat = 300
        let x = EventTimelineView.xPosition(ms: 5_000, durationMs: 10_000, trackWidth: trackWidth)
        XCTAssertEqual(x, trackWidth / 2, accuracy: 0.5, "Midpoint must map to half the track width")
    }

    // AN3B-T04: zero-duration guard → returns 0 (no division by zero crash)
    func test_AN3B_T04_xPosition_zeroDuration_returnsZero() {
        let x = EventTimelineView.xPosition(ms: 5_000, durationMs: 0, trackWidth: 300)
        XCTAssertEqual(x, 0, accuracy: 0.5, "Zero-duration must return 0 to avoid divide-by-zero")
    }

    // AN3B-T05: timestamp beyond duration → clamped to trackWidth
    func test_AN3B_T05_xPosition_beyondDuration_clamped() {
        let trackWidth: CGFloat = 300
        let x = EventTimelineView.xPosition(ms: 15_000, durationMs: 10_000, trackWidth: trackWidth)
        XCTAssertLessThanOrEqual(x, trackWidth, "x must not exceed trackWidth for over-range timestamps")
    }

    // AN3B-T06: proportional position check (25% = quarter-width)
    func test_AN3B_T06_xPosition_quarterDuration_isQuarterWidth() {
        let trackWidth: CGFloat = 400
        let x = EventTimelineView.xPosition(ms: 2_500, durationMs: 10_000, trackWidth: trackWidth)
        XCTAssertEqual(x, 100, accuracy: 0.5, "25% into timeline must be at x=trackWidth*0.25")
    }

    // MARK: — pinColor

    // AN3B-T07: known status values return non-clear colors
    func test_AN3B_T07_pinColor_knownStatuses_nonClear() {
        let statuses: [ContactEventSyncStatus] = [
            .localOnly, .syncing, .synced, .conflicted, .needsReconciliation
        ]
        for status in statuses {
            let color = EventTimelineView.pinColor(for: status)
            XCTAssertNotEqual(color, Color.clear,
                "pinColor for \(status) must not be .clear — pin must be visible")
        }
    }

    // AN3B-T08: .deleted status returns .clear (deleted events have no visible pin)
    func test_AN3B_T08_pinColor_deleted_isClear() {
        let color = EventTimelineView.pinColor(for: .deleted)
        XCTAssertEqual(color, Color.clear,
            "Deleted events must render as .clear — they should not appear on the timeline")
    }
}

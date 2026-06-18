import XCTest
import SwiftUI
@testable import LFAEducationCenter

// MARK: — BallFeedbackPanelTests (AN-3B2B1, BFP-01..04)

final class BallFeedbackPanelTests: XCTestCase {

    // MARK: — BFP-01: confirm decision → green

    func test_BFP_01_confirmIsGreen() {
        XCTAssertEqual(BallFeedbackPanelColorHelper.decisionColor("confirm"), Color.green)
    }

    // MARK: — BFP-02: no_ball decision → red

    func test_BFP_02_noBallIsRed() {
        XCTAssertEqual(BallFeedbackPanelColorHelper.decisionColor("no_ball"), Color.red)
    }

    // MARK: — BFP-03: corrected decision → orange

    func test_BFP_03_correctedIsOrange() {
        XCTAssertEqual(BallFeedbackPanelColorHelper.decisionColor("corrected"), Color.orange)
    }

    // MARK: — BFP-04: unknown decision → secondary

    func test_BFP_04_unknownIsSecondary() {
        XCTAssertEqual(BallFeedbackPanelColorHelper.decisionColor("unknown_xyz"), Color.secondary)
    }
}

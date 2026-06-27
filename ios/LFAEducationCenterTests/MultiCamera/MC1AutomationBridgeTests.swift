@testable import LFAEducationCenter
import XCTest

// MARK: — MC1-AUTO-1: lfa-mc1:// deep link parsing for the physical-test automation bridge

@MainActor
final class MC1AutomationBridgeTests: XCTestCase {

    private func makeBridge() -> MC1AutomationBridge {
        // MC1AutomationBridge.shared is a singleton with persistent @Published state;
        // tests use the shared instance but only assert on the return value and the
        // freshly-set lastAction/presentSessionLab after each handle() call.
        MC1AutomationBridge.shared
    }

    // MARK: — AB-01: join with explicit player role

    func test_AB_01_join_withPlayerRole() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=join&session_uuid=abc-123&role=player")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction, .joinSession(uuid: "abc-123", role: .player))
        XCTAssertTrue(bridge.presentSessionLab)
    }

    // MARK: — AB-02: join with instructor role

    func test_AB_02_join_withInstructorRole() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=join&session_uuid=xyz-789&role=instructor")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction, .joinSession(uuid: "xyz-789", role: .instructor))
    }

    // MARK: — AB-03: join with missing role defaults to player

    func test_AB_03_join_missingRole_defaultsToPlayer() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=join&session_uuid=def-456")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction, .joinSession(uuid: "def-456", role: .player))
    }

    // MARK: — AB-04: join with missing session_uuid is rejected

    func test_AB_04_join_missingSessionUuid_rejected() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=join&role=player")!)
        XCTAssertFalse(handled)
    }

    // MARK: — AB-05: mark-ready

    func test_AB_05_markDevicesReady() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=mark-ready")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction, .markDevicesReady)
    }

    // MARK: — AB-06: begin-cycle

    func test_AB_06_beginCycle() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=begin-cycle")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction, .beginCycle)
    }

    // MARK: — AB-07: end-cycle

    func test_AB_07_endCycle() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=end-cycle")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction, .endCycle)
    }

    // MARK: — AB-08: unknown action is rejected

    func test_AB_08_unknownAction_rejected() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=does-not-exist")!)
        XCTAssertFalse(handled)
    }

    // MARK: — AB-09: missing action query item is rejected

    func test_AB_09_missingAction_rejected() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate")!)
        XCTAssertFalse(handled)
    }

    // MARK: — AB-10: wrong scheme is rejected

    func test_AB_10_wrongScheme_rejected() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "https://example.com/automate?action=begin-cycle")!)
        XCTAssertFalse(handled)
    }

    // MARK: — AB-11: wrong host is rejected

    func test_AB_11_wrongHost_rejected() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://not-automate?action=begin-cycle")!)
        XCTAssertFalse(handled)
    }
}

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
        XCTAssertEqual(bridge.lastAction?.action, .joinSession(uuid: "abc-123", role: .player))
        XCTAssertTrue(bridge.presentSessionLab)
    }

    // MARK: — AB-02: join with instructor role

    func test_AB_02_join_withInstructorRole() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=join&session_uuid=xyz-789&role=instructor")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .joinSession(uuid: "xyz-789", role: .instructor))
    }

    // MARK: — AB-03: join with missing role defaults to player

    func test_AB_03_join_missingRole_defaultsToPlayer() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=join&session_uuid=def-456")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .joinSession(uuid: "def-456", role: .player))
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
        XCTAssertEqual(bridge.lastAction?.action, .markDevicesReady)
    }

    // MARK: — AB-06: begin-cycle

    func test_AB_06_beginCycle() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=begin-cycle")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .beginCycle)
    }

    // MARK: — AB-07: end-cycle

    func test_AB_07_endCycle() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=end-cycle")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .endCycle)
    }

    // MARK: — AB-07b: dump-snapshot

    func test_AB_07b_dumpSnapshot() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=dump-snapshot")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .dumpSnapshot)
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

    // MARK: — AB-12: reset-session resets ViewModel to .idle between scenarios

    func test_AB_12_resetSession_setsLastAction() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=reset-session")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .resetSession)
    }

    // MARK: — AB-12b: gopro-connect

    func test_AB_12b_goProConnect_withDeviceId() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-connect&gopro_device_id=99")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .goProConnect(goProDeviceId: 99))
    }

    func test_AB_12c_goProConnect_withoutDeviceId() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-connect")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .goProConnect(goProDeviceId: nil))
    }

    // MARK: — AB-13: gopro-start

    func test_AB_13_goProStart() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-start&gopro_device_id=42")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .goProStartRecording(goProDeviceId: 42))
    }

    func test_AB_13b_goProStart_missingDeviceId_rejected() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-start")!)
        XCTAssertFalse(handled)
    }

    // MARK: — AB-14: gopro-stop

    func test_AB_14_goProStop() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-stop&gopro_device_id=42")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .goProStopRecording(goProDeviceId: 42))
    }

    func test_AB_14b_goProStop_missingDeviceId_rejected() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-stop")!)
        XCTAssertFalse(handled)
    }

    // MARK: — AB-15: gopro-status

    func test_AB_15_goProStatus() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-status")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .goProStatus)
    }

    // MARK: — AB-16: gopro-media-list

    func test_AB_16_goProMediaList() {
        let bridge = makeBridge()
        let handled = bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-media-list")!)
        XCTAssertTrue(handled)
        XCTAssertEqual(bridge.lastAction?.action, .goProMediaList)
    }

    // MARK: — AB-17: consume() replay protection (P0 hardening, 2026-07-04 review)
    //
    // @Published lastAction replays its current value to every new subscriber.
    // consume() must return true exactly once per posted action so a view
    // rebuild / re-subscription cannot re-dispatch a stale gopro-start,
    // reset-session, or pose-overlay-diag.

    func test_AB_17_consume_returnsTrueExactlyOnce() {
        let bridge = makeBridge()
        bridge.handle(url: URL(string: "lfa-mc1://automate?action=gopro-start&gopro_device_id=7")!)
        guard let envelope = bridge.lastAction else { return XCTFail("no action posted") }
        XCTAssertTrue(bridge.consume(envelope), "first delivery must be dispatchable")
        XCTAssertFalse(bridge.consume(envelope), "replayed envelope must NOT be dispatchable")
        XCTAssertFalse(bridge.consume(envelope), "every further replay must stay consumed")
    }

    func test_AB_17b_identicalActionsGetDistinctSeqs_eachConsumableOnce() {
        let bridge = makeBridge()
        bridge.handle(url: URL(string: "lfa-mc1://automate?action=begin-cycle")!)
        guard let first = bridge.lastAction else { return XCTFail("no first action") }
        bridge.handle(url: URL(string: "lfa-mc1://automate?action=begin-cycle")!)
        guard let second = bridge.lastAction else { return XCTFail("no second action") }

        XCTAssertEqual(first.action, second.action)
        XCTAssertNotEqual(first.seq, second.seq, "identical actions must be distinguishable by seq")
        XCTAssertGreaterThan(second.seq, first.seq, "seq must be monotonically increasing")
        XCTAssertTrue(bridge.consume(first))
        XCTAssertTrue(bridge.consume(second), "a genuinely new identical action must still run")
        XCTAssertFalse(bridge.consume(first))
        XCTAssertFalse(bridge.consume(second))
    }

    func test_AB_17c_resetSessionReplay_notDispatchableTwice() {
        let bridge = makeBridge()
        bridge.handle(url: URL(string: "lfa-mc1://automate?action=reset-session")!)
        guard let envelope = bridge.lastAction else { return XCTFail("no action posted") }
        XCTAssertTrue(bridge.consume(envelope))
        // Simulates the fullScreenCover re-present: a new .onReceive subscription
        // receives the same envelope via @Published replay.
        XCTAssertFalse(bridge.consume(envelope), "re-presented view must not re-run reset-session")
    }
}

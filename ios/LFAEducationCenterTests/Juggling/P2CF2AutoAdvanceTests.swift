import XCTest
@testable import LFAEducationCenter

// MARK: — P2C-FLOW-2 auto-advance tests
//
// The auto-advance behavior introduced in P2C-FLOW-2 is entirely view-layer
// (@State hasAutoAdvanced, onAppear, handleOpenEvent). These tests verify the
// ViewModel contract that the view relies on:
//
//   vm.nextUnlabeledId — the single source of truth for which event to route to.
//
// Mapping of user-requested scenarios to tests:
//
//   "első megnyitás → legkorábbi befejezetlen"   → P2CF2_01, P2CF2_03
//   "overview-ba visszalépés → nincs re-route"   → hasAutoAdvanced flag (view-level,
//                                                   verified by code review; the flag
//                                                   is @State so it persists across
//                                                   selectedEventId nil-cycles but resets
//                                                   on new sheet instance)
//   "sheet bezárás/újranyitás → újra auto-route" → @State freshness guarantee (SwiftUI
//                                                   creates a new LabelingOverviewView
//                                                   on each sheet open → hasAutoAdvanced = false)
//   "nincs pending event → overview marad"        → P2CF2_02
//   "két pending event, első után a második jön"  → P2CF2_05
//   ".localOnly nem lehet célpont"                → P2CF2_04

@MainActor
final class P2CF2AutoAdvanceTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("p2cf2_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    private func makeViewModel() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-p2cf2",
            apiClient: MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // MARK: — P2CF2_01
    // Auto-advance target: nextUnlabeledId returns the first .labelPending event
    // after enterLabelingMode() — this is what autoAdvanceIfNeeded() routes to.

    func test_P2CF2_01_nextUnlabeledId_returns_first_labelPending_after_enterLabeling() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 5_000)!
        let d2 = vm.markTimestamp(ms: 10_000)!
        vm.enterLabelingMode()

        let nextId = vm.nextUnlabeledId

        XCTAssertNotNil(nextId, "nextUnlabeledId must be non-nil when .labelPending events exist")
        XCTAssertEqual(nextId, d1.deviceEventId,
                       "auto-advance target must be d1 (earlier timestamp)")
        XCTAssertNotEqual(nextId, d2.deviceEventId,
                          "d2 (later timestamp) must not be the first auto-advance target")
    }

    // MARK: — P2CF2_02
    // No pending events → no auto-advance target → overview stays visible.
    // nextUnlabeledId returns nil when all events are .localOnly.

    func test_P2CF2_02_nextUnlabeledId_nil_when_no_pending_events() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 2_000)!
        vm.enterLabelingMode()

        // Label both → .localOnly
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head", side: "center", annotationConfidence: "certain")
        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "chest", side: "center", annotationConfidence: "certain")

        XCTAssertNil(vm.nextUnlabeledId,
                     "nextUnlabeledId must be nil when all events are .localOnly")
    }

    // MARK: — P2CF2_03
    // Auto-advance picks the earliest timestamp, not arbitrary array order.
    // Events inserted in reverse order: the one with the smallest timestampMs must win.

    func test_P2CF2_03_nextUnlabeledId_picks_earliest_timestamp() async {
        let vm = makeViewModel()
        await vm.onAppear()

        // Inserted latest first
        let d_late   = vm.markTimestamp(ms: 9_000)!
        let d_early  = vm.markTimestamp(ms: 1_000)!
        let d_middle = vm.markTimestamp(ms: 5_000)!
        vm.enterLabelingMode()

        XCTAssertEqual(vm.nextUnlabeledId, d_early.deviceEventId,
                       "auto-advance must target the event with the smallest timestampMs")
        XCTAssertNotEqual(vm.nextUnlabeledId, d_late.deviceEventId)
        XCTAssertNotEqual(vm.nextUnlabeledId, d_middle.deviceEventId)
    }

    // MARK: — P2CF2_04
    // .localOnly events must not be auto-advance targets.
    // Only .unlabeled and .labelPending events qualify.

    func test_P2CF2_04_localOnly_events_are_not_auto_advance_targets() async {
        let vm = makeViewModel()
        await vm.onAppear()

        // d1 at an earlier timestamp but will be labeled → .localOnly
        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 5_000)!   // stays .labelPending
        vm.enterLabelingMode()

        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head", side: "center", annotationConfidence: "certain")

        // d1 is now .localOnly (earlier timestamp) — must NOT be returned
        let nextId = vm.nextUnlabeledId
        XCTAssertEqual(nextId, d2.deviceEventId,
                       "auto-advance must skip .localOnly d1 and target .labelPending d2")
    }

    // MARK: — P2CF2_05
    // After the first pending event is labeled, nextUnlabeledId returns the second.
    // This models the auto-advance progression: first → label → second becomes the target.

    func test_P2CF2_05_nextUnlabeledId_advances_to_second_after_first_labeled() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 3_000)!
        let d2 = vm.markTimestamp(ms: 7_000)!
        vm.enterLabelingMode()

        // Simulate "auto-advance: open d1, label it, advance"
        XCTAssertEqual(vm.nextUnlabeledId, d1.deviceEventId, "initial target is d1")

        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "left_knee", side: "left",
                        annotationConfidence: "certain")

        // d1 is now .localOnly — next target must be d2
        XCTAssertEqual(vm.nextUnlabeledId, d2.deviceEventId,
                       "after labeling d1, nextUnlabeledId must return d2")
    }

    // MARK: — P2CF2_06
    // nextUnlabeledId also covers .unlabeled events (edge case: enterLabelingMode
    // not yet called, e.g. .resumeLabeling CTA state with stuck .labelPending +
    // new .unlabeled events). Both .unlabeled and .labelPending qualify as targets.

    func test_P2CF2_06_nextUnlabeledId_includes_unlabeled_before_enterLabeling() async {
        let vm = makeViewModel()
        await vm.onAppear()

        // markTimestamp creates .unlabeled events; enterLabelingMode not called yet
        let d1 = vm.markTimestamp(ms: 2_000)!
        let d2 = vm.markTimestamp(ms: 8_000)!

        // Without enterLabelingMode, events are .unlabeled
        let nextId = vm.nextUnlabeledId
        XCTAssertNotNil(nextId, "nextUnlabeledId must include .unlabeled events")
        XCTAssertEqual(nextId, d1.deviceEventId,
                       "earliest .unlabeled event (d1) must be the auto-advance target")
    }
}

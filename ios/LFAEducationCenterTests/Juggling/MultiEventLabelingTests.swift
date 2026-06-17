import XCTest
@testable import LFAEducationCenter

// MARK: — Multi-event labeling regression tests (AN-3B2A P0 audit)
//
// These tests verify the save path when labeling more than one event in the
// same session. Specifically they guard against:
//
//   • Wrong-event save: relabelEvent() writes to the correct deviceEventId even
//     when multiple events are present and the user is not on the "first" one.
//   • Event-ID swap: after labeling events in any order, each UUID retains its
//     own contact type and side — no cross-contamination.
//   • Independent state: labeling event A has zero effect on event B's fields.
//   • Re-label correctness: relabeling an already-.localOnly event updates the
//     correct record and leaves the sibling unchanged.
//   • Disk persistence: a fresh store reload returns all events with their
//     respective labels intact.
//
// These are ViewModel-level tests — they test the save/load chain
// (relabelEvent → labelEvent → persistSession → LocalAnnotationStore) directly,
// independent of SwiftUI view state or .onChange timing.

@MainActor
final class MultiEventLabelingTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("multi_event_labeling_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    private func makeViewModel() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-multi",
            apiClient: MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // MARK: — MULTI_01
    // Labeling event 2 (higher timestamp) writes only to event 2, not event 1.
    // Verifies: save path uses deviceEventId, not list position.

    func test_MULTI_01_labelSecondEventOnlyAffectsSecondEvent() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 5_000)!   // event 1, earlier
        let d2 = vm.markTimestamp(ms: 10_000)!  // event 2, later
        vm.enterLabelingMode()

        let ok = vm.relabelEvent(
            deviceEventId:        d2.deviceEventId,
            contactType:          "left_knee",
            side:                 "left",
            annotationConfidence: "certain"
        )

        XCTAssertTrue(ok, "relabelEvent for event 2 should succeed")

        let events = vm.activeEvents
        let e1 = events.first { $0.deviceEventId == d1.deviceEventId }!
        let e2 = events.first { $0.deviceEventId == d2.deviceEventId }!

        // Event 2: labeled correctly
        XCTAssertEqual(e2.contactType,   "left_knee", "event 2 contactType")
        XCTAssertEqual(e2.side,          "left",      "event 2 side")
        XCTAssertEqual(e2.syncStatus,    .localOnly,  "event 2 syncStatus")

        // Event 1: untouched
        XCTAssertNil(e1.contactType,     "event 1 contactType must stay nil")
        XCTAssertNil(e1.side,            "event 1 side must stay nil")
        XCTAssertEqual(e1.syncStatus,    .labelPending, "event 1 syncStatus unchanged")
    }

    // MARK: — MULTI_02
    // Label event 1 then event 2 sequentially — both end up with independent labels.

    func test_MULTI_02_labelBothEventsSequentiallyPreservesIndependentLabels() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 5_000)!
        let d2 = vm.markTimestamp(ms: 10_000)!
        vm.enterLabelingMode()

        let ok1 = vm.relabelEvent(
            deviceEventId: d1.deviceEventId,
            contactType:   "right_instep",
            side:          "right",
            annotationConfidence: "certain"
        )
        let ok2 = vm.relabelEvent(
            deviceEventId: d2.deviceEventId,
            contactType:   "left_knee",
            side:          "left",
            annotationConfidence: "probable"
        )

        XCTAssertTrue(ok1)
        XCTAssertTrue(ok2)

        let events = vm.activeEvents
        let e1 = events.first { $0.deviceEventId == d1.deviceEventId }!
        let e2 = events.first { $0.deviceEventId == d2.deviceEventId }!

        XCTAssertEqual(e1.contactType,   "right_instep")
        XCTAssertEqual(e1.side,          "right")
        XCTAssertEqual(e1.syncStatus,    .localOnly)

        XCTAssertEqual(e2.contactType,   "left_knee")
        XCTAssertEqual(e2.side,          "left")
        XCTAssertEqual(e2.syncStatus,    .localOnly)
    }

    // MARK: — MULTI_03
    // Label event 2 FIRST, then event 1 — reverse order must not corrupt either.

    func test_MULTI_03_labelInReverseOrderDoesNotCorruptLabels() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 5_000)!
        let d2 = vm.markTimestamp(ms: 10_000)!
        vm.enterLabelingMode()

        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "chest", side: "center",
                        annotationConfidence: "certain")
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head",  side: "center",
                        annotationConfidence: "uncertain")

        let e1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        let e2 = vm.activeEvents.first { $0.deviceEventId == d2.deviceEventId }!

        XCTAssertEqual(e1.contactType, "head",  "event 1 must have head, not chest")
        XCTAssertEqual(e2.contactType, "chest", "event 2 must have chest, not head")
    }

    // MARK: — MULTI_04
    // DeviceEventId values must never be swapped in activeEvents after labeling.

    func test_MULTI_04_deviceEventIdsNeverSwapAfterLabeling() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 2_000)!
        vm.enterLabelingMode()

        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "right_knee", side: "right",
                        annotationConfidence: "certain")
        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "left_hip",  side: "left",
                        annotationConfidence: "certain")

        let events = vm.activeEvents.sorted { $0.timestampMs < $1.timestampMs }
        XCTAssertEqual(events.count, 2)

        // Position 0 (t=1000) is d1: right_knee
        XCTAssertEqual(events[0].deviceEventId, d1.deviceEventId)
        XCTAssertEqual(events[0].contactType, "right_knee")

        // Position 1 (t=2000) is d2: left_hip
        XCTAssertEqual(events[1].deviceEventId, d2.deviceEventId)
        XCTAssertEqual(events[1].contactType, "left_hip")
    }

    // MARK: — MULTI_05
    // Re-labeling an already-.localOnly event updates it; sibling stays unchanged.

    func test_MULTI_05_relabelAlreadyLocalOnlyEventUpdatesOnlyThatEvent() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 2_000)!
        vm.enterLabelingMode()

        // First pass
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "left_instep", side: "left",
                        annotationConfidence: "certain")
        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "right_instep", side: "right",
                        annotationConfidence: "certain")

        // Re-label event 1 with a new type (d1 is now .localOnly)
        let ok = vm.relabelEvent(
            deviceEventId: d1.deviceEventId,
            contactType:   "left_knee",
            side:          "left",
            annotationConfidence: "probable"
        )

        XCTAssertTrue(ok, "re-labeling a .localOnly event must succeed")

        let e1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        let e2 = vm.activeEvents.first { $0.deviceEventId == d2.deviceEventId }!

        XCTAssertEqual(e1.contactType, "left_knee",    "event 1 updated to left_knee")
        XCTAssertEqual(e1.annotationConfidence, "probable", "event 1 confidence updated")
        XCTAssertEqual(e2.contactType, "right_instep", "event 2 must not change")
    }

    // MARK: — MULTI_06
    // After labeling 2 events and reloading from disk, both labels survive.
    // Verifies persistence chain: relabelEvent → disk → fresh VM load.

    func test_MULTI_06_labelsPersistToDiskForBothEvents() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 3_000)!
        let d2 = vm.markTimestamp(ms: 7_000)!
        vm.enterLabelingMode()

        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head", side: "center",
                        annotationConfidence: "certain")
        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "left_shoulder", side: "left",
                        annotationConfidence: "probable")

        // Fresh VM reading the same store
        let vm2 = makeViewModel()
        await vm2.onAppear()

        let events2 = vm2.activeEvents.sorted { $0.timestampMs < $1.timestampMs }
        XCTAssertEqual(events2.count, 2, "both events survive reload")

        let r1 = events2.first { $0.deviceEventId == d1.deviceEventId }
        let r2 = events2.first { $0.deviceEventId == d2.deviceEventId }

        XCTAssertNotNil(r1, "event 1 found after reload")
        XCTAssertNotNil(r2, "event 2 found after reload")
        XCTAssertEqual(r1?.contactType, "head")
        XCTAssertEqual(r2?.contactType, "left_shoulder")
        XCTAssertEqual(r1?.syncStatus,  .localOnly)
        XCTAssertEqual(r2?.syncStatus,  .localOnly)
    }

    // MARK: — MULTI_07
    // relabelEvent for a UUID not in activeEvents returns false and changes nothing.

    func test_MULTI_07_relabelEventUnknownUUIDReturnsFalse() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        vm.enterLabelingMode()

        let bogus = UUID()
        let ok = vm.relabelEvent(
            deviceEventId: bogus,
            contactType:   "head",
            side:          "center",
            annotationConfidence: "certain"
        )

        XCTAssertFalse(ok, "unknown UUID must return false")

        // Event 1 must be unchanged
        let e1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        XCTAssertNil(e1.contactType,   "no accidental write to event 1")
        XCTAssertEqual(e1.syncStatus,  .labelPending, "status unchanged")
    }

    // MARK: — MULTI_08
    // Label event 2 when event 1 has a different first-timestamp (sorted order check).
    // This specifically guards the "second event by timestamp" scenario from the P0 report.

    func test_MULTI_08_secondEventByTimestampReceivesCorrectLabel() async {
        let vm = makeViewModel()
        await vm.onAppear()

        // Deliberately insert d2 FIRST so its deviceEventId is "earlier" in
        // session.drafts array order, but d1 has the smaller timestamp.
        let d1 = vm.markTimestamp(ms: 8_000)!   // will be "second" by timestamp
        let d2 = vm.markTimestamp(ms: 3_000)!   // will be "first" by timestamp

        vm.enterLabelingMode()

        // Label the SECOND by timestamp (d1, t=8000), which is NOT first in draft array
        let ok = vm.relabelEvent(
            deviceEventId:        d1.deviceEventId,
            contactType:          "left_knee",
            side:                 "left",
            annotationConfidence: "certain"
        )

        XCTAssertTrue(ok)

        let events = vm.activeEvents.sorted { $0.timestampMs < $1.timestampMs }
        // index 0: d2 (t=3000), index 1: d1 (t=8000)
        XCTAssertEqual(events[0].deviceEventId, d2.deviceEventId)
        XCTAssertNil(events[0].contactType, "first-by-timestamp event must be untouched")

        XCTAssertEqual(events[1].deviceEventId, d1.deviceEventId)
        XCTAssertEqual(events[1].contactType, "left_knee", "second-by-timestamp event must be labeled")
    }
}

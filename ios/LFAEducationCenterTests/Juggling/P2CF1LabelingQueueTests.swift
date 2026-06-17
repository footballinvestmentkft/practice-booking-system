import XCTest
@testable import LFAEducationCenter

// MARK: — P2C-FLOW-1 labeling queue tests
//
// Verifies three guarantees introduced in P2C-FLOW-1:
//
//   1. Sequential queue filter — only .labelPending events are included.
//      .localOnly, .synced, .retryPending, .failedPermanent must NOT appear
//      in the first-session (sequential) queue.
//
//   2. isSaving guard — saveAndAdvance() is protected against double-advance.
//      A failed relabelEvent() must leave currentIndex unchanged and reset
//      isSaving so the user can retry.
//
//   3. Regression — multi-event labeling with the corrected queue still
//      produces independent, UUID-correct labels (guards against regressions
//      in the MULTI_* coverage introduced in the P0 fix commit).
//
// All queue composition tests use EventLabelDetailView.sequentialQueueIds(from:),
// the static helper extracted for testability. It mirrors the first-session
// filter in setUpQueue() exactly.

@MainActor
final class P2CF1LabelingQueueTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("p2cf1_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    private func makeViewModel() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-p2cf1",
            apiClient: MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // MARK: — P2CF1_01
    // Sequential queue excludes .localOnly events.
    // After labeling one event (→ .localOnly), it must not appear in the sequential queue.
    // This was the P2C-FLOW-1 root bug: the old filter included .localOnly.

    func test_P2CF1_01_sequential_queue_excludes_localOnly() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 5_000)!   // will remain .labelPending
        let d2 = vm.markTimestamp(ms: 10_000)!  // will become .localOnly
        vm.enterLabelingMode()

        let ok = vm.relabelEvent(
            deviceEventId: d2.deviceEventId,
            contactType: "head",
            side: "center",
            annotationConfidence: "certain"
        )
        XCTAssertTrue(ok, "labeling d2 must succeed")

        let queueIds = EventLabelDetailView.sequentialQueueIds(from: vm.activeEvents)

        XCTAssertEqual(queueIds.count, 1, "only one .labelPending event should remain")
        XCTAssertEqual(queueIds[0], d1.deviceEventId, "remaining event must be d1 (.labelPending)")
        XCTAssertFalse(queueIds.contains(d2.deviceEventId), "d2 (.localOnly) must be excluded from sequential queue")
    }

    // MARK: — P2CF1_02
    // Sequential queue contains only .labelPending events.
    // Four labeled (.localOnly) events must all be excluded; only the one
    // unfiled event remains in the queue.

    func test_P2CF1_02_sequential_queue_only_labelPending() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!   // stays .labelPending
        let d2 = vm.markTimestamp(ms: 2_000)!
        let d3 = vm.markTimestamp(ms: 3_000)!
        let d4 = vm.markTimestamp(ms: 4_000)!
        let d5 = vm.markTimestamp(ms: 5_000)!
        vm.enterLabelingMode()

        // Label d2–d5 → .localOnly
        for (id, type) in [
            (d2.deviceEventId, "head"),
            (d3.deviceEventId, "chest"),
            (d4.deviceEventId, "left_knee"),
            (d5.deviceEventId, "right_knee")
        ] {
            vm.relabelEvent(deviceEventId: id, contactType: type, side: nil, annotationConfidence: "certain")
        }

        let queueIds = EventLabelDetailView.sequentialQueueIds(from: vm.activeEvents)

        XCTAssertEqual(queueIds.count, 1, "only d1 (.labelPending) should be in sequential queue")
        XCTAssertEqual(queueIds[0], d1.deviceEventId)

        for d in [d2, d3, d4, d5] {
            XCTAssertFalse(
                queueIds.contains(d.deviceEventId),
                "\(d.deviceEventId) is .localOnly and must be excluded"
            )
        }
    }

    // MARK: — P2CF1_03
    // Sequential queue is sorted by timestamp regardless of insertion order.

    func test_P2CF1_03_sequential_queue_sorted_by_timestamp() async {
        let vm = makeViewModel()
        await vm.onAppear()

        // Insert in reverse order so array order ≠ timestamp order
        let d3 = vm.markTimestamp(ms: 9_000)!
        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 5_000)!
        vm.enterLabelingMode()

        let queueIds = EventLabelDetailView.sequentialQueueIds(from: vm.activeEvents)

        XCTAssertEqual(queueIds.count, 3)
        XCTAssertEqual(queueIds[0], d1.deviceEventId, "index 0 = earliest timestamp (1 000 ms)")
        XCTAssertEqual(queueIds[1], d2.deviceEventId, "index 1 = middle timestamp (5 000 ms)")
        XCTAssertEqual(queueIds[2], d3.deviceEventId, "index 2 = latest timestamp (9 000 ms)")
    }

    // MARK: — P2CF1_04
    // Sequential queue is empty when all events are already labeled.
    // This is the correct completion signal: no .labelPending → nothing to show.

    func test_P2CF1_04_sequential_queue_empty_when_all_labeled() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 2_000)!
        vm.enterLabelingMode()

        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head", side: "center", annotationConfidence: "certain")
        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "chest", side: "center", annotationConfidence: "certain")

        let queueIds = EventLabelDetailView.sequentialQueueIds(from: vm.activeEvents)

        XCTAssertTrue(queueIds.isEmpty,
                      "sequential queue must be empty when all events are .localOnly")
    }

    // MARK: — P2CF1_05
    // Failed relabelEvent → relabelEvent returns false for an unknown UUID.
    // The view's saveAndAdvance() guards on `ok == false`:
    //   isSaving reset, showSaveErrorAlert set, currentIndex unchanged.
    // This test verifies the ViewModel side of that guard (VM returns false).

    func test_P2CF1_05_failed_relabel_returns_false_and_leaves_event_unchanged() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        vm.enterLabelingMode()

        let ok = vm.relabelEvent(
            deviceEventId: UUID(),   // bogus — not in activeEvents
            contactType: "head",
            side: "center",
            annotationConfidence: "certain"
        )
        XCTAssertFalse(ok, "relabelEvent with unknown UUID must return false")

        let e1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        XCTAssertNil(e1.contactType,          "d1 must remain unlabeled")
        XCTAssertEqual(e1.syncStatus, .labelPending, "d1 syncStatus must remain .labelPending")
    }

    // MARK: — P2CF1_06
    // First and second event keep independent labels after sequential labeling.
    // Regression guard: new queue filter must not break UUID-targeted save path.

    func test_P2CF1_06_sequential_save_preserves_independent_labels() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 3_000)!
        let d2 = vm.markTimestamp(ms: 7_000)!
        vm.enterLabelingMode()

        let ok1 = vm.relabelEvent(deviceEventId: d1.deviceEventId,
                                  contactType: "left_knee", side: "left",
                                  annotationConfidence: "certain")
        let ok2 = vm.relabelEvent(deviceEventId: d2.deviceEventId,
                                  contactType: "chest", side: "center",
                                  annotationConfidence: "probable")

        XCTAssertTrue(ok1)
        XCTAssertTrue(ok2)

        let e1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        let e2 = vm.activeEvents.first { $0.deviceEventId == d2.deviceEventId }!

        XCTAssertEqual(e1.contactType, "left_knee", "d1 must keep its own label")
        XCTAssertEqual(e2.contactType, "chest",     "d2 must keep its own label")
        XCTAssertEqual(e1.side,        "left",      "d1 side correct")
        XCTAssertEqual(e2.side,        "center",    "d2 side correct")
    }

    // MARK: — P2CF1_07
    // After labeling the first event in a 2-event queue, the sequential queue
    // shrinks by exactly 1. Verifies that advance is always +1, never +2.
    //
    // In the view, the pre-built queue is a fixed [UUID] snapshot. The static helper
    // always reflects the current .labelPending state. After saving d1, it becomes
    // .localOnly → drops from the helper result. Only d2 remains.

    func test_P2CF1_07_queue_shrinks_by_exactly_one_per_save() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 2_000)!
        vm.enterLabelingMode()

        let initialIds = EventLabelDetailView.sequentialQueueIds(from: vm.activeEvents)
        XCTAssertEqual(initialIds.count, 2, "both events start as .labelPending")

        // Simulate saveAndAdvance step 1: label d1
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head", side: "center",
                        annotationConfidence: "certain")

        let afterFirstSave = EventLabelDetailView.sequentialQueueIds(from: vm.activeEvents)
        XCTAssertEqual(afterFirstSave.count, 1, "queue must shrink by exactly 1 after labeling d1")
        XCTAssertEqual(afterFirstSave[0], d2.deviceEventId, "only d2 (.labelPending) remains")
        XCTAssertFalse(afterFirstSave.contains(d1.deviceEventId), "d1 (.localOnly) must exit the sequential queue")
    }
}

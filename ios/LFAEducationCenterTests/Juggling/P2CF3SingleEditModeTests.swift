import XCTest
@testable import LFAEducationCenter

// MARK: — P2C-FLOW-3 single-edit mode tests
//
// Tests the LabelingDetailMode routing introduced in P2C-FLOW-3:
//
//   .sequential — opened for a .labelPending event (or no startingEventId):
//                 queue = all .labelPending, auto-advance after each save.
//   .singleEdit — opened for .localOnly/.synced/.retryPending/.failedPermanent:
//                 queue = [startId] only; save → navigateBack(); no completionView.
//
// Test approach:
//   • Mode detection: EventLabelDetailView.detectMode(for:syncStatus:) is a static
//     helper (internal) — tests call it directly with explicit syncStatus values.
//   • Queue composition: EventLabelDetailView.sequentialQueueIds(from:) static helper.
//   • Navigation behavior (navigateBack, currentIndex): ViewModel-level assertions
//     verify the save-path correctness underlying the view's navigation decisions.
//   • "singleEdit mentés után onBack hívódik" is a view-layer guarantee;
//     the ViewModel tests here verify that relabelEvent() for .localOnly succeeds
//     and the resulting state is correct so the view CAN safely call navigateBack().

@MainActor
final class P2CF3SingleEditModeTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("p2cf3_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    private func makeViewModel() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-p2cf3",
            apiClient: MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // MARK: — Mode detection tests (static helper, no SwiftUI required)

    // P2CF3_01: nil startingEventId → .sequential (first-session flow)
    func test_P2CF3_01_nil_startingEventId_yields_sequential() {
        let mode = EventLabelDetailView.detectMode(for: nil, syncStatus: .labelPending)
        XCTAssertEqual(mode, .sequential,
                       "nil startingEventId must always produce .sequential mode")
    }

    // P2CF3_02: .labelPending target → .sequential (pending event in auto-flow)
    func test_P2CF3_02_labelPending_target_yields_sequential() {
        let id = UUID()
        let mode = EventLabelDetailView.detectMode(for: id, syncStatus: .labelPending)
        XCTAssertEqual(mode, .sequential,
                       ".labelPending event must open in .sequential mode")
    }

    // P2CF3_03: .localOnly target → .singleEdit
    func test_P2CF3_03_localOnly_target_yields_singleEdit() {
        let id = UUID()
        let mode = EventLabelDetailView.detectMode(for: id, syncStatus: .localOnly)
        XCTAssertEqual(mode, .singleEdit,
                       ".localOnly event must open in .singleEdit mode")
    }

    // P2CF3_04: .synced target → .singleEdit
    func test_P2CF3_04_synced_target_yields_singleEdit() {
        let id = UUID()
        let mode = EventLabelDetailView.detectMode(for: id, syncStatus: .synced)
        XCTAssertEqual(mode, .singleEdit,
                       ".synced event must open in .singleEdit mode")
    }

    // P2CF3_05: .retryPending target → .singleEdit
    func test_P2CF3_05_retryPending_target_yields_singleEdit() {
        let id = UUID()
        let mode = EventLabelDetailView.detectMode(for: id, syncStatus: .retryPending)
        XCTAssertEqual(mode, .singleEdit,
                       ".retryPending event must open in .singleEdit mode")
    }

    // P2CF3_06: .failedPermanent target → .singleEdit
    func test_P2CF3_06_failedPermanent_target_yields_singleEdit() {
        let id = UUID()
        let mode = EventLabelDetailView.detectMode(for: id, syncStatus: .failedPermanent)
        XCTAssertEqual(mode, .singleEdit,
                       ".failedPermanent event must open in .singleEdit mode")
    }

    // P2CF3_07: blocked states → .sequential (safety: targetEventMissing fires in setUpQueue)
    func test_P2CF3_07_blocked_states_yield_sequential_for_safety_view() {
        let id = UUID()
        for blocked: ContactEventSyncStatus in [.syncing, .updating, .deleting,
                                                .conflicted, .needsReconciliation, .deleted] {
            let mode = EventLabelDetailView.detectMode(for: id, syncStatus: blocked)
            XCTAssertEqual(mode, .sequential,
                           "\(blocked) must return .sequential (targetEventMissing safety will fire)")
        }
    }

    // P2CF3_08: nil syncStatus (event missing from activeEvents) → .sequential (safety)
    func test_P2CF3_08_nil_syncStatus_yields_sequential() {
        let id = UUID()
        let mode = EventLabelDetailView.detectMode(for: id, syncStatus: nil)
        XCTAssertEqual(mode, .sequential,
                       "nil syncStatus must return .sequential (targetEventMissing safety)")
    }

    // MARK: — Queue composition: singleEdit queue is exactly 1 element

    // P2CF3_09: singleEdit target (.localOnly) is NOT in sequentialQueueIds.
    // This guarantees that the queue in singleEdit mode is exactly [startId] — a
    // single-element queue — and not the full pending queue.
    func test_P2CF3_09_singleEdit_target_excluded_from_sequential_queue() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        let d2 = vm.markTimestamp(ms: 2_000)!  // will become .localOnly (singleEdit target)
        vm.enterLabelingMode()

        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "head", side: "center",
                        annotationConfidence: "certain")

        // d2 is .localOnly — detectMode returns .singleEdit, queue = [d2]
        // sequentialQueueIds must NOT include d2
        let sequentialIds = EventLabelDetailView.sequentialQueueIds(from: vm.activeEvents)

        XCTAssertFalse(sequentialIds.contains(d2.deviceEventId),
                       ".localOnly event (singleEdit target) must not appear in sequential queue")
        XCTAssertEqual(sequentialIds, [d1.deviceEventId],
                       "only the remaining .labelPending event (d1) is in sequential queue")
    }

    // MARK: — ViewModel behavioral tests for singleEdit save path

    // P2CF3_10: relabeling a .localOnly event succeeds.
    // This underpins the singleEdit "Mentés" button: relabelEvent must succeed for
    // .localOnly events so the view can safely call navigateBack() afterward.
    func test_P2CF3_10_relabeling_localOnly_event_succeeds() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 5_000)!
        vm.enterLabelingMode()

        // First label (→ .localOnly)
        let ok1 = vm.relabelEvent(deviceEventId: d1.deviceEventId,
                                  contactType: "head", side: "center",
                                  annotationConfidence: "certain")
        XCTAssertTrue(ok1)

        let after1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        XCTAssertEqual(after1.syncStatus, .localOnly)
        XCTAssertEqual(after1.contactType, "head")

        // Re-label the .localOnly event (singleEdit "Mentés" path)
        let ok2 = vm.relabelEvent(deviceEventId: d1.deviceEventId,
                                  contactType: "left_knee", side: "left",
                                  annotationConfidence: "probable")
        XCTAssertTrue(ok2, "relabeling a .localOnly event must succeed")

        let after2 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        XCTAssertEqual(after2.contactType, "left_knee",     "contactType updated")
        XCTAssertEqual(after2.side,        "left",          "side updated")
        XCTAssertEqual(after2.annotationConfidence, "probable", "confidence updated")
        XCTAssertEqual(after2.syncStatus,  .localOnly,      "status stays .localOnly")
    }

    // P2CF3_11: failed relabeling must not trigger navigateBack.
    // relabelEvent returns false for an unknown UUID; the view keeps isSaving = false
    // and stays on the current event — no navigateBack (verified: view guard ok else { return }).
    func test_P2CF3_11_failed_save_returns_false_no_side_effects() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 1_000)!
        vm.enterLabelingMode()
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head", side: "center", annotationConfidence: "certain")

        // Attempt to relabel with bogus UUID
        let ok = vm.relabelEvent(
            deviceEventId: UUID(),
            contactType: "chest", side: "center",
            annotationConfidence: "certain"
        )
        XCTAssertFalse(ok, "relabelEvent with bogus UUID must return false")

        // d1 must be unchanged
        let e1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        XCTAssertEqual(e1.contactType, "head", "d1 must not be overwritten by failed save")
    }

    // P2CF3_12: sequential save after a .labelPending event advances to the next pending event.
    // Verifies that the sequential path still works correctly alongside the new singleEdit path.
    func test_P2CF3_12_sequential_save_advances_to_next_pending() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 3_000)!
        let d2 = vm.markTimestamp(ms: 7_000)!
        vm.enterLabelingMode()

        // Label d1 (simulates sequential saveAndAdvance)
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "left_knee", side: "left",
                        annotationConfidence: "certain")

        // After labeling d1, next sequential target must be d2
        XCTAssertEqual(vm.nextUnlabeledId, d2.deviceEventId,
                       "sequential: next pending event after d1 must be d2")

        // Label d2
        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "chest", side: "center",
                        annotationConfidence: "probable")

        // No more pending events → completion state
        XCTAssertNil(vm.nextUnlabeledId,
                     "sequential: nextUnlabeledId nil after all events labeled")
    }

    // P2CF3_13: two events edited in singleEdit mode stay independent.
    // Opening d1 in singleEdit, updating its label, then opening d2 in singleEdit:
    // d1's updated label must not bleed into d2.
    func test_P2CF3_13_two_singleEdit_sessions_do_not_contaminate() async {
        let vm = makeViewModel()
        await vm.onAppear()

        let d1 = vm.markTimestamp(ms: 2_000)!
        let d2 = vm.markTimestamp(ms: 4_000)!
        vm.enterLabelingMode()

        // Label both (→ .localOnly), simulating singleEdit "Mentés" for each
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "head", side: "center",
                        annotationConfidence: "certain")
        vm.relabelEvent(deviceEventId: d2.deviceEventId,
                        contactType: "left_knee", side: "left",
                        annotationConfidence: "probable")

        // SingleEdit re-label d1 only (second singleEdit session for d1)
        vm.relabelEvent(deviceEventId: d1.deviceEventId,
                        contactType: "chest", side: "center",
                        annotationConfidence: "uncertain")

        let e1 = vm.activeEvents.first { $0.deviceEventId == d1.deviceEventId }!
        let e2 = vm.activeEvents.first { $0.deviceEventId == d2.deviceEventId }!

        XCTAssertEqual(e1.contactType, "chest",      "d1 re-labeled to chest")
        XCTAssertEqual(e2.contactType, "left_knee",  "d2 must remain left_knee")
        XCTAssertEqual(e1.side, "center")
        XCTAssertEqual(e2.side, "left")
    }
}

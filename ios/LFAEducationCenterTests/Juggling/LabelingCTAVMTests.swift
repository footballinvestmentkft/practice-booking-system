import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3B2A P2B-5A: LabelingCTAState, labeledCount, nextUnlabeledId,
//         showLabelingCTA, labelingCTAText, relabelEvent, enterLabelingMode idempotency

@MainActor
final class LabelingCTAVMTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("labeling_cta_vm_tests_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    // MARK: — Helpers

    private func makeViewModel() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId:        1,
            videoId:       "vid-1",
            apiClient:     MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore:    LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // Pre-populates the temp session file with arbitrary drafts so that
    // vm.onAppear() loads them instead of creating a fresh empty session.
    private func seedSession(drafts: [ContactEventDraft]) throws {
        try FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        let store = LocalAnnotationStore(baseDirectory: tempDir)
        var session = store.emptySession(userId: 1, videoId: "vid-1", taxonomyVersion: "v1")
        session.drafts = drafts
        try store.save(session: &session)
    }

    // Factory for a draft in a given sync state (non-unlabeled, with contactType).
    private func makeDraft(
        timestampMs: Int = 1000,
        contactType: String = "right_instep",
        side: String? = "right",
        syncStatus: ContactEventSyncStatus = .localOnly
    ) -> ContactEventDraft {
        var d = ContactEventDraft.new(
            timestampMs:          timestampMs,
            contactType:          contactType,
            side:                 side,
            annotationConfidence: "certain"
        )
        d.syncStatus    = syncStatus
        d.serverEventId = syncStatus == .localOnly ? nil : UUID()
        d.version       = 1
        return d
    }

    // Factory for a Phase 1 (timestamp-only) draft, optionally in .labelPending.
    private func makeTimestampDraft(
        timestampMs: Int = 1000,
        syncStatus: ContactEventSyncStatus = .unlabeled
    ) -> ContactEventDraft {
        var d = ContactEventDraft.timestamp(ms: timestampMs)
        d.syncStatus = syncStatus
        return d
    }

    // MARK: — VM_LABEL_01: labeledCount is 0 when all events are .unlabeled

    func test_VM_LABEL_01_labeledCount_isZero_whenAllUnlabeled() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        vm.markTimestamp(ms: 2000)
        XCTAssertEqual(vm.labeledCount, 0)
    }

    // MARK: — VM_LABEL_02: labeledCount counts .localOnly events

    func test_VM_LABEL_02_labeledCount_countsLocalOnly() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let d1 = vm.markTimestamp(ms: 1000)!
        vm.markTimestamp(ms: 2000)
        vm.enterLabelingMode()
        vm.labelEvent(deviceEventId: d1.deviceEventId,
                      contactType: "right_instep", side: "right", annotationConfidence: "certain")
        XCTAssertEqual(vm.labeledCount, 1)
    }

    // MARK: — VM_LABEL_03: labeledCount counts .synced events

    func test_VM_LABEL_03_labeledCount_countsSynced() async throws {
        let synced = makeDraft(syncStatus: .synced)
        try seedSession(drafts: [synced])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labeledCount, 1)
    }

    // MARK: — VM_LABEL_04: nextUnlabeledId returns first event (by timestamp) that needs labeling

    func test_VM_LABEL_04_nextUnlabeledId_returnsFirstByTimestamp() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 3000)
        let first = vm.markTimestamp(ms: 1000)!
        vm.markTimestamp(ms: 2000)

        XCTAssertEqual(vm.nextUnlabeledId, first.deviceEventId)
    }

    // MARK: — VM_LABEL_05: nextUnlabeledId returns nil when all events are labeled

    func test_VM_LABEL_05_nextUnlabeledId_isNil_whenAllLabeled() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let d1 = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()
        vm.labelEvent(deviceEventId: d1.deviceEventId,
                      contactType: "right_instep", side: "right", annotationConfidence: "certain")
        XCTAssertNil(vm.nextUnlabeledId)
    }

    // MARK: — VM_LABEL_06: showLabelingCTA is false when there are no active events

    func test_VM_LABEL_06_showLabelingCTA_isFalse_whenNoEvents() async {
        let vm = makeViewModel()
        await vm.onAppear()
        XCTAssertFalse(vm.showLabelingCTA)
    }

    // MARK: — VM_LABEL_07: showLabelingCTA is true when .unlabeled events exist

    func test_VM_LABEL_07_showLabelingCTA_isTrue_forUnlabeled() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        XCTAssertTrue(vm.showLabelingCTA)
    }

    // MARK: — VM_LABEL_08: showLabelingCTA is true when all events are .localOnly (review mode)

    func test_VM_LABEL_08_showLabelingCTA_isTrue_forAllLocalOnly() async throws {
        let local = makeDraft(syncStatus: .localOnly)
        try seedSession(drafts: [local])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertTrue(vm.showLabelingCTA)
        XCTAssertEqual(vm.labelingCTAState, .reviewLocal)
    }

    // MARK: — VM_LABEL_09: relabelEvent routes to labelEvent for .labelPending

    func test_VM_LABEL_09_relabelEvent_routesToLabelEvent_forLabelPending() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()

        let ok = vm.relabelEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "right_instep",
            side:                 "right",
            annotationConfidence: "certain"
        )

        XCTAssertTrue(ok)
        let event = vm.activeEvents.first!
        XCTAssertEqual(event.syncStatus, .localOnly)   // .labelPending → .localOnly
        XCTAssertEqual(event.contactType, "right_instep")
    }

    // MARK: — VM_LABEL_10: relabelEvent routes to labelEvent for .localOnly (re-label)

    func test_VM_LABEL_10_relabelEvent_routesToLabelEvent_forLocalOnly() async throws {
        let local = makeDraft(contactType: "right_instep", syncStatus: .localOnly)
        try seedSession(drafts: [local])

        let vm = makeViewModel()
        await vm.onAppear()

        let ok = vm.relabelEvent(
            deviceEventId:        local.deviceEventId,
            contactType:          "left_instep",
            side:                 "left",
            annotationConfidence: "probable"
        )

        XCTAssertTrue(ok)
        let event = vm.activeEvents.first!
        XCTAssertEqual(event.syncStatus, .localOnly)
        XCTAssertEqual(event.contactType, "left_instep")
        XCTAssertEqual(event.side, "left")
    }

    // MARK: — VM_LABEL_11: relabelEvent routes to editEvent for .synced → .updating

    func test_VM_LABEL_11_relabelEvent_routesToEditEvent_forSynced() async throws {
        let synced = makeDraft(syncStatus: .synced)
        try seedSession(drafts: [synced])

        let vm = makeViewModel()
        await vm.onAppear()

        let ok = vm.relabelEvent(
            deviceEventId:        synced.deviceEventId,
            contactType:          "left_instep",
            side:                 "left",
            annotationConfidence: "probable"
        )

        XCTAssertTrue(ok)
        let event = vm.activeEvents.first!
        XCTAssertEqual(event.syncStatus, .updating)    // editEvent: .synced → .updating
        XCTAssertEqual(event.contactType, "left_instep")
    }

    // MARK: — VM_LABEL_12: relabelEvent returns false for .syncing (in-flight, blocked)

    func test_VM_LABEL_12_relabelEvent_returnsFalse_forSyncing() async throws {
        let syncing = makeDraft(syncStatus: .syncing)
        try seedSession(drafts: [syncing])

        let vm = makeViewModel()
        await vm.onAppear()

        let ok = vm.relabelEvent(
            deviceEventId:        syncing.deviceEventId,
            contactType:          "left_instep",
            side:                 "left",
            annotationConfidence: "certain"
        )

        XCTAssertFalse(ok)
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .syncing)   // unchanged
    }

    // MARK: — VM_LABEL_13: enterLabelingMode is idempotent with stuck .labelPending events

    func test_VM_LABEL_13_enterLabelingMode_idempotent_withStuckLabelPending() async throws {
        // Simulate a crash mid-session: one event stuck in .labelPending
        let pending = makeTimestampDraft(syncStatus: .labelPending)
        try seedSession(drafts: [pending])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.unlabeledCount, 0)
        XCTAssertEqual(vm.labelPendingCount, 1)

        vm.enterLabelingMode()   // no .unlabeled → no transition, no persist

        XCTAssertEqual(vm.screenMode, .labeling)       // mode still set
        XCTAssertNil(vm.saveError)                      // no save error (no write attempted)
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .labelPending)  // unchanged
    }

    // MARK: — VM_LABEL_14: labelingCTAText is "Tovább a címkézéshez" when .unlabeled exists

    func test_VM_LABEL_14_labelingCTAText_isUnlabeled() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)

        XCTAssertEqual(vm.labelingCTAState, .unlabeled)
        XCTAssertEqual(vm.labelingCTAText, "Tovább a címkézéshez")
    }

    // MARK: — VM_LABEL_15: labelingCTAText is "Címkézés folytatása" for stuck .labelPending

    func test_VM_LABEL_15_labelingCTAText_isResumeLabeling_forStuckLabelPending() async throws {
        let pending = makeTimestampDraft(syncStatus: .labelPending)
        try seedSession(drafts: [pending])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labelingCTAState, .resumeLabeling)
        XCTAssertEqual(vm.labelingCTAText, "Címkézés folytatása")
    }

    // MARK: — VM_LABEL_16: labelingCTAText is "Címkék áttekintése" when all .localOnly

    func test_VM_LABEL_16_labelingCTAText_isReviewLocal_whenAllLocalOnly() async throws {
        let l1 = makeDraft(timestampMs: 1000, syncStatus: .localOnly)
        let l2 = makeDraft(timestampMs: 2000, contactType: "left_instep", side: "left", syncStatus: .localOnly)
        try seedSession(drafts: [l1, l2])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labelingCTAState, .reviewLocal)
        XCTAssertEqual(vm.labelingCTAText, "Címkék áttekintése")
    }

    // MARK: — VM_LABEL_17: labelingCTAText is "Megtekintés / szerkesztés" when all .synced

    func test_VM_LABEL_17_labelingCTAText_isViewOrEdit_whenAllSynced() async throws {
        let synced = makeDraft(syncStatus: .synced)
        try seedSession(drafts: [synced])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labelingCTAState, .viewOrEdit)
        XCTAssertEqual(vm.labelingCTAText, "Megtekintés / szerkesztés")
    }

    // MARK: — Extra: hasProblems for .conflicted event

    func test_VM_LABEL_18_labelingCTAState_hasProblems_forConflicted() async throws {
        var conflicted = makeDraft(syncStatus: .synced)
        conflicted.syncStatus = .conflicted
        try seedSession(drafts: [conflicted])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labelingCTAState, .hasProblems)
        XCTAssertEqual(vm.labelingCTAText, "Problémás események")
    }

    // MARK: — Extra: hasProblems for .failedPermanent event

    func test_VM_LABEL_19_labelingCTAState_hasProblems_forFailedPermanent() async throws {
        var failed = makeDraft(syncStatus: .synced)
        failed.syncStatus    = .failedPermanent
        failed.failureReason = "403"
        try seedSession(drafts: [failed])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labelingCTAState, .hasProblems)
    }

    // MARK: — Extra: .unlabeled takes priority over .labelPending

    func test_VM_LABEL_20_unlabeled_takesPriorityOver_labelPending() async throws {
        let pending  = makeTimestampDraft(timestampMs: 1000, syncStatus: .labelPending)
        let unlabeled = makeTimestampDraft(timestampMs: 2000, syncStatus: .unlabeled)
        try seedSession(drafts: [pending, unlabeled])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labelingCTAState, .unlabeled)
    }

    // MARK: — Extra: mixed .localOnly + .synced → viewOrEdit (not reviewLocal)

    func test_VM_LABEL_21_mixedLocalOnlyAndSynced_isViewOrEdit() async throws {
        let local  = makeDraft(timestampMs: 1000, syncStatus: .localOnly)
        let synced = makeDraft(timestampMs: 2000, syncStatus: .synced)
        try seedSession(drafts: [local, synced])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.labelingCTAState, .viewOrEdit)
    }

    // MARK: — Extra: relabelEvent returns false for .unlabeled (enterLabelingMode required first)

    func test_VM_LABEL_22_relabelEvent_returnsFalse_forUnlabeled() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!   // .unlabeled, enterLabelingMode NOT called

        let ok = vm.relabelEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "right_instep",
            side:                 "right",
            annotationConfidence: "certain"
        )

        XCTAssertFalse(ok)
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .unlabeled)
    }

    // MARK: — Extra: relabelEvent for .retryPending → routes to editEvent → .localOnly

    func test_VM_LABEL_23_relabelEvent_routesToEditEvent_forRetryPending() async throws {
        var retrying = makeDraft(syncStatus: .synced)
        retrying.syncStatus    = .retryPending
        retrying.failureReason = "network"
        retrying.retryCount    = 1
        try seedSession(drafts: [retrying])

        let vm = makeViewModel()
        await vm.onAppear()

        let ok = vm.relabelEvent(
            deviceEventId:        retrying.deviceEventId,
            contactType:          "left_instep",
            side:                 "left",
            annotationConfidence: "probable"
        )

        XCTAssertTrue(ok)
        let event = vm.activeEvents.first!
        // editEvent for .retryPending → .localOnly + retryCount reset
        XCTAssertEqual(event.syncStatus, .localOnly)
        XCTAssertEqual(event.contactType, "left_instep")
        XCTAssertEqual(event.retryCount, 0)
    }

    // MARK: — Extra: enterLabelingMode with only .synced events still sets .labeling mode

    func test_VM_LABEL_24_enterLabelingMode_setsLabeling_whenOnlySyncedEvents() async throws {
        let synced = makeDraft(syncStatus: .synced)
        try seedSession(drafts: [synced])

        let vm = makeViewModel()
        await vm.onAppear()

        XCTAssertEqual(vm.screenMode, .marking)
        vm.enterLabelingMode()
        XCTAssertEqual(vm.screenMode, .labeling)
        XCTAssertNil(vm.saveError)
    }
}

import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3B2A P2: labelEvent, enterLabelingMode/exitLabelingMode screenMode

@MainActor
final class EventLabelingVMTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("event_labeling_vm_tests_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    private func makeViewModel() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-1",
            apiClient: MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    // P2-1: enterLabelingMode switches screenMode to .labeling when an
    // .unlabeled event exists.
    func test_P2_enterLabelingModeSetsModeWhenUnlabeledExists() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        XCTAssertEqual(vm.screenMode, .marking)
        vm.enterLabelingMode()
        XCTAssertEqual(vm.screenMode, .labeling)
    }

    // P2-1b: exitLabelingMode returns to .marking.
    func test_P2_exitLabelingModeReturnsToMarking() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        vm.enterLabelingMode()
        XCTAssertEqual(vm.screenMode, .labeling)
        vm.exitLabelingMode()
        XCTAssertEqual(vm.screenMode, .marking)
    }

    // P2-2: labelEvent transitions .labelPending → .localOnly.
    func test_P2_labelEventTransitionsLabelPendingToLocalOnly() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()

        let ok = vm.labelEvent(
            deviceEventId: draft.deviceEventId,
            contactType: "right_instep",
            side: "right",
            annotationConfidence: "certain"
        )

        XCTAssertTrue(ok)
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .localOnly)
    }

    // P2-3: labelEvent sets contactType/side/annotationConfidence/custom fields.
    func test_P2_labelEventSetsContactTypeSideConfidenceAndCustomFields() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()

        vm.labelEvent(
            deviceEventId: draft.deviceEventId,
            contactType: "custom_other",
            side: "left",
            annotationConfidence: "uncertain",
            customLabel: "belső csüd",
            customDescription: "egyedi leírás"
        )

        let updated = vm.activeEvents.first!
        XCTAssertEqual(updated.contactType, "custom_other")
        XCTAssertEqual(updated.side, "left")
        XCTAssertEqual(updated.annotationConfidence, "uncertain")
        XCTAssertEqual(updated.customLabel, "belső csüd")
        XCTAssertEqual(updated.customDescription, "egyedi leírás")
    }

    // P2-4: labelEvent persists — fresh store load shows .localOnly with the chosen type.
    func test_P2_labelEventPersistsToDisk() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()
        vm.labelEvent(
            deviceEventId: draft.deviceEventId,
            contactType: "right_instep",
            side: "right",
            annotationConfidence: "certain"
        )

        let store = LocalAnnotationStore(baseDirectory: tempDir)
        if case .loaded(let saved) = store.load(userId: 1, videoId: "vid-1") {
            let reloaded = saved.drafts.first { !$0.deletedLocally && $0.syncStatus != .deleted }
            XCTAssertEqual(reloaded?.syncStatus, .localOnly)
            XCTAssertEqual(reloaded?.contactType, "right_instep")
            XCTAssertEqual(reloaded?.side, "right")
        } else {
            XCTFail("Expected loaded session on disk after labelEvent")
        }
    }

    // P2-5: labelEvent allows re-labeling an already-.localOnly event (back-navigation case).
    func test_P2_labelEventAllowsRelabelingLocalOnly() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()
        vm.labelEvent(
            deviceEventId: draft.deviceEventId,
            contactType: "right_instep",
            side: "right",
            annotationConfidence: "certain"
        )

        let ok = vm.labelEvent(
            deviceEventId: draft.deviceEventId,
            contactType: "left_instep",
            side: "left",
            annotationConfidence: "probable"
        )

        XCTAssertTrue(ok)
        let updated = vm.activeEvents.first!
        XCTAssertEqual(updated.syncStatus, .localOnly)
        XCTAssertEqual(updated.contactType, "left_instep")
        XCTAssertEqual(updated.side, "left")
        XCTAssertEqual(updated.annotationConfidence, "probable")
    }

    // P2-6: labelEvent returns false for a draft that is not .labelPending/.localOnly
    // (e.g. still .unlabeled — enterLabelingMode was not called).
    func test_P2_labelEventReturnsFalseForUnlabeledDraft() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        // enterLabelingMode() NOT called — draft stays .unlabeled.

        let ok = vm.labelEvent(
            deviceEventId: draft.deviceEventId,
            contactType: "right_instep",
            side: "right",
            annotationConfidence: "certain"
        )

        XCTAssertFalse(ok)
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .unlabeled)
    }

    // P2-7: labelEvent rolls back (returns false, draft unchanged) when the
    // local save fails. Forces a write failure by making the session
    // directory read-only after the initial session file has been created.
    func test_P2_labelEventRollsBackOnSaveFailure() async throws {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()

        let sessionDir = vm.diagSessionFilePath.deletingLastPathComponent()
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: sessionDir.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: sessionDir.path) }

        let ok = vm.labelEvent(
            deviceEventId: draft.deviceEventId,
            contactType: "right_instep",
            side: "right",
            annotationConfidence: "certain"
        )

        XCTAssertFalse(ok)
        XCTAssertNotNil(vm.saveError)
        XCTAssertEqual(vm.saveStatus, .failed)
        // In-memory draft unchanged — still .labelPending, no contactType.
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .labelPending)
        XCTAssertNil(vm.activeEvents.first?.contactType)
    }

    // P2-8: labeling progress survives close + reopen — a fresh VM instance
    // loading the same session sees the .localOnly event with its label,
    // and the remaining .labelPending event untouched.
    func test_P2_labelingProgressPersistsAcrossReopen() async {
        let vm1 = makeViewModel()
        await vm1.onAppear()
        let first  = vm1.markTimestamp(ms: 1000)!
        let second = vm1.markTimestamp(ms: 2000)!
        vm1.enterLabelingMode()
        vm1.labelEvent(
            deviceEventId: first.deviceEventId,
            contactType: "right_instep",
            side: "right",
            annotationConfidence: "certain"
        )
        _ = second

        let vm2 = JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-1",
            apiClient: MockAnnotationAPIClient(),
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
        await vm2.onAppear()

        let events = vm2.activeEvents.sorted { $0.timestampMs < $1.timestampMs }
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events[0].syncStatus, .localOnly)
        XCTAssertEqual(events[0].contactType, "right_instep")
        XCTAssertEqual(events[1].syncStatus, .labelPending)
        XCTAssertNil(events[1].contactType)
    }
}

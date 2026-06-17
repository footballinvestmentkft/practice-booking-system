import XCTest
@testable import LFAEducationCenter

// MARK: — M01..M17: markTimestamp, unlabeledCount, labelPendingCount, enterLabelingMode

@MainActor
final class MarkTimestampVMTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("mark_ts_vm_tests_\(UUID().uuidString)", isDirectory: true)
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

    // M01: markTimestamp returns a draft when session is loaded.
    func test_M01_markTimestampReturnsDraftWhenSessionLoaded() async {
        let vm = makeViewModel()
        await vm.onAppear()
        XCTAssertNotNil(vm.markTimestamp(ms: 1000))
    }

    // M02: returned draft has .unlabeled status.
    func test_M02_markTimestampReturnsDraftWithUnlabeledStatus() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)
        XCTAssertEqual(draft?.syncStatus, .unlabeled)
    }

    // M03: returned draft has nil contactType.
    func test_M03_markTimestampReturnsDraftWithNilContactType() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)
        XCTAssertNil(draft?.contactType)
    }

    // M04: marking does not trigger sync — draft stays .unlabeled after the call.
    // Verified by status: .syncing would indicate an API call was started.
    func test_M04_markTimestampDoesNotTriggerSync() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .unlabeled)
    }

    // M05: markTimestamp persists to disk — a fresh store load returns the event.
    func test_M05_markTimestampPersistsToDisk() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 2500)

        let store = LocalAnnotationStore(baseDirectory: tempDir)
        if case .loaded(let saved) = store.load(userId: 1, videoId: "vid-1") {
            let draft = saved.drafts.first { !$0.deletedLocally && $0.syncStatus != .deleted }
            XCTAssertEqual(draft?.timestampMs, 2500)
            XCTAssertEqual(draft?.syncStatus, .unlabeled)
        } else {
            XCTFail("Expected loaded session on disk after markTimestamp")
        }
    }

    // M06: dedup — second mark at the exact same ms returns nil.
    func test_M06_dedupBlocksExactSameTimestamp() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        let second = vm.markTimestamp(ms: 1000)
        XCTAssertNil(second)
        XCTAssertEqual(vm.activeEvents.count, 1)
    }

    // M07: dedup — mark at ms+199 is within the 200ms window and is blocked.
    func test_M07_dedupBlocksWithin200msWindow() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        let second = vm.markTimestamp(ms: 1199)
        XCTAssertNil(second)
        XCTAssertEqual(vm.activeEvents.count, 1)
    }

    // M08: dedup — mark at ms+200 is outside the window (abs difference is not < 200).
    func test_M08_dedupAllowsMarkAt200msDistance() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        let second = vm.markTimestamp(ms: 1200)
        XCTAssertNotNil(second)
        XCTAssertEqual(vm.activeEvents.count, 2)
    }

    // M09: unlabeledCount counts only .unlabeled events.
    func test_M09_unlabeledCountReflectsUnlabeledDrafts() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        vm.markTimestamp(ms: 2000)
        XCTAssertEqual(vm.unlabeledCount, 2)
    }

    // M10: labelPendingCount is zero before enterLabelingMode is called.
    func test_M10_labelPendingCountIsZeroBeforeEnterLabelingMode() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        XCTAssertEqual(vm.labelPendingCount, 0)
    }

    // M11: enterLabelingMode transitions all .unlabeled → .labelPending.
    func test_M11_enterLabelingModeTransitionsUnlabeledToLabelPending() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        vm.markTimestamp(ms: 2000)
        vm.enterLabelingMode()
        XCTAssertEqual(vm.labelPendingCount, 2)
        XCTAssertEqual(vm.unlabeledCount, 0)
    }

    // M12: enterLabelingMode does not affect .localOnly drafts.
    func test_M12_enterLabelingModeDoesNotAffectLocalOnlyDrafts() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.addEvent(timestampMs: 500, contactType: "foot_volley_r", side: "right", annotationConfidence: "certain")
        vm.markTimestamp(ms: 1000)
        vm.enterLabelingMode()
        let localOnly = vm.activeEvents.filter { $0.syncStatus == .localOnly }
        XCTAssertEqual(localOnly.count, 1)
        XCTAssertEqual(localOnly.first?.contactType, "foot_volley_r")
    }

    // M13: enterLabelingMode persists — fresh store load shows .labelPending.
    func test_M13_enterLabelingModePersistsToDisk() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 3000)
        vm.enterLabelingMode()

        let store = LocalAnnotationStore(baseDirectory: tempDir)
        if case .loaded(let saved) = store.load(userId: 1, videoId: "vid-1") {
            let draft = saved.drafts.first { !$0.deletedLocally && $0.syncStatus != .deleted }
            XCTAssertEqual(draft?.syncStatus, .labelPending)
        } else {
            XCTFail("Expected loaded session on disk after enterLabelingMode")
        }
    }

    // M14: markDeleted on .unlabeled transitions to .deleted immediately (no server call needed).
    func test_M14_markDeletedOnUnlabeledTransitionsToDeleted() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.markDeleted(deviceEventId: draft.deviceEventId)
        XCTAssertTrue(vm.activeEvents.isEmpty)
    }

    // M15: markDeleted on .labelPending transitions to .deleted immediately.
    func test_M15_markDeletedOnLabelPendingTransitionsToDeleted() async {
        let vm = makeViewModel()
        await vm.onAppear()
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()
        vm.markDeleted(deviceEventId: draft.deviceEventId)
        XCTAssertTrue(vm.activeEvents.isEmpty)
    }

    // M16: after markDeleted on one of two .labelPending drafts, activeEvents count drops by one.
    func test_M16_markDeletedOnLabelPendingReducesActiveEvents() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.markTimestamp(ms: 1000)
        vm.markTimestamp(ms: 2000)
        vm.enterLabelingMode()
        let toDelete = vm.activeEvents.first!
        vm.markDeleted(deviceEventId: toDelete.deviceEventId)
        XCTAssertEqual(vm.activeEvents.count, 1)
    }

    // M17: enterLabelingMode with zero unlabeled events is a no-op (no crash, counts stay zero).
    func test_M17_enterLabelingModeWithNoUnlabeledIsNoOp() async {
        let vm = makeViewModel()
        await vm.onAppear()
        vm.enterLabelingMode()
        XCTAssertEqual(vm.unlabeledCount, 0)
        XCTAssertEqual(vm.labelPendingCount, 0)
    }
}

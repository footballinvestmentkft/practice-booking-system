import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3B2: JugglingAnnotationViewModel conflict resolution (AN3B-S01..S10)
//
// Tests use the DI init (apiClient + localStore) to avoid real network calls.
// The local store is seeded with a draft already in .conflicted state so tests
// do not need to drive the full POST→PATCH→versionConflict pipeline.
//
// resolveConflict(deviceEventId:) is async and calls apiClient.listContacts().
// acceptServerVersion / keepLocalVersion are synchronous.

@MainActor
final class AnnotationVMConflictTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("an3b2_vm_conflict_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    // MARK: — Helpers

    /// Seeds the local store with one draft already in .conflicted state,
    /// then loads the VM from that store. Returns (VM, mock API, draft UUID).
    private func makeVMWithConflictedDraft(
        retryCount: Int
    ) async -> (JugglingAnnotationViewModel, MockAnnotationAPIClient, UUID) {
        let mock       = MockAnnotationAPIClient()
        let localStore = LocalAnnotationStore(baseDirectory: tempDir)
        let taxStore   = ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir)

        var draft = ContactEventDraft.new(
            timestampMs:          5000,
            contactType:          "foot_full",
            side:                 "left",
            annotationConfidence: "certain"
        )
        draft.syncStatus         = .conflicted
        draft.serverEventId      = UUID()   // PATCH target — needed for resolveConflictedDraft
        draft.conflictRetryCount = retryCount

        var session = localStore.emptySession(userId: 1, videoId: "vid-1")
        session.drafts.append(draft)
        try! localStore.save(session: &session)

        let vm = JugglingAnnotationViewModel(
            userId:        1,
            videoId:       "vid-1",
            apiClient:     mock,
            taxonomyStore: taxStore,
            localStore:    localStore
        )
        await vm.onAppear()
        return (vm, mock, draft.deviceEventId)
    }

    private func makeServerEvent(deviceEventId: UUID) -> ContactEventOut {
        ContactEventOut(
            eventId:                UUID(),
            deviceEventId:          deviceEventId,
            timestampMs:            5000,
            contactType:            "foot_full",
            side:                   "right",
            annotationConfidence:   "medium",
            annotationReviewStatus: "pending",
            taxonomyReviewStatus:   "stable",
            excludedFromTraining:   false,
            customLabel:            nil,
            customDescription:      nil,
            version:                2,
            createdAt:              Date(),
            updatedAt:              Date()
        )
    }

    // MARK: — resolveConflict auto-resolve (retryCount ≤ 3)

    // AN3B-S01: retryCount=0 → increments to 1 → ≤3 → auto server-wins, no pendingConflictId
    func test_AN3B_S01_firstConflict_autoServerWins() async {
        let (vm, mock, id) = await makeVMWithConflictedDraft(retryCount: 0)
        mock.listContactsResult = .success(ContactEventListOut(
            videoId: "vid-1", annotationStatus: nil,
            events: [makeServerEvent(deviceEventId: id)]
        ))

        await vm.resolveConflict(deviceEventId: id)

        XCTAssertNil(vm.pendingConflictId,
            "retryCount 0→1 must auto-resolve without surfacing conflict UI")
    }

    // AN3B-S02: retryCount=2 → increments to 3 → still ≤3 → auto server-wins
    func test_AN3B_S02_thirdConflict_autoServerWins() async {
        let (vm, mock, id) = await makeVMWithConflictedDraft(retryCount: 2)
        mock.listContactsResult = .success(ContactEventListOut(
            videoId: "vid-1", annotationStatus: nil,
            events: [makeServerEvent(deviceEventId: id)]
        ))

        await vm.resolveConflict(deviceEventId: id)

        XCTAssertNil(vm.pendingConflictId, "retryCount 2→3 must still auto-resolve")
    }

    // AN3B-S03: retryCount=3 → increments to 4 → >3 → pendingConflictId surfaced
    func test_AN3B_S03_fourthConflict_surfacesPendingConflictId() async {
        let (vm, mock, id) = await makeVMWithConflictedDraft(retryCount: 3)
        mock.listContactsResult = .success(ContactEventListOut(
            videoId: "vid-1", annotationStatus: nil,
            events: [makeServerEvent(deviceEventId: id)]
        ))

        await vm.resolveConflict(deviceEventId: id)

        XCTAssertEqual(vm.pendingConflictId, id,
            "4th conflict (retryCount 3→4) must set pendingConflictId for manual resolution")
    }

    // AN3B-S04: retryCount=3 → resolveConflict stores pendingServerSnapshot on the draft
    func test_AN3B_S04_fourthConflict_storesPendingServerSnapshot() async {
        let (vm, mock, id) = await makeVMWithConflictedDraft(retryCount: 3)
        mock.listContactsResult = .success(ContactEventListOut(
            videoId: "vid-1", annotationStatus: nil,
            events: [makeServerEvent(deviceEventId: id)]
        ))

        await vm.resolveConflict(deviceEventId: id)

        let updated = vm.activeEvents.first { $0.deviceEventId == id }
        XCTAssertNotNil(updated?.pendingServerSnapshot,
            "4th conflict must store pendingServerSnapshot so ConflictResolutionView can compare")
    }

    // AN3B-S05: network error → draft stays .conflicted; pendingConflictId stays nil
    func test_AN3B_S05_networkError_draftStaysConflicted() async {
        let (vm, mock, id) = await makeVMWithConflictedDraft(retryCount: 0)
        mock.listContactsResult = .failure(AnnotationAPIError.retryable(code: nil))

        await vm.resolveConflict(deviceEventId: id)

        let updated = vm.activeEvents.first { $0.deviceEventId == id }
        XCTAssertEqual(updated?.syncStatus, .conflicted,
            "Network error must leave draft in .conflicted so caller can retry")
        XCTAssertNil(vm.pendingConflictId)
    }

    // MARK: — acceptServerVersion

    // Helper: get VM into state where pendingConflictId is set (after 4th conflict)
    private func makeVMReadyForManualResolution() async -> (JugglingAnnotationViewModel, UUID) {
        let (vm, mock, id) = await makeVMWithConflictedDraft(retryCount: 3)
        mock.listContactsResult = .success(ContactEventListOut(
            videoId: "vid-1", annotationStatus: nil,
            events: [makeServerEvent(deviceEventId: id)]
        ))
        await vm.resolveConflict(deviceEventId: id)
        XCTAssertNotNil(vm.pendingConflictId)   // precondition guard
        return (vm, id)
    }

    // AN3B-S06: acceptServerVersion clears pendingConflictId
    func test_AN3B_S06_acceptServer_clearsPendingConflictId() async {
        let (vm, id) = await makeVMReadyForManualResolution()

        vm.acceptServerVersion(deviceEventId: id)

        XCTAssertNil(vm.pendingConflictId, "acceptServerVersion must clear pendingConflictId")
    }

    // AN3B-S07: acceptServerVersion clears pendingServerSnapshot on the draft
    func test_AN3B_S07_acceptServer_clearsPendingSnapshot() async {
        let (vm, id) = await makeVMReadyForManualResolution()

        vm.acceptServerVersion(deviceEventId: id)

        let updated = vm.activeEvents.first { $0.deviceEventId == id }
        XCTAssertNil(updated?.pendingServerSnapshot,
            "acceptServerVersion must clear pendingServerSnapshot after applying server state")
    }

    // MARK: — keepLocalVersion

    // AN3B-S08: keepLocalVersion clears pendingConflictId
    func test_AN3B_S08_keepLocal_clearsPendingConflictId() async {
        let (vm, id) = await makeVMReadyForManualResolution()

        vm.keepLocalVersion(deviceEventId: id)

        XCTAssertNil(vm.pendingConflictId, "keepLocalVersion must clear pendingConflictId")
    }

    // AN3B-S09: keepLocalVersion resets conflictRetryCount to 0
    func test_AN3B_S09_keepLocal_resetsRetryCount() async {
        let (vm, id) = await makeVMReadyForManualResolution()

        vm.keepLocalVersion(deviceEventId: id)

        let updated = vm.activeEvents.first { $0.deviceEventId == id }
        XCTAssertEqual(updated?.conflictRetryCount, 0,
            "keepLocalVersion must reset conflictRetryCount so next sync attempt starts fresh")
    }

    // AN3B-S10: keepLocalVersion sets syncStatus to .localOnly for re-queuing
    func test_AN3B_S10_keepLocal_setsLocalOnlyStatus() async {
        let (vm, id) = await makeVMReadyForManualResolution()

        vm.keepLocalVersion(deviceEventId: id)

        let updated = vm.activeEvents.first { $0.deviceEventId == id }
        XCTAssertEqual(updated?.syncStatus, .localOnly,
            "keepLocalVersion must set .localOnly so flushPending() re-sends the local payload")
    }
}

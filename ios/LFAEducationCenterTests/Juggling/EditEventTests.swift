import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3A: editEvent and resolveConflict state-machine tests (AN3-T01..T13)
//
// Naming follows the AN-2 convention: test_AN3_T<n>_<description>.
// All tests are synchronous (editEvent is not async) except resolveConflict tests.

@MainActor
final class EditEventTests: XCTestCase {

    // MARK: — Helpers

    private func makeVM(draft: ContactEventDraft) async -> (JugglingAnnotationViewModel, MockAnnotationAPIClient) {
        let api   = MockAnnotationAPIClient()
        let store = LocalAnnotationStore(baseDirectory: FileManager.default.temporaryDirectory
            .appendingPathComponent("EditEventTests-\(UUID().uuidString)"))
        let taxonomy = ContactTaxonomyStore(authManager: AuthManager())
        let vm = JugglingAnnotationViewModel(
            userId: 1, videoId: "vid-edit",
            apiClient: api, taxonomyStore: taxonomy, localStore: store
        )
        // Save the desired draft, then let onAppear() load it (session is private(set)).
        var session = store.emptySession(userId: 1, videoId: "vid-edit", taxonomyVersion: "v1")
        session.drafts = [draft]
        try? store.save(session: &session)
        await vm.onAppear()
        return (vm, api)
    }

    private func baseDraft(status: ContactEventSyncStatus, serverEventId: UUID? = nil) -> ContactEventDraft {
        var d = ContactEventDraft.new(
            timestampMs:          1000,
            contactType:          "instep_right",
            side:                 "right",
            annotationConfidence: "certain"
        )
        d.syncStatus    = status
        d.serverEventId = serverEventId
        return d
    }

    // MARK: — editEvent transitions

    // AN3-T01
    func test_AN3_T01_editLocalOnlyStaysLocalOnly() async {
        let draft = baseDraft(status: .localOnly)
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "toe_right",
            side:                 "right",
            annotationConfidence: "probable",
            customLabel:          nil,
            customDescription:    nil
        )

        XCTAssertTrue(result)
        let edited = vm.activeEvents.first!
        XCTAssertEqual(edited.syncStatus,           .localOnly)
        XCTAssertEqual(edited.contactType,           "toe_right")
        XCTAssertEqual(edited.annotationConfidence,  "probable")
        XCTAssertEqual(edited.deviceEventId,         draft.deviceEventId) // immutable
        XCTAssertEqual(edited.version,               draft.version)        // unchanged
    }

    // AN3-T02
    func test_AN3_T02_editSyncedBecomesUpdating_deviceEventIdAndVersionUnchanged() async {
        let sid = UUID()
        var draft = baseDraft(status: .synced, serverEventId: sid)
        draft.version = 3
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "heel_left",
            side:                 "left",
            annotationConfidence: "uncertain"
        )

        XCTAssertTrue(result)
        let edited = vm.activeEvents.first!
        XCTAssertEqual(edited.syncStatus,     .updating)
        XCTAssertEqual(edited.contactType,    "heel_left")
        XCTAssertEqual(edited.deviceEventId,  draft.deviceEventId)
        XCTAssertEqual(edited.serverEventId,  sid)
        XCTAssertEqual(edited.version,        3)  // MUST NOT be mutated by editEvent
    }

    // AN3-T03
    func test_AN3_T03_editRetryPendingBecomesLocalOnlyWithRetryReset() async {
        var draft = baseDraft(status: .retryPending)
        draft.retryCount    = 2
        draft.failureReason = "network_error"
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "toe_left",
            side:                 "left",
            annotationConfidence: "certain"
        )

        XCTAssertTrue(result)
        let edited = vm.activeEvents.first!
        XCTAssertEqual(edited.syncStatus,    .localOnly)
        XCTAssertEqual(edited.contactType,   "toe_left")
        XCTAssertEqual(edited.retryCount,    0)
        XCTAssertNil(edited.failureReason)
    }

    // AN3-T04
    func test_AN3_T04_editFailedPermanentNoServerIdBecomesLocalOnly() async {
        var draft = baseDraft(status: .failedPermanent, serverEventId: nil)
        draft.failureReason = "422 validation error"
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "knee_right",
            side:                 "right",
            annotationConfidence: "certain"
        )

        XCTAssertTrue(result)
        let edited = vm.activeEvents.first!
        XCTAssertEqual(edited.syncStatus, .localOnly)
        XCTAssertNil(edited.failureReason)
        XCTAssertEqual(edited.retryCount, 0)
    }

    // AN3-T05
    func test_AN3_T05_editFailedPermanentWithServerIdBecomesUpdating() async {
        let sid = UUID()
        var draft = baseDraft(status: .failedPermanent, serverEventId: sid)
        draft.version       = 2
        draft.failureReason = "422 validation error"
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "knee_right",
            side:                 "right",
            annotationConfidence: "certain"
        )

        XCTAssertTrue(result)
        let edited = vm.activeEvents.first!
        XCTAssertEqual(edited.syncStatus,    .updating)
        XCTAssertEqual(edited.serverEventId, sid)
        XCTAssertEqual(edited.version,       2)   // unchanged
        XCTAssertNil(edited.failureReason)
        XCTAssertEqual(edited.retryCount, 0)
    }

    // AN3-T06: idempotency_conflict failedPermanent is not fixable by editing
    func test_AN3_T06_editFailedPermanentIdempotencyConflictBlocked() async {
        var draft = baseDraft(status: .failedPermanent, serverEventId: nil)
        draft.failureReason = "idempotency_conflict: detail"
        let originalType = draft.contactType
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "toe_right",
            side:                 nil,
            annotationConfidence: "certain"
        )

        XCTAssertFalse(result)
        let unchanged = vm.session?.drafts.first!
        XCTAssertEqual(unchanged?.syncStatus,  .failedPermanent)
        XCTAssertEqual(unchanged?.contactType, originalType)   // not mutated
    }

    // AN3-T07: conflicted blocked
    func test_AN3_T07_editConflictedBlocked() async {
        let draft = baseDraft(status: .conflicted, serverEventId: UUID())
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId: draft.deviceEventId, contactType: "toe_right",
            side: nil, annotationConfidence: "certain"
        )

        XCTAssertFalse(result)
        XCTAssertEqual(vm.session?.drafts.first?.syncStatus, .conflicted)
    }

    // AN3-T08: needsReconciliation blocked
    func test_AN3_T08_editNeedsReconciliationBlocked() async {
        let draft = baseDraft(status: .needsReconciliation, serverEventId: UUID())
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId: draft.deviceEventId, contactType: "toe_right",
            side: nil, annotationConfidence: "certain"
        )

        XCTAssertFalse(result)
        XCTAssertEqual(vm.session?.drafts.first?.syncStatus, .needsReconciliation)
    }

    // AN3-T09: syncing (in-flight POST) blocked
    func test_AN3_T09_editSyncingBlocked() async {
        let draft = baseDraft(status: .syncing)
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId: draft.deviceEventId, contactType: "toe_right",
            side: nil, annotationConfidence: "certain"
        )

        XCTAssertFalse(result)
        XCTAssertEqual(vm.session?.drafts.first?.syncStatus, .syncing)
    }

    // AN3-T10: updating (in-flight PATCH) blocked
    func test_AN3_T10_editUpdatingBlocked() async {
        let draft = baseDraft(status: .updating, serverEventId: UUID())
        let (vm, _) = await makeVM(draft: draft)

        let result = vm.editEvent(
            deviceEventId: draft.deviceEventId, contactType: "toe_right",
            side: nil, annotationConfidence: "certain"
        )

        XCTAssertFalse(result)
        XCTAssertEqual(vm.session?.drafts.first?.syncStatus, .updating)
    }

    // MARK: — resolveConflict

    // AN3-T11: resolveConflict for a conflicted draft that exists on the server
    func test_AN3_T11_resolveConflictFoundOnServerBecomesSynced() async throws {
        let serverEventId = UUID()
        var draft = baseDraft(status: .conflicted, serverEventId: serverEventId)
        draft.version = 1
        let (vm, api) = await makeVM(draft: draft)

        let serverEvent = makeServerEvent(
            deviceEventId: draft.deviceEventId,
            serverEventId: serverEventId,
            contactType:   "heel_right",
            version:       2
        )
        api.listContactsResult = .success(ContactEventListOut(
            videoId: "vid-edit", annotationStatus: "in_progress", events: [serverEvent]
        ))

        await vm.resolveConflict(deviceEventId: draft.deviceEventId)

        let resolved = vm.session?.drafts.first!
        XCTAssertEqual(resolved?.syncStatus,  .synced)
        XCTAssertEqual(resolved?.version,     2)          // server version applied
        XCTAssertEqual(resolved?.contactType, "heel_right") // server payload applied
        XCTAssertNil(resolved?.failureReason)
    }

    // AN3-T12: resolveConflict — draft absent from server → .deleted
    func test_AN3_T12_resolveConflictAbsentOnServerBecomesDeleted() async throws {
        let draft = baseDraft(status: .conflicted, serverEventId: UUID())
        let (vm, api) = await makeVM(draft: draft)

        api.listContactsResult = .success(ContactEventListOut(
            videoId: "vid-edit", annotationStatus: "in_progress", events: []
        ))

        await vm.resolveConflict(deviceEventId: draft.deviceEventId)

        XCTAssertEqual(vm.session?.drafts.first?.syncStatus, .deleted)
    }

    // AN3-T13: resolveConflict — network error → stays conflicted
    func test_AN3_T13_resolveConflictNetworkErrorStaysConflicted() async {
        let draft = baseDraft(status: .conflicted, serverEventId: UUID())
        let (vm, api) = await makeVM(draft: draft)

        api.listContactsResult = .failure(AnnotationAPIError.retryable(code: nil))

        await vm.resolveConflict(deviceEventId: draft.deviceEventId)

        XCTAssertEqual(vm.session?.drafts.first?.syncStatus, .conflicted)
    }

    // MARK: — Private helpers

    private func makeServerEvent(
        deviceEventId: UUID,
        serverEventId: UUID,
        contactType:   String,
        version:       Int
    ) -> ContactEventOut {
        ContactEventOut(
            eventId:                serverEventId,
            deviceEventId:          deviceEventId,
            timestampMs:            1000,
            contactType:            contactType,
            side:                   "right",
            annotationConfidence:   "certain",
            annotationReviewStatus: "pending",
            taxonomyReviewStatus:   "pending",
            excludedFromTraining:   false,
            customLabel:            nil,
            customDescription:      nil,
            version:                version,
            createdAt:              Date(),
            updatedAt:              Date()
        )
    }
}

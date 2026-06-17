import XCTest
@testable import LFAEducationCenter

// MARK: — MockAnnotationAPIClient
//
// Configurable stand-in for JugglingAnnotationAPIClient. Each endpoint has a
// Result<_, Error> slot set by the test before calling the engine.

@MainActor
final class MockAnnotationAPIClient: JugglingAnnotationAPIClientProtocol {

    var listContactsResult:    Result<ContactEventListOut, Error> = .success(ContactEventListOut(videoId: "vid-1", annotationStatus: nil, events: []))
    var createContactResult:   Result<CreateContactResult, Error> = .failure(AnnotationAPIError.unauthorized)
    var patchContactResult:    Result<ContactEventOut, Error> = .failure(AnnotationAPIError.unauthorized)
    var deleteContactResult:   Result<DeleteContactResult, Error> = .failure(AnnotationAPIError.unauthorized)
    var finishAnnotationResult: Result<FinishAnnotationOut, Error> = .failure(AnnotationAPIError.unauthorized)
    // B-2 I-1: video-level storage-release delete
    var deleteVideoResult:     Result<Void, Error> = .failure(VideoDeleteError.unauthorized)
    // B-3: upload pipeline
    var uploadInitResult:       Result<JugglingUploadInitResponse, Error> = .failure(JugglingUploadError.unauthorized)
    var uploadVideoFileResult:  Result<JugglingUploadFileResponse, Error> = .failure(JugglingUploadError.unauthorized)
    var completeUploadResult:   Result<JugglingCompleteResponse, Error>   = .failure(JugglingUploadError.unauthorized)

    func listContacts(videoId: String) async throws -> ContactEventListOut {
        try listContactsResult.get()
    }

    func createContact(videoId: String, request: ContactEventCreateRequest) async throws -> CreateContactResult {
        try createContactResult.get()
    }

    func patchContact(videoId: String, eventId: UUID, request: ContactEventPatchRequest) async throws -> ContactEventOut {
        try patchContactResult.get()
    }

    func deleteContact(videoId: String, eventId: UUID) async throws -> DeleteContactResult {
        try deleteContactResult.get()
    }

    func finishAnnotation(videoId: String, confirmZero: Bool) async throws -> FinishAnnotationOut {
        try finishAnnotationResult.get()
    }

    func deleteVideo(videoId: String) async throws {
        try deleteVideoResult.get()
    }

    func uploadInit(sourceType: String, uploadSource: String) async throws -> JugglingUploadInitResponse {
        try uploadInitResult.get()
    }

    func uploadVideoFile(videoId: String, fileURL: URL, mimeType: String) async throws -> JugglingUploadFileResponse {
        try uploadVideoFileResult.get()
    }

    func completeUpload(videoId: String) async throws -> JugglingCompleteResponse {
        try completeUploadResult.get()
    }
}

// MARK: — AN2-T17..T25: AnnotationSyncEngine state transitions

@MainActor
final class AnnotationSyncEngineTests: XCTestCase {

    // AN2-T17: successful create (201) → synced, server identity captured.
    func test_AN2_T17_pushCreateSuccessBecomesSynced() async {
        let mock = MockAnnotationAPIClient()
        let draft = ContactEventDraft.new(timestampMs: 100, contactType: "right_instep", side: "right", annotationConfidence: "certain")
        let serverEvent = makeServerEvent(deviceEventId: draft.deviceEventId, contactType: "right_instep", side: "right", version: 1)
        mock.createContactResult = .success(CreateContactResult(event: serverEvent, isNew: true))

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushCreate(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .synced)
        XCTAssertEqual(result.serverEventId, serverEvent.eventId)
        XCTAssertEqual(result.version, 1)
        XCTAssertEqual(result.retryCount, 0)
    }

    // AN2-T18: network error → retryPending, retryCount incremented.
    func test_AN2_T18_pushCreateNetworkErrorBecomesRetryPending() async {
        let mock = MockAnnotationAPIClient()
        mock.createContactResult = .failure(AnnotationAPIError.retryable(code: nil))
        let draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushCreate(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .retryPending)
        XCTAssertEqual(result.retryCount, 1)
    }

    // AN2-T19: permanent error (403 consent blocked) → failedPermanent, no retry.
    func test_AN2_T19_pushCreatePermanentErrorBecomesFailedPermanent() async {
        let mock = MockAnnotationAPIClient()
        mock.createContactResult = .failure(AnnotationAPIError.permanent(code: 403, detail: "consent_blocked"))
        let draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushCreate(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .failedPermanent)
        XCTAssertEqual(result.retryCount, 0)
    }

    // AN2-T20: retryable error once retryCount has hit maxRetries → failedPermanent.
    func test_AN2_T20_pushCreateRetryableAtMaxRetriesBecomesFailedPermanent() async {
        let mock = MockAnnotationAPIClient()
        mock.createContactResult = .failure(AnnotationAPIError.retryable(code: 503))
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.retryCount = AnnotationSyncEngine.maxRetries

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushCreate(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .failedPermanent)
        XCTAssertEqual(result.retryCount, AnnotationSyncEngine.maxRetries)
    }

    // AN2-T21: DELETE 204 → deleted.
    func test_AN2_T21_pushDeleteConfirmedBecomesDeleted() async {
        let mock = MockAnnotationAPIClient()
        mock.deleteContactResult = .success(.deleted)
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.serverEventId = UUID()
        draft.syncStatus = .synced

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushDelete(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .deleted)
    }

    // AN2-T22: DELETE 404 is ownership-ambiguous → needsReconciliation, not auto-success.
    func test_AN2_T22_pushDeleteNotFoundBecomesNeedsReconciliation() async {
        let mock = MockAnnotationAPIClient()
        mock.deleteContactResult = .success(.notFoundAmbiguous)
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.serverEventId = UUID()
        draft.syncStatus = .synced

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushDelete(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .needsReconciliation)
        XCTAssertEqual(result.failureReason, "delete_404_ambiguous")
    }

    // AN2-T23: reconcile finds the event on the server → synced with server state applied.
    func test_AN2_T23_reconcileFindsEventOnServerBecomesSynced() async throws {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.syncStatus = .needsReconciliation

        let serverEvent = makeServerEvent(deviceEventId: draft.deviceEventId, contactType: "head", side: "center", version: 2)
        mock.listContactsResult = .success(ContactEventListOut(videoId: "vid-1", annotationStatus: "in_progress", events: [serverEvent]))

        let engine = AnnotationSyncEngine(apiClient: mock)
        var session = makeSession(drafts: [draft])
        try await engine.reconcile(session: &session)

        XCTAssertEqual(session.drafts[0].syncStatus, .synced)
        XCTAssertEqual(session.drafts[0].serverEventId, serverEvent.eventId)
        XCTAssertEqual(session.drafts[0].version, 2)
    }

    // AN2-T24: reconcile finds the event absent from the server → deleted.
    func test_AN2_T24_reconcileEventAbsentFromServerBecomesDeleted() async throws {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.syncStatus = .needsReconciliation
        draft.serverEventId = UUID()

        mock.listContactsResult = .success(ContactEventListOut(videoId: "vid-1", annotationStatus: "in_progress", events: []))

        let engine = AnnotationSyncEngine(apiClient: mock)
        var session = makeSession(drafts: [draft])
        try await engine.reconcile(session: &session)

        XCTAssertEqual(session.drafts[0].syncStatus, .deleted)
    }

    // AN2-T25: flushPending pushes localOnly creates and skips never-synced
    // drafts that were deleted locally before ever reaching the server.
    func test_AN2_T25_flushPendingSyncsCreatesAndSkipsLocallyDeletedNeverSynced() async {
        let mock = MockAnnotationAPIClient()
        let toCreate = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        var toSkip = ContactEventDraft.new(timestampMs: 2, contactType: "chest", side: "center", annotationConfidence: "certain")
        toSkip.deletedLocally = true

        let serverEvent = makeServerEvent(deviceEventId: toCreate.deviceEventId, contactType: "head", side: "center", version: 1)
        mock.createContactResult = .success(CreateContactResult(event: serverEvent, isNew: true))

        let engine = AnnotationSyncEngine(apiClient: mock)
        var session = makeSession(drafts: [toCreate, toSkip])
        await engine.flushPending(session: &session)

        let created = session.drafts.first { $0.deviceEventId == toCreate.deviceEventId }!
        let skipped = session.drafts.first { $0.deviceEventId == toSkip.deviceEventId }!

        XCTAssertEqual(created.syncStatus, .synced)
        XCTAssertEqual(created.serverEventId, serverEvent.eventId)
        XCTAssertEqual(skipped.syncStatus, .deleted)
    }

    // AN2-T26: PATCH 200 → synced, server state (incl. bumped version) applied.
    func test_AN2_T26_pushPatchSuccessBecomesSynced() async {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.serverEventId = UUID()
        draft.syncStatus = .synced
        draft.version = 1

        let serverEvent = makeServerEvent(deviceEventId: draft.deviceEventId, contactType: "chest", side: "center", version: 2)
        mock.patchContactResult = .success(serverEvent)

        let engine = AnnotationSyncEngine(apiClient: mock)
        let request = ContactEventPatchRequest(version: 1, contactType: "chest", annotationConfidence: nil, side: nil, customLabel: nil, customDescription: nil)
        let result = await engine.pushPatch(draft: draft, videoId: "vid-1", request: request)

        XCTAssertEqual(result.syncStatus, .synced)
        XCTAssertEqual(result.contactType, "chest")
        XCTAssertEqual(result.version, 2)
        XCTAssertNil(result.failureReason)
    }

    // AN2-T27: PATCH 409 version_conflict → conflicted, failureReason carries detail.
    func test_AN2_T27_pushPatchVersionConflictBecomesConflicted() async {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.serverEventId = UUID()
        draft.syncStatus = .synced
        mock.patchContactResult = .failure(AnnotationAPIError.versionConflict(detail: "version_conflict: expected 1, got 2"))

        let engine = AnnotationSyncEngine(apiClient: mock)
        let request = ContactEventPatchRequest(version: 1, contactType: nil, annotationConfidence: nil, side: nil, customLabel: nil, customDescription: nil)
        let result = await engine.pushPatch(draft: draft, videoId: "vid-1", request: request)

        XCTAssertEqual(result.syncStatus, .conflicted)
        XCTAssertEqual(result.failureReason, "version_conflict: expected 1, got 2")
    }

    // AN2-T28: PATCH 422 validation error → failedPermanent, not retried.
    func test_AN2_T28_pushPatchValidationErrorBecomesFailedPermanent() async {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.serverEventId = UUID()
        draft.syncStatus = .synced
        mock.patchContactResult = .failure(AnnotationAPIError.permanent(code: 422, detail: "validation_error"))

        let engine = AnnotationSyncEngine(apiClient: mock)
        let request = ContactEventPatchRequest(version: 1, contactType: nil, annotationConfidence: nil, side: nil, customLabel: nil, customDescription: nil)
        let result = await engine.pushPatch(draft: draft, videoId: "vid-1", request: request)

        XCTAssertEqual(result.syncStatus, .failedPermanent)
        XCTAssertEqual(result.retryCount, 0)
    }

    // AN2-T29: PATCH timeout/network error → needsReconciliation (outcome unknown, not idempotent).
    func test_AN2_T29_pushPatchTimeoutBecomesNeedsReconciliation() async {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.serverEventId = UUID()
        draft.syncStatus = .synced
        mock.patchContactResult = .failure(AnnotationAPIError.retryable(code: nil))

        let engine = AnnotationSyncEngine(apiClient: mock)
        let request = ContactEventPatchRequest(version: 1, contactType: nil, annotationConfidence: nil, side: nil, customLabel: nil, customDescription: nil)
        let result = await engine.pushPatch(draft: draft, videoId: "vid-1", request: request)

        XCTAssertEqual(result.syncStatus, .needsReconciliation)
        XCTAssertEqual(result.failureReason, "patch_timeout_or_unavailable")
    }

    // AN2-T30: DELETE timeout/network error → needsReconciliation (outcome unknown).
    func test_AN2_T30_pushDeleteTimeoutBecomesNeedsReconciliation() async {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.serverEventId = UUID()
        draft.syncStatus = .synced
        mock.deleteContactResult = .failure(AnnotationAPIError.retryable(code: nil))

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushDelete(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .needsReconciliation)
        XCTAssertEqual(result.failureReason, "delete_timeout_or_unavailable")
    }

    // AN2-T31: POST 409 idempotency_conflict → failedPermanent, requires user resolution (no auto-retry).
    func test_AN2_T31_pushCreateIdempotencyConflictBecomesFailedPermanent() async {
        let mock = MockAnnotationAPIClient()
        mock.createContactResult = .failure(AnnotationAPIError.idempotencyConflict(detail: "idempotency_conflict: payload mismatch"))
        let draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")

        let engine = AnnotationSyncEngine(apiClient: mock)
        let result = await engine.pushCreate(draft: draft, videoId: "vid-1")

        XCTAssertEqual(result.syncStatus, .failedPermanent)
        XCTAssertEqual(result.retryCount, 0)
        XCTAssertEqual(result.failureReason, "idempotency_conflict: idempotency_conflict: payload mismatch")
    }

    // AN2-T32: reconcile — server list contains an event for an unrelated
    // device (not present locally) alongside the one this draft needs.
    // The extra server-side event must be ignored; only the matching draft
    // is updated.
    func test_AN2_T32_reconcileIgnoresUnrelatedServerEvent() async throws {
        let mock = MockAnnotationAPIClient()
        var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        draft.syncStatus = .needsReconciliation

        let matchingServerEvent = makeServerEvent(deviceEventId: draft.deviceEventId, contactType: "head", side: "center", version: 1)
        let unrelatedServerEvent = makeServerEvent(deviceEventId: UUID(), contactType: "chest", side: "center", version: 1)
        mock.listContactsResult = .success(ContactEventListOut(videoId: "vid-1", annotationStatus: "in_progress", events: [matchingServerEvent, unrelatedServerEvent]))

        let engine = AnnotationSyncEngine(apiClient: mock)
        var session = makeSession(drafts: [draft])
        try await engine.reconcile(session: &session)

        XCTAssertEqual(session.drafts.count, 1, "reconcile must not add drafts for server events with no local match")
        XCTAssertEqual(session.drafts[0].syncStatus, .synced)
        XCTAssertEqual(session.drafts[0].serverEventId, matchingServerEvent.eventId)
    }

    // AN2-T33: reconcile — drafts not in needsReconciliation are left untouched.
    func test_AN2_T33_reconcileLeavesNonReconciliationDraftsUntouched() async throws {
        let mock = MockAnnotationAPIClient()
        var needsRecon = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        needsRecon.syncStatus = .needsReconciliation
        needsRecon.serverEventId = UUID()

        var alreadySynced = ContactEventDraft.new(timestampMs: 2, contactType: "chest", side: "center", annotationConfidence: "certain")
        alreadySynced.syncStatus = .synced
        alreadySynced.serverEventId = UUID()
        alreadySynced.version = 5

        mock.listContactsResult = .success(ContactEventListOut(videoId: "vid-1", annotationStatus: "in_progress", events: []))

        let engine = AnnotationSyncEngine(apiClient: mock)
        var session = makeSession(drafts: [needsRecon, alreadySynced])
        try await engine.reconcile(session: &session)

        XCTAssertEqual(session.drafts[0].syncStatus, .deleted, "absent needsReconciliation draft must become deleted")
        XCTAssertEqual(session.drafts[1].syncStatus, .synced, "drafts outside needsReconciliation must be left untouched")
        XCTAssertEqual(session.drafts[1].version, 5)
    }

    // MARK: — Helpers

    private func makeSession(drafts: [ContactEventDraft]) -> AnnotationSessionFile {
        AnnotationSessionFile(
            schemaVersion: 1, userId: 1, videoId: "vid-1",
            taxonomyVersion: "v1", lastUpdatedAt: Date(),
            drafts: drafts, checksum: ""
        )
    }

    private func makeServerEvent(
        deviceEventId: UUID, contactType: String, side: String?, version: Int
    ) -> ContactEventOut {
        ContactEventOut(
            eventId: UUID(),
            deviceEventId: deviceEventId,
            timestampMs: 1,
            contactType: contactType,
            side: side,
            annotationConfidence: "certain",
            annotationReviewStatus: "pending",
            taxonomyReviewStatus: "not_applicable",
            excludedFromTraining: true,
            customLabel: nil,
            customDescription: nil,
            version: version,
            createdAt: Date(),
            updatedAt: Date()
        )
    }
}

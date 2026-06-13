import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3A: flushPending .updating extension tests (AN3-T14..T16)
//
// Verifies that flushPending now also handles .updating drafts (edited events
// that need a PATCH pushed to the server).

@MainActor
final class FlushPendingEditTests: XCTestCase {

    // MARK: — Helpers

    private func makeEngine(api: MockAnnotationAPIClient) -> AnnotationSyncEngine {
        AnnotationSyncEngine(apiClient: api)
    }

    private func makeSyncedDraft(contactType: String = "instep_right") -> ContactEventDraft {
        var d = ContactEventDraft.new(
            timestampMs:          2000,
            contactType:          contactType,
            side:                 "right",
            annotationConfidence: "certain"
        )
        d.syncStatus    = .synced
        d.serverEventId = UUID()
        d.version       = 1
        return d
    }

    private func editedDraft(from draft: ContactEventDraft, newType: String = "heel_right") -> ContactEventDraft {
        var d = draft
        d.contactType = newType
        d.syncStatus  = .updating
        return d
    }

    private func makeSession(drafts: [ContactEventDraft]) -> AnnotationSessionFile {
        AnnotationSessionFile(
            schemaVersion:   1,
            userId:          1,
            videoId:         "vid-flush",
            taxonomyVersion: "v1",
            lastUpdatedAt:   Date(),
            drafts:          drafts,
            checksum:        ""
        )
    }

    // AN3-T14: .updating draft with serverEventId → pushPatch called → .synced
    func test_AN3_T14_flushPendingUpdatingDraftCallsPushPatch() async {
        let api     = MockAnnotationAPIClient()
        let engine  = makeEngine(api: api)
        let synced  = makeSyncedDraft()
        let updated = editedDraft(from: synced, newType: "heel_right")

        let serverResponse = ContactEventOut(
            eventId:                synced.serverEventId!,
            deviceEventId:          synced.deviceEventId,
            timestampMs:            2000,
            contactType:            "heel_right",
            side:                   "right",
            annotationConfidence:   "certain",
            annotationReviewStatus: "pending",
            taxonomyReviewStatus:   "pending",
            excludedFromTraining:   false,
            customLabel:            nil,
            customDescription:      nil,
            version:                2,
            createdAt:              Date(),
            updatedAt:              Date()
        )
        api.patchContactResult = .success(serverResponse)

        var session = makeSession(drafts: [updated])
        await engine.flushPending(session: &session)

        // Outcome: .synced with server's version and fields applied.
        let result = session.drafts[0]
        XCTAssertEqual(result.syncStatus,  .synced)
        XCTAssertEqual(result.contactType, "heel_right")
        XCTAssertEqual(result.version,     2)
        // createContact was not invoked (draft already had serverEventId →
        // .synced outcome proves pushPatch was taken, not pushCreate).
        XCTAssertNotEqual(result.serverEventId, nil)
    }

    // AN3-T15: .updating + deletedLocally → DELETE path taken instead of PATCH
    func test_AN3_T15_flushPendingUpdatingDeletedTakesDeletePath() async {
        let api    = MockAnnotationAPIClient()
        let engine = makeEngine(api: api)
        let synced = makeSyncedDraft()
        var updated = editedDraft(from: synced)
        updated.deletedLocally = true

        api.deleteContactResult = .success(.deleted)

        var session = makeSession(drafts: [updated])
        await engine.flushPending(session: &session)

        // .deleted outcome proves pushDelete was called, not pushPatch.
        XCTAssertEqual(session.drafts[0].syncStatus, .deleted)
    }

    // AN3-T16: .updating without serverEventId → failedPermanent (defensive path)
    func test_AN3_T16_flushPendingUpdatingNoServerIdBecomesFailedPermanent() async {
        let api    = MockAnnotationAPIClient()
        let engine = makeEngine(api: api)

        var draft = ContactEventDraft.new(
            timestampMs: 3000, contactType: "toe_right",
            side: "right", annotationConfidence: "certain"
        )
        draft.syncStatus    = .updating
        draft.serverEventId = nil  // defensive: should not occur in normal flow

        var session = makeSession(drafts: [draft])
        await engine.flushPending(session: &session)

        XCTAssertEqual(session.drafts[0].syncStatus, .failedPermanent)
        XCTAssertEqual(session.drafts[0].failureReason, "patch_without_server_id")
    }
}

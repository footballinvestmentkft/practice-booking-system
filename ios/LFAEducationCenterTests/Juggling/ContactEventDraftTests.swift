import XCTest
@testable import LFAEducationCenter

// MARK: — AN2-T11..T16: ContactEventDraft model + FinishReadiness + wire decode

@MainActor
final class ContactEventDraftTests: XCTestCase {

    // AN2-T11: .new() produces a localOnly draft with sane defaults.
    func test_AN2_T11_newDraftDefaults() {
        let draft = ContactEventDraft.new(
            timestampMs: 5000, contactType: "left_knee",
            side: "left", annotationConfidence: "probable"
        )
        XCTAssertEqual(draft.syncStatus, .localOnly)
        XCTAssertEqual(draft.version, 1)
        XCTAssertEqual(draft.retryCount, 0)
        XCTAssertFalse(draft.deletedLocally)
        XCTAssertNil(draft.serverEventId)
        XCTAssertNil(draft.failureReason)
    }

    // AN2-T12: deviceEventId is the identity and is immutable across mutation.
    func test_AN2_T12_deviceEventIdIsStableIdentity() {
        var draft = ContactEventDraft.new(
            timestampMs: 1, contactType: "chest",
            side: "center", annotationConfidence: "certain"
        )
        let originalId = draft.deviceEventId
        draft.syncStatus = .synced
        draft.version = 2
        draft.serverEventId = UUID()

        XCTAssertEqual(draft.deviceEventId, originalId)
        XCTAssertEqual(draft.id, originalId)
    }

    // AN2-T13: zero drafts → readyZero.
    func test_AN2_T13_finishReadinessReadyZeroWhenEmpty() {
        let engine = AnnotationSyncEngine(apiClient: MockAnnotationAPIClient())
        let session = makeSession(drafts: [])
        XCTAssertEqual(engine.finishReadiness(for: session), .readyZero)
    }

    // AN2-T14: synced drafts → readyWithCount(N).
    func test_AN2_T14_finishReadinessReadyWithCount() {
        let engine = AnnotationSyncEngine(apiClient: MockAnnotationAPIClient())
        var d1 = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        d1.syncStatus = .synced
        var d2 = ContactEventDraft.new(timestampMs: 2, contactType: "chest", side: "center", annotationConfidence: "certain")
        d2.syncStatus = .synced
        let session = makeSession(drafts: [d1, d2])

        XCTAssertEqual(engine.finishReadiness(for: session), .readyWithCount(2))
    }

    // AN2-T15: any in-flight/unresolved active draft blocks finish.
    func test_AN2_T15_finishReadinessBlockedByLocalOnly() {
        let engine = AnnotationSyncEngine(apiClient: MockAnnotationAPIClient())
        var synced = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        synced.syncStatus = .synced
        let pending = ContactEventDraft.new(timestampMs: 2, contactType: "chest", side: "center", annotationConfidence: "certain")
        // pending.syncStatus == .localOnly by default
        let session = makeSession(drafts: [synced, pending])

        XCTAssertEqual(engine.finishReadiness(for: session), .blocked([.localOnly]))
    }

    // AN2-T16: ContactEventBatchResult decodes "duplicate_skipped" wire key correctly.
    func test_AN2_T16_batchResultDecodesDuplicateSkippedKey() throws {
        let json = """
        {
          "created": 2,
          "duplicate_skipped": 1,
          "conflict": 0,
          "results": [
            {"device_event_id": "11111111-1111-1111-1111-111111111111", "status": "created", "event_id": "22222222-2222-2222-2222-222222222222", "detail": null},
            {"device_event_id": "33333333-3333-3333-3333-333333333333", "status": "duplicate", "event_id": null, "detail": "exact_duplicate"}
          ]
        }
        """.data(using: .utf8)!

        let result = try JSONDecoder().decode(ContactEventBatchResult.self, from: json)

        XCTAssertEqual(result.created, 2)
        XCTAssertEqual(result.duplicateSkipped, 1)
        XCTAssertEqual(result.conflict, 0)
        XCTAssertEqual(result.results.count, 2)
        XCTAssertEqual(result.results[1].status, "duplicate")
        XCTAssertNil(result.results[1].eventId)
    }

    // AN2-T34: full Finish-blocked matrix — every blocking status, in isolation,
    // blocks finish with exactly that status reported.
    func test_AN2_T34_finishReadinessBlockedMatrix() {
        let engine = AnnotationSyncEngine(apiClient: MockAnnotationAPIClient())
        let blockingStatuses: [ContactEventSyncStatus] = [
            .localOnly, .syncing, .updating, .deleting,
            .retryPending, .conflicted, .needsReconciliation,
        ]

        for status in blockingStatuses {
            var draft = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
            draft.syncStatus = status
            let session = makeSession(drafts: [draft])

            XCTAssertEqual(
                engine.finishReadiness(for: session), .blocked([status]),
                "status \(status) must block finish"
            )
        }
    }

    // AN2-T35: a failedPermanent active event does NOT block finish — it
    // requires user resolution but finish proceeds with the remaining events.
    func test_AN2_T35_finishReadinessNotBlockedByFailedPermanent() {
        let engine = AnnotationSyncEngine(apiClient: MockAnnotationAPIClient())
        var failed = ContactEventDraft.new(timestampMs: 1, contactType: "head", side: "center", annotationConfidence: "certain")
        failed.syncStatus = .failedPermanent

        let zeroSession = makeSession(drafts: [failed])
        XCTAssertEqual(engine.finishReadiness(for: zeroSession), .readyZero)

        var synced = ContactEventDraft.new(timestampMs: 2, contactType: "chest", side: "center", annotationConfidence: "certain")
        synced.syncStatus = .synced
        let withSyncedSession = makeSession(drafts: [failed, synced])
        XCTAssertEqual(engine.finishReadiness(for: withSyncedSession), .readyWithCount(1))
    }

    // MARK: — Helper

    private func makeSession(drafts: [ContactEventDraft]) -> AnnotationSessionFile {
        AnnotationSessionFile(
            schemaVersion: 1, userId: 1, videoId: "vid-1",
            taxonomyVersion: "v1", lastUpdatedAt: Date(),
            drafts: drafts, checksum: ""
        )
    }
}

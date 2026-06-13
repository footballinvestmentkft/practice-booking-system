import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3B2: JugglingAnnotationViewModel conflict resolution (AN3B-S01..S10)
//
// Tests cover:
//   - resolveConflict() auto-resolves (server-wins) for retryCount ≤ 3
//   - resolveConflict() surfaces pendingConflictId + pendingServerSnapshot on 4th attempt
//   - acceptServerVersion() clears snapshot, advances pendingConflictId
//   - keepLocalVersion() resets retryCount, clears snapshot, re-queues event

@MainActor
final class AnnotationVMConflictTests: XCTestCase {

    // MARK: — Helpers

    private func makeVM() -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId:      42,
            videoId:     "test-video-001",
            authManager: MockAuthManager()
        )
    }

    private func makeDraft(
        key:          String = "foot_full",
        side:         String? = "left",
        retryCount:   Int    = 0
    ) -> ContactEventDraft {
        var d = ContactEventDraft.new(
            timestampMs:          5000,
            contactType:          key,
            side:                 side,
            annotationConfidence: "high",
            customLabel:          nil,
            customDescription:    nil
        )
        d.conflictRetryCount = retryCount
        return d
    }

    private func makeServerSnapshot() -> ContactEventOut {
        ContactEventOut(
            id:                   999,
            deviceEventId:        UUID().uuidString,
            videoId:              "test-video-001",
            timestampMs:          5100,
            contactType:          "foot_instep",
            side:                 "right",
            annotationConfidence: "medium",
            customLabel:          nil,
            customDescription:    nil,
            annotatorUserId:      42,
            annotationState:      "in_progress",
            createdAt:            "2026-06-13T00:00:00Z",
            updatedAt:            "2026-06-13T00:00:01Z"
        )
    }

    // MARK: — resolveConflict auto-resolve (retryCount ≤ 3)

    // AN3B-S01: first conflict (retryCount=0) → auto server-wins, no pendingConflictId set
    func test_AN3B_S01_firstConflict_autoServerWins() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 0)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)

        XCTAssertNil(vm.pendingConflictId,
            "Auto-resolve (retry 0) must NOT set pendingConflictId")
    }

    // AN3B-S02: second conflict (retryCount=1) → still auto server-wins
    func test_AN3B_S02_secondConflict_autoServerWins() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 1)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)

        XCTAssertNil(vm.pendingConflictId,
            "Auto-resolve (retry 1) must NOT set pendingConflictId")
    }

    // AN3B-S03: third conflict (retryCount=2) → still auto server-wins
    func test_AN3B_S03_thirdConflict_autoServerWins() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 2)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)

        XCTAssertNil(vm.pendingConflictId,
            "Auto-resolve (retry 2) must NOT set pendingConflictId")
    }

    // AN3B-S04: fourth conflict (retryCount=3) → manual, pendingConflictId set
    func test_AN3B_S04_fourthConflict_surfacesManualResolution() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 3)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)

        XCTAssertEqual(vm.pendingConflictId, draft.deviceEventId,
            "4th conflict (retryCount=3) must surface pendingConflictId for manual resolution")
    }

    // AN3B-S05: fourth conflict → pendingServerSnapshot stored on draft
    func test_AN3B_S05_fourthConflict_storesPendingServerSnapshot() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 3)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)

        let updated = vm.activeEvents.first { $0.deviceEventId == draft.deviceEventId }
        XCTAssertNotNil(updated?.pendingServerSnapshot,
            "4th conflict must store pendingServerSnapshot on the draft")
        XCTAssertEqual(updated?.pendingServerSnapshot?.id, 999)
    }

    // MARK: — acceptServerVersion

    // AN3B-S06: acceptServerVersion clears pendingConflictId
    func test_AN3B_S06_acceptServer_clearsPendingConflictId() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 3)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)
        XCTAssertNotNil(vm.pendingConflictId)

        await vm.acceptServerVersion(deviceEventId: draft.deviceEventId)

        XCTAssertNil(vm.pendingConflictId,
            "acceptServerVersion must clear pendingConflictId")
    }

    // AN3B-S07: acceptServerVersion clears pendingServerSnapshot on draft
    func test_AN3B_S07_acceptServer_clearsPendingSnapshot() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 3)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)
        await vm.acceptServerVersion(deviceEventId: draft.deviceEventId)

        let updated = vm.activeEvents.first { $0.deviceEventId == draft.deviceEventId }
        XCTAssertNil(updated?.pendingServerSnapshot,
            "acceptServerVersion must clear pendingServerSnapshot on the draft")
    }

    // MARK: — keepLocalVersion

    // AN3B-S08: keepLocalVersion clears pendingConflictId
    func test_AN3B_S08_keepLocal_clearsPendingConflictId() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 3)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)
        await vm.keepLocalVersion(deviceEventId: draft.deviceEventId)

        XCTAssertNil(vm.pendingConflictId,
            "keepLocalVersion must clear pendingConflictId")
    }

    // AN3B-S09: keepLocalVersion resets conflictRetryCount to 0
    func test_AN3B_S09_keepLocal_resetsRetryCount() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 3)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)
        await vm.keepLocalVersion(deviceEventId: draft.deviceEventId)

        let updated = vm.activeEvents.first { $0.deviceEventId == draft.deviceEventId }
        XCTAssertEqual(updated?.conflictRetryCount, 0,
            "keepLocalVersion must reset conflictRetryCount to 0 to allow re-sync")
    }

    // AN3B-S10: keepLocalVersion sets syncStatus to .localOnly (re-queues for flush)
    func test_AN3B_S10_keepLocal_setsLocalOnlyStatus() async {
        let vm     = makeVM()
        var draft  = makeDraft(retryCount: 3)
        let server = makeServerSnapshot()
        draft.deviceEventId = UUID()

        await vm.injectDraft(draft)
        await vm.resolveConflict(deviceEventId: draft.deviceEventId, serverSnapshot: server)
        await vm.keepLocalVersion(deviceEventId: draft.deviceEventId)

        let updated = vm.activeEvents.first { $0.deviceEventId == draft.deviceEventId }
        XCTAssertEqual(updated?.syncStatus, .localOnly,
            "keepLocalVersion must set syncStatus=.localOnly so flushPending() re-sends it")
    }
}

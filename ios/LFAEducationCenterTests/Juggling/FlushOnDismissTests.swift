import XCTest
@testable import LFAEducationCenter

// MARK: — FlushOnDismissTests
//
// Verifies the two flush trigger points introduced in AN-3B2A:
//
//   FPD-01  Labeling sheet dismiss (exitLabelingMode + flushPending):
//           a freshly-labeled .localOnly event reaches .synced and receives
//           a serverEventId — proving POST /contacts was issued.
//
//   FPD-02  .unlabeled events must NOT be sent by flushPending.
//           The mock's createContactResult is left at its default
//           .failure(.unauthorized), so any inadvertent POST attempt would
//           produce .retryPending or .failedPermanent — not .unlabeled.
//           The guard in AnnotationSyncEngine (syncStatus == .localOnly ||
//           .retryPending) is what guarantees this invariant.
//
//   FPC-01  Close flow (saveNow + flushPending):
//           same labeling path, but driven through the saveNow() call that
//           precedes flushPending() in performClose() — outcome identical.

@MainActor
final class FlushOnDismissTests: XCTestCase {

    private var tempDir: URL!

    override func setUp() {
        super.setUp()
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("flush_on_dismiss_tests_\(UUID().uuidString)", isDirectory: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: tempDir)
        tempDir = nil
        super.tearDown()
    }

    // MARK: — Helpers

    private func makeViewModel(apiClient: MockAnnotationAPIClient) -> JugglingAnnotationViewModel {
        JugglingAnnotationViewModel(
            userId: 1,
            videoId: "vid-flush-dismiss",
            apiClient: apiClient,
            taxonomyStore: ContactTaxonomyStore(authManager: AuthManager(), cacheDirectory: tempDir),
            localStore: LocalAnnotationStore(baseDirectory: tempDir)
        )
    }

    private func makeServerEvent(deviceEventId: UUID, contactType: String) -> ContactEventOut {
        ContactEventOut(
            eventId:                UUID(),
            deviceEventId:          deviceEventId,
            timestampMs:            1000,
            contactType:            contactType,
            side:                   "right",
            annotationConfidence:   "certain",
            annotationReviewStatus: "pending",
            taxonomyReviewStatus:   "not_applicable",
            excludedFromTraining:   true,
            customLabel:            nil,
            customDescription:      nil,
            version:                1,
            createdAt:              Date(),
            updatedAt:              Date()
        )
    }

    // FPD-01: labeling sheet onDismiss sequence — exitLabelingMode() +
    // flushPending() — uploads the freshly-labeled event to the backend.
    //
    // Precondition:  event is .localOnly after labelEvent().
    // Sequence:      vm.exitLabelingMode(); await vm.flushPending()
    //                (mirrors JugglingAnnotationScreen sheet onDismiss)
    // Postcondition: event is .synced with a serverEventId assigned.
    func test_FPD_01_labelingDismissSequenceUploadsLocalOnlyEvent() async {
        let api = MockAnnotationAPIClient()
        let vm  = makeViewModel(apiClient: api)
        await vm.onAppear()

        // Mark → enter labeling → label (produces .localOnly)
        let draft = vm.markTimestamp(ms: 1000)!
        vm.enterLabelingMode()
        let serverEvent = makeServerEvent(deviceEventId: draft.deviceEventId, contactType: "right_instep")
        api.createContactResult = .success(CreateContactResult(event: serverEvent, isNew: true))

        let labeled = vm.labelEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "right_instep",
            side:                 "right",
            annotationConfidence: "certain"
        )
        XCTAssertTrue(labeled, "labelEvent must succeed")
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .localOnly,
                       "precondition: event must be .localOnly before flush")

        // Simulate labeling sheet onDismiss
        vm.exitLabelingMode()
        await vm.flushPending()

        let result = vm.activeEvents.first
        XCTAssertEqual(result?.syncStatus, .synced,
                       "event must be .synced after exitLabelingMode + flushPending")
        XCTAssertNotNil(result?.serverEventId,
                        "serverEventId must be assigned from the backend response")
        XCTAssertEqual(result?.serverEventId, serverEvent.eventId,
                       "serverEventId must match the value returned by the server")
    }

    // FPD-02: .unlabeled events are silently skipped by flushPending.
    //
    // The mock createContactResult is left at its default .failure(.unauthorized).
    // If flushPending incorrectly attempted a POST, the event would transition
    // to .retryPending or .failedPermanent — not remain .unlabeled.
    func test_FPD_02_unlabeledEventNotSentByFlushPending() async {
        let api = MockAnnotationAPIClient()
        // Default createContactResult = .failure(.unauthorized) — intentional.
        // Any POST attempt would produce a non-.unlabeled terminal status.
        let vm = makeViewModel(apiClient: api)
        await vm.onAppear()

        vm.markTimestamp(ms: 2000)
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .unlabeled,
                       "precondition: freshly marked event must be .unlabeled")

        await vm.flushPending()

        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .unlabeled,
                       ".unlabeled event must remain .unlabeled after flushPending — no POST allowed")
    }

    // FPC-01: close-flow sequence — saveNow() + flushPending() — uploads the
    // labeled event before the screen is dismissed.
    //
    // Mirrors the performClose() body:
    //   let ok = vm.saveNow()   → local persistence
    //   await vm.flushPending() → backend upload
    //   dismiss()               → (not testable in a unit test)
    func test_FPC_01_closeFlowSequenceUploadsLocalOnlyEvent() async {
        let api = MockAnnotationAPIClient()
        let vm  = makeViewModel(apiClient: api)
        await vm.onAppear()

        let draft = vm.markTimestamp(ms: 3000)!
        vm.enterLabelingMode()
        let serverEvent = makeServerEvent(deviceEventId: draft.deviceEventId, contactType: "heel_right")
        api.createContactResult = .success(CreateContactResult(event: serverEvent, isNew: true))

        vm.labelEvent(
            deviceEventId:        draft.deviceEventId,
            contactType:          "heel_right",
            side:                 "right",
            annotationConfidence: "certain"
        )
        XCTAssertEqual(vm.activeEvents.first?.syncStatus, .localOnly,
                       "precondition: event must be .localOnly before close flow")

        // Simulate performClose() body (without the actual UI dismiss)
        let saved = vm.saveNow()
        XCTAssertTrue(saved, "saveNow() must succeed")
        await vm.flushPending()

        let result = vm.activeEvents.first
        XCTAssertEqual(result?.syncStatus, .synced,
                       "event must be .synced after saveNow + flushPending (close flow)")
        XCTAssertEqual(result?.serverEventId, serverEvent.eventId,
                       "serverEventId must match the backend response in close flow")
    }
}

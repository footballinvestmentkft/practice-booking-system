import XCTest
@testable import LFAEducationCenter

// MARK: — BallFeedbackVMTests (AN-3B2B1, BFB-01..12)
//
// All tests use MockBallFeedbackAPIClient — no real network calls.

@MainActor
final class BallFeedbackVMTests: XCTestCase {

    // MARK: — Fixtures

    private func makeItem(frameMs: Int = 1000, confidence: Double? = 0.40) -> BallFeedbackQueueItem {
        BallFeedbackQueueItem(
            frameMs: frameMs,
            priorityScore: 0.36,
            modelPredictedX: 0.5,
            modelPredictedY: 0.5,
            modelConfidence: confidence,
            modelTrackingState: confidence == nil ? "lost" : "detected",
            existingFeedbackCount: 0
        )
    }

    private func makeQueue(items: [BallFeedbackQueueItem] = [], maxPerSession: Int = 3) -> BallFeedbackQueueResponse {
        BallFeedbackQueueResponse(
            videoId: "test-video",
            queueItems: items,
            total: items.count,
            maxPerSession: maxPerSession
        )
    }

    private func makeVM(
        queueResponse: BallFeedbackQueueResponse? = nil,
        submitResult: Result<BallFeedbackOut, BallFeedbackAPIError> = .success(makeFeedbackOut())
    ) -> BallFeedbackViewModel {
        let client = MockBallFeedbackAPIClient(
            queueResponse: queueResponse,
            submitResult: submitResult
        )
        return BallFeedbackViewModel(videoId: "test-video", apiClient: client)
    }

    nonisolated private static func makeFeedbackOut() -> BallFeedbackOut {
        BallFeedbackOut(
            id: UUID(),
            videoId: UUID(),
            frameMs: 1000,
            decision: "confirm",
            approvalState: "pending",
            createdAt: Date()
        )
    }

    // MARK: — BFB-01: loadQueue → ready with items

    func test_BFB_01_loadQueue_ready() async {
        let items = [makeItem(frameMs: 1000), makeItem(frameMs: 2000), makeItem(frameMs: 3000)]
        let vm = makeVM(queueResponse: makeQueue(items: items))
        await vm.loadQueue()
        if case .ready(let loaded) = vm.sessionState {
            XCTAssertEqual(loaded.count, 3)
        } else {
            XCTFail("Expected .ready, got \(vm.sessionState)")
        }
        XCTAssertEqual(vm.currentIndex, 0)
        XCTAssertEqual(vm.submittedCount, 0)
    }

    // MARK: — BFB-02: loadQueue → empty when queue_items = []

    func test_BFB_02_loadQueue_empty() async {
        let vm = makeVM(queueResponse: makeQueue(items: []))
        await vm.loadQueue()
        XCTAssertEqual(vm.sessionState, .empty)
    }

    // MARK: — BFB-03: loadQueue → unavailable when 503 (nil response)

    func test_BFB_03_loadQueue_unavailable() async {
        let vm = makeVM(queueResponse: nil)
        await vm.loadQueue()
        XCTAssertEqual(vm.sessionState, .unavailable)
        XCTAssertFalse(vm.isAvailable)
    }

    // MARK: — BFB-04: submitFeedback → optimistic advance (submittedCount, index)

    func test_BFB_04_submitFeedback_optimisticAdvance() async {
        let items = [makeItem(frameMs: 1000), makeItem(frameMs: 2000)]
        let vm = makeVM(queueResponse: makeQueue(items: items))
        await vm.loadQueue()
        await vm.submitFeedback(decision: "confirm")
        XCTAssertEqual(vm.submittedCount, 1)
        XCTAssertEqual(vm.currentIndex, 1)
        XCTAssertEqual(vm.currentItem?.frameMs, 2000)
    }

    // MARK: — BFB-05: 409 duplicate → silent ignore, optimistic state kept

    func test_BFB_05_duplicate_409_silentIgnore() async {
        let items = [makeItem(frameMs: 1000), makeItem(frameMs: 2000)]
        let vm = makeVM(
            queueResponse: makeQueue(items: items),
            submitResult: .failure(.duplicate)
        )
        await vm.loadQueue()
        await vm.submitFeedback(decision: "confirm")
        // Optimistic state must stay (index advanced, submitted incremented)
        XCTAssertEqual(vm.submittedCount, 1)
        XCTAssertEqual(vm.currentIndex, 1)
        XCTAssertNil(vm.lastErrorMessage)
    }

    // MARK: — BFB-06: network/5xx → rollback to prior state

    func test_BFB_06_networkError_rollback() async {
        let items = [makeItem(frameMs: 1000), makeItem(frameMs: 2000)]
        let vm = makeVM(
            queueResponse: makeQueue(items: items),
            submitResult: .failure(.network)
        )
        await vm.loadQueue()
        let stateBefore = vm.sessionState
        await vm.submitFeedback(decision: "confirm")
        // Rolled back
        XCTAssertEqual(vm.sessionState, stateBefore)
        XCTAssertEqual(vm.currentIndex, 0)
        XCTAssertEqual(vm.submittedCount, 0)
        XCTAssertNotNil(vm.lastErrorMessage)
    }

    // MARK: — BFB-07: skip() → index advances, submittedCount unchanged

    func test_BFB_07_skip_advancesIndexOnly() async {
        let items = [makeItem(frameMs: 1000), makeItem(frameMs: 2000)]
        let vm = makeVM(queueResponse: makeQueue(items: items))
        await vm.loadQueue()
        vm.skip()
        XCTAssertEqual(vm.currentIndex, 1)
        XCTAssertEqual(vm.submittedCount, 0)
    }

    // MARK: — BFB-08: submittedCount >= maxPerSession → sessionComplete

    func test_BFB_08_sessionComplete_afterMaxFeedbacks() async {
        let items = (0..<5).map { makeItem(frameMs: $0 * 1000) }
        let vm = makeVM(queueResponse: makeQueue(items: items, maxPerSession: 2))
        await vm.loadQueue()
        await vm.submitFeedback(decision: "confirm")
        await vm.submitFeedback(decision: "confirm")
        XCTAssertEqual(vm.sessionState, .sessionComplete)
        XCTAssertEqual(vm.submittedCount, 2)
    }

    // MARK: — BFB-09: corrected without coords → no submit (assert guarded in prod, tested via state)

    func test_BFB_09_corrected_withoutCoords_doesNotAdvance() async {
        let items = [makeItem(frameMs: 1000)]
        let vm = makeVM(queueResponse: makeQueue(items: items))
        await vm.loadQueue()
        // assertionFailure is DEBUG-only; in test build it is a no-op via the guard return
        // We verify that index did NOT advance and submittedCount stayed 0.
        // (We call with correctedX/Y = nil which triggers the guard return, not the API.)
        await vm.submitFeedback(decision: "corrected", correctedX: nil, correctedY: nil)
        XCTAssertEqual(vm.currentIndex, 0)
        XCTAssertEqual(vm.submittedCount, 0)
    }

    // MARK: — BFB-10: corrected with coords → request built correctly (via mock capture)

    func test_BFB_10_corrected_withCoords_submits() async {
        let items = [makeItem(frameMs: 1000)]
        let mock = MockBallFeedbackAPIClient(
            queueResponse: makeQueue(items: items),
            submitResult: .success(BallFeedbackVMTests.makeFeedbackOut())
        )
        let vm = BallFeedbackViewModel(videoId: "test-video", apiClient: mock)
        await vm.loadQueue()
        await vm.submitFeedback(decision: "corrected", correctedX: 0.3, correctedY: 0.6, correctionMethod: "tap")
        XCTAssertEqual(vm.submittedCount, 1)
        XCTAssertEqual(mock.lastSubmittedRequest?.decision, "corrected")
        XCTAssertEqual(mock.lastSubmittedRequest?.correctedX, 0.3)
        XCTAssertEqual(mock.lastSubmittedRequest?.correctedY, 0.6)
        XCTAssertEqual(mock.lastSubmittedRequest?.correctionMethod, "tap")
    }

    // MARK: — BFB-11: currentItem nil when index beyond items

    func test_BFB_11_currentItem_nilWhenExhausted() async {
        let items = [makeItem(frameMs: 1000)]
        let vm = makeVM(queueResponse: makeQueue(items: items))
        await vm.loadQueue()
        vm.skip()  // exhausts the 1-item queue → .empty
        XCTAssertNil(vm.currentItem)
    }

    // MARK: — BFB-12: model context snapshot in submitted request

    func test_BFB_12_modelContextSnapshotInRequest() async {
        let item = makeItem(frameMs: 2500)
        let mock = MockBallFeedbackAPIClient(
            queueResponse: makeQueue(items: [item]),
            submitResult: .success(BallFeedbackVMTests.makeFeedbackOut())
        )
        let vm = BallFeedbackViewModel(videoId: "test-video", apiClient: mock)
        await vm.loadQueue()
        await vm.submitFeedback(decision: "confirm")
        XCTAssertEqual(mock.lastSubmittedRequest?.frameMs, 2500)
        XCTAssertEqual(mock.lastSubmittedRequest?.modelPredictedX, item.modelPredictedX)
        XCTAssertEqual(mock.lastSubmittedRequest?.modelPredictedY, item.modelPredictedY)
        XCTAssertEqual(mock.lastSubmittedRequest?.modelConfidence, item.modelConfidence)
        XCTAssertEqual(mock.lastSubmittedRequest?.modelTrackingState, item.modelTrackingState)
    }
}

// MARK: — MockBallFeedbackAPIClient

@MainActor
final class MockBallFeedbackAPIClient: JugglingAnnotationAPIClientProtocol {

    private let _queueResponse: BallFeedbackQueueResponse?
    private let _submitResult:  Result<BallFeedbackOut, BallFeedbackAPIError>
    private(set) var lastSubmittedRequest: BallFeedbackRequest?

    init(
        queueResponse: BallFeedbackQueueResponse?,
        submitResult: Result<BallFeedbackOut, BallFeedbackAPIError>
    ) {
        _queueResponse = queueResponse
        _submitResult  = submitResult
    }

    func fetchFeedbackQueue(videoId: String, limit: Int) async -> BallFeedbackQueueResponse? {
        _queueResponse
    }

    func submitBallFeedback(videoId: String, request: BallFeedbackRequest) async throws -> BallFeedbackOut {
        lastSubmittedRequest = request
        switch _submitResult {
        case .success(let out): return out
        case .failure(let err): throw err
        }
    }

    // MARK: — Unused protocol stubs

    func listContacts(videoId: String) async throws -> ContactEventListOut {
        throw AnnotationAPIError.permanent(code: 501, detail: "stub")
    }
    func createContact(videoId: String, request: ContactEventCreateRequest) async throws -> CreateContactResult {
        throw AnnotationAPIError.permanent(code: 501, detail: "stub")
    }
    func patchContact(videoId: String, eventId: UUID, request: ContactEventPatchRequest) async throws -> ContactEventOut {
        throw AnnotationAPIError.permanent(code: 501, detail: "stub")
    }
    func deleteContact(videoId: String, eventId: UUID) async throws -> DeleteContactResult { .deleted }
    func finishAnnotation(videoId: String, confirmZero: Bool) async throws -> FinishAnnotationOut {
        throw AnnotationAPIError.permanent(code: 501, detail: "stub")
    }
    func deleteVideo(videoId: String) async throws {}
    func uploadInit(sourceType: String, uploadSource: String) async throws -> JugglingUploadInitResponse {
        throw JugglingUploadError.unauthorized
    }
    func uploadVideoFile(videoId: String, fileURL: URL, mimeType: String) async throws -> JugglingUploadFileResponse {
        throw JugglingUploadError.unauthorized
    }
    func completeUpload(videoId: String) async throws -> JugglingCompleteResponse {
        throw JugglingUploadError.unauthorized
    }
    func fetchBallDetection(videoId: String, eventId: UUID) async throws -> BallDetectionOut {
        throw AnnotationAPIError.permanent(code: 404, detail: "stub")
    }
    func postBallDetection(videoId: String, eventId: UUID, request: BallDetectionManualRequest) async throws -> BallDetectionOut {
        throw AnnotationAPIError.permanent(code: 501, detail: "stub")
    }
}

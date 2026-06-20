import XCTest
@testable import LFAEducationCenter

// MARK: — BallTrainingHubVMTests (AN-3B2F PR-2, BTH-VM-01..30)
//
// All tests use MockBallTrainingAPIClient — no real networking.
// ViewModel is always created via the test init: BallTrainingHubViewModel(apiClient: mock).

// MARK: — Mock

@MainActor
final class MockBallTrainingAPIClient: BallTrainingAPIClientProtocol {

    var queueResult:    Result<GlobalTrainingQueueResponse, Error>       = .success(MockBallTrainingAPIClient.emptyQueue())
    var frameResult:    Result<Data, Error>                              = .success(MockBallTrainingAPIClient.fakeJpeg())
    var feedbackResult: Result<BallTrainingFeedbackResponse, Error>      = .success(MockBallTrainingAPIClient.fakeFeedback())

    var fetchQueueCallCount:    Int = 0
    var fetchFrameCallCount:    Int = 0
    var submitCallCount:        Int = 0
    var lastSubmittedRequest:   BallTrainingFeedbackRequest?

    func fetchQueue() async throws -> GlobalTrainingQueueResponse {
        fetchQueueCallCount += 1
        return try queueResult.get()
    }

    func fetchFrame(assignmentId: UUID) async throws -> Data {
        fetchFrameCallCount += 1
        return try frameResult.get()
    }

    func submitFeedback(_ request: BallTrainingFeedbackRequest) async throws -> BallTrainingFeedbackResponse {
        submitCallCount += 1
        lastSubmittedRequest = request
        return try feedbackResult.get()
    }

    // MARK: — Factories

    static func emptyQueue() -> GlobalTrainingQueueResponse {
        GlobalTrainingQueueResponse(tasks: [], maxPerSession: 5, totalInQueue: 0)
    }

    static func makeQueue(count: Int, maxPerSession: Int = 5) -> GlobalTrainingQueueResponse {
        let items = (0..<count).map { _ in makeItem() }
        return GlobalTrainingQueueResponse(tasks: items, maxPerSession: maxPerSession, totalInQueue: count)
    }

    static func makeItem(
        assignmentId: UUID = UUID(),
        predictedX: Double? = 0.5,
        predictedY: Double? = 0.4,
        confidence: Double? = 0.85,
        trackingState: String? = "detected"
    ) -> GlobalTrainingQueueItem {
        GlobalTrainingQueueItem(
            assignmentId: assignmentId,
            modelPredictedX: predictedX,
            modelPredictedY: predictedY,
            modelConfidence: confidence,
            modelTrackingState: trackingState,
            existingFeedbackCount: 0,
            priorityScore: 1.0,
            expiresAt: "2099-12-31T23:59:59Z"
        )
    }

    static func fakeJpeg() -> Data {
        // Minimal valid JPEG header (SOI marker)
        Data([0xFF, 0xD8, 0xFF, 0xE0])
    }

    static func fakeFeedback(decision: String = "confirm") -> BallTrainingFeedbackResponse {
        BallTrainingFeedbackResponse(
            assignmentId: UUID(),
            decision: decision,
            submittedAt: "2026-06-19T10:00:00Z",
            correctedX: nil,
            correctedY: nil
        )
    }
}

// MARK: — Test helpers

@MainActor
private func makeVM(
    queueCount: Int = 1,
    maxPerSession: Int = 5,
    queueError: Error? = nil,
    frameError: Error? = nil,
    feedbackError: Error? = nil
) -> (BallTrainingHubViewModel, MockBallTrainingAPIClient) {
    let mock = MockBallTrainingAPIClient()
    if let err = queueError {
        mock.queueResult = .failure(err)
    } else {
        mock.queueResult = .success(MockBallTrainingAPIClient.makeQueue(count: queueCount, maxPerSession: maxPerSession))
    }
    if let err = frameError {
        mock.frameResult = .failure(err)
    }
    if let err = feedbackError {
        mock.feedbackResult = .failure(err)
    }
    let vm = BallTrainingHubViewModel(apiClient: mock)
    return (vm, mock)
}

// MARK: — Tests

@MainActor
final class BallTrainingHubVMTests: XCTestCase {

    // BTH-VM-01: loadQueue transitions through loading → ready
    func test_BTH_VM_01_loadQueue_ready() async {
        let (vm, _) = makeVM(queueCount: 3)
        XCTAssertEqual(vm.sessionState, .idle)
        await vm.loadQueue(authManager: AuthManager())
        if case .ready(let items) = vm.sessionState {
            XCTAssertEqual(items.count, 3)
        } else {
            XCTFail("Expected .ready, got \(vm.sessionState)")
        }
    }

    // BTH-VM-02: loadQueue with empty queue → empty state
    func test_BTH_VM_02_loadQueue_empty() async {
        let (vm, _) = makeVM(queueCount: 0)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertEqual(vm.sessionState, .empty)
    }

    // BTH-VM-03: loadQueue with 503 → unavailable
    func test_BTH_VM_03_loadQueue_unavailable() async {
        let (vm, _) = makeVM(queueError: BallTrainingAPIError.unavailable)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertEqual(vm.sessionState, .unavailable)
    }

    // BTH-VM-04: loadQueue with 403 → forbidden
    func test_BTH_VM_04_loadQueue_forbidden() async {
        let (vm, _) = makeVM(queueError: BallTrainingAPIError.forbidden)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertEqual(vm.sessionState, .forbidden)
    }

    // BTH-VM-05: loadQueue with network error → error state
    func test_BTH_VM_05_loadQueue_networkError() async {
        let (vm, _) = makeVM(queueError: BallTrainingAPIError.network)
        await vm.loadQueue(authManager: AuthManager())
        if case .error = vm.sessionState { } else {
            XCTFail("Expected .error, got \(vm.sessionState)")
        }
    }

    // BTH-VM-06: confirm calls submitFeedback with decision="confirm"
    func test_BTH_VM_06_confirm_callsAPI() async {
        let (vm, mock) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        await vm.confirm()
        XCTAssertEqual(mock.submitCallCount, 1)
        XCTAssertEqual(mock.lastSubmittedRequest?.decision, "confirm")
        XCTAssertNil(mock.lastSubmittedRequest?.tapX)
        XCTAssertNil(mock.lastSubmittedRequest?.tapY)
    }

    // BTH-VM-07: noBall calls submitFeedback with decision="no_ball"
    func test_BTH_VM_07_noBall_callsAPI() async {
        let (vm, mock) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        await vm.noBall()
        XCTAssertEqual(mock.submitCallCount, 1)
        XCTAssertEqual(mock.lastSubmittedRequest?.decision, "no_ball")
    }

    // BTH-VM-08: corrected sends normalised tap_x/tap_y to API
    func test_BTH_VM_08_corrected_sendsTapCoords() async {
        let (vm, mock) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        await vm.corrected(tapX: 0.35, tapY: 0.72)
        XCTAssertEqual(mock.submitCallCount, 1)
        XCTAssertEqual(mock.lastSubmittedRequest?.decision, "corrected")
        XCTAssertEqual(mock.lastSubmittedRequest?.tapX ?? -1, 0.35, accuracy: 0.001)
        XCTAssertEqual(mock.lastSubmittedRequest?.tapY ?? -1, 0.72, accuracy: 0.001)
    }

    // BTH-VM-09: skip does NOT call submitFeedback
    func test_BTH_VM_09_skip_noNetworkCall() async {
        let (vm, mock) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        vm.skip()
        XCTAssertEqual(mock.submitCallCount, 0)
    }

    // BTH-VM-10: submittedCount increments after confirm
    func test_BTH_VM_10_submittedCount_increments() async {
        let (vm, _) = makeVM(queueCount: 3)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertEqual(vm.submittedCount, 0)
        await vm.confirm()
        XCTAssertEqual(vm.submittedCount, 1)
        await vm.noBall()
        XCTAssertEqual(vm.submittedCount, 2)
    }

    // BTH-VM-11: sessionComplete when submittedCount >= maxPerSession
    func test_BTH_VM_11_sessionComplete_whenMaxReached() async {
        let (vm, _) = makeVM(queueCount: 5, maxPerSession: 2)
        await vm.loadQueue(authManager: AuthManager())
        await vm.confirm()   // submitted=1
        await vm.confirm()   // submitted=2 → session complete
        XCTAssertEqual(vm.sessionState, .sessionComplete)
    }

    // BTH-VM-12: sessionComplete when all items exhausted after submits
    func test_BTH_VM_12_sessionComplete_whenItemsExhausted() async {
        let (vm, _) = makeVM(queueCount: 2, maxPerSession: 10)
        await vm.loadQueue(authManager: AuthManager())
        await vm.confirm()   // item 0 → item 1
        await vm.confirm()   // item 1 → no more items → sessionComplete
        XCTAssertEqual(vm.sessionState, .sessionComplete)
    }

    // BTH-VM-13: corrected tap values are passed verbatim in [0,1]
    func test_BTH_VM_13_correctedTap_passedVerbatim() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        await vm.corrected(tapX: 0.0, tapY: 1.0)
        XCTAssertEqual(mock.lastSubmittedRequest?.tapX ?? -1, 0.0, accuracy: 0.0001)
        XCTAssertEqual(mock.lastSubmittedRequest?.tapY ?? -1, 1.0, accuracy: 0.0001)
    }

    // BTH-VM-14: corrected tap with edge values (0.0 and 1.0) accepted
    func test_BTH_VM_14_correctedTap_edgeBounds() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        await vm.corrected(tapX: 0.001, tapY: 0.999)
        XCTAssertEqual(mock.lastSubmittedRequest?.decision, "corrected")
    }

    // BTH-VM-15: skip does not trigger fetchFrame call for first item
    func test_BTH_VM_15_skip_advancesIndex() async {
        let (vm, mock) = makeVM(queueCount: 3)
        await vm.loadQueue(authManager: AuthManager())
        let frameCallsAfterLoad = mock.fetchFrameCallCount
        vm.skip()
        // fetchCurrentFrame is async-dispatched by skip(); wait a moment
        try? await Task.sleep(nanoseconds: 50_000_000)
        XCTAssertEqual(vm.currentIndex, 1)
        XCTAssertGreaterThan(mock.fetchFrameCallCount, frameCallsAfterLoad)
        XCTAssertEqual(mock.submitCallCount, 0)
    }

    // BTH-VM-16: expired assignment (410) on frame fetch → advances silently
    func test_BTH_VM_16_frameExpired_advancesSilently() async {
        let (vm, mock) = makeVM(queueCount: 2, frameError: BallTrainingAPIError.expired)
        await vm.loadQueue(authManager: AuthManager())
        // After queue load, fetchCurrentFrame fails with expired; should advance
        // (first item expired → advance to second, second also expires → empty)
        XCTAssertEqual(vm.sessionState, .empty)
    }

    // BTH-VM-17: consumed assignment on submit → counts as success, advances
    func test_BTH_VM_17_submitConsumed_countsAsSuccess() async {
        let (vm, _) = makeVM(queueCount: 2, feedbackError: BallTrainingAPIError.consumed)
        await vm.loadQueue(authManager: AuthManager())
        await vm.confirm()
        XCTAssertEqual(vm.submittedCount, 1)
    }

    // BTH-VM-18: isSubmitting is false after submit completes
    func test_BTH_VM_18_isSubmitting_falseAfterSubmit() async {
        let (vm, _) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        await vm.confirm()
        XCTAssertFalse(vm.isSubmitting)
    }

    // BTH-VM-19: fetchCurrentFrame loads frameData on success
    func test_BTH_VM_19_fetchFrame_loadsData() async {
        let (vm, _) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertNotNil(vm.frameData)
        XCTAssertFalse(vm.isFrameLoading)
    }

    // BTH-VM-20: fetchCurrentFrame with network error → frameErrorMessage set
    func test_BTH_VM_20_fetchFrame_networkError_setsMessage() async {
        let (vm, _) = makeVM(queueCount: 1, frameError: BallTrainingAPIError.network)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertNotNil(vm.frameErrorMessage)
        XCTAssertNil(vm.frameData)
    }

    // BTH-VM-21: isFrameLoading is false after frame load
    func test_BTH_VM_21_isFrameLoading_falseAfterLoad() async {
        let (vm, _) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertFalse(vm.isFrameLoading)
    }

    // BTH-VM-22: progressText is "1/1" for single-item queue
    func test_BTH_VM_22_progressText_singleItem() async {
        let (vm, _) = makeVM(queueCount: 1, maxPerSession: 5)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertEqual(vm.progressText, "1/1")
    }

    // BTH-VM-23: progressText updates after skip
    func test_BTH_VM_23_progressText_updatesAfterSkip() async {
        let (vm, _) = makeVM(queueCount: 3, maxPerSession: 5)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertEqual(vm.progressText, "1/3")
        vm.skip()
        XCTAssertEqual(vm.progressText, "2/3")
    }

    // BTH-VM-24: currentItem returns the item at currentIndex
    func test_BTH_VM_24_currentItem_correctIndex() async {
        let id0 = UUID()
        let id1 = UUID()
        let mock = MockBallTrainingAPIClient()
        mock.queueResult = .success(GlobalTrainingQueueResponse(
            tasks: [
                MockBallTrainingAPIClient.makeItem(assignmentId: id0),
                MockBallTrainingAPIClient.makeItem(assignmentId: id1)
            ],
            maxPerSession: 5,
            totalInQueue: 2
        ))
        let vm = BallTrainingHubViewModel(apiClient: mock)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertEqual(vm.currentItem?.assignmentId, id0)
        vm.skip()
        XCTAssertEqual(vm.currentItem?.assignmentId, id1)
    }

    // BTH-VM-25: skip advances currentIndex without incrementing submittedCount
    func test_BTH_VM_25_skip_doesNotIncrementSubmitted() async {
        let (vm, _) = makeVM(queueCount: 3)
        await vm.loadQueue(authManager: AuthManager())
        vm.skip()
        XCTAssertEqual(vm.submittedCount, 0)
        XCTAssertEqual(vm.currentIndex, 1)
    }

    // BTH-VM-26: skipping all items → empty state
    func test_BTH_VM_26_skipAll_empty() async {
        let (vm, _) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        vm.skip()   // → index 1
        vm.skip()   // → no more items → empty
        XCTAssertEqual(vm.sessionState, .empty)
    }

    // BTH-VM-27: reload resets state to idle → loading → ready
    func test_BTH_VM_27_reload_resetsState() async {
        let (vm, _) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertNotEqual(vm.sessionState, .idle)
        await vm.reload(authManager: AuthManager())
        if case .ready = vm.sessionState { } else {
            XCTFail("Expected .ready after reload, got \(vm.sessionState)")
        }
    }

    // BTH-VM-28: guard prevents double-loadQueue
    func test_BTH_VM_28_guard_preventsDoubleLoad() async {
        let (vm, mock) = makeVM(queueCount: 1)
        // Call twice concurrently — guard case .idle should block the second
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await vm.loadQueue(authManager: AuthManager()) }
            group.addTask { await vm.loadQueue(authManager: AuthManager()) }
        }
        XCTAssertEqual(mock.fetchQueueCallCount, 1)
    }

    // BTH-VM-29: enterCorrectionMode / cancelCorrectionMode
    func test_BTH_VM_29_correctionMode_toggle() async {
        let (vm, _) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        XCTAssertFalse(vm.isInCorrectionMode)
        vm.enterCorrectionMode()
        XCTAssertTrue(vm.isInCorrectionMode)
        vm.cancelCorrectionMode()
        XCTAssertFalse(vm.isInCorrectionMode)
    }

    // BTH-VM-30: corrected action exits correction mode
    func test_BTH_VM_30_corrected_exitsCorrectionMode() async {
        let (vm, _) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        vm.enterCorrectionMode()
        XCTAssertTrue(vm.isInCorrectionMode)
        await vm.corrected(tapX: 0.5, tapY: 0.5)
        XCTAssertFalse(vm.isInCorrectionMode)
    }

    // BTH-VM-31: setPendingTap sets pendingTapX/Y without POST
    func test_BTH_VM_31_setPendingTap_setsPendingWithoutPOST() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.42, y: 0.67)
        XCTAssertEqual(vm.pendingTapX ?? -1, 0.42, accuracy: 0.0001)
        XCTAssertEqual(vm.pendingTapY ?? -1, 0.67, accuracy: 0.0001)
        XCTAssertEqual(mock.submitCallCount, 0, "setPendingTap must not call submitFeedback")
    }

    // BTH-VM-32: confirmCorrection sends exactly 1 POST with decision="corrected"
    func test_BTH_VM_32_confirmCorrection_submitsOnce() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.5, y: 0.5)
        await vm.confirmCorrection()
        XCTAssertEqual(mock.submitCallCount, 1)
        XCTAssertEqual(mock.lastSubmittedRequest?.decision, "corrected")
    }

    // BTH-VM-33: confirmCorrection sends pending coords as tap_x / tap_y
    func test_BTH_VM_33_confirmCorrection_sendsPendingCoords() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.38, y: 0.72)
        await vm.confirmCorrection()
        XCTAssertEqual(mock.lastSubmittedRequest?.tapX ?? -1, 0.38, accuracy: 0.0001)
        XCTAssertEqual(mock.lastSubmittedRequest?.tapY ?? -1, 0.72, accuracy: 0.0001)
    }

    // BTH-VM-34: confirmCorrection clears pending after submit
    func test_BTH_VM_34_confirmCorrection_clearsPendingAfterSubmit() async {
        let (vm, _) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.5, y: 0.5)
        await vm.confirmCorrection()
        XCTAssertNil(vm.pendingTapX)
        XCTAssertNil(vm.pendingTapY)
    }

    // BTH-VM-35: confirmCorrection exits correction mode
    func test_BTH_VM_35_confirmCorrection_exitsCorrectionMode() async {
        let (vm, _) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        vm.enterCorrectionMode()
        vm.setPendingTap(x: 0.5, y: 0.5)
        await vm.confirmCorrection()
        XCTAssertFalse(vm.isInCorrectionMode)
    }

    // BTH-VM-36: confirmCorrection without pending → 0 POST
    func test_BTH_VM_36_confirmCorrection_withoutPending_noSubmit() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.enterCorrectionMode()
        // No setPendingTap called
        await vm.confirmCorrection()
        XCTAssertEqual(mock.submitCallCount, 0,
                       "confirmCorrection must not submit when pendingTap is nil")
    }

    // BTH-VM-37: clearPendingTap → pending nil, 0 POST
    func test_BTH_VM_37_clearPendingTap_noPOST() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.5, y: 0.5)
        vm.clearPendingTap()
        XCTAssertNil(vm.pendingTapX)
        XCTAssertNil(vm.pendingTapY)
        XCTAssertEqual(mock.submitCallCount, 0)
    }

    // BTH-VM-38: cancelCorrectionMode clears pending + exits mode, 0 POST
    func test_BTH_VM_38_cancelCorrectionMode_clearsPendingAndMode() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.enterCorrectionMode()
        vm.setPendingTap(x: 0.6, y: 0.4)
        vm.cancelCorrectionMode()
        XCTAssertNil(vm.pendingTapX)
        XCTAssertNil(vm.pendingTapY)
        XCTAssertFalse(vm.isInCorrectionMode)
        XCTAssertEqual(mock.submitCallCount, 0)
    }

    // BTH-VM-39: setPendingTap twice → second overwrites first, still 0 POST
    func test_BTH_VM_39_setPendingTap_overwritesPrevious() async {
        let (vm, mock) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.3, y: 0.3)
        vm.setPendingTap(x: 0.7, y: 0.8)
        XCTAssertEqual(vm.pendingTapX ?? -1, 0.7, accuracy: 0.0001)
        XCTAssertEqual(vm.pendingTapY ?? -1, 0.8, accuracy: 0.0001)
        XCTAssertEqual(mock.submitCallCount, 0)
    }

    // BTH-VM-40: hasPendingTap is false before any tap
    func test_BTH_VM_40_hasPendingTap_falseInitially() {
        let vm = BallTrainingHubViewModel(apiClient: MockBallTrainingAPIClient())
        XCTAssertFalse(vm.hasPendingTap)
    }

    // BTH-VM-41: hasPendingTap is true after setPendingTap
    func test_BTH_VM_41_hasPendingTap_trueAfterSet() async {
        let (vm, _) = makeVM(queueCount: 1)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.5, y: 0.5)
        XCTAssertTrue(vm.hasPendingTap)
    }

    // BTH-VM-42: confirmCorrection blocked when isSubmitting=true (double-submit guard)
    func test_BTH_VM_42_confirmCorrection_blockedWhileSubmitting() async {
        let (vm, mock) = makeVM(queueCount: 2)
        await vm.loadQueue(authManager: AuthManager())
        vm.setPendingTap(x: 0.5, y: 0.5)
        vm.isSubmitting = true   // simulate in-flight request
        await vm.confirmCorrection()
        XCTAssertEqual(mock.submitCallCount, 0,
                       "confirmCorrection must be blocked when isSubmitting=true")
    }

    // BTH-VM-43: sessionComplete fires after 5 submits when maxPerSession=5
    func test_BTH_VM_43_sessionComplete_after5Submits_withLimit5() async {
        let (vm, _) = makeVM(queueCount: 5, maxPerSession: 5)
        await vm.loadQueue(authManager: AuthManager())
        await vm.confirm()  // 1
        await vm.confirm()  // 2
        await vm.confirm()  // 3
        await vm.noBall()   // 4
        await vm.noBall()   // 5 → sessionComplete
        XCTAssertEqual(vm.sessionState, .sessionComplete)
        XCTAssertEqual(vm.submittedCount, 5)
    }

    // BTH-VM-44: submittedCount resets to 0 on reload
    func test_BTH_VM_44_submittedCount_resetsOnReload() async {
        let (vm, _) = makeVM(queueCount: 3, maxPerSession: 5)
        await vm.loadQueue(authManager: AuthManager())
        await vm.confirm()
        XCTAssertEqual(vm.submittedCount, 1)
        await vm.reload(authManager: AuthManager())
        XCTAssertEqual(vm.submittedCount, 0)
    }
}

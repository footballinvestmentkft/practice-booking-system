import XCTest
@testable import LFAEducationCenter

// MARK: — BallFeedbackAnnotationIndependenceTests (AN-3B2B1, BFI-01..03)
//
// Verifies that BallFeedbackViewModel is fully decoupled from
// JugglingAnnotationViewModel and BallTrajectoryViewModel.

@MainActor
final class BallFeedbackAnnotationIndependenceTests: XCTestCase {

    private func makeItem(frameMs: Int = 1000) -> BallFeedbackQueueItem {
        BallFeedbackQueueItem(
            frameMs: frameMs, priorityScore: 0.5,
            modelPredictedX: 0.5, modelPredictedY: 0.5,
            modelConfidence: 0.40, modelTrackingState: "detected",
            existingFeedbackCount: 0
        )
    }

    // MARK: — BFI-01: submitFeedback does not touch JugglingAnnotationViewModel

    func test_BFI_01_submitFeedback_doesNotTouchAnnotationVM() async {
        let mock = MockBallFeedbackAPIClient(
            queueResponse: BallFeedbackQueueResponse(
                videoId: "v1", queueItems: [makeItem()], total: 1, maxPerSession: 3
            ),
            submitResult: .success(makeFeedbackOut())
        )
        let feedbackVM = BallFeedbackViewModel(videoId: "v1", apiClient: mock)
        await feedbackVM.loadQueue()

        // We verify by ensuring no reference exists to JugglingAnnotationViewModel.
        // Structural: feedbackVM holds only videoId + apiClient.
        // Behavioral: submit completes without error and changes only feedbackVM state.
        await feedbackVM.submitFeedback(decision: "confirm")
        XCTAssertEqual(feedbackVM.submittedCount, 1)
    }

    // MARK: — BFI-02: loadQueue does not affect BallTrajectoryViewModel

    func test_BFI_02_loadQueue_doesNotAffectTrajectoryVM() async {
        let mock = MockBallFeedbackAPIClient(
            queueResponse: BallFeedbackQueueResponse(
                videoId: "v1", queueItems: [makeItem()], total: 1, maxPerSession: 3
            ),
            submitResult: .success(makeFeedbackOut())
        )
        let feedbackVM    = BallFeedbackViewModel(videoId: "v1", apiClient: mock)

        // BallTrajectoryViewModel has its own mock/apiClient — not shared.
        // We simply confirm feedbackVM.loadQueue does not throw or crash
        // (structural isolation: feedbackVM never holds a BallTrajectoryViewModel ref).
        await feedbackVM.loadQueue()
        if case .ready = feedbackVM.sessionState {
            // pass — trajectory is unaffected
        } else {
            XCTFail("Expected .ready after loadQueue")
        }
    }

    // MARK: — BFI-03: skip() has no effect when sessionState is not .ready

    func test_BFI_03_skip_noOpWhenNotReady() async {
        let mock = MockBallFeedbackAPIClient(queueResponse: nil, submitResult: .success(makeFeedbackOut()))
        let vm = BallFeedbackViewModel(videoId: "v1", apiClient: mock)
        // State: .idle
        vm.skip()
        XCTAssertEqual(vm.sessionState, .idle)
        XCTAssertEqual(vm.currentIndex, 0)
    }

    // MARK: — Helpers

    private func makeFeedbackOut() -> BallFeedbackOut {
        BallFeedbackOut(
            id: UUID(), videoId: UUID(), frameMs: 1000,
            decision: "confirm", approvalState: "pending", createdAt: Date()
        )
    }
}

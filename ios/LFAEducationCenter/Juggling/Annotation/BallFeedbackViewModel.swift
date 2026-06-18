import Foundation
import SwiftUI

// MARK: — BallFeedbackSessionState

enum BallFeedbackSessionState: Equatable {
    case idle                            // feedback mode not active
    case loading                         // GET /ball-feedback/queue in flight
    case ready([BallFeedbackQueueItem])  // queue loaded, panel visible
    case empty                           // queue returned 0 items (or exhausted)
    case sessionComplete                 // submittedCount >= maxPerSession
    case unavailable                     // 503 — BALL_FEEDBACK_ENABLED=false
    case error(String)                   // unexpected failure

    static func == (lhs: BallFeedbackSessionState, rhs: BallFeedbackSessionState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.loading, .loading), (.empty, .empty),
             (.sessionComplete, .sessionComplete), (.unavailable, .unavailable): return true
        case (.ready(let a), .ready(let b)): return a == b
        case (.error(let a), .error(let b)): return a == b
        default: return false
        }
    }
}

// MARK: — BallFeedbackViewModel

@MainActor
final class BallFeedbackViewModel: ObservableObject {

    @Published var sessionState: BallFeedbackSessionState = .idle
    @Published var currentIndex: Int = 0
    @Published var submittedCount: Int = 0
    @Published var lastErrorMessage: String? = nil

    private(set) var maxPerSession: Int = 3
    let videoId: String
    private let apiClient: JugglingAnnotationAPIClientProtocol

    init(videoId: String, apiClient: JugglingAnnotationAPIClientProtocol) {
        self.videoId   = videoId
        self.apiClient = apiClient
    }

    // MARK: — Computed

    var currentItem: BallFeedbackQueueItem? {
        guard case .ready(let items) = sessionState,
              currentIndex < items.count else { return nil }
        return items[currentIndex]
    }

    var remainingInQueue: Int {
        guard case .ready(let items) = sessionState else { return 0 }
        return max(0, items.count - currentIndex)
    }

    var isAvailable: Bool {
        if case .unavailable = sessionState { return false }
        return true
    }

    // MARK: — Load queue

    func loadQueue() async {
        sessionState  = .loading
        submittedCount = 0
        currentIndex  = 0
        lastErrorMessage = nil

        guard let response = await apiClient.fetchFeedbackQueue(videoId: videoId, limit: 5) else {
            sessionState = .unavailable
            return
        }
        maxPerSession = response.maxPerSession
        if response.queueItems.isEmpty {
            sessionState = .empty
        } else {
            sessionState = .ready(response.queueItems)
        }
    }

    // MARK: — Skip (client-only, no API call)

    func skip() {
        guard case .ready(let items) = sessionState else { return }
        advanceQueue(items: items)
    }

    // MARK: — Submit feedback (optimistic update + rollback on failure)

    func submitFeedback(
        decision: String,
        correctedX: Double? = nil,
        correctedY: Double? = nil,
        correctionMethod: String? = nil
    ) async {
        guard case .ready(let items) = sessionState,
              currentIndex < items.count else { return }

        if decision == "corrected" {
            guard correctedX != nil, correctedY != nil else { return }
        }

        let item            = items[currentIndex]
        let priorState      = sessionState
        let priorIndex      = currentIndex
        let priorSubmitted  = submittedCount

        // Optimistic: advance UI immediately
        submittedCount += 1
        lastErrorMessage = nil
        if submittedCount >= maxPerSession {
            sessionState = .sessionComplete
        } else {
            advanceQueue(items: items)
        }

        let req = BallFeedbackRequest(
            frameMs:            item.frameMs,
            decision:           decision,
            correctedX:         correctedX,
            correctedY:         correctedY,
            correctionMethod:   correctionMethod,
            modelPredictedX:    item.modelPredictedX,
            modelPredictedY:    item.modelPredictedY,
            modelConfidence:    item.modelConfidence,
            modelTrackingState: item.modelTrackingState
        )

        do {
            _ = try await apiClient.submitBallFeedback(videoId: videoId, request: req)
        } catch BallFeedbackAPIError.duplicate {
            // 409 — frame already submitted; optimistic state is correct
        } catch {
            // Rollback to pre-submit state
            sessionState   = priorState
            currentIndex   = priorIndex
            submittedCount = priorSubmitted
            lastErrorMessage = "Hiba történt, próbáld újra."
        }
    }

    func clearError() { lastErrorMessage = nil }

    // MARK: — Private

    private func advanceQueue(items: [BallFeedbackQueueItem]) {
        let next = currentIndex + 1
        if next >= items.count {
            sessionState = .empty
        } else {
            currentIndex = next
        }
    }
}

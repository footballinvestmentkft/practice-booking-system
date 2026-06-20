import Foundation

// MARK: — BallTrainingSessionState

enum BallTrainingSessionState: Equatable {
    case idle
    case loading
    case ready([GlobalTrainingQueueItem])
    case empty
    case sessionComplete
    case unavailable
    case forbidden
    case error(String)

    static func == (lhs: BallTrainingSessionState, rhs: BallTrainingSessionState) -> Bool {
        switch (lhs, rhs) {
        case (.idle, .idle), (.loading, .loading), (.empty, .empty),
             (.sessionComplete, .sessionComplete), (.unavailable, .unavailable),
             (.forbidden, .forbidden): return true
        case (.ready(let a), .ready(let b)): return a == b
        case (.error(let a), .error(let b)): return a == b
        default: return false
        }
    }
}

// MARK: — BallTrainingHubViewModel

@MainActor
final class BallTrainingHubViewModel: ObservableObject {

    @Published var sessionState:        BallTrainingSessionState = .idle
    @Published var currentIndex:        Int  = 0
    @Published var submittedCount:      Int  = 0
    @Published var frameData:           Data? = nil
    @Published var isFrameLoading:      Bool = false
    @Published var isSubmitting:        Bool = false
    @Published var isInCorrectionMode:  Bool = false
    @Published var frameErrorMessage:   String? = nil
    @Published var lastErrorMessage:    String? = nil
    @Published var pendingTapX:         Double? = nil
    @Published var pendingTapY:         Double? = nil

    private(set) var maxPerSession: Int = 5

    // Lazily created in production from the passed-in AuthManager.
    // Injected directly in tests via the test-only init.
    private var apiClient: BallTrainingAPIClientProtocol?

    // MARK: — Init

    // SwiftUI @StateObject production init.
    init() {}

    // Test init — inject a mock client.
    init(apiClient: BallTrainingAPIClientProtocol) {
        self.apiClient = apiClient
    }

    // MARK: — Computed

    var currentItem: GlobalTrainingQueueItem? {
        guard case .ready(let items) = sessionState,
              currentIndex < items.count else { return nil }
        return items[currentIndex]
    }

    var hasPendingTap: Bool { pendingTapX != nil }

    var progressText: String {
        guard case .ready(let items) = sessionState else { return "" }
        let total = min(items.count, maxPerSession)
        return "\(min(currentIndex, total - 1) + 1)/\(total)"
    }

    // MARK: — Load queue

    func loadQueue(authManager: AuthManager) async {
        guard case .idle = sessionState else { return }
        if apiClient == nil {
            apiClient = BallTrainingAPIClient(authManager: authManager)
        }
        await _doLoadQueue()
    }

    func reload(authManager: AuthManager) async {
        sessionState = .idle
        frameData    = nil
        await loadQueue(authManager: authManager)
    }

    // MARK: — Frame loading

    func fetchCurrentFrame() async {
        guard let item = currentItem else { return }
        isFrameLoading      = true
        frameData           = nil
        frameErrorMessage   = nil

        do {
            frameData = try await apiClient!.fetchFrame(assignmentId: item.assignmentId)
        } catch BallTrainingAPIError.expired {
            frameErrorMessage = "Ez a feladat lejárt. Következő betöltése…"
            await advanceAfterFrameError()
        } catch BallTrainingAPIError.consumed {
            await advanceAfterFrameError()
        } catch BallTrainingAPIError.unavailable {
            sessionState = .unavailable
        } catch {
            frameErrorMessage = "A kép betöltése nem sikerült. Érintsd meg az újrapróbálkozáshoz."
        }
        isFrameLoading = false
    }

    // MARK: — Actions

    func confirm() async {
        await submitDecision("confirm", tapX: nil, tapY: nil)
    }

    func noBall() async {
        await submitDecision("no_ball", tapX: nil, tapY: nil)
    }

    func corrected(tapX: Double, tapY: Double) async {
        isInCorrectionMode = false
        await submitDecision("corrected", tapX: tapX, tapY: tapY)
    }

    func enterCorrectionMode() {
        isInCorrectionMode = true
        clearPendingTap()
    }

    func cancelCorrectionMode() {
        isInCorrectionMode = false
        clearPendingTap()
    }

    func setPendingTap(x: Double, y: Double) {
        pendingTapX = x
        pendingTapY = y
    }

    func confirmCorrection() async {
        guard let x = pendingTapX, let y = pendingTapY, !isSubmitting else { return }
        clearPendingTap()
        isInCorrectionMode = false
        await submitDecision("corrected", tapX: x, tapY: y)
    }

    func clearPendingTap() {
        pendingTapX = nil
        pendingTapY = nil
    }

    func skip() {
        guard !isSubmitting else { return }
        isInCorrectionMode = false
        clearPendingTap()
        guard case .ready(let items) = sessionState else { return }
        let next = currentIndex + 1
        if next >= items.count {
            sessionState = .empty
        } else {
            currentIndex = next
            frameData    = nil
            Task { await fetchCurrentFrame() }
        }
    }

    // MARK: — Private

    private func _doLoadQueue() async {
        sessionState        = .loading
        submittedCount      = 0
        currentIndex        = 0
        frameData           = nil
        lastErrorMessage    = nil
        isInCorrectionMode  = false
        clearPendingTap()

        do {
            let response    = try await apiClient!.fetchQueue()
            maxPerSession   = max(1, response.maxPerSession)
            if response.tasks.isEmpty {
                sessionState = .empty
            } else {
                sessionState = .ready(response.tasks)
                await fetchCurrentFrame()
            }
        } catch BallTrainingAPIError.unavailable {
            sessionState = .unavailable
        } catch BallTrainingAPIError.forbidden {
            sessionState = .forbidden
        } catch {
            sessionState = .error("A feladatok betöltése nem sikerült. Ellenőrizd a kapcsolatot.")
        }
    }

    private func submitDecision(_ decision: String, tapX: Double?, tapY: Double?) async {
        guard let item = currentItem, !isSubmitting else { return }
        isSubmitting        = true
        lastErrorMessage    = nil

        let req = BallTrainingFeedbackRequest(
            assignmentId: item.assignmentId,
            decision:     decision,
            tapX:         tapX,
            tapY:         tapY
        )

        do {
            _ = try await apiClient!.submitFeedback(req)
            submittedCount += 1
            await advanceAfterSubmit()
        } catch BallTrainingAPIError.consumed, BallTrainingAPIError.expired {
            submittedCount += 1
            await advanceAfterSubmit()
        } catch {
            lastErrorMessage = "Hiba történt, próbáld újra."
        }
        isSubmitting = false
    }

    private func advanceAfterSubmit() async {
        if submittedCount >= maxPerSession {
            sessionState = .sessionComplete
            return
        }
        guard case .ready(let items) = sessionState else { return }
        let next = currentIndex + 1
        if next >= items.count {
            sessionState = .sessionComplete
        } else {
            currentIndex = next
            frameData    = nil
            await fetchCurrentFrame()
        }
    }

    private func advanceAfterFrameError() async {
        guard case .ready(let items) = sessionState else { return }
        let next = currentIndex + 1
        if next >= items.count {
            sessionState = .empty
        } else {
            currentIndex = next
            frameData    = nil
            await fetchCurrentFrame()
        }
    }
}

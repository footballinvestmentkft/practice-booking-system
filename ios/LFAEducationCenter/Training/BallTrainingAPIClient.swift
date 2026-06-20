import Foundation

// MARK: — BallTrainingAPIClientProtocol

@MainActor
protocol BallTrainingAPIClientProtocol: AnyObject {
    func fetchQueue() async throws -> GlobalTrainingQueueResponse
    func fetchFrame(assignmentId: UUID) async throws -> Data
    func submitFeedback(_ request: BallTrainingFeedbackRequest) async throws -> BallTrainingFeedbackResponse
}

// MARK: — BallTrainingAPIClient

@MainActor
final class BallTrainingAPIClient: BallTrainingAPIClientProtocol {

    private let authManager: AuthManager

    init(authManager: AuthManager) {
        self.authManager = authManager
    }

    // GET /api/v1/users/me/ball-training/queue
    func fetchQueue() async throws -> GlobalTrainingQueueResponse {
        do {
            return try await authManager.authenticatedGet(
                path: "/api/v1/users/me/ball-training/queue?limit=5"
            )
        } catch APIError.httpError(503, _) {
            throw BallTrainingAPIError.unavailable
        } catch APIError.httpError(403, _) {
            throw BallTrainingAPIError.forbidden
        } catch {
            throw BallTrainingAPIError.network
        }
    }

    // GET /api/v1/users/me/ball-training/frame/{assignment_id}
    // Returns raw JPEG binary.  The server sets display_mode on the assignment
    // internally; the client normalises its tap to the displayed image size and
    // the server back-calculates full-frame coordinates.
    func fetchFrame(assignmentId: UUID) async throws -> Data {
        do {
            return try await authManager.authenticatedFetchData(
                path: "/api/v1/users/me/ball-training/frame/\(assignmentId.uuidString.lowercased())"
            )
        } catch APIError.httpError(410, _) {
            throw BallTrainingAPIError.expired
        } catch APIError.httpError(409, _) {
            throw BallTrainingAPIError.consumed
        } catch APIError.httpError(403, _) {
            throw BallTrainingAPIError.forbidden
        } catch APIError.httpError(404, _) {
            throw BallTrainingAPIError.notFound
        } catch APIError.httpError(503, _) {
            throw BallTrainingAPIError.unavailable
        } catch {
            throw BallTrainingAPIError.network
        }
    }

    // POST /api/v1/users/me/ball-training/feedback
    func submitFeedback(_ request: BallTrainingFeedbackRequest) async throws -> BallTrainingFeedbackResponse {
        do {
            return try await authManager.authenticatedPost(
                path: "/api/v1/users/me/ball-training/feedback",
                body: request
            )
        } catch APIError.httpError(410, _) {
            throw BallTrainingAPIError.expired
        } catch APIError.httpError(409, _) {
            throw BallTrainingAPIError.consumed
        } catch APIError.httpError(403, _) {
            throw BallTrainingAPIError.forbidden
        } catch {
            throw BallTrainingAPIError.network
        }
    }
}

import Foundation

// MARK: — Global Ball Training Hub — Data models (AN-3B2F PR-2)
//
// Privacy invariant: video_id, frame_ms, storage_path, and the video owner's
// identity are never received from the server.  The client only handles
// assignment_id (opaque UUID4) and model metadata needed to render the UI.

// MARK: — Queue

struct GlobalTrainingQueueItem: Decodable, Equatable, Identifiable {
    let assignmentId:           UUID
    let modelPredictedX:        Double?
    let modelPredictedY:        Double?
    let modelConfidence:        Double?
    let modelTrackingState:     String?
    let existingFeedbackCount:  Int
    let priorityScore:          Double
    let expiresAt:              String   // ISO-8601; expiry is enforced server-side (410)

    var id: UUID { assignmentId }

    enum CodingKeys: String, CodingKey {
        case assignmentId           = "assignment_id"
        case modelPredictedX        = "model_predicted_x"
        case modelPredictedY        = "model_predicted_y"
        case modelConfidence        = "model_confidence"
        case modelTrackingState     = "model_tracking_state"
        case existingFeedbackCount  = "existing_feedback_count"
        case priorityScore          = "priority_score"
        case expiresAt              = "expires_at"
    }
}

struct GlobalTrainingQueueResponse: Decodable {
    let tasks:          [GlobalTrainingQueueItem]
    let maxPerSession:  Int
    let totalInQueue:   Int

    enum CodingKeys: String, CodingKey {
        case tasks
        case maxPerSession  = "max_per_session"
        case totalInQueue   = "total_in_queue"
    }
}

// MARK: — Feedback

struct BallTrainingFeedbackRequest: Encodable {
    let assignmentId:   UUID
    let decision:       String    // "confirm" | "no_ball" | "corrected"
    let tapX:           Double?   // required when decision == "corrected"
    let tapY:           Double?

    enum CodingKeys: String, CodingKey {
        case assignmentId   = "assignment_id"
        case decision
        case tapX           = "tap_x"
        case tapY           = "tap_y"
    }
}

struct BallTrainingFeedbackResponse: Decodable {
    let assignmentId:   UUID
    let decision:       String
    let submittedAt:    String    // ISO-8601
    let correctedX:     Double?
    let correctedY:     Double?

    enum CodingKeys: String, CodingKey {
        case assignmentId   = "assignment_id"
        case decision
        case submittedAt    = "submitted_at"
        case correctedX     = "corrected_x"
        case correctedY     = "corrected_y"
    }
}

// MARK: — BallTrainingAPIError

enum BallTrainingAPIError: Error, Equatable {
    case unavailable    // 503 — BALL_TRAINING_FRAME_ENABLED=false
    case forbidden      // 403 — user not in allowlist
    case expired        // 410 — assignment expired (server-side TTL)
    case consumed       // 409 — assignment already submitted
    case notFound       // 404
    case network        // transport or unexpected failure
}

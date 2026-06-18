import Foundation

// MARK: — Ball Feedback DTOs (AN-3B2B1)
//
// Maps to the backend BallFeedbackRequest / BallFeedbackOut /
// BallFeedbackQueueItem / BallFeedbackQueueResponse schemas.
// Decisions: "confirm" | "no_ball" | "corrected"  — "skip" is client-only.

struct BallFeedbackQueueItem: Decodable, Equatable {
    let frameMs:                Int
    let priorityScore:          Double
    let modelPredictedX:        Double?
    let modelPredictedY:        Double?
    let modelConfidence:        Double?
    let modelTrackingState:     String?
    let existingFeedbackCount:  Int

    enum CodingKeys: String, CodingKey {
        case frameMs               = "frame_ms"
        case priorityScore         = "priority_score"
        case modelPredictedX       = "model_predicted_x"
        case modelPredictedY       = "model_predicted_y"
        case modelConfidence       = "model_confidence"
        case modelTrackingState    = "model_tracking_state"
        case existingFeedbackCount = "existing_feedback_count"
    }
}

struct BallFeedbackQueueResponse: Decodable {
    let videoId:       String
    let queueItems:    [BallFeedbackQueueItem]
    let total:         Int
    let maxPerSession: Int

    enum CodingKeys: String, CodingKey {
        case videoId       = "video_id"
        case queueItems    = "queue_items"
        case total
        case maxPerSession = "max_per_session"
    }
}

struct BallFeedbackRequest: Encodable {
    let frameMs:           Int
    let decision:          String    // "confirm" | "no_ball" | "corrected"
    let correctedX:        Double?   // required when decision = "corrected"
    let correctedY:        Double?
    let correctionMethod:  String?   // "tap" (D3: drag deferred)
    // Model context snapshot — populated from the queue item
    let modelPredictedX:   Double?
    let modelPredictedY:   Double?
    let modelConfidence:   Double?
    let modelTrackingState: String?

    enum CodingKeys: String, CodingKey {
        case frameMs            = "frame_ms"
        case decision
        case correctedX         = "corrected_x"
        case correctedY         = "corrected_y"
        case correctionMethod   = "correction_method"
        case modelPredictedX    = "model_predicted_x"
        case modelPredictedY    = "model_predicted_y"
        case modelConfidence    = "model_confidence"
        case modelTrackingState = "model_tracking_state"
    }
}

struct BallFeedbackOut: Decodable {
    let id:            UUID
    let videoId:       UUID
    let frameMs:       Int
    let decision:      String
    let approvalState: String
    let createdAt:     Date

    enum CodingKeys: String, CodingKey {
        case id
        case videoId       = "video_id"
        case frameMs       = "frame_ms"
        case decision
        case approvalState = "approval_state"
        case createdAt     = "created_at"
    }
}

// MARK: — BallFeedbackAPIError

enum BallFeedbackAPIError: Error, Equatable {
    case duplicate    // 409 — user already submitted for this frame
    case unavailable  // 503 — BALL_FEEDBACK_ENABLED=false
    case network      // transport or unexpected failure
}

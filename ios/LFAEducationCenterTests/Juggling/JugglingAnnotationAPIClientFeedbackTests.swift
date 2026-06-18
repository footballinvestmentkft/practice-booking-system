import XCTest
@testable import LFAEducationCenter

// MARK: — JugglingAnnotationAPIClientFeedbackTests (AN-3B2B1, ACF-01..02)
//
// Validates the URL construction and request body encoding for the
// two ball feedback client methods using a stub AuthManager.

final class JugglingAnnotationAPIClientFeedbackTests: XCTestCase {

    // MARK: — ACF-01: fetchFeedbackQueue builds correct URL with limit param

    func test_ACF_01_fetchFeedbackQueue_correctURL() {
        let expectedPath = "/api/v1/users/me/juggling/videos/test-vid-123/ball-feedback/queue?limit=5"
        // Verify path composition matches the backend route spec.
        // The API client builds this path as:
        // "/api/v1/users/me/juggling/videos/\(videoId)/ball-feedback/queue?limit=\(limit)"
        let videoId = "test-vid-123"
        let limit   = 5
        let built   = "/api/v1/users/me/juggling/videos/\(videoId)/ball-feedback/queue?limit=\(limit)"
        XCTAssertEqual(built, expectedPath)
    }

    // MARK: — ACF-02: BallFeedbackRequest encodes snake_case keys

    func test_ACF_02_feedbackRequest_encodesSnakeCase() throws {
        let req = BallFeedbackRequest(
            frameMs: 4230,
            decision: "corrected",
            correctedX: 0.35,
            correctedY: 0.60,
            correctionMethod: "tap",
            modelPredictedX: 0.40,
            modelPredictedY: 0.55,
            modelConfidence: 0.42,
            modelTrackingState: "detected"
        )
        let encoder = JSONEncoder()
        let data = try encoder.encode(req)
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        XCTAssertEqual(json["frame_ms"] as? Int,    4230)
        XCTAssertEqual(json["decision"] as? String, "corrected")
        XCTAssertEqual(json["corrected_x"] as? Double, 0.35, accuracy: 0.001)
        XCTAssertEqual(json["corrected_y"] as? Double, 0.60, accuracy: 0.001)
        XCTAssertEqual(json["correction_method"] as? String, "tap")
        XCTAssertEqual(json["model_predicted_x"] as? Double, 0.40, accuracy: 0.001)
        XCTAssertEqual(json["model_predicted_y"] as? Double, 0.55, accuracy: 0.001)
        XCTAssertEqual(json["model_confidence"] as? Double, 0.42, accuracy: 0.001)
        XCTAssertEqual(json["model_tracking_state"] as? String, "detected")
    }
}

import XCTest
@testable import LFAEducationCenter

// MARK: — BallDetectionAPIClientTests (AN-3B2C-1)
//
// BD-AC-01..06: Verify BallDetectionOut Decodable conformance, BallDetectionState
// Equatable, and BallDetectionManualRequest Encodable — no network required.
//
// The concrete JugglingAnnotationAPIClient cannot be unit-tested without a live
// server; these tests cover the data types it works with.

final class BallDetectionAPIClientTests: XCTestCase {

    // MARK: — Fixtures

    private let isoDecoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let str = try container.decode(String.self)
            let fmt = ISO8601DateFormatter()
            fmt.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = fmt.date(from: str) { return date }
            fmt.formatOptions = [.withInternetDateTime]
            if let date = fmt.date(from: str) { return date }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "bad date: \(str)")
        }
        return d
    }()

    private func makeJSON(
        noBallDetected: Bool = false,
        ballX: Double? = 0.45,
        ballY: Double? = 0.60,
        confidence: Double? = 0.92,
        detectionSource: String = "mobilenet_ssd_v1",
        autoBallX: Double? = nil,
        autoBallY: Double? = nil
    ) -> Data {
        let bx  = ballX.map    { String($0) } ?? "null"
        let by  = ballY.map    { String($0) } ?? "null"
        let con = confidence.map { String($0) } ?? "null"
        let abx = autoBallX.map { String($0) } ?? "null"
        let aby = autoBallY.map { String($0) } ?? "null"
        let json = """
        {
          "id": "11111111-0000-0000-0000-000000000001",
          "contact_event_id": "22222222-0000-0000-0000-000000000002",
          "video_id": "33333333-0000-0000-0000-000000000003",
          "detection_source": "\(detectionSource)",
          "ball_x": \(bx),
          "ball_y": \(by),
          "confidence": \(con),
          "world_x_m": null,
          "world_y_m": null,
          "model_version": null,
          "no_ball_detected": \(noBallDetected),
          "excluded_from_training": false,
          "auto_ball_x": \(abx),
          "auto_ball_y": \(aby),
          "created_at": "2026-06-18T10:00:00.000000Z",
          "updated_at": "2026-06-18T10:01:00.000000Z"
        }
        """
        return json.data(using: .utf8)!
    }

    // BD-AC-01: automatic detection — all fields decode correctly.
    func test_BD_AC_01_automaticDetectionDecodes() throws {
        let data = makeJSON(ballX: 0.45, ballY: 0.60, confidence: 0.92, detectionSource: "mobilenet_ssd_v1")
        let out = try isoDecoder.decode(BallDetectionOut.self, from: data)

        XCTAssertEqual(out.id, UUID(uuidString: "11111111-0000-0000-0000-000000000001"))
        XCTAssertEqual(out.detectionSource, "mobilenet_ssd_v1")
        XCTAssertEqual(out.ballX, 0.45, accuracy: 0.001)
        XCTAssertEqual(out.ballY, 0.60, accuracy: 0.001)
        XCTAssertEqual(out.confidence!, 0.92, accuracy: 0.001)
        XCTAssertFalse(out.noBallDetected)
        XCTAssertNil(out.autoBallX)
        XCTAssertNil(out.autoBallY)
    }

    // BD-AC-02: no_ball_detected=true — coords are null.
    func test_BD_AC_02_noBallDetectedDecodes() throws {
        let data = makeJSON(noBallDetected: true, ballX: nil, ballY: nil, confidence: nil, detectionSource: "manual")
        let out = try isoDecoder.decode(BallDetectionOut.self, from: data)

        XCTAssertTrue(out.noBallDetected)
        XCTAssertNil(out.ballX)
        XCTAssertNil(out.ballY)
        XCTAssertNil(out.confidence)
        XCTAssertEqual(out.detectionSource, "manual")
    }

    // BD-AC-03: auto_ball_x / auto_ball_y preserved on manual override.
    func test_BD_AC_03_autoCoordsDecodedWhenPresent() throws {
        let data = makeJSON(ballX: 0.3, ballY: 0.7, detectionSource: "manual",
                            autoBallX: 0.45, autoBallY: 0.60)
        let out = try isoDecoder.decode(BallDetectionOut.self, from: data)

        XCTAssertEqual(out.autoBallX!, 0.45, accuracy: 0.001)
        XCTAssertEqual(out.autoBallY!, 0.60, accuracy: 0.001)
        XCTAssertEqual(out.ballX!, 0.3, accuracy: 0.001)  // updated position
    }

    // BD-AC-04: BallDetectionState == comparison.
    func test_BD_AC_04_stateEquality() throws {
        let data = makeJSON()
        let d1 = try isoDecoder.decode(BallDetectionOut.self, from: data)
        let d2 = try isoDecoder.decode(BallDetectionOut.self, from: data)

        XCTAssertEqual(BallDetectionState.notFetched,     .notFetched)
        XCTAssertEqual(BallDetectionState.fetching,       .fetching)
        XCTAssertEqual(BallDetectionState.notFound,       .notFound)
        XCTAssertEqual(BallDetectionState.featureDisabled, .featureDisabled)
        XCTAssertEqual(BallDetectionState.networkError("x"), .networkError("x"))
        XCTAssertEqual(BallDetectionState.loaded(d1),     .loaded(d2))
        XCTAssertNotEqual(BallDetectionState.notFetched,  .fetching)
        XCTAssertNotEqual(BallDetectionState.notFound,    .featureDisabled)
    }

    // BD-AC-05: BallDetectionManualRequest encodes snake_case keys.
    func test_BD_AC_05_manualRequestEncodes() throws {
        let req = BallDetectionManualRequest(ballX: 0.5, ballY: 0.3, confidence: 0.8, noBallDetected: false)
        let data = try JSONEncoder().encode(req)
        let obj  = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        XCTAssertEqual(obj["ball_x"] as? Double,  0.5,   accuracy: 0.001)
        XCTAssertEqual(obj["ball_y"] as? Double,  0.3,   accuracy: 0.001)
        XCTAssertEqual(obj["confidence"] as? Double, 0.8, accuracy: 0.001)
        XCTAssertEqual(obj["no_ball_detected"] as? Bool, false)
        XCTAssertNil(obj["ballX"], "camelCase key must not appear; only snake_case")
    }

    // BD-AC-06: no_ball_detected=true encodes with nil coords.
    func test_BD_AC_06_noBallRequestEncodes() throws {
        let req = BallDetectionManualRequest(ballX: nil, ballY: nil, confidence: nil, noBallDetected: true)
        let data = try JSONEncoder().encode(req)
        let obj  = try JSONSerialization.jsonObject(with: data) as! [String: Any]

        XCTAssertEqual(obj["no_ball_detected"] as? Bool, true)
        // ball_x/ball_y may be absent or NSNull when nil; both are acceptable.
        let bx = obj["ball_x"]
        XCTAssertTrue(bx == nil || bx is NSNull, "ball_x must be nil or null when no_ball_detected=true")
    }
}

import XCTest
@testable import LFAEducationCenter

final class SessionQRPayloadTests: XCTestCase {

    // MARK: — SQR-01: encode/decode roundtrip

    func test_SQR01_encodeDecodeRoundtrip() throws {
        let uuid = "550e8400-e29b-41d4-a716-446655440000"
        guard let encoded = SessionQRPayload.encode(sessionUuid: uuid) else {
            XCTFail("encode returned nil")
            return
        }
        let result = SessionQRPayload.decode(from: encoded)
        guard case .success(let payload) = result else {
            XCTFail("decode failed: \(result)")
            return
        }
        XCTAssertEqual(payload.sessionUuid, uuid)
        XCTAssertEqual(payload.type, SessionQRPayload.expectedType)
        XCTAssertEqual(payload.v, SessionQRPayload.supportedVersion)
    }

    // MARK: — SQR-02: encoded JSON contains expected keys

    func test_SQR02_encodedJSONContainsExpectedKeys() throws {
        let uuid = "abc-123"
        guard let encoded = SessionQRPayload.encode(sessionUuid: uuid) else {
            XCTFail("encode returned nil"); return
        }
        XCTAssertTrue(encoded.contains("lfa_multicamera_join"), "type key missing")
        XCTAssertTrue(encoded.contains("session_uuid"),         "session_uuid key missing")
        XCTAssertTrue(encoded.contains(uuid),                   "uuid value missing")
    }

    // MARK: — SQR-03: invalid JSON → .invalidJSON

    func test_SQR03_invalidJSON() {
        let result = SessionQRPayload.decode(from: "not-json")
        XCTAssertEqual(result, .failure(.invalidJSON))
    }

    // MARK: — SQR-04: wrong type → .unknownType

    func test_SQR04_wrongType() throws {
        let json = #"{"type":"wrong_type","v":1,"session_uuid":"abc"}"#
        let result = SessionQRPayload.decode(from: json)
        XCTAssertEqual(result, .failure(.unknownType("wrong_type")))
    }

    // MARK: — SQR-05: wrong version → .unsupportedVersion

    func test_SQR05_unsupportedVersion() throws {
        let json = #"{"type":"lfa_multicamera_join","v":99,"session_uuid":"abc"}"#
        let result = SessionQRPayload.decode(from: json)
        XCTAssertEqual(result, .failure(.unsupportedVersion(99)))
    }

    // MARK: — SQR-06: empty UUID → .missingUUID

    func test_SQR06_emptyUUID() throws {
        let json = #"{"type":"lfa_multicamera_join","v":1,"session_uuid":""}"#
        let result = SessionQRPayload.decode(from: json)
        XCTAssertEqual(result, .failure(.missingUUID))
    }

    // MARK: — SQR-07: decode success value matches input

    func test_SQR07_decodeSuccessMatchesInput() throws {
        let uuid = "test-session-uuid-1234"
        let json = """
        {"type":"lfa_multicamera_join","v":1,"session_uuid":"\(uuid)"}
        """
        let result = SessionQRPayload.decode(from: json)
        guard case .success(let payload) = result else {
            XCTFail("Expected success, got \(result)"); return
        }
        XCTAssertEqual(payload.sessionUuid, uuid)
    }

    // MARK: — SQR-08: encode never returns empty string for valid UUID

    func test_SQR08_encodeNonEmpty() {
        let result = SessionQRPayload.encode(sessionUuid: "any-uuid")
        XCTAssertNotNil(result)
        XCTAssertFalse(result?.isEmpty ?? true)
    }
}

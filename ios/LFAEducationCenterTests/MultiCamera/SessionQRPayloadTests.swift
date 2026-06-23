import XCTest
@testable import LFAEducationCenter

final class SessionQRPayloadTests: XCTestCase {

    // QR-01: Round-trip encode → decode returns same UUID
    func test_QR_01_round_trip() {
        let uuid = "79597f96-85ec-469f-a491-9658d418f901"
        guard let encoded = SessionQRPayload.encode(sessionUuid: uuid) else {
            XCTFail("encode returned nil"); return
        }
        switch SessionQRPayload.decode(from: encoded) {
        case .success(let payload):
            XCTAssertEqual(payload.sessionUuid, uuid)
            XCTAssertEqual(payload.type, SessionQRPayload.expectedType)
            XCTAssertEqual(payload.v, SessionQRPayload.supportedVersion)
        case .failure(let err):
            XCTFail("decode failed: \(err)")
        }
    }

    // QR-02: Encoded JSON uses snake_case key
    func test_QR_02_encoded_uses_snake_case_key() {
        let uuid = "aaaabbbb-0000-1111-2222-ccccddddeeee"
        guard let encoded = SessionQRPayload.encode(sessionUuid: uuid) else {
            XCTFail("encode returned nil"); return
        }
        XCTAssertTrue(encoded.contains("session_uuid"), "Expected snake_case key in JSON: \(encoded)")
        XCTAssertFalse(encoded.contains("sessionUuid"), "Must not contain camelCase key")
    }

    // QR-03: Invalid JSON → invalidJSON error
    func test_QR_03_invalid_json() {
        let result = SessionQRPayload.decode(from: "not json at all")
        if case .failure(.invalidJSON) = result { } else {
            XCTFail("Expected .invalidJSON, got \(result)")
        }
    }

    // QR-04: Wrong type field → unknownType error
    func test_QR_04_wrong_type() {
        let json = #"{"type":"something_else","v":1,"session_uuid":"aaa"}"#
        let result = SessionQRPayload.decode(from: json)
        if case .failure(.unknownType(let t)) = result {
            XCTAssertEqual(t, "something_else")
        } else {
            XCTFail("Expected .unknownType, got \(result)")
        }
    }

    // QR-05: Unsupported version → unsupportedVersion error
    func test_QR_05_unsupported_version() {
        let json = #"{"type":"lfa_multicamera_join","v":99,"session_uuid":"aaa"}"#
        let result = SessionQRPayload.decode(from: json)
        if case .failure(.unsupportedVersion(let v)) = result {
            XCTAssertEqual(v, 99)
        } else {
            XCTFail("Expected .unsupportedVersion, got \(result)")
        }
    }

    // QR-06: Empty session_uuid → missingUUID error
    func test_QR_06_empty_uuid() {
        let json = #"{"type":"lfa_multicamera_join","v":1,"session_uuid":""}"#
        let result = SessionQRPayload.decode(from: json)
        if case .failure(.missingUUID) = result { } else {
            XCTFail("Expected .missingUUID, got \(result)")
        }
    }

    // QR-07: Error messages are non-empty (localized descriptions present)
    func test_QR_07_error_descriptions_non_empty() {
        let errors: [SessionQRPayload.DecodeError] = [
            .invalidJSON, .unknownType("x"), .unsupportedVersion(2), .missingUUID
        ]
        for err in errors {
            XCTAssertFalse(err.localizedDescription.isEmpty,
                           "Empty errorDescription for \(err)")
        }
    }

    // QR-08: Equatable — same payload equals itself
    func test_QR_08_equatable() {
        let uuid = "test-uuid-1234"
        let a = SessionQRPayload(type: SessionQRPayload.expectedType,
                                 v: SessionQRPayload.supportedVersion,
                                 sessionUuid: uuid)
        let b = SessionQRPayload(type: SessionQRPayload.expectedType,
                                 v: SessionQRPayload.supportedVersion,
                                 sessionUuid: uuid)
        XCTAssertEqual(a, b)
    }
}

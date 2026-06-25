import XCTest
@testable import LFAEducationCenter

final class CycleIdempotencyKeyTests: XCTestCase {

    // IDK-01: output matches pattern <8hex>:<8hex>:c<index>
    func test_IDK_01_format() {
        let key = CycleIdempotencyKey.make(sessionUuid: "AABBCCDD-1122-3344-5566-778899AABBCC", cycleIndex: 3)
        // Three colon-separated parts
        let parts = key.split(separator: ":", maxSplits: 2, omittingEmptySubsequences: false)
        XCTAssertEqual(parts.count, 3, "Key should have 3 colon-separated parts")
        // First two parts are 8 hex chars
        XCTAssertEqual(parts[0].count, 8, "Device prefix should be 8 chars")
        XCTAssertEqual(parts[1].count, 8, "Session prefix should be 8 chars")
        // Third part matches c<index>
        XCTAssertEqual(parts[2], "c3", "Cycle part should be c3")
    }

    // IDK-02: length always <= 64 chars
    func test_IDK_02_length() {
        let sessionUuid = UUID().uuidString
        for index in [0, 1, 99, 1000] {
            let key = CycleIdempotencyKey.make(sessionUuid: sessionUuid, cycleIndex: index)
            XCTAssertLessThanOrEqual(key.count, 64, "Key should be <= 64 chars for cycleIndex=\(index)")
        }
    }

    // IDK-03: same input same output (idempotent)
    func test_IDK_03_sameInputSameOutput() {
        let sessionUuid = "12345678-ABCD-EF12-3456-789ABCDEF012"
        let key1 = CycleIdempotencyKey.make(sessionUuid: sessionUuid, cycleIndex: 0)
        let key2 = CycleIdempotencyKey.make(sessionUuid: sessionUuid, cycleIndex: 0)
        XCTAssertEqual(key1, key2, "Same inputs should produce same key")
    }

    // IDK-04: different cycleIndex → different key
    func test_IDK_04_differentCycleIndexDifferentKey() {
        let sessionUuid = UUID().uuidString
        let key0 = CycleIdempotencyKey.make(sessionUuid: sessionUuid, cycleIndex: 0)
        let key1 = CycleIdempotencyKey.make(sessionUuid: sessionUuid, cycleIndex: 1)
        XCTAssertNotEqual(key0, key1, "Different cycleIndex should produce different key")
    }

    // IDK-05: different sessionUuid → different key
    func test_IDK_05_differentSessionDifferentKey() {
        let session1 = "AAAAAAAA-1111-1111-1111-AAAAAAAAAAAA"
        let session2 = "BBBBBBBB-2222-2222-2222-BBBBBBBBBBBB"
        let key1 = CycleIdempotencyKey.make(sessionUuid: session1, cycleIndex: 0)
        let key2 = CycleIdempotencyKey.make(sessionUuid: session2, cycleIndex: 0)
        XCTAssertNotEqual(key1, key2, "Different sessionUuid should produce different key")
    }
}

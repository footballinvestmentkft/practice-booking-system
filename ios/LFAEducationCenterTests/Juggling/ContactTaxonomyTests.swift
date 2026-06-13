import XCTest
import CryptoKit
@testable import LFAEducationCenter

// MARK: — AN2-T01..T04: Taxonomy bundling, decode, and validation
//
// Files are located relative to #filePath rather than via Bundle.main —
// the unit test target does not register the iOS Resources bundle, and the
// authoritative drift check (bundled copy == dataset source) is owned by
// scripts/check_taxonomy_bundle_drift.py in CI. These tests verify the
// bundled JSON decodes and validates correctly, plus the checksum constant
// is consistent with the file actually shipped.

@MainActor
final class ContactTaxonomyTests: XCTestCase {

    // ios/LFAEducationCenterTests/Juggling/ContactTaxonomyTests.swift
    //   → ../../LFAEducationCenter/Juggling/Annotation/Resources/contact_types_v1.json
    private static var bundledJSONURL: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()                  // .../Juggling
            .deletingLastPathComponent()                  // .../LFAEducationCenterTests
            .deletingLastPathComponent()                  // .../ios
            .appendingPathComponent("LFAEducationCenter/Juggling/Annotation/Resources/contact_types_v1.json")
    }

    // AN2-T01: bundled checksum constant matches the bundled file on disk.
    func test_AN2_T01_bundledChecksumMatchesShippedFile() throws {
        let data = try Data(contentsOf: Self.bundledJSONURL)
        let digest = Insecure.MD5.hash(data: data)
        let hex = digest.map { String(format: "%02x", $0) }.joined()

        XCTAssertEqual(hex, ContactTaxonomyStore.bundledChecksum,
                        "Bundled contact_types_v1.json drifted from ContactTaxonomyStore.bundledChecksum — run scripts/check_taxonomy_bundle_drift.py")
    }

    // AN2-T02: bundled JSON decodes with correct version / counts.
    func test_AN2_T02_decodeBundledHasCorrectCounts() throws {
        let data = try Data(contentsOf: Self.bundledJSONURL)
        let doc = try ContactTaxonomyStore.decodeAndValidate(data)

        XCTAssertEqual(doc.taxonomyVersion, "v1")
        XCTAssertEqual(doc.totalCount, 18)
        XCTAssertEqual(doc.stableCount, 17)
        XCTAssertEqual(doc.allKeys.count, 18)
        XCTAssertEqual(doc.stableKeys.count, 17)
    }

    // AN2-T03: custom_other present, thigh forbidden.
    func test_AN2_T03_customOtherPresentThighForbidden() throws {
        let data = try Data(contentsOf: Self.bundledJSONURL)
        let doc = try ContactTaxonomyStore.decodeAndValidate(data)

        XCTAssertTrue(doc.allKeys.contains("custom_other"))
        XCTAssertFalse(doc.allKeys.contains("thigh"))

        let customOther = doc.groups
            .flatMap(\.contactTypes)
            .first { $0.key == "custom_other" }
        XCTAssertNotNil(customOther)
        XCTAssertNil(customOther?.side)
        XCTAssertEqual(customOther?.sidePolicy, "explicit_required")
        XCTAssertFalse(customOther?.isStable ?? true)
    }

    // AN2-T04: validation rejects a taxonomy with the wrong total_count.
    func test_AN2_T04_invalidTotalCountThrows() throws {
        let validData = try Data(contentsOf: Self.bundledJSONURL)
        var json = try JSONSerialization.jsonObject(with: validData) as! [String: Any]
        json["total_count"] = 99
        let invalidData = try JSONSerialization.data(withJSONObject: json)

        XCTAssertThrowsError(try ContactTaxonomyStore.decodeAndValidate(invalidData)) { error in
            guard case TaxonomyError.invalidTotalCount(let n) = error else {
                return XCTFail("Expected invalidTotalCount, got \(error)")
            }
            XCTAssertEqual(n, 99)
        }
    }
}

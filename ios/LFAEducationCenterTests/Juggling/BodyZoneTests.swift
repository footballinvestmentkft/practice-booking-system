import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3B2A P2B-1: BodyZone → taxonomy mapping

@MainActor
final class BodyZoneTests: XCTestCase {

    private var document: TaxonomyDocument!

    override func setUpWithError() throws {
        try super.setUpWithError()
        document = try ContactTaxonomyStore.decodeBundled()
    }

    override func tearDown() {
        document = nil
        super.tearDown()
    }

    // P2B-1: every zone maps to at least one contact type, and every
    // returned type's `side` matches the zone's expected side (or is
    // center/nil for the center zones).

    func test_rightFoot_mapsToFourRightFootTypes() {
        let types = BodyZone.rightFoot.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["right_instep", "right_inside_foot", "right_outside_foot", "right_heel"])
        XCTAssertTrue(types.allSatisfy { $0.side == "right" })
    }

    func test_leftFoot_mapsToFourLeftFootTypes() {
        let types = BodyZone.leftFoot.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["left_instep", "left_inside_foot", "left_outside_foot", "left_heel"])
        XCTAssertTrue(types.allSatisfy { $0.side == "left" })
    }

    func test_rightKnee_mapsToSingleRightKneeType() {
        let types = BodyZone.rightKnee.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["right_knee"])
        XCTAssertEqual(types.first?.side, "right")
    }

    func test_leftKnee_mapsToSingleLeftKneeType() {
        let types = BodyZone.leftKnee.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["left_knee"])
        XCTAssertEqual(types.first?.side, "left")
    }

    func test_rightHip_mapsToSingleRightHipType() {
        let types = BodyZone.rightHip.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["right_hip"])
        XCTAssertEqual(types.first?.side, "right")
    }

    func test_leftHip_mapsToSingleLeftHipType() {
        let types = BodyZone.leftHip.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["left_hip"])
        XCTAssertEqual(types.first?.side, "left")
    }

    func test_chest_mapsToSingleCenterType() {
        let types = BodyZone.chest.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["chest"])
        XCTAssertEqual(types.first?.side, "center")
        XCTAssertEqual(types.first?.sidePolicy, "center")
    }

    func test_head_mapsToSingleCenterType() {
        let types = BodyZone.head.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["head"])
        XCTAssertEqual(types.first?.side, "center")
        XCTAssertEqual(types.first?.sidePolicy, "center")
    }

    func test_rightShoulder_mapsToSingleRightShoulderType() {
        let types = BodyZone.rightShoulder.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["right_shoulder"])
        XCTAssertEqual(types.first?.side, "right")
    }

    func test_leftShoulder_mapsToSingleLeftShoulderType() {
        let types = BodyZone.leftShoulder.contactTypes(in: document)
        XCTAssertEqual(types.map(\.key), ["left_shoulder"])
        XCTAssertEqual(types.first?.side, "left")
    }

    // P2B-1: "back" and "custom_other" are not reachable via any zone —
    // they remain fallback-only (list view).
    func test_backAndCustomOther_areNotCoveredByAnyZone() {
        let allMappedKeys = Set(BodyZone.allCases.flatMap { $0.contactTypes(in: document).map(\.key) })
        XCTAssertFalse(allMappedKeys.contains("back"))
        XCTAssertFalse(allMappedKeys.contains("custom_other"))
    }

    // P2B-1: all 10 zones together cover exactly the 16 stable, laterality-
    // mapped/center types (18 total - back - custom_other = 16).
    func test_allZonesCombined_coverSixteenStableTypes() {
        let allMappedKeys = BodyZone.allCases.flatMap { $0.contactTypes(in: document).map(\.key) }
        XCTAssertEqual(allMappedKeys.count, 16)
        XCTAssertEqual(Set(allMappedKeys).count, 16, "no contact type should belong to more than one zone")
    }

    // P2B-1: every zone's labelHu is non-empty (used as accessibility label in P2B-4).
    func test_everyZone_hasNonEmptyHungarianLabel() {
        for zone in BodyZone.allCases {
            XCTAssertFalse(zone.labelHu.isEmpty, "\(zone) is missing labelHu")
        }
    }
}

import XCTest
@testable import LFAEducationCenter

// MARK: — SILO-1: EmojiBodyZonePickerView model-layer tests
//
// Tests the properties and helpers introduced in SILO-1:
//   BodyZone.emoji            — emoji mapping for all 10 zones
//   BodyZone.isAutoSelect     — true for 8 single-type zones, false for 2 foot zones
//   EmojiBodyZonePickerView.shortLabel(for:) — strips "Bal "/"Jobb " prefix
//   EmojiBodyZonePickerView.minButtonHeight  — design contract ≥ 44pt
//
// Tests that are already covered by BodyZoneTests (contactTypes keys, side values,
// back/custom_other exclusion, labelHu non-empty) are NOT duplicated here.
//
// View-layer assertions (binding effects on tap, SwiftUI layout) cannot be
// exercised in XCTest without UITest infrastructure; they are covered by
// code review + the architectural contracts tested here.

@MainActor
final class SILO1EmojiZonePickerTests: XCTestCase {

    private var document: TaxonomyDocument!

    override func setUpWithError() throws {
        try super.setUpWithError()
        document = try ContactTaxonomyStore.decodeBundled()
    }

    override func tearDown() {
        document = nil
        super.tearDown()
    }

    // MARK: — Zone count (SILO-1 coverage confirmation)

    // SILO1_01: exactly 10 BodyZone cases (grid has 10 buttons total).
    func test_SILO1_01_allCases_count_is_ten() {
        XCTAssertEqual(BodyZone.allCases.count, 10,
                       "EmojiBodyZonePickerView renders exactly 10 zone buttons")
    }

    // MARK: — Emoji mapping

    // SILO1_02: every zone returns a non-empty emoji string.
    func test_SILO1_02_every_zone_has_emoji() {
        for zone in BodyZone.allCases {
            XCTAssertFalse(zone.emoji.isEmpty,
                           "\(zone).emoji must not be empty")
        }
    }

    // SILO1_03: explicit emoji values for all 10 zones.
    func test_SILO1_03_emoji_mapping_explicit() {
        XCTAssertEqual(BodyZone.head.emoji,          "🙂")
        XCTAssertEqual(BodyZone.chest.emoji,         "🫁")
        XCTAssertEqual(BodyZone.leftShoulder.emoji,  "💪")
        XCTAssertEqual(BodyZone.rightShoulder.emoji, "💪")
        XCTAssertEqual(BodyZone.leftHip.emoji,       "🦴")
        XCTAssertEqual(BodyZone.rightHip.emoji,      "🦴")
        XCTAssertEqual(BodyZone.leftKnee.emoji,      "🦵")
        XCTAssertEqual(BodyZone.rightKnee.emoji,     "🦵")
        XCTAssertEqual(BodyZone.leftFoot.emoji,      "🦶")
        XCTAssertEqual(BodyZone.rightFoot.emoji,     "🦶")
    }

    // MARK: — isAutoSelect

    // SILO1_04: the 8 non-foot zones are auto-select (exactly 1 contact type).
    func test_SILO1_04_non_foot_zones_are_auto_select() {
        let autoSelectZones: [BodyZone] = [
            .head, .chest,
            .leftShoulder, .rightShoulder,
            .leftHip, .rightHip,
            .leftKnee, .rightKnee
        ]
        for zone in autoSelectZones {
            XCTAssertTrue(zone.isAutoSelect(in: document),
                          "\(zone) should be auto-select (1 contact type)")
        }
    }

    // SILO1_05: foot zones are NOT auto-select (4 contact types each).
    func test_SILO1_05_foot_zones_are_not_auto_select() {
        XCTAssertFalse(BodyZone.leftFoot.isAutoSelect(in: document),
                       "leftFoot has 4 types — not auto-select")
        XCTAssertFalse(BodyZone.rightFoot.isAutoSelect(in: document),
                       "rightFoot has 4 types — not auto-select")
    }

    // SILO1_06: exactly 8 auto-select zones and 2 non-auto-select zones.
    func test_SILO1_06_auto_select_count() {
        let auto    = BodyZone.allCases.filter {  $0.isAutoSelect(in: document) }
        let nonAuto = BodyZone.allCases.filter { !$0.isAutoSelect(in: document) }
        XCTAssertEqual(auto.count,    8, "8 zones should be auto-select")
        XCTAssertEqual(nonAuto.count, 2, "2 foot zones should require sub-type selection")
    }

    // MARK: — Left/right label content (grid labeling correctness)

    // SILO1_07: left-side zones contain "Bal" in labelHu.
    func test_SILO1_07_left_zones_labeled_bal() {
        let leftZones: [BodyZone] = [.leftShoulder, .leftHip, .leftKnee, .leftFoot]
        for zone in leftZones {
            XCTAssertTrue(zone.labelHu.contains("Bal"),
                          "\(zone).labelHu must contain 'Bal', got: \(zone.labelHu)")
        }
    }

    // SILO1_08: right-side zones contain "Jobb" in labelHu.
    func test_SILO1_08_right_zones_labeled_jobb() {
        let rightZones: [BodyZone] = [.rightShoulder, .rightHip, .rightKnee, .rightFoot]
        for zone in rightZones {
            XCTAssertTrue(zone.labelHu.contains("Jobb"),
                          "\(zone).labelHu must contain 'Jobb', got: \(zone.labelHu)")
        }
    }

    // SILO1_09: center zones (head, chest) contain neither "Bal" nor "Jobb".
    func test_SILO1_09_center_zones_have_no_side_prefix() {
        let centerZones: [BodyZone] = [.head, .chest]
        for zone in centerZones {
            XCTAssertFalse(zone.labelHu.contains("Bal"),
                           "\(zone) should not contain 'Bal'")
            XCTAssertFalse(zone.labelHu.contains("Jobb"),
                           "\(zone) should not contain 'Jobb'")
        }
    }

    // MARK: — shortLabel (foot sub-type chips)

    // SILO1_10: shortLabel strips "Bal " prefix and capitalises correctly.
    func test_SILO1_10_shortLabel_strips_bal_prefix() {
        let leftTypes = BodyZone.leftFoot.contactTypes(in: document)
        let labels    = leftTypes.map { EmojiBodyZonePickerView.shortLabel(for: $0) }
        XCTAssertEqual(labels, ["Rüszt", "Belső", "Külső", "Sarok"],
                       "Left foot sub-type chip labels must be Rüszt/Belső/Külső/Sarok")
    }

    // SILO1_11: shortLabel strips "Jobb " prefix — produces same result as left side.
    func test_SILO1_11_shortLabel_strips_jobb_prefix() {
        let rightTypes = BodyZone.rightFoot.contactTypes(in: document)
        let labels     = rightTypes.map { EmojiBodyZonePickerView.shortLabel(for: $0) }
        XCTAssertEqual(labels, ["Rüszt", "Belső", "Külső", "Sarok"],
                       "Right foot sub-type chip labels must be Rüszt/Belső/Külső/Sarok")
    }

    // SILO1_12: shortLabel on a non-prefixed label returns it unchanged.
    func test_SILO1_12_shortLabel_no_prefix_unchanged() {
        // Create a minimal stub type with no "Bal"/"Jobb" prefix.
        let headTypes = BodyZone.head.contactTypes(in: document)
        let headType  = try! XCTUnwrap(headTypes.first)
        // "Fej" has no side prefix → must come back as-is.
        let result = EmojiBodyZonePickerView.shortLabel(for: headType)
        XCTAssertEqual(result, headType.labelHu,
                       "shortLabel on a non-prefixed type must return labelHu unchanged")
    }

    // SILO1_13: shortLabel capitalises the first character after stripping the prefix.
    func test_SILO1_13_shortLabel_capitalises_first_character() {
        let types  = BodyZone.leftFoot.contactTypes(in: document)
        let labels = types.map { EmojiBodyZonePickerView.shortLabel(for: $0) }
        for label in labels {
            let first = String(label.prefix(1))
            XCTAssertEqual(first, first.uppercased(),
                           "shortLabel '\(label)' must start with an uppercase character")
        }
    }

    // MARK: — Minimum button height design contract

    // SILO1_14: minButtonHeight is at least 44pt (HIG accessibility requirement).
    func test_SILO1_14_min_button_height_is_44pt() {
        XCTAssertGreaterThanOrEqual(
            EmojiBodyZonePickerView.minButtonHeight, 44.0,
            "All zone buttons and sub-type chips must meet the 44pt minimum tap target"
        )
    }

    // SILO1_15: minButtonHeight is declared as exactly 44pt (not over-specified).
    func test_SILO1_15_min_button_height_is_exactly_44pt() {
        XCTAssertEqual(EmojiBodyZonePickerView.minButtonHeight, 44.0)
    }
}

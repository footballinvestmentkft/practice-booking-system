import XCTest
@testable import LFAEducationCenter

// MARK: — AN-3B2: ContactPickerValidation unit tests (AN3B-C01..C08)
//
// ContactPickerValidation.canSave() and autoSide() are static helpers
// that contain all save-gating logic, unit-testable without SwiftUI.

final class ContactPickerLogicTests: XCTestCase {

    // MARK: — canSave

    // AN3B-C01: No type selected → cannot save
    func test_AN3B_C01_noTypeSelected_cannotSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   nil,
            selectedSide:  nil,
            sidePolicy:    nil,
            customLabel:   "",
            requiresLabel: false
        )
        XCTAssertFalse(result, "canSave must be false when no type is selected")
    }

    // AN3B-C02: explicit_required type, side nil → cannot save
    func test_AN3B_C02_explicitRequiredSide_noSideSelected_cannotSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   "foot_full",
            selectedSide:  nil,
            sidePolicy:    "explicit_required",
            customLabel:   "",
            requiresLabel: false
        )
        XCTAssertFalse(result, "explicit_required type must require side before save")
    }

    // AN3B-C03: explicit_required type, side selected → can save
    func test_AN3B_C03_explicitRequiredSide_sideSelected_canSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   "foot_full",
            selectedSide:  "left",
            sidePolicy:    "explicit_required",
            customLabel:   "",
            requiresLabel: false
        )
        XCTAssertTrue(result, "explicit_required type with side selected must allow save")
    }

    // AN3B-C04: fixed sidePolicy, side nil (auto-set at selection) → can save
    // (side is provided by taxonomy, not by the user; canSave does not block)
    func test_AN3B_C04_fixedSidePolicy_canSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   "foot_instep",
            selectedSide:  nil,   // fixed → side comes from type.side, shown but not toggled
            sidePolicy:    "fixed",
            customLabel:   "",
            requiresLabel: false
        )
        XCTAssertTrue(result, "fixed sidePolicy must not block save regardless of selectedSide")
    }

    // AN3B-C05: center sidePolicy, side nil → can save
    func test_AN3B_C05_centerSidePolicy_canSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   "foot_sole",
            selectedSide:  nil,
            sidePolicy:    "center",
            customLabel:   "",
            requiresLabel: false
        )
        XCTAssertTrue(result, "center sidePolicy must not block save")
    }

    // AN3B-C06: requiresLabel true, empty label → cannot save
    func test_AN3B_C06_requiresLabel_empty_cannotSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   "custom_other",
            selectedSide:  nil,
            sidePolicy:    "center",
            customLabel:   "   ",   // whitespace only
            requiresLabel: true
        )
        XCTAssertFalse(result, "requiresLabel type with blank label must block save")
    }

    // AN3B-C07: requiresLabel true, non-empty label → can save
    func test_AN3B_C07_requiresLabel_filled_canSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   "custom_other",
            selectedSide:  nil,
            sidePolicy:    "center",
            customLabel:   "belső csüd",
            requiresLabel: true
        )
        XCTAssertTrue(result, "requiresLabel type with non-empty label must allow save")
    }

    // AN3B-C08: type selected, fixed sidePolicy, no label required → can save (happy path)
    func test_AN3B_C08_happyPath_noSideNoLabel_canSave() {
        let result = ContactPickerValidation.canSave(
            selectedKey:   "foot_full",
            selectedSide:  "right",
            sidePolicy:    "explicit_required",
            customLabel:   "",
            requiresLabel: false
        )
        XCTAssertTrue(result)
    }

    // MARK: — autoSide

    // AN3B-C09: fixed sidePolicy → returns type.side (e.g. "left")
    func test_AN3B_C09_autoSide_fixed_returnsTypeSide() {
        let type = makeTaxonomyType(key: "foot_instep", sidePolicy: "fixed", side: "left")
        let side = ContactPickerValidation.autoSide(for: type)
        XCTAssertEqual(side, "left", "fixed sidePolicy must return type.side")
    }

    // AN3B-C10: center sidePolicy → returns "center" (from type.side)
    func test_AN3B_C10_autoSide_center_returnsCenter() {
        let type = makeTaxonomyType(key: "foot_sole", sidePolicy: "center", side: "center")
        let side = ContactPickerValidation.autoSide(for: type)
        XCTAssertEqual(side, "center", "center sidePolicy must return type.side")
    }

    // AN3B-C11: explicit_required sidePolicy → returns nil (user must pick)
    func test_AN3B_C11_autoSide_explicitRequired_returnsNil() {
        let type = makeTaxonomyType(key: "foot_full", sidePolicy: "explicit_required", side: nil)
        let side = ContactPickerValidation.autoSide(for: type)
        XCTAssertNil(side, "explicit_required sidePolicy must not auto-set a side")
    }

    // MARK: — Helpers

    private func makeTaxonomyType(
        key:        String,
        sidePolicy: String,
        side:       String?
    ) -> TaxonomyContactType {
        TaxonomyContactType(
            key:                       key,
            labelHu:                   "Test",
            labelEn:                   "Test",
            side:                      side,
            sidePolicy:                sidePolicy,
            isStable:                  true,
            sortOrder:                 0,
            iosIcon:                   nil,
            excludedFromTrainingAuto:  false,
            requiresCustomLabel:       nil,
            requiresCustomDescription: nil,
            requiresExplicitSide:      nil
        )
    }
}

import XCTest
@testable import LFAEducationCenter

// MARK: — SILO-2: EventLabelDetailView layout-layer tests
//
// Tests the properties introduced in SILO-2:
//   EventLabelDetailView.previewHeight(for:) — 240pt normal / 200pt SE (≤667pt)
//
// View-layer assertions (actual SwiftUI rendering, pinned bar, scroll body)
// require UITest infrastructure and are covered by the E2E checklist below.
// Model-layer regressions (detectMode, sequentialQueueIds, canSave, autoSide)
// are covered by P2CF1/P2CF2/P2CF3 tests and are not duplicated here.
//
// E2E manual test list (to be executed after SILO-2 merge):
//   SILO2_E2E_01: open labeling session from empty-queue → completionView renders
//   SILO2_E2E_02: open labeling with 3 pending events → "Cimkézés (1/3)" title shown
//   SILO2_E2E_03: preview height ≥ 200pt on all devices (screenshot: normal + SE)
//   SILO2_E2E_04: scroll body scrollable — swipe down in emoji grid, grid scrolls
//   SILO2_E2E_05: pinned bottom bar always visible when keyboard is closed
//   SILO2_E2E_06: confidence segmented picker in pinned bar — tap "Valószínű"
//   SILO2_E2E_07: back button disabled on first event when onBack == nil
//   SILO2_E2E_08: "← Áttekintő" back button visible when onBack != nil
//   SILO2_E2E_09: "Mentés és következő" on non-last event; "Mentés és befejezés" on last
//   SILO2_E2E_10: "Mentés" label in singleEdit mode
//   SILO2_E2E_11: emoji grid renders 10 zone buttons (head … rightFoot)
//   SILO2_E2E_12: tap "Fej" (auto-select) → checkmark, save button enabled
//   SILO2_E2E_13: tap "Bal lábfej" → foot expansion chips appear inline
//   SILO2_E2E_14: tap "Rüszt" chip → selectedKey set, expansion stays open
//   SILO2_E2E_15: tap "Egyéb / Lista nézet" → taxonomy list appears
//   SILO2_E2E_16: tap "← Vissza az ábrához" in taxonomy → emoji grid restored
//   SILO2_E2E_17: sequential mode — save advances to event 2, title updates
//   SILO2_E2E_18: singleEdit mode — save → navigateBack fires, screen dismissed
//   SILO2_E2E_19: singleEdit re-label from overview — form pre-populated correctly
//   SILO2_E2E_20: sequential re-open from singleEdit blocked state → missingEventView

@MainActor
final class SILO2LayoutTests: XCTestCase {

    // MARK: — previewHeight(for:) — adaptive height static helper

    // SILO2_01: standard height (667pt boundary, exclusive) → 240pt.
    func test_SILO2_01_previewHeight_standard_screen() {
        let h = EventLabelDetailView.previewHeight(for: 812)
        XCTAssertEqual(h, 240, "Screen height 812pt should return 240pt preview")
    }

    // SILO2_02: exactly at SE boundary (667pt) → 200pt (compact path).
    func test_SILO2_02_previewHeight_exactly_at_se_boundary() {
        let h = EventLabelDetailView.previewHeight(for: 667)
        XCTAssertEqual(h, 200, "Screen height 667pt (SE boundary) should return 200pt preview")
    }

    // SILO2_03: screen height below SE boundary → 200pt.
    func test_SILO2_03_previewHeight_below_se_boundary() {
        let h = EventLabelDetailView.previewHeight(for: 568)  // iPhone SE 1st gen
        XCTAssertEqual(h, 200, "Screen height 568pt should return 200pt preview")
    }

    // SILO2_04: screen height just above SE boundary → 240pt.
    func test_SILO2_04_previewHeight_just_above_se_boundary() {
        let h = EventLabelDetailView.previewHeight(for: 668)
        XCTAssertEqual(h, 240, "Screen height 668pt (just above SE) should return 240pt preview")
    }

    // SILO2_05: large screen (1366pt — iPad Pro) → 240pt.
    func test_SILO2_05_previewHeight_large_screen() {
        let h = EventLabelDetailView.previewHeight(for: 1366)
        XCTAssertEqual(h, 240, "Large screen (1366pt) should return 240pt preview")
    }

    // SILO2_06: compact height is strictly less than standard.
    func test_SILO2_06_compact_height_less_than_standard() {
        let compact  = EventLabelDetailView.previewHeight(for: 600)
        let standard = EventLabelDetailView.previewHeight(for: 800)
        XCTAssertLessThan(compact, standard,
                          "Compact preview must be shorter than standard preview")
    }

    // SILO2_07: the return type matches CGFloat (design contract).
    func test_SILO2_07_previewHeight_returns_cgfloat() {
        let h: CGFloat = EventLabelDetailView.previewHeight(for: 844)
        XCTAssertTrue(h > 0, "previewHeight must return a positive CGFloat")
    }

    // MARK: — previewHeight minimum floor

    // SILO2_08: compact height is at least 100pt (not zero or negative).
    func test_SILO2_08_compact_height_minimum_floor() {
        let h = EventLabelDetailView.previewHeight(for: 1)
        XCTAssertGreaterThanOrEqual(h, 100,
                                    "Even on a 1pt screen, previewHeight must return ≥100pt")
    }

    // SILO2_09: both compact and standard heights are positive.
    func test_SILO2_09_all_heights_positive() {
        let heights = [568, 667, 668, 736, 812, 844, 926, 932, 1024, 1366].map {
            EventLabelDetailView.previewHeight(for: CGFloat($0))
        }
        for h in heights {
            XCTAssertGreaterThan(h, 0, "previewHeight must be positive for any screen size")
        }
    }

    // MARK: — P2C-FLOW-3 regression (SILO-2 must not break mode detection)

    // SILO2_10: detectMode nil → sequential (no regression after SILO-2 refactor).
    func test_SILO2_10_detectMode_nil_is_sequential() {
        let m = EventLabelDetailView.detectMode(for: nil, syncStatus: nil)
        XCTAssertEqual(m, .sequential)
    }

    // SILO2_11: detectMode .labelPending → sequential.
    func test_SILO2_11_detectMode_labelPending_is_sequential() {
        let id = UUID()
        let m  = EventLabelDetailView.detectMode(for: id, syncStatus: .labelPending)
        XCTAssertEqual(m, .sequential)
    }

    // SILO2_12: detectMode .localOnly → singleEdit.
    func test_SILO2_12_detectMode_localOnly_is_singleEdit() {
        let id = UUID()
        let m  = EventLabelDetailView.detectMode(for: id, syncStatus: .localOnly)
        XCTAssertEqual(m, .singleEdit)
    }

    // SILO2_13: detectMode .synced → singleEdit.
    func test_SILO2_13_detectMode_synced_is_singleEdit() {
        let id = UUID()
        let m  = EventLabelDetailView.detectMode(for: id, syncStatus: .synced)
        XCTAssertEqual(m, .singleEdit)
    }

    // SILO2_14: detectMode .retryPending → singleEdit.
    func test_SILO2_14_detectMode_retryPending_is_singleEdit() {
        let id = UUID()
        let m  = EventLabelDetailView.detectMode(for: id, syncStatus: .retryPending)
        XCTAssertEqual(m, .singleEdit)
    }

    // SILO2_15: detectMode .failedPermanent → singleEdit.
    func test_SILO2_15_detectMode_failedPermanent_is_singleEdit() {
        let id = UUID()
        let m  = EventLabelDetailView.detectMode(for: id, syncStatus: .failedPermanent)
        XCTAssertEqual(m, .singleEdit)
    }
}

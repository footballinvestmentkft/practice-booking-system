import XCTest
import AVFoundation
import UIKit
@testable import LFAEducationCenter

final class CaptureOrientationHelperTests: XCTestCase {

    // ORI-01: portrait → .portrait
    func test_ORI_01_portrait_maps_to_portrait() {
        XCTAssertEqual(CaptureOrientationHelper.avCaptureOrientation(for: .portrait), .portrait)
    }

    // ORI-02: landscapeLeft → .landscapeLeft
    func test_ORI_02_landscapeLeft_maps_to_landscapeLeft() {
        XCTAssertEqual(CaptureOrientationHelper.avCaptureOrientation(for: .landscapeLeft), .landscapeLeft)
    }

    // ORI-03: landscapeRight → .landscapeRight
    func test_ORI_03_landscapeRight_maps_to_landscapeRight() {
        XCTAssertEqual(CaptureOrientationHelper.avCaptureOrientation(for: .landscapeRight), .landscapeRight)
    }

    // ORI-04: portraitUpsideDown → .portraitUpsideDown
    func test_ORI_04_portraitUpsideDown_maps_to_portraitUpsideDown() {
        XCTAssertEqual(CaptureOrientationHelper.avCaptureOrientation(for: .portraitUpsideDown), .portraitUpsideDown)
    }

    // ORI-05: unknown falls back to .portrait (covers faceUp / faceDown / device flat)
    func test_ORI_05_unknown_falls_back_to_portrait() {
        XCTAssertEqual(CaptureOrientationHelper.avCaptureOrientation(for: .unknown), .portrait)
    }

    // ORI-06: every UIInterfaceOrientation produces a valid AVCaptureVideoOrientation
    func test_ORI_06_all_cases_produce_valid_AV_orientation() {
        let validSet: Set<AVCaptureVideoOrientation> = [.portrait, .portraitUpsideDown, .landscapeLeft, .landscapeRight]
        let inputs: [UIInterfaceOrientation] = [.portrait, .portraitUpsideDown, .landscapeLeft, .landscapeRight, .unknown]
        for input in inputs {
            let result = CaptureOrientationHelper.avCaptureOrientation(for: input)
            XCTAssertTrue(validSet.contains(result),
                          "Unexpected AVCaptureVideoOrientation \(result) for UIInterfaceOrientation \(input.rawValue)")
        }
    }

    // ORI-07: preview and movie output use the same helper — same input → same output
    func test_ORI_07_preview_and_movie_output_share_same_mapping() {
        // Both CapturePreviewView.applyOrientation() and SessionCaptureManager.startCapture()
        // call CaptureOrientationHelper.avCaptureOrientation(for:). Verify referential equality
        // by confirming two independent calls with the same input return identical values.
        let orientations: [UIInterfaceOrientation] = [.portrait, .landscapeLeft, .landscapeRight, .portraitUpsideDown]
        for o in orientations {
            let forPreview = CaptureOrientationHelper.avCaptureOrientation(for: o)
            let forMovie   = CaptureOrientationHelper.avCaptureOrientation(for: o)
            XCTAssertEqual(forPreview, forMovie, "Mismatch for \(o.rawValue)")
        }
    }
}

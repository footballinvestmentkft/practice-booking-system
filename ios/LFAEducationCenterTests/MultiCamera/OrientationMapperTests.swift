import XCTest
import AVFoundation
@testable import LFAEducationCenter

final class OrientationMapperTests: XCTestCase {

    // OM-01..04: AVCaptureVideoOrientation.landscapeLeft/Right are defined
    // OPPOSITE to UIInterfaceOrientation.landscapeLeft/Right (a documented
    // AVFoundation quirk) — these tests pin that exact inversion so a future
    // "simplification" doesn't silently re-break physical landscape recording.
    func test_OM_01_portraitMapsToPortrait() {
        XCTAssertEqual(OrientationMapper.captureOrientation(for: .portrait), .portrait)
    }

    func test_OM_02_portraitUpsideDownMapsToPortraitUpsideDown() {
        XCTAssertEqual(OrientationMapper.captureOrientation(for: .portraitUpsideDown), .portraitUpsideDown)
    }

    func test_OM_03_interfaceLandscapeLeftMapsToCaptureLandscapeRight() {
        XCTAssertEqual(OrientationMapper.captureOrientation(for: .landscapeLeft), .landscapeRight)
    }

    func test_OM_04_interfaceLandscapeRightMapsToCaptureLandscapeLeft() {
        XCTAssertEqual(OrientationMapper.captureOrientation(for: .landscapeRight), .landscapeLeft)
    }

    func test_OM_05_unknownFallsBackToPortrait() {
        XCTAssertEqual(OrientationMapper.captureOrientation(for: .unknown), .portrait)
    }

    // OM-06: applyCurrentOrientation no-ops safely on a connection that
    // doesn't support orientation (or a nil connection) — must not crash.
    func test_OM_06_applyCurrentOrientation_nilConnectionDoesNotCrash() {
        OrientationMapper.applyCurrentOrientation(to: nil)
        // No assertion needed beyond "did not crash" — XCTest fails on trap.
    }
}

import XCTest
@testable import LFAEducationCenter

final class CaptureFormatSelectorTests: XCTestCase {

    // CFS-01: 720p@30 available → picks it, profile = .hd720
    func test_CFS_01_prefers720pWhenAvailable() {
        let descriptors = [
            FormatDescriptor(width: 1920, height: 1080, maxFrameRate: 60),
            FormatDescriptor(width: 1280, height: 720, maxFrameRate: 30),
            FormatDescriptor(width: 640, height: 360, maxFrameRate: 30),
        ]
        let result = CaptureFormatSelector.select(from: descriptors)
        XCTAssertEqual(result?.descriptor, FormatDescriptor(width: 1280, height: 720, maxFrameRate: 30))
        XCTAssertEqual(result?.profile.label, "1280x720")
    }

    // CFS-02: only 360p available → falls back, profile = .sd360
    func test_CFS_02_fallsBackTo360pWhen720pUnavailable() {
        let descriptors = [
            FormatDescriptor(width: 640, height: 360, maxFrameRate: 30),
            FormatDescriptor(width: 320, height: 240, maxFrameRate: 30),
        ]
        let result = CaptureFormatSelector.select(from: descriptors)
        XCTAssertEqual(result?.descriptor, FormatDescriptor(width: 640, height: 360, maxFrameRate: 30))
        XCTAssertEqual(result?.profile.label, "640x360")
    }

    // CFS-03: neither available → nil, no silent fallback to something else
    func test_CFS_03_neitherAvailableReturnsNil() {
        let descriptors = [
            FormatDescriptor(width: 1920, height: 1080, maxFrameRate: 60),
            FormatDescriptor(width: 320, height: 240, maxFrameRate: 15),
        ]
        XCTAssertNil(CaptureFormatSelector.select(from: descriptors))
    }

    // CFS-04: 720p exists but only below 30fps → rejected, falls through to 360p
    func test_CFS_04_rejects720pBelowTargetFPS() {
        let descriptors = [
            FormatDescriptor(width: 1280, height: 720, maxFrameRate: 24),
            FormatDescriptor(width: 640, height: 360, maxFrameRate: 30),
        ]
        let result = CaptureFormatSelector.select(from: descriptors)
        XCTAssertEqual(result?.profile.label, "640x360")
    }

    // CFS-05: multiple 720p variants (e.g. 30fps and 60fps-capable format) → picks the
    // tightest fit at/above target, not necessarily the highest-fps one.
    func test_CFS_05_picksTightestFitAboveTarget() {
        let descriptors = [
            FormatDescriptor(width: 1280, height: 720, maxFrameRate: 60),
            FormatDescriptor(width: 1280, height: 720, maxFrameRate: 30),
        ]
        let result = CaptureFormatSelector.select(from: descriptors)
        XCTAssertEqual(result?.descriptor.maxFrameRate, 30)
    }

    // CFS-06: empty descriptor list → nil
    func test_CFS_06_emptyListReturnsNil() {
        XCTAssertNil(CaptureFormatSelector.select(from: []))
    }
}

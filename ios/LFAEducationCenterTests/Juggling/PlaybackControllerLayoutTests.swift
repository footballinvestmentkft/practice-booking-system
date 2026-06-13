import XCTest
import AVFoundation
@testable import LFAEducationCenter

// MARK: — AN-3B2: PlaybackController.displaySize + videoNaturalSize (AN3B-V10..V13)
//
// PlaybackController.displaySize(naturalSize:preferredTransform:) is a static
// helper exposed specifically for unit testing without a real AVAsset.
// It applies the preferredTransform and returns abs(width) × abs(height).

@MainActor
final class PlaybackControllerLayoutTests: XCTestCase {

    // AN3B-V10: landscape video (1920×1080, identity transform) → width > height
    func test_AN3B_V10_landscapeVideoIsWide() {
        let size      = CGSize(width: 1920, height: 1080)
        let transform = CGAffineTransform.identity
        let result    = PlaybackController.displaySize(naturalSize: size, preferredTransform: transform)
        XCTAssertGreaterThan(result.width, result.height,
            "Landscape video must have width > height after transform")
        XCTAssertEqual(result.width, 1920, accuracy: 0.5)
        XCTAssertEqual(result.height, 1080, accuracy: 0.5)
    }

    // AN3B-V11: portrait video (1080×1920, identity transform) → height > width
    func test_AN3B_V11_portraitVideoIsTall() {
        let size      = CGSize(width: 1080, height: 1920)
        let transform = CGAffineTransform.identity
        let result    = PlaybackController.displaySize(naturalSize: size, preferredTransform: transform)
        XCTAssertGreaterThan(result.height, result.width,
            "Portrait video must have height > width after transform")
        XCTAssertEqual(result.width, 1080, accuracy: 0.5)
        XCTAssertEqual(result.height, 1920, accuracy: 0.5)
    }

    // AN3B-V12: landscape video stored rotated 90° CCW (naturalSize 1080×1920,
    // preferredTransform = 90° rotation) → display is 1920×1080 → width > height
    func test_AN3B_V12_rotated90DegreesProducesLandscapeDisplay() {
        // Camera-recorded landscape: naturalSize is portrait due to sensor orientation,
        // preferredTransform rotates it 90° counter-clockwise to display as landscape.
        let naturalSize = CGSize(width: 1080, height: 1920)
        let transform   = CGAffineTransform(rotationAngle: .pi / 2)   // 90° CCW
        let result      = PlaybackController.displaySize(naturalSize: naturalSize, preferredTransform: transform)
        // After 90° rotation: displayed width ≈ 1920, height ≈ 1080
        XCTAssertGreaterThan(result.width, result.height,
            "After 90° rotation a landscape video must display as wide (width > height)")
    }

    // AN3B-V13: videoNaturalSize is nil before loadAsset is called
    func test_AN3B_V13_videoNaturalSizeIsNilBeforeLoad() {
        let controller = PlaybackController(player: MockPlayer())
        XCTAssertNil(controller.videoNaturalSize,
            "videoNaturalSize must be nil until loadAsset() is called")
    }

    // AN3B-V14: mirrored transform (negative scale) produces positive dimensions
    func test_AN3B_V14_mirroredTransformProducesPositiveDimensions() {
        let size      = CGSize(width: 1920, height: 1080)
        let transform = CGAffineTransform(scaleX: -1, y: 1)   // horizontal mirror
        let result    = PlaybackController.displaySize(naturalSize: size, preferredTransform: transform)
        XCTAssertGreaterThan(result.width, 0,  "width must be positive after abs()")
        XCTAssertGreaterThan(result.height, 0, "height must be positive after abs()")
        XCTAssertEqual(result.width,  1920, accuracy: 0.5)
        XCTAssertEqual(result.height, 1080, accuracy: 0.5)
    }
}

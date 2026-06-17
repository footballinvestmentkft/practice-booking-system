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

    // MARK: — computeVideoRenderSize (RC-06..RC-11)
    //
    // Verifies that the render frame assigned to AVPlayerLayerView before
    // .rotationEffect() always fits entirely within the container, for all
    // four user-rotation values.

    // RC-06: landscape 1280×720 at 0° — render width fills container width
    func test_RC06_landscapeAt0DegreeFillsContainerWidth() {
        let renderSize = PlaybackController.computeVideoRenderSize(
            videoSize:    CGSize(width: 1280, height: 720),
            container:    CGSize(width: 390, height: 221),
            userRotation: 0
        )
        XCTAssertLessThanOrEqual(renderSize.width,  390 + 0.5, "renderW must not overflow container width")
        XCTAssertLessThanOrEqual(renderSize.height, 221 + 0.5, "renderH must not overflow container height")
        // 16:9 in 390 wide → height ≈ 219 (width-constrained)
        XCTAssertEqual(renderSize.width, 390, accuracy: 1.0)
        XCTAssertEqual(renderSize.height, 390 * (720.0 / 1280.0), accuracy: 1.0)
    }

    // RC-07: landscape 1280×720 at 90° — visual footprint (renderH × renderW) fits in container
    func test_RC07_landscapeAt90DegreesVisualFootprintFitsContainer() {
        let renderSize = PlaybackController.computeVideoRenderSize(
            videoSize:    CGSize(width: 1280, height: 720),
            container:    CGSize(width: 390, height: 221),
            userRotation: 90
        )
        // After 90° rotation: visual width = renderH, visual height = renderW
        let visualW = renderSize.height
        let visualH = renderSize.width
        XCTAssertLessThanOrEqual(visualW, 390 + 0.5, "visual width after 90° must fit container width")
        XCTAssertLessThanOrEqual(visualH, 221 + 0.5, "visual height after 90° must fit container height")
        // Render frame itself must not overflow either (pre-rotation bounds)
        XCTAssertLessThanOrEqual(renderSize.width,  390 + 0.5)
        XCTAssertLessThanOrEqual(renderSize.height, 221 + 0.5)
    }

    // RC-08: landscape 1280×720 at 270° — same scale as 90° (symmetric)
    func test_RC08_landscapeAt270DegreesSameScaleAs90() {
        let at90  = PlaybackController.computeVideoRenderSize(
            videoSize: CGSize(width: 1280, height: 720),
            container: CGSize(width: 390, height: 221),
            userRotation: 90
        )
        let at270 = PlaybackController.computeVideoRenderSize(
            videoSize: CGSize(width: 1280, height: 720),
            container: CGSize(width: 390, height: 221),
            userRotation: 270
        )
        XCTAssertEqual(at90.width,  at270.width,  accuracy: 0.01)
        XCTAssertEqual(at90.height, at270.height, accuracy: 0.01)
    }

    // RC-09: landscape 1280×720 at 180° — same renderSize as 0°
    func test_RC09_landscapeAt180DegreesSameScaleAs0() {
        let at0   = PlaybackController.computeVideoRenderSize(
            videoSize: CGSize(width: 1280, height: 720),
            container: CGSize(width: 390, height: 221),
            userRotation: 0
        )
        let at180 = PlaybackController.computeVideoRenderSize(
            videoSize: CGSize(width: 1280, height: 720),
            container: CGSize(width: 390, height: 221),
            userRotation: 180
        )
        XCTAssertEqual(at0.width,  at180.width,  accuracy: 0.01)
        XCTAssertEqual(at0.height, at180.height, accuracy: 0.01)
    }

    // RC-10: portrait video 720×1280 at 0° — height-constrained, fits container
    func test_RC10_portraitAt0DegreeHeightConstrained() {
        let renderSize = PlaybackController.computeVideoRenderSize(
            videoSize:    CGSize(width: 720, height: 1280),
            container:    CGSize(width: 390, height: 221),
            userRotation: 0
        )
        XCTAssertLessThanOrEqual(renderSize.width,  390 + 0.5)
        XCTAssertLessThanOrEqual(renderSize.height, 221 + 0.5)
        // height-constrained: renderH ≈ 221, renderW ≈ 221 * (720/1280) ≈ 124
        XCTAssertEqual(renderSize.height, 221, accuracy: 1.0)
    }

    // RC-11: zero-size video — guard returns container size without crashing
    func test_RC11_zeroSizeVideoReturnsContainerFallback() {
        let container  = CGSize(width: 390, height: 221)
        let renderSize = PlaybackController.computeVideoRenderSize(
            videoSize:    CGSize.zero,
            container:    container,
            userRotation: 0
        )
        XCTAssertEqual(renderSize.width,  container.width,  accuracy: 0.01)
        XCTAssertEqual(renderSize.height, container.height, accuracy: 0.01)
    }
}
